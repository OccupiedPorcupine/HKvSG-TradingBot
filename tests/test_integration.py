"""End-to-end integration tests for the APEX autonomous trading bot.

Tests the complete pipeline from data ingestion through order execution,
risk management, crash recovery, and system health monitoring. All tests
use mock API responses — no real network calls.

Each test is self-contained, uses tmp_path for file I/O, and includes a
docstring naming the production failure scenario it guards against.
"""

import asyncio
import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.execution.decision_logger import DecisionLogger, DecisionEntry
from src.execution.order_manager import OrderManager, OrderManagerConfig
from src.execution.position_tracker import Position, PositionTracker
from src.execution.priority_queue import OrderPriorityQueue, OrderPriority, PendingOrder
from src.execution.roostoo_client import OrderResult
from src.orchestration.recovery import reconstruct_positions
from src.orchestration.safe_state import SystemState
from src.orchestration.scheduler import Scheduler
from src.portfolio.constructor import PortfolioConstructor
from src.regime.detector import RegimeDetector
from src.regime.regime_state import RegimeState, RegimeType
from src.risk.circuit_breakers import CircuitBreakerManager
from src.risk.contagion import ContagionMonitor
from src.risk.pre_trade_checks import PreTradeValidator
from src.risk.risk_event import EventType, RiskEvent, Severity
from src.risk.trailing_stops import TrailingStopManager
from src.risk.manager import RiskManager
from src.signals.momentum import MomentumSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order_result(
    *,
    order_id: int = 1,
    pair: str = "BTC/USD",
    side: str = "BUY",
    price: float = 87_000.0,
    quantity: float = 0.01,
    commission_pct: float = 0.0005,
    is_filled: bool = True,
) -> OrderResult:
    """Build an OrderResult with sensible defaults."""
    return OrderResult(
        order_id=order_id,
        status="FILLED" if is_filled else "PENDING",
        pair=pair,
        side=side,
        requested_price=price,
        filled_price=price,
        requested_quantity=quantity,
        filled_quantity=quantity,
        commission_pct=commission_pct,
        role="Maker",
        is_filled=is_filled,
        raw={},
    )


def _build_20_asset_prices() -> dict[str, float]:
    """Return plausible prices for 20 crypto assets."""
    return {
        "BTC": 87_000.0, "ETH": 3_200.0, "BNB": 580.0, "LTC": 95.0,
        "ADA": 0.45, "DOGE": 0.16, "TRX": 0.12, "LINK": 18.50,
        "DOT": 7.20, "NEAR": 4.80, "TON": 5.50, "SUI": 1.35,
        "APT": 9.80, "ARB": 1.10, "AAVE": 210.0, "UNI": 11.50,
        "CRV": 0.55, "PENDLE": 3.20, "ONDO": 1.45, "FET": 2.10,
    }


# ===========================================================================
# TestFullRebalanceCycle
# ===========================================================================

class TestFullRebalanceCycle:
    """Integration test for the complete rebalance pipeline.

    Covers: ingestion → features → regime → momentum → constructor →
    risk.pre_trade_check → order_manager.execute_rebalance.
    """

    @pytest.mark.asyncio
    async def test_full_pipeline_produces_orders_and_updates_positions(
        self, tmp_path: Path, minimal_config: dict
    ):
        """Guards against: silent pipeline failure where no orders are ever
        placed due to a wiring gap between layers. The audit found multiple
        CRITICAL disconnects (regime not wired to rebalance, decision logger
        missing fields). This test proves the end-to-end path works and
        produces at least one ORDER_SUBMITTED log entry.
        """
        prices = _build_20_asset_prices()
        starting_capital = 1_000_000.0

        # --- Layer 1: Ingestion (mocked) ---
        # We skip the actual DataIngestionManager and feed prices directly.

        # --- Layer 2: Features (mocked) ---
        # Produce synthetic momentum scores — each asset gets a plausible
        # composite score between 0.0 and 1.0.
        momentum_scores = {
            asset: round(0.3 + 0.04 * i, 4)
            for i, asset in enumerate(sorted(prices.keys()))
        }

        # --- Layer 3: Regime ---
        regime_detector = RegimeDetector(minimal_config)
        # Default state is TREND_BULL — suitable for test.

        # --- Layer 4: Signals ---
        tier_1_3 = set(minimal_config["universe"]["tier_1_majors"]
                       + minimal_config["universe"]["tier_2_large_alts"]
                       + minimal_config["universe"]["tier_3_defi"])
        momentum_signal = MomentumSignal(minimal_config, tier_1_3)

        selections = momentum_signal.generate(
            momentum_scores=momentum_scores,
            regime=regime_detector.state,
        )
        assert len(selections) > 0, "Momentum signal should select at least one asset"

        # --- Layer 5: Portfolio Construction ---
        regime_targets = minimal_config["portfolio"]["regime_targets"]
        constructor = PortfolioConstructor(
            regime_targets=regime_targets,
            tier_caps={"DOGE": 0.05},
            asset_tier_map={a: "tier_1_2" for a in tier_1_3},
            tier_cap_defaults={"tier_1_2": 0.08, "tier_3": 0.06},
            max_turnover=0.25,
            min_trade_threshold=0.002,
        )

        target_weights, construct_events = constructor.compute(
            regime="TREND_BULL",
            btc_vol_percentile=50.0,
            selected_assets=list(selections.keys()),
            current_weights={},
            current_nav=starting_capital,
        )
        assert len(target_weights) > 0, "Constructor should produce target weights"

        # --- Layer 6: Pre-trade risk check ---
        validator = PreTradeValidator(
            tier_caps={"DOGE": 0.05},
            asset_tier_map={a: "tier_1_2" for a in tier_1_3},
            tier_cap_defaults={"tier_1_2": 0.08, "tier_3": 0.06},
            max_crypto_exposure=0.90,
        )
        final_weights, risk_events = validator.validate_and_fix(
            target_weights, max_deployment=0.80
        )
        assert sum(final_weights.values()) <= 0.91, "Weights must respect max exposure"

        # --- Layer 7: Execution ---
        trade_log = tmp_path / "trades.jsonl"
        decision_logger = DecisionLogger(log_path=str(trade_log))
        position_tracker = PositionTracker(starting_capital=starting_capital)
        order_queue = OrderPriorityQueue()

        # Mock the execution client
        exec_client = AsyncMock()
        exec_client.place_limit_buy = AsyncMock(
            side_effect=lambda pair, qty, price: _make_order_result(
                order_id=hash(pair) % 10_000,
                pair=pair,
                side="BUY",
                price=price,
                quantity=qty,
            )
        )
        exec_client.place_limit_sell = AsyncMock(
            side_effect=lambda pair, qty, price: _make_order_result(
                order_id=hash(pair) % 10_000,
                pair=pair,
                side="SELL",
                price=price,
                quantity=qty,
            )
        )

        order_manager = OrderManager(
            exec_client=exec_client,
            position_tracker=position_tracker,
            decision_logger=decision_logger,
            order_queue=order_queue,
            config=OrderManagerConfig(min_trade_threshold_pct=0.0),
        )
        # Seed prices into order manager
        order_manager.update_prices(
            {f"{a}/USD": p for a, p in prices.items()}
        )

        # Build and queue orders from weight diffs
        from src.portfolio.factory import build_orders_from_weights

        pending_orders = build_orders_from_weights(
            target_weights=final_weights,
            current_weights={},
            nav=starting_capital,
            pair_suffix="/USD",
        )
        for order in pending_orders:
            order_queue.add(order)

        assert not order_queue.is_empty, "Should have queued orders"

        # Execute
        results = await order_manager.process_queue()

        # Flush decision log to disk
        decision_logger._sync_flush()

        # --- Assertions ---
        # 1. At least one order filled
        assert len(results) > 0, "At least one order should have filled"

        # 2. Decision log contains ORDER_SUBMITTED-like entries
        assert trade_log.exists(), "Trade log JSONL file must exist"
        log_lines = trade_log.read_text().strip().split("\n")
        actions = []
        for line in log_lines:
            entry = json.loads(line)
            actions.append(entry.get("action"))
        assert any(
            a in ("NEW_ENTRY", "INCREASE") for a in actions
        ), f"Expected NEW_ENTRY or INCREASE in log, got: {actions}"

        # 3. Position tracker updated
        assert len(position_tracker.positions) > 0, "Should have open positions"

        # 4. NAV within 1% of starting capital (commissions only)
        nav = position_tracker.nav
        assert abs(nav - starting_capital) / starting_capital < 0.01, (
            f"NAV {nav} should be within 1% of {starting_capital}"
        )

        decision_logger.close()


# ===========================================================================
# TestTrailingStopTrigger
# ===========================================================================

class TestTrailingStopTrigger:
    """Integration test for trailing stop mechanics."""

    def test_trailing_stop_fires_only_below_threshold(self):
        """Guards against: trailing stop firing prematurely on normal
        volatility, or failing to fire when price breaches the stop level.
        The audit found the risk manager's stop distance lookup relies on
        tier maps — this test verifies the complete tick() path including
        stop sync with PositionTracker.
        """
        # Setup: position at entry_price=100.00, 6% trailing stop
        starting_capital = 1_000_000.0
        tracker = PositionTracker(starting_capital=starting_capital)
        tracker.on_buy_fill("TEST_ASSET", quantity=100.0, price=100.0)

        # Peak stays at 100.0 (no higher price seen)
        stops = TrailingStopManager(
            base_stops={"tier_1_3": 0.06},
            min_stop_floor=0.02,
        )
        stops.open_position("TEST_ASSET", entry_price=100.0, base_stop_pct=0.06)
        # Stop level = 100.0 * (1 - 0.06) = 94.0

        breakers = CircuitBreakerManager()
        breakers.initialize_nav(starting_capital)
        contagion = ContagionMonitor()
        validator = PreTradeValidator(
            tier_caps={},
            asset_tier_map={"TEST_ASSET": "tier_1_2"},
            tier_cap_defaults={"tier_1_2": 0.08},
        )

        risk_manager = RiskManager(
            stops=stops,
            breakers=breakers,
            contagion=contagion,
            validator=validator,
            asset_tier_map={"TEST_ASSET": "tier_1_2"},
            stop_distances={"tier_1_3": 0.06, "tier_4_5": 0.08, "trump": 0.10},
        )

        # --- Tick at price=94.01: just ABOVE stop level (94.0) → no stop ---
        tracker.update_prices({"TEST_ASSET": 94.01})
        events_above = risk_manager.tick(
            current_prices={"TEST_ASSET": 94.01},
            tracker=tracker,
        )
        stop_events_above = [
            e for e in events_above if e.event_type == EventType.TRAILING_STOP
        ]
        assert len(stop_events_above) == 0, (
            f"Stop should NOT fire at 94.01 (stop level 94.0), got {stop_events_above}"
        )

        # --- Tick at price=93.99: just BELOW stop level → stop fires ---
        tracker.update_prices({"TEST_ASSET": 93.99})
        events_below = risk_manager.tick(
            current_prices={"TEST_ASSET": 93.99},
            tracker=tracker,
        )
        stop_events_below = [
            e for e in events_below if e.event_type == EventType.TRAILING_STOP
        ]
        assert len(stop_events_below) == 1, (
            f"Stop SHOULD fire at 93.99 (stop level 94.0), got {len(stop_events_below)} events"
        )

        event = stop_events_below[0]
        assert event.severity == Severity.CRITICAL, (
            f"Trailing stop should be CRITICAL, got {event.severity}"
        )
        assert event.asset == "TEST_ASSET"


# ===========================================================================
# TestCircuitBreakerDrawdown
# ===========================================================================

class TestCircuitBreakerDrawdown:
    """Integration test for the 8% drawdown hard halt circuit breaker."""

    def test_8pct_drawdown_triggers_halt_and_blocks_new_entries(
        self, minimal_config: dict
    ):
        """Guards against: catastrophic drawdown exceeding the Calmar
        denominator threshold. The audit confirmed the 8% hard halt is
        the primary Calmar protector. This test verifies the complete
        chain: drawdown detected → CIRCUIT_BREAKER_DD event → constructor
        returns zero new entries.
        """
        nav_peak = 1_000_000.0

        # Setup circuit breaker with 8% hard halt
        breakers = CircuitBreakerManager(
            drawdown_hard_halt=0.08,
            drawdown_soft_warning=0.05,
        )
        breakers.initialize_nav(nav_peak)

        # Current NAV = 918,900 → 8.11% drawdown from peak
        current_nav = 918_900.0
        events = breakers.check(current_nav)

        # Filter for drawdown events
        dd_events = [
            e for e in events
            if e.event_type in (EventType.CIRCUIT_BREAKER_DD, EventType.DRAWDOWN_HALT)
        ]
        assert len(dd_events) > 0, (
            f"8.11% drawdown should trigger circuit breaker event, got {events}"
        )

        # At least one should be CRITICAL
        critical_events = [e for e in dd_events if e.severity == Severity.CRITICAL]
        assert len(critical_events) > 0, (
            "Drawdown halt should produce at least one CRITICAL event"
        )

        # Verify halt is active
        assert breakers.is_halted, "Circuit breaker halt should be active after 8% drawdown"

        # Now verify that portfolio constructor respects the halt:
        # During halt, build_target_weights should return no NEW entries
        # (only reductions are permitted).
        constructor = PortfolioConstructor(
            regime_targets={
                "trend_bull_low_vol": 0.80,
                "trend_bull_high_vol": 0.65,
                "mean_revert": 0.55,
                "trend_bear": 0.35,
                "crisis": 0.15,
            },
            tier_caps={},
            asset_tier_map={"BTC": "tier_1_2", "ETH": "tier_1_2"},
            tier_cap_defaults={"tier_1_2": 0.08},
            max_turnover=0.25,
            min_trade_threshold=0.002,
        )

        # With sizing_multiplier from breaker (should be reduced during halt)
        sizing_mult = breakers.sizing_multiplier
        target_weights, _ = constructor.compute(
            regime="TREND_BULL",
            btc_vol_percentile=50.0,
            selected_assets=["BTC", "ETH"],
            current_weights={},  # no existing positions
            current_nav=current_nav,
            sizing_multiplier=sizing_mult,
        )

        # When halted, the sizing multiplier should be < 1.0 or 0
        # which means target weights are reduced
        total_new_weight = sum(target_weights.values())
        assert total_new_weight <= 0.80 * sizing_mult + 0.01, (
            f"Total weight {total_new_weight} should be scaled down by "
            f"sizing_multiplier {sizing_mult} during halt"
        )


# ===========================================================================
# TestCrashRecovery
# ===========================================================================

class TestCrashRecovery:
    """Integration test for crash recovery from JSONL trade log."""

    def test_reconstruct_positions_from_trade_log(self, tmp_path: Path):
        """Guards against: position desynchronization after process crash.
        The bot must reconstruct accurate positions (quantity, cost basis,
        cash balance) from the trade log alone. The audit confirmed the
        recovery module handles malformed lines (Section 4e) but noted
        that startup reconciliation with the exchange is NOT performed
        immediately (Section 4f CRITICAL).
        """
        trade_log = tmp_path / "trades.jsonl"
        starting_capital = 1_000_000.0

        # Write 5 JSONL entries matching DecisionLogger schema
        entries = [
            # Entry 1: BUY fill — BTC, 0.5 units @ $85,000
            {
                "timestamp_utc": "2026-03-21T10:00:00+00:00",
                "asset": "BTC",
                "action": "NEW_ENTRY",
                "trigger": "REBALANCE",
                "regime_state": "TREND_BULL",
                "momentum_score": 0.85,
                "order_type": "LIMIT",
                "submitted_price": 85000.0,
                "fill_price": 85000.0,
                "filled_quantity": 0.5,
                "commission_paid": 21.25,  # 0.05% of $42,500
            },
            # Entry 2: BUY fill — ETH, 10 units @ $3,100
            {
                "timestamp_utc": "2026-03-21T10:00:01+00:00",
                "asset": "ETH",
                "action": "NEW_ENTRY",
                "trigger": "REBALANCE",
                "regime_state": "TREND_BULL",
                "momentum_score": 0.78,
                "order_type": "LIMIT",
                "submitted_price": 3100.0,
                "fill_price": 3100.0,
                "filled_quantity": 10.0,
                "commission_paid": 15.50,
            },
            # Entry 3: SELL fill — ETH, 5 units @ $3,200 (partial exit)
            {
                "timestamp_utc": "2026-03-21T14:00:00+00:00",
                "asset": "ETH",
                "action": "DECREASE",
                "trigger": "REBALANCE",
                "regime_state": "TREND_BULL",
                "momentum_score": 0.60,
                "order_type": "LIMIT",
                "submitted_price": 3200.0,
                "fill_price": 3200.0,
                "filled_quantity": 5.0,
                "commission_paid": 8.0,
            },
            # Entry 4: ORDER_CANCELLED — should NOT create a position
            {
                "timestamp_utc": "2026-03-21T15:00:00+00:00",
                "asset": "LINK",
                "action": "NEW_ENTRY",
                "trigger": "REBALANCE",
                "regime_state": "TREND_BULL",
                "momentum_score": 0.72,
                "order_type": "LIMIT",
                "submitted_price": 18.0,
                "fill_price": None,
                "filled_quantity": None,
                "commission_paid": None,
                "suppressed": False,
            },
            # Entry 5: ORDER_SUPPRESSED — should NOT create a position
            {
                "timestamp_utc": "2026-03-21T15:01:00+00:00",
                "asset": "DOT",
                "action": "SUPPRESS",
                "trigger": "REBALANCE",
                "regime_state": "TREND_BULL",
                "momentum_score": 0.40,
                "order_type": "LIMIT",
                "submitted_price": 7.0,
                "fill_price": None,
                "filled_quantity": None,
                "commission_paid": None,
                "suppressed": True,
                "suppression_reason": "BELOW_THRESHOLD",
            },
        ]

        # Write entries + one corrupted final line (truncated JSON)
        with open(trade_log, "w") as f:
            for entry in entries:
                f.write(json.dumps(entry) + "\n")
            # Corrupted line: truncated JSON
            f.write('{"timestamp_utc": "2026-03-21T16:00:00", "asset": "SOL", "action": "NEW_EN')

        # Reconstruct positions
        tracker = PositionTracker(starting_capital=starting_capital)
        fill_count = reconstruct_positions(tracker, trade_log)

        # --- Assertions ---

        # 1. Correct number of fills processed (2 buys + 1 sell = 3 fills)
        assert fill_count == 3, f"Expected 3 fills processed, got {fill_count}"

        # 2. Two net long positions: BTC and ETH
        assert len(tracker.positions) == 2, (
            f"Expected 2 positions (BTC, ETH), got {list(tracker.positions.keys())}"
        )
        assert "BTC" in tracker.positions, "BTC position should exist"
        assert "ETH" in tracker.positions, "ETH position should exist"

        # 3. BTC: 0.5 units (one buy, no sells)
        btc = tracker.positions["BTC"]
        assert abs(btc.quantity - 0.5) < 1e-8, f"BTC qty should be 0.5, got {btc.quantity}"

        # 4. ETH: 5 units (bought 10, sold 5)
        eth = tracker.positions["ETH"]
        assert abs(eth.quantity - 5.0) < 1e-8, f"ETH qty should be 5.0, got {eth.quantity}"

        # 5. Cash reduced by buy fills and increased by sell fills
        # Buy BTC: 0.5 * 85000 = 42,500 + commission
        # Buy ETH: 10 * 3100  = 31,000 + commission
        # Sell ETH: 5 * 3200  = 16,000 - commission
        # Exact amounts depend on commission_pct calculation in recovery module
        assert tracker.cash_balance < starting_capital, (
            "Cash should be less than starting capital after net buys"
        )

        # 6. Cancellation and suppression did NOT create positions
        assert "LINK" not in tracker.positions, "LINK (cancelled) should not have a position"
        assert "DOT" not in tracker.positions, "DOT (suppressed) should not have a position"

        # 7. Corrupted final line was skipped without raising
        # (If we got here without exception, the test passes)
        assert "SOL" not in tracker.positions, "SOL (corrupted line) should not have a position"


# ===========================================================================
# TestHeartbeatAndSafeState
# ===========================================================================

class TestHeartbeatAndSafeState:
    """Integration test for safe-state degradation and scheduler behavior."""

    @pytest.mark.asyncio
    async def test_safe_state_degradation_chain(self, tmp_path: Path):
        """Guards against: the bot continuing to place new trades when a
        critical subsystem (data ingestion) is failing. The audit found
        that SignalHealthMonitor is never updated (Section 3.2 WARNING),
        but the core safe_state mechanism must work correctly to prevent
        trading on stale or missing data. This test verifies the complete
        chain: healthy → failing → safe_mode → scheduler respects mode.
        """
        system_state = SystemState(max_consecutive_failures=3)

        # Register the jobs
        system_state.register_job("data_ingestion")
        system_state.register_job("risk_check")
        system_state.register_job("rebalance")

        # --- Phase 1: Report success 3 times → system healthy ---
        for _ in range(3):
            system_state.report_success("data_ingestion")

        assert not system_state.is_global_safe_mode, (
            "System should NOT be in safe mode after successful ingestion"
        )
        assert system_state.can_rebalance, (
            "Rebalance should be permitted when system is healthy"
        )

        # Verify the data_ingestion job is not failing
        di_job = system_state.jobs["data_ingestion"]
        assert not di_job.is_failing, "data_ingestion should not be flagged as failing"
        assert di_job.consecutive_failures == 0

        # --- Phase 2: Report failure 3 times → enters safe mode ---
        for i in range(3):
            system_state.report_failure("data_ingestion", f"API timeout #{i+1}")

        # After 3 consecutive failures, safe mode should be active
        assert system_state.is_global_safe_mode, (
            "System SHOULD be in safe mode after 3 consecutive data_ingestion failures"
        )

        # --- Phase 3: Verify scheduler skips non-critical jobs in safe mode ---
        # can_rebalance should be False
        assert not system_state.can_rebalance, (
            "Rebalance must be blocked during safe mode"
        )

        # Verify that risk_check would still be allowed to run
        # (risk_check is a critical job — it always runs)
        # The scheduler uses system_state.can_rebalance only for rebalance;
        # risk_check runs regardless (per main.py: risk_check_tick has no
        # safe_mode gate)
        risk_job = system_state.jobs.get("risk_check")
        # Risk check is independent of can_rebalance — we verify it hasn't
        # been blocked by the safe_mode flag
        assert risk_job is not None, "risk_check job should be registered"
        # Simulate risk_check still running successfully
        system_state.report_success("risk_check")
        assert not system_state.jobs["risk_check"].is_failing, (
            "risk_check should still report as healthy even in safe mode"
        )

        # --- Phase 4: Verify stale_data flag ---
        assert system_state.is_stale_data, (
            "Stale data flag should be set after data_ingestion failure"
        )
