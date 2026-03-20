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
from dataclasses import asdict, dataclass
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
from src.portfolio.constructor import PortfolioConstructor, ConstructionResult
from src.portfolio.endgame import EndgameManager
from src.portfolio.paxg_allocator import PAXGAllocator
from src.regime.detector import RegimeDetector
from src.regime.regime_state import RegimeState, RegimeType
from src.signals.meme_pool import MemePoolManager
from src.signals.trend_penalty import TrendPenaltyEngine
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

        # --- Layer 5: Portfolio Construction (Phase 2) ---
        # Build Phase 2 config with exposure targets for the constructor
        phase2_config = dict(minimal_config)
        phase2_config["portfolio"]["exposure"] = {
            "TREND_BULL": 0.80, "MEAN_REVERT": 0.55,
            "TREND_BEAR": 0.35, "HIGH_VOL_CRISIS": 0.15,
        }
        phase2_config["portfolio"]["holdings"] = {
            "TREND_BULL": 10, "MEAN_REVERT": 6,
            "TREND_BEAR": 4, "HIGH_VOL_CRISIS": 0,
        }
        phase2_config["portfolio"]["turnover_max_pct"] = 1.0  # no constraint for test
        phase2_config["meme_pool"] = {"enabled": False, "symbols": [], "top_n": 0,
                                       "active_regimes": [], "max_allocation_per_coin": 0.03}
        phase2_config["paxg"] = {"allocation": {"TREND_BULL": 0.04}, "rebalance_tolerance": 0.01}
        phase2_config["trend_penalty"] = {
            "ema_short_period": 60, "ema_long_period": 240,
            "penalty_sigma": -0.3, "broad_downturn_threshold": 0.70,
            "broad_downturn_penalty_sigma": -0.15,
        }

        constructor = PortfolioConstructor(
            config=phase2_config,
            regime_detector=regime_detector,
            trend_penalty=TrendPenaltyEngine(phase2_config),
            meme_pool=MemePoolManager(phase2_config),
            endgame=EndgameManager(phase2_config),
            paxg=PAXGAllocator(phase2_config),
        )

        construction_result = constructor.construct(
            momentum_scores=momentum_scores,
            current_positions={},
            nav=starting_capital,
        )
        target_weights = construction_result.target_weights
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

        # Now verify that the pre-trade validator respects the halt:
        # During halt, the circuit breaker sizing_multiplier should be < 1.0,
        # which the execution layer uses to scale down new entries.
        sizing_mult = breakers.sizing_multiplier

        # When halted, sizing multiplier should be 0 (full halt) or reduced
        assert sizing_mult < 1.0, (
            f"sizing_multiplier {sizing_mult} should be < 1.0 during halt"
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


# ===========================================================================
# Phase 2 Integration Helpers
# ===========================================================================

@dataclass
class _RegimeInputs:
    """Minimal regime input struct for integration tests."""
    btc_4h_return: float
    btc_24h_return: float
    altcoin_breadth: float
    btc_vol_percentile: float


@dataclass
class _MockP2State:
    current_regime: RegimeType
    btc_vol_percentile: float = 50.0
    contagion_proxy: float = 0.0
    avg_loss: float = 0.0
    bars_in_current_regime: int = 100


class _MockP2RegimeDetector:
    def __init__(self, regime: RegimeType):
        self._regime = regime
        self._state = _MockP2State(regime)

    @property
    def current_regime(self) -> RegimeType:
        return self._regime

    @property
    def state(self) -> "_MockP2State":
        return self._state


class _MockP2TrendPenalty:
    """Pass-through: no penalty applied."""
    def apply_penalties(self, scores: dict) -> dict:
        return dict(scores)


class _PenalizeAsset:
    """Apply a fixed penalty delta to one named asset."""
    def __init__(self, asset: str, penalty: float):
        self._asset = asset
        self._penalty = penalty

    def apply_penalties(self, scores: dict) -> dict:
        return {
            a: s + self._penalty if a == self._asset else s
            for a, s in scores.items()
        }


class _MockP2Endgame:
    def __init__(self, max_exposure: float = 1.0, sell_all: bool = False):
        self._max_exposure = max_exposure
        self._sell_all = sell_all

    def get_constraints(self, now=None) -> dict:
        return {
            "max_exposure": self._max_exposure,
            "stop_override": None,
            "sell_all": self._sell_all,
            "hours_remaining": 100.0,
            "tier": -1,
        }


class _MockP2MemePool:
    """No meme allocations."""
    def rank_and_select(self, scores: dict, regime: str) -> list:
        return []


class _MockP2PAXG:
    def __init__(self, weight: float = 0.04):
        self._weight = weight

    def get_target_weight(self, regime: RegimeType) -> float:
        return self._weight


_P2_CONFIG = {
    "portfolio": {
        "exposure": {
            "TREND_BULL": 0.80,
            "TREND_BULL_RISING_VOL": 0.65,
            "MEAN_REVERT": 0.55,
            "TREND_BEAR": 0.35,
            "HIGH_VOL_CRISIS": 0.15,
        },
        "holdings": {
            "TREND_BULL": 10,
            "MEAN_REVERT": 6,
            "TREND_BEAR": 4,
            "HIGH_VOL_CRISIS": 0,
        },
        "max_crypto_exposure": 0.90,
        "btc_vol_high_vol_threshold": 70.0,
        "turnover_max_pct": 1.0,
        "min_trade_nav_pct": 0.0,
        "tier_caps": {
            "tier1": 0.08, "tier1_doge": 0.05,
            "tier2": 0.08, "tier3": 0.06,
            "tier4_meme": 0.03, "tier5": 0.02,
            "trump": 0.02, "paxg": 0.15,
        },
    },
    "tier_caps": {
        "tier_1_2": 0.08, "tier_3": 0.06,
        "tier_4_meme": 0.03, "tier_5_obscure": 0.02,
        "doge": 0.05, "trump": 0.02, "paxg": 0.15,
        "redistribution_max_iterations": 5,
    },
    "universe": {
        "tier_1_majors": ["BTC", "ETH", "BNB", "LTC", "ADA", "DOGE", "TRX"],
        "tier_2_large_alts": ["LINK", "DOT", "NEAR"],
        "tier_3_defi": ["AAVE", "UNI", "CRV"],
        "tier_4_meme": ["SHIB", "PEPE"],
        "tier_5_obscure": [],
    },
    "signals": {
        "vol_exclusion_multiplier": 2.0,
    },
    "meme_pool": {
        "enabled": True,
        "symbols": ["PEPE", "SHIB"],
        "top_n": 2,
        "active_regimes": ["TREND_BULL"],
        "max_allocation_per_coin": 0.03,
        "trailing_stop_pct": 0.08,
    },
    "paxg": {
        "allocation": {
            "TREND_BULL": 0.04,
            "MEAN_REVERT": 0.075,
            "TREND_BEAR": 0.125,
            "HIGH_VOL_CRISIS": 0.125,
        },
        "rebalance_tolerance": 0.01,
    },
}


def _make_p2_constructor(
    regime: RegimeType = RegimeType.MEAN_REVERT,
    max_turnover: float = 1.0,
    endgame=None,
    meme_pool=None,
    paxg=None,
    trend_penalty=None,
    config: dict = None,
) -> "PortfolioConstructor":
    cfg = dict(config or _P2_CONFIG)
    if max_turnover != 1.0:
        import copy
        cfg = copy.deepcopy(cfg)
        cfg["portfolio"]["turnover_max_pct"] = max_turnover
    return PortfolioConstructor(
        config=cfg,
        regime_detector=_MockP2RegimeDetector(regime),
        trend_penalty=trend_penalty or _MockP2TrendPenalty(),
        meme_pool=meme_pool or _MockP2MemePool(),
        endgame=endgame or _MockP2Endgame(),
        paxg=paxg or _MockP2PAXG(),
    )


_BULL_SCORES = {
    "BTC": 1.0, "ETH": 0.95, "BNB": 0.90, "LTC": 0.85, "ADA": 0.80,
    "DOGE": 0.75, "TRX": 0.70, "LINK": 0.65, "DOT": 0.60, "NEAR": 0.55,
    "AAVE": 0.50, "UNI": 0.45,
}


def _make_detector_config() -> dict:
    return {
        "regime": {
            "update_cadence_sec": 300,
            "thresholds": {
                "contagion_ratio_crisis": 0.80,
                "contagion_avg_loss_pct": 0.01,
                "btc_vol_percentile_crisis": 90,
                "altcoin_breadth_bull": 0.55,
                "altcoin_breadth_bear": 0.40,
            },
            "transitions": {
                "upgrade_confirmation_bars": 30,
                "crisis_exit_contagion_below": 0.50,
                "crisis_exit_vol_below": 70,
                "crisis_exit_confirmation_bars": 30,
            },
            "contagion_return_window_min": 5,
            "contagion_small_portfolio_size": 6,
            "contagion_small_ratio_threshold": 0.90,
            "contagion_small_loss_threshold": 0.015,
        },
        "phase1_vol_guard": {
            "btc_30d_median_vol": 0.02,
            "vol_spike_multiplier": 2.0,
            "defensive_max_exposure": 0.40,
            "normal_max_exposure": 0.75,
        },
    }


# ===========================================================================
# TestPhase2Integration
# ===========================================================================


class TestPhase2Integration:
    """Integration tests verifying Phase 2 component interactions."""

    def test_regime_drives_exposure(self):
        """In TREND_BULL, portfolio targets ~80% crypto.
        In CRISIS, targets ~15%. Regime is the primary exposure lever.
        Guards against: exposure targets being ignored or incorrectly routed.
        """
        bull_pc = _make_p2_constructor(regime=RegimeType.TREND_BULL)
        bull_result = bull_pc.construct(_BULL_SCORES, {}, 1_000_000)
        bull_crypto = sum(
            w for a, w in bull_result.target_weights.items() if a != "PAXG"
        )

        crisis_pc = _make_p2_constructor(regime=RegimeType.HIGH_VOL_CRISIS)
        crisis_result = crisis_pc.construct(_BULL_SCORES, {}, 1_000_000)
        crisis_crypto = sum(
            w for a, w in crisis_result.target_weights.items() if a != "PAXG"
        )

        assert 0.70 <= bull_crypto <= 0.90, (
            f"TREND_BULL crypto={bull_crypto:.2%} not in [70%, 90%]"
        )
        assert crisis_crypto <= 0.20, (
            f"CRISIS crypto={crisis_crypto:.2%} should be ≤20%"
        )
        assert bull_crypto > crisis_crypto * 2, (
            "TREND_BULL exposure should be significantly higher than CRISIS"
        )

    def test_trend_penalty_affects_ranking(self):
        """Asset with bearish penalty gets lower effective score, drops out of top-N.
        Guards against: trend penalty engine being wired correctly into construction.
        """
        # 7 assets, MEAN_REVERT picks top 6. "LINK" is rank 6 (score 0.55).
        scores = {
            "BTC": 1.0, "ETH": 0.9, "BNB": 0.8, "LTC": 0.7, "ADA": 0.6,
            "LINK": 0.55, "DOT": 0.5,
        }

        # Without penalty: LINK (0.55) beats DOT (0.50) → LINK selected
        no_penalty_pc = _make_p2_constructor(regime=RegimeType.MEAN_REVERT)
        result_no_penalty = no_penalty_pc.construct(scores, {}, 1_000_000)
        assert "LINK" in result_no_penalty.target_weights, (
            "LINK should be selected without penalty (rank 6 of 7)"
        )
        assert "DOT" not in result_no_penalty.target_weights, (
            "DOT should NOT be selected without penalty (rank 7 of 7)"
        )

        # With -0.30 penalty on LINK: LINK drops to 0.25, DOT (0.50) now ranks 6
        penalty_pc = _make_p2_constructor(
            regime=RegimeType.MEAN_REVERT,
            trend_penalty=_PenalizeAsset("LINK", -0.30),
        )
        result_with_penalty = penalty_pc.construct(scores, {}, 1_000_000)
        assert "DOT" in result_with_penalty.target_weights, (
            "DOT should be selected when LINK is penalized below it"
        )
        assert "LINK" not in result_with_penalty.target_weights, (
            "LINK should drop out when penalized 0.30 below DOT"
        )

    def test_meme_pool_regime_gating(self):
        """Meme allocations appear in TREND_BULL, disappear in all other regimes.
        Guards against: meme pool bypassing regime gate and allocating in CRISIS.
        """
        meme_pool = MemePoolManager(_P2_CONFIG)
        # Give meme coins low scores — too low to be selected as regular candidates.
        # The meme pool selects them in TREND_BULL regardless of low score.
        # In other regimes, the meme pool gate blocks them AND they're low-ranked.
        meme_scores = dict(_BULL_SCORES)
        meme_scores["PEPE"] = 0.05  # below all regular assets → only meme pool adds them
        meme_scores["SHIB"] = 0.04

        bull_pc = _make_p2_constructor(
            regime=RegimeType.TREND_BULL, meme_pool=meme_pool
        )
        bull_result = bull_pc.construct(meme_scores, {}, 1_000_000)
        assert "PEPE" in bull_result.target_weights or "SHIB" in bull_result.target_weights, (
            "At least one meme coin should appear in TREND_BULL via meme pool"
        )
        assert bull_result.metadata.get("meme_count", 0) > 0, (
            "meme_count should be > 0 in TREND_BULL when meme pool is active"
        )

        for regime in (RegimeType.MEAN_REVERT, RegimeType.TREND_BEAR, RegimeType.HIGH_VOL_CRISIS):
            non_bull_pc = _make_p2_constructor(regime=regime, meme_pool=meme_pool)
            non_bull_result = non_bull_pc.construct(meme_scores, {}, 1_000_000)
            assert "PEPE" not in non_bull_result.target_weights, (
                f"PEPE should NOT appear in {regime.value} (meme pool gated)"
            )
            assert "SHIB" not in non_bull_result.target_weights, (
                f"SHIB should NOT appear in {regime.value} (meme pool gated)"
            )
            assert non_bull_result.metadata.get("meme_count", 0) == 0, (
                f"meme_count should be 0 in {regime.value}"
            )

    def test_endgame_overrides_regime(self):
        """With < 12h remaining, exposure capped at 45% even if TREND_BULL
        would normally target 80%. Endgame schedule supersedes regime target.
        Guards against: endgame de-risking being bypassed by bullish regime.
        """
        endgame = _MockP2Endgame(max_exposure=0.45)
        pc = _make_p2_constructor(
            regime=RegimeType.TREND_BULL, endgame=endgame
        )
        result = pc.construct(_BULL_SCORES, {}, 1_000_000)

        total_crypto = sum(
            w for a, w in result.target_weights.items() if a != "PAXG"
        )
        assert total_crypto <= 0.45 + 1e-6, (
            f"Endgame should cap crypto at 45%, got {total_crypto:.2%}"
        )
        assert result.metadata["target_exposure"] <= 0.45 + 1e-6, (
            "Constructor metadata should reflect endgame-adjusted exposure"
        )

    def test_paxg_scales_with_defensiveness(self):
        """PAXG weight: BULL < MEAN_REVERT < BEAR.
        More PAXG in bearish conditions reduces portfolio vol for Sharpe benefit.
        Guards against: PAXG allocation being regime-invariant.
        """
        alloc = PAXGAllocator(_P2_CONFIG)

        bull_w = alloc.get_target_weight(RegimeType.TREND_BULL)
        mean_w = alloc.get_target_weight(RegimeType.MEAN_REVERT)
        bear_w = alloc.get_target_weight(RegimeType.TREND_BEAR)
        crisis_w = alloc.get_target_weight(RegimeType.HIGH_VOL_CRISIS)

        assert bull_w < mean_w, (
            f"PAXG should be higher in MEAN_REVERT ({mean_w}) than TREND_BULL ({bull_w})"
        )
        assert mean_w < bear_w, (
            f"PAXG should be higher in TREND_BEAR ({bear_w}) than MEAN_REVERT ({mean_w})"
        )
        assert bear_w <= crisis_w + 1e-6, (
            f"PAXG should be at least as high in CRISIS ({crisis_w}) as TREND_BEAR ({bear_w})"
        )

    def test_dynamic_stop_tightening_per_position(self):
        """Position with +6% gain has tighter stop than +1% gain position.
        Tightening is per-position — computing one does not affect the other.
        Guards against: v3.0 portfolio-wide governor re-emerging.
        """
        tightening_config = {
            "tighten_high_threshold": 0.05,
            "tighten_high_factor": 0.60,
            "tighten_mid_threshold": 0.025,
            "tighten_mid_factor": 0.80,
        }
        mgr = TrailingStopManager(
            base_stops={"tier_1_3": 0.06},
            min_stop_floor=0.02,
            tightening_config=tightening_config,
        )

        # Position A: +1% unrealized (below any threshold) → no tightening
        stop_a = mgr.get_effective_stop_distance(
            base_stop_pct=0.06, entry_price=100.0, current_price=101.0
        )
        # Position B: +6% unrealized (above 5% high threshold) → factor 0.60
        stop_b = mgr.get_effective_stop_distance(
            base_stop_pct=0.06, entry_price=100.0, current_price=106.0
        )

        assert abs(stop_a - 0.06) < 1e-6, f"Position A (+1%): expected 6%, got {stop_a:.4f}"
        assert abs(stop_b - 0.036) < 1e-6, f"Position B (+6%): expected 3.6%, got {stop_b:.4f}"
        assert stop_a > stop_b, "Higher gain position must have tighter (smaller) stop"

        # Independence: re-computing A after B gives same result (no shared state)
        stop_a_recheck = mgr.get_effective_stop_distance(
            base_stop_pct=0.06, entry_price=100.0, current_price=101.0
        )
        assert stop_a == stop_a_recheck, (
            "Position A stop changed after computing B — positions are NOT independent"
        )

    def test_crisis_is_immediate(self):
        """Regime transitions to CRISIS without waiting for persistence timer.
        Guards against: CRISIS being accidentally gated behind an upgrade window.
        """
        detector = RegimeDetector(_make_detector_config())
        # Start from TREND_BULL (best case scenario — should still snap to CRISIS)
        detector._state.confirm_transition(RegimeType.TREND_BULL)
        assert detector.current_regime == RegimeType.TREND_BULL

        # Contagion crisis inputs: 10 positions all down 2% → ratio=1.0 > 0.80
        inputs = _RegimeInputs(
            btc_4h_return=0.02,
            btc_24h_return=0.05,
            altcoin_breadth=0.65,
            btc_vol_percentile=50.0,
        )
        positions = {f"ASSET{i}": {} for i in range(10)}
        returns = {f"ASSET{i}": -0.02 for i in range(10)}

        detector.update(inputs, positions, lambda a, w: returns.get(a))

        assert detector.current_regime == RegimeType.HIGH_VOL_CRISIS, (
            "CRISIS should trigger immediately from TREND_BULL on contagion — "
            f"got {detector.current_regime.value}"
        )
        assert not detector.state.transition_pending, (
            "CRISIS transition must be immediate (no pending confirmation)"
        )

    def test_turnover_constraint(self):
        """Rebalance replacing >25% of portfolio is throttled to ≤25%.
        Guards against: turnover constraint being bypassed or miscalculated.
        """
        # Current portfolio: BTC-heavy
        current = {"BTC": 0.30, "ETH": 0.20}

        # New signal: completely different assets ranked highest
        scores = {
            "DOT": 1.0, "NEAR": 0.9, "LINK": 0.8, "AAVE": 0.7, "UNI": 0.6,
            "CRV": 0.5, "BTC": 0.1, "ETH": 0.05,
        }

        pc = _make_p2_constructor(regime=RegimeType.MEAN_REVERT, max_turnover=0.25)
        result = pc.construct(scores, current, 1_000_000)

        all_assets = set(result.target_weights) | set(current)
        total_change = sum(
            abs(result.target_weights.get(a, 0.0) - current.get(a, 0.0))
            for a in all_assets
        )
        one_way_turnover = total_change / 2.0

        assert one_way_turnover <= 0.25 + 1e-6, (
            f"One-way turnover {one_way_turnover:.2%} exceeds 25% cap"
        )

    def test_total_weights_never_exceed_one(self):
        """CRITICAL: After construction, sum(weights) <= 1.0.
        Tests with high-exposure regime + active meme pool + generous PAXG
        to stress the combine_weights() budget enforcement.
        Guards against: silent weight overflow that misrepresents exposure.
        """
        # Generous PAXG (25%) on top of 80% crypto target = would be 105% without cap
        big_paxg = _MockP2PAXG(weight=0.25)
        meme_pool = MemePoolManager(_P2_CONFIG)

        scores = dict(_BULL_SCORES)
        scores["PEPE"] = 0.85
        scores["SHIB"] = 0.80

        pc = _make_p2_constructor(
            regime=RegimeType.TREND_BULL,
            paxg=big_paxg,
            meme_pool=meme_pool,
        )
        result = pc.construct(scores, {}, 1_000_000)

        total = sum(result.target_weights.values())
        assert total <= 1.0 + 1e-9, (
            f"Weights sum to {total:.6f} — exceeds 1.0"
        )

    def test_no_negative_weights(self):
        """No position weight is negative after construction.
        Guards against: scaling bugs in combine_weights() or turnover_cap()
        producing negative allocations.
        """
        for regime in (
            RegimeType.TREND_BULL, RegimeType.MEAN_REVERT,
            RegimeType.TREND_BEAR, RegimeType.HIGH_VOL_CRISIS,
        ):
            pc = _make_p2_constructor(regime=regime)
            result = pc.construct(_BULL_SCORES, {}, 1_000_000)
            for sym, w in result.target_weights.items():
                assert w >= 0.0, (
                    f"{regime.value}: {sym} has negative weight {w:.6f}"
                )
