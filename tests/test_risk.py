"""Tests for Layer 6 — Risk Management.

Covers: RiskEvent, trailing stops, circuit breakers, contagion monitor,
pre-trade checks.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.risk.risk_event import (
    CRITICAL_PRIORITY,
    EventType,
    RiskEvent,
    Severity,
)
from src.risk.trailing_stops import PositionStop, TrailingStopManager
from src.risk.circuit_breakers import CircuitBreakerManager
from src.risk.contagion import ContagionMonitor
from src.risk.pre_trade_checks import PreTradeValidator


# =========================================================================
# RiskEvent tests
# =========================================================================


class TestRiskEvent:
    """Tests for the RiskEvent dataclass."""

    def test_create_risk_event(self) -> None:
        event = RiskEvent(
            event_type=EventType.TRAILING_STOP,
            severity=Severity.CRITICAL,
            triggered_value=0.065,
            limit_value=0.06,
            action_required="SELL ALL BTC",
            asset="BTC",
        )
        assert event.event_type == EventType.TRAILING_STOP
        assert event.severity == Severity.CRITICAL
        assert event.asset == "BTC"
        assert event.is_critical
        assert event.is_asset_level

    def test_portfolio_level_event(self) -> None:
        event = RiskEvent(
            event_type=EventType.CIRCUIT_BREAKER_DD,
            severity=Severity.CRITICAL,
            triggered_value=0.085,
            limit_value=0.08,
            action_required="HALT all new entries",
        )
        assert event.asset is None
        assert not event.is_asset_level
        assert event.is_critical

    def test_severity_levels(self) -> None:
        medium = RiskEvent(
            event_type=EventType.CONCENTRATION_BREACH,
            severity=Severity.MEDIUM,
            triggered_value=0.09,
            limit_value=0.08,
            action_required="Log warning",
        )
        assert not medium.is_critical
        assert medium.severity == Severity.MEDIUM

    def test_priority_ordering(self) -> None:
        """CRITICAL trailing stops have highest priority."""
        trailing = RiskEvent(
            event_type=EventType.TRAILING_STOP,
            severity=Severity.CRITICAL,
            triggered_value=0.07,
            limit_value=0.06,
            action_required="SELL",
            asset="BTC",
        )
        circuit = RiskEvent(
            event_type=EventType.CIRCUIT_BREAKER_DD,
            severity=Severity.CRITICAL,
            triggered_value=0.09,
            limit_value=0.08,
            action_required="HALT",
        )
        contagion = RiskEvent(
            event_type=EventType.CONTAGION_CRISIS,
            severity=Severity.CRITICAL,
            triggered_value=0.85,
            limit_value=0.80,
            action_required="REDUCE",
        )
        high = RiskEvent(
            event_type=EventType.CONCENTRATION_BREACH,
            severity=Severity.HIGH,
            triggered_value=0.09,
            limit_value=0.08,
            action_required="CAP",
        )

        # Sort: trailing < circuit < contagion < high
        events = sorted([high, contagion, circuit, trailing])
        assert events[0].event_type == EventType.TRAILING_STOP
        assert events[1].event_type == EventType.CIRCUIT_BREAKER_DD
        assert events[2].event_type == EventType.CONTAGION_CRISIS
        assert events[3].event_type == EventType.CONCENTRATION_BREACH

    def test_to_log_dict(self) -> None:
        event = RiskEvent(
            event_type=EventType.TRAILING_STOP,
            severity=Severity.CRITICAL,
            triggered_value=0.065432,
            limit_value=0.06,
            action_required="SELL",
            asset="ETH",
        )
        d = event.to_log_dict()
        assert d["event_type"] == "TRAILING_STOP"
        assert d["severity"] == "CRITICAL"
        assert d["asset"] == "ETH"
        assert isinstance(d["timestamp"], str)

    def test_frozen_dataclass(self) -> None:
        """RiskEvents should be immutable."""
        event = RiskEvent(
            event_type=EventType.TRAILING_STOP,
            severity=Severity.CRITICAL,
            triggered_value=0.07,
            limit_value=0.06,
            action_required="SELL",
        )
        try:
            event.triggered_value = 0.08  # type: ignore
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass  # expected


# =========================================================================
# PositionStop tests
# =========================================================================


class TestPositionStop:
    """Tests for individual position stop tracking."""

    def test_peak_tracking(self) -> None:
        """Peak price updates only when price goes higher."""
        stop = PositionStop(asset="BTC", entry_price=100.0, peak_price=100.0, base_stop_pct=0.06)

        stop.update_peak(110.0)
        assert stop.peak_price == 110.0

        stop.update_peak(105.0)  # Lower — should not update
        assert stop.peak_price == 110.0

        stop.update_peak(115.0)
        assert stop.peak_price == 115.0

    def test_stop_price_calculation(self) -> None:
        """Stop price = peak × (1 - stop_pct)."""
        stop = PositionStop(asset="ETH", entry_price=100.0, peak_price=200.0, base_stop_pct=0.06)
        assert stop.stop_price() == 200.0 * 0.94  # 188.0

    def test_stop_price_with_override(self) -> None:
        stop = PositionStop(asset="ETH", entry_price=100.0, peak_price=200.0, base_stop_pct=0.06)
        assert stop.stop_price(effective_stop_pct=0.03) == 200.0 * 0.97  # 194.0

    def test_trigger_at_stop(self) -> None:
        stop = PositionStop(asset="BTC", entry_price=100.0, peak_price=100.0, base_stop_pct=0.06)
        # Price at exactly stop level
        assert stop.is_triggered(94.0)
        # Price just above stop
        assert not stop.is_triggered(94.01)

    def test_trigger_after_rally_then_drop(self) -> None:
        """Position rallies 20%, then drops. Stop is peak-based, not entry-based."""
        stop = PositionStop(asset="BTC", entry_price=100.0, peak_price=100.0, base_stop_pct=0.06)

        # Rally to 120
        stop.update_peak(120.0)
        assert stop.peak_price == 120.0

        # Stop is at 120 × 0.94 = 112.8 (NOT 100 × 0.94 = 94)
        assert stop.stop_price() == 120.0 * 0.94
        assert not stop.is_triggered(113.0)  # above stop
        assert stop.is_triggered(112.0)  # below stop

    def test_never_above_entry(self) -> None:
        """Position that never goes above entry still has a stop."""
        stop = PositionStop(asset="DOGE", entry_price=100.0, peak_price=100.0, base_stop_pct=0.08)

        stop.update_peak(99.0)
        assert stop.peak_price == 100.0  # Peak unchanged

        assert stop.stop_price() == 92.0
        assert stop.is_triggered(91.0)


# =========================================================================
# TrailingStopManager tests
# =========================================================================


class TestTrailingStopManager:
    """Tests for the trailing stop manager."""

    def _make_manager(self, tightening: bool = False) -> TrailingStopManager:
        tightening_config = None
        if tightening:
            tightening_config = {
                "threshold_high_pnl": 0.02,
                "tightening_high": 0.40,
                "threshold_medium_pnl": 0.01,
                "tightening_medium": 0.20,
            }
        return TrailingStopManager(
            base_stops={"tier_1_3": 0.06, "tier_4_5": 0.08, "trump": 0.10},
            min_stop_floor=0.02,
            tightening_config=tightening_config,
        )

    def test_open_and_close_position(self) -> None:
        mgr = self._make_manager()
        mgr.open_position("BTC", entry_price=50000.0, base_stop_pct=0.06)
        assert "BTC" in mgr.positions
        mgr.close_position("BTC")
        assert "BTC" not in mgr.positions

    def test_no_trigger_above_stop(self) -> None:
        mgr = self._make_manager()
        mgr.open_position("BTC", entry_price=50000.0, base_stop_pct=0.06)

        events = mgr.check_all({"BTC": 48000.0})
        assert len(events) == 0  # 4% drop, stop at 6%

    def test_trigger_below_stop(self) -> None:
        mgr = self._make_manager()
        mgr.open_position("BTC", entry_price=50000.0, base_stop_pct=0.06)

        events = mgr.check_all({"BTC": 46000.0})
        assert len(events) == 1
        assert events[0].event_type == EventType.TRAILING_STOP
        assert events[0].severity == Severity.CRITICAL
        assert events[0].asset == "BTC"

    def test_peak_updates_then_triggers(self) -> None:
        mgr = self._make_manager()
        mgr.open_position("ETH", entry_price=3000.0, base_stop_pct=0.06)

        # Price rallies to 3600 (20% gain)
        events = mgr.check_all({"ETH": 3600.0})
        assert len(events) == 0
        assert mgr.positions["ETH"].peak_price == 3600.0

        # Stop is at 3600 × 0.94 = 3384
        events = mgr.check_all({"ETH": 3400.0})
        assert len(events) == 0  # still above stop

        events = mgr.check_all({"ETH": 3380.0})
        assert len(events) == 1  # below stop

    def test_multiple_positions(self) -> None:
        mgr = self._make_manager()
        mgr.open_position("BTC", 50000.0, 0.06)
        mgr.open_position("SHIB", 0.00001, 0.08)

        # BTC fine, SHIB triggers
        events = mgr.check_all({"BTC": 49000.0, "SHIB": 0.000009})
        assert len(events) == 1
        assert events[0].asset == "SHIB"

    def test_dynamic_tightening_high(self) -> None:
        """Per-position unrealized P&L > +2% tightens stops by 40%."""
        mgr = self._make_manager(tightening=True)

        # Entry at 100.0, current at 102.5 → unrealized P&L = +2.5%
        pos = PositionStop(asset="TEST", entry_price=100.0, peak_price=102.5, base_stop_pct=0.06)
        effective = mgr.compute_effective_stop(pos, current_price=102.5)
        # 6% × (1 - 0.40) = 3.6%
        assert abs(effective - 0.036) < 1e-6

    def test_dynamic_tightening_medium(self) -> None:
        """Per-position unrealized P&L +1% to +2% tightens stops by 20%."""
        mgr = self._make_manager(tightening=True)

        # Entry at 100.0, current at 101.5 → unrealized P&L = +1.5%
        pos = PositionStop(asset="TEST", entry_price=100.0, peak_price=101.5, base_stop_pct=0.06)
        effective = mgr.compute_effective_stop(pos, current_price=101.5)
        # 6% × (1 - 0.20) = 4.8%
        assert abs(effective - 0.048) < 1e-6

    def test_tightening_floor(self) -> None:
        """Even with 40% tightening, stops never go below 2%."""
        mgr = self._make_manager(tightening=True)

        # Entry at 100.0, current at 102.5 → +2.5% P&L → 40% tightening
        # With a 3% base stop: 3% × 0.6 = 1.8% < floor
        pos = PositionStop(asset="TEST", entry_price=100.0, peak_price=102.5, base_stop_pct=0.03)
        effective = mgr.compute_effective_stop(pos, current_price=102.5)
        assert effective == 0.02  # floor

    def test_endgame_stop_override(self) -> None:
        """End-game stop override takes effect when tighter."""
        mgr = self._make_manager()

        # Base 6%, endgame 3%
        pos = PositionStop(asset="TEST", entry_price=100.0, peak_price=100.0, base_stop_pct=0.06)
        effective = mgr.compute_effective_stop(pos, current_price=100.0, endgame_stop_override=0.03)
        assert abs(effective - 0.03) < 1e-6

    def test_endgame_plus_tightening(self) -> None:
        """When both tightening and endgame apply, take the tighter."""
        mgr = self._make_manager(tightening=True)

        # Base 6%, tightened by 40% = 3.6%, endgame = 3%
        # Entry at 100.0, current at 102.5 → +2.5% P&L → 40% tightening
        # Tightened = 6% × 0.6 = 3.6%, endgame = 3%. Take min(3.6%, 3%) = 3%
        pos = PositionStop(asset="TEST", entry_price=100.0, peak_price=102.5, base_stop_pct=0.06)
        effective = mgr.compute_effective_stop(pos, current_price=102.5, endgame_stop_override=0.03)
        assert abs(effective - 0.03) < 1e-6

        # Endgame = 5% is less tight than tightened 3.6%
        effective2 = mgr.compute_effective_stop(pos, current_price=102.5, endgame_stop_override=0.05)
        # tightened = 3.6%, endgame = 5%, min(3.6%, 5%) = 3.6%
        assert abs(effective2 - 0.036) < 1e-6

    def test_restore_position(self) -> None:
        mgr = self._make_manager()
        mgr.restore_position("BTC", entry_price=50000.0, peak_price=55000.0, base_stop_pct=0.06)

        assert mgr.positions["BTC"].peak_price == 55000.0
        assert mgr.positions["BTC"].entry_price == 50000.0

    def test_get_stop_summary(self) -> None:
        mgr = self._make_manager()
        mgr.open_position("BTC", 50000.0, 0.06)
        mgr.positions["BTC"].update_peak(55000.0)

        summary = mgr.get_stop_summary()
        assert "BTC" in summary
        assert summary["BTC"]["peak_price"] == 55000.0
        assert summary["BTC"]["stop_price"] == 55000.0 * 0.94

    def test_missing_price_skipped(self) -> None:
        """Assets not in current_prices are silently skipped."""
        mgr = self._make_manager()
        mgr.open_position("BTC", 50000.0, 0.06)
        events = mgr.check_all({})  # No prices at all
        assert len(events) == 0


# =========================================================================
# CircuitBreakerManager tests
# =========================================================================


class TestCircuitBreakers:
    """Tests for portfolio-level circuit breakers."""

    def _make_manager(self) -> CircuitBreakerManager:
        mgr = CircuitBreakerManager(
            drawdown_soft_warning=0.05,
            drawdown_hard_halt=0.08,
            daily_loss_soft_warning=0.03,
            daily_loss_hard_reduce=0.05,
            single_asset_loss_soft=0.04,
            single_asset_loss_hard=0.06,
            halt_cooldown_hr=2.0,
            resume_sizing_fraction=0.50,
            full_sizing_within_pct_of_peak=0.04,
        )
        mgr.initialize_nav(1_000_000.0)
        return mgr

    def test_no_events_in_profit(self) -> None:
        mgr = self._make_manager()
        events = mgr.check(1_010_000.0)
        assert len(events) == 0
        assert not mgr.is_halted

    def test_drawdown_soft_warning(self) -> None:
        mgr = self._make_manager()
        # 5.5% drawdown
        events = mgr.check(945_000.0)
        assert any(e.severity == Severity.MEDIUM for e in events)
        assert not mgr.is_halted

    def test_drawdown_hard_halt(self) -> None:
        mgr = self._make_manager()
        # 9% drawdown
        events = mgr.check(910_000.0)
        assert any(
            e.event_type == EventType.DRAWDOWN_HALT and e.severity == Severity.CRITICAL
            for e in events
        )
        assert mgr.is_halted
        assert mgr.sizing_multiplier == 0.0

    def test_daily_loss_soft_warning(self) -> None:
        mgr = self._make_manager()
        # 3.5% daily loss
        events = mgr.check(965_000.0)
        daily_events = [e for e in events if e.event_type == EventType.CIRCUIT_BREAKER_DAILY]
        assert any(e.severity == Severity.MEDIUM for e in daily_events)

    def test_daily_loss_hard_reduce(self) -> None:
        mgr = self._make_manager()
        # 6% daily loss
        events = mgr.check(940_000.0)
        daily_events = [e for e in events if e.event_type == EventType.CIRCUIT_BREAKER_DAILY]
        assert any(e.severity == Severity.CRITICAL for e in daily_events)

    def test_daily_pnl_reset_at_midnight(self) -> None:
        mgr = self._make_manager()
        # Simulate end of day
        mgr.daily_pnl.current_day = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).date()
        old_start = mgr.daily_pnl.day_start_nav

        # Check with current NAV — should reset day_start
        mgr.check(980_000.0)
        assert mgr.daily_pnl.day_start_nav == 980_000.0

    def test_daily_pnl_pct(self) -> None:
        mgr = self._make_manager()
        pnl = mgr.get_daily_pnl_pct(1_025_000.0)
        assert abs(pnl - 0.025) < 1e-6

    def test_single_asset_loss_soft(self) -> None:
        mgr = self._make_manager()
        events = mgr.check_single_asset_loss("BTC", entry_price=100.0, current_price=95.5)
        assert any(e.severity == Severity.MEDIUM for e in events)

    def test_single_asset_loss_hard(self) -> None:
        mgr = self._make_manager()
        events = mgr.check_single_asset_loss("BTC", entry_price=100.0, current_price=93.0)
        assert any(
            e.severity == Severity.CRITICAL
            and e.event_type == EventType.SINGLE_ASSET_LOSS
            for e in events
        )

    def test_single_asset_in_profit(self) -> None:
        mgr = self._make_manager()
        events = mgr.check_single_asset_loss("BTC", entry_price=100.0, current_price=110.0)
        assert len(events) == 0

    def test_peak_nav_updates(self) -> None:
        mgr = self._make_manager()
        mgr.check(1_050_000.0)
        assert mgr.drawdown.peak_nav == 1_050_000.0

        mgr.check(1_040_000.0)  # Doesn't go higher
        assert mgr.drawdown.peak_nav == 1_050_000.0


# =========================================================================
# ContagionMonitor tests
# =========================================================================


class TestContagionMonitor:
    """Tests for the contagion circuit breaker."""

    def test_no_trigger_below_threshold(self) -> None:
        mon = ContagionMonitor(ratio_threshold=0.80, avg_loss_threshold=0.01)
        event = mon.check(contagion_ratio=0.60, avg_loss=0.005)
        assert event is None

    def test_trigger_when_both_exceeded(self) -> None:
        mon = ContagionMonitor(ratio_threshold=0.80, avg_loss_threshold=0.01)
        event = mon.check(contagion_ratio=0.85, avg_loss=0.015)
        assert event is not None
        assert event.event_type == EventType.CONTAGION_CRISIS
        assert event.severity == Severity.CRITICAL

    def test_no_trigger_high_ratio_low_loss(self) -> None:
        """Both conditions must be met (AND, not OR)."""
        mon = ContagionMonitor(ratio_threshold=0.80, avg_loss_threshold=0.01)
        event = mon.check(contagion_ratio=0.90, avg_loss=0.005)
        assert event is None

    def test_crisis_clears_when_ratio_drops(self) -> None:
        mon = ContagionMonitor(ratio_threshold=0.80, avg_loss_threshold=0.01)
        mon.check(contagion_ratio=0.85, avg_loss=0.015)
        assert mon.triggered

        # Ratio drops below 60% of threshold (0.80 × 0.6 = 0.48)
        mon.check(contagion_ratio=0.40, avg_loss=0.001)
        assert not mon.triggered


# =========================================================================
# PreTradeValidator tests
# =========================================================================


class TestPreTradeValidator:
    """Tests for pre-rebalance validation."""

    def _make_validator(self) -> PreTradeValidator:
        return PreTradeValidator(
            tier_caps={"DOGE": 0.05, "TRUMP": 0.02, "PAXG": 0.15},
            asset_tier_map={
                "BTC": "tier_1_2", "ETH": "tier_1_2", "DOGE": "tier_1_2",
                "LINK": "tier_1_2", "AAVE": "tier_3", "SHIB": "tier_4_meme",
                "SOMI": "tier_5_obscure",
            },
            tier_cap_defaults={
                "tier_1_2": 0.08,
                "tier_3": 0.06,
                "tier_4_meme": 0.03,
                "tier_5_obscure": 0.02,
            },
            max_crypto_exposure=0.90,
            redistribution_max_iterations=5,
        )

    def test_no_violations(self) -> None:
        v = self._make_validator()
        weights = {"BTC": 0.07, "ETH": 0.07, "LINK": 0.06}
        fixed, events = v.validate_and_fix(weights, max_deployment=0.80)
        assert len(events) == 0
        assert abs(fixed["BTC"] - 0.07) < 1e-6

    def test_cap_and_redistribute(self) -> None:
        v = self._make_validator()
        weights = {"BTC": 0.12, "ETH": 0.05, "LINK": 0.05}
        fixed, events = v.validate_and_fix(weights, max_deployment=0.80)

        # BTC should be capped at 0.08
        assert fixed["BTC"] <= 0.08 + 1e-6
        # Excess (0.04) redistributed to ETH and LINK
        assert fixed["ETH"] > 0.05
        assert fixed["LINK"] > 0.05
        assert len(events) > 0

    def test_doge_special_cap(self) -> None:
        v = self._make_validator()
        weights = {"DOGE": 0.07}
        fixed, events = v.validate_and_fix(weights, max_deployment=0.80)
        assert fixed["DOGE"] <= 0.05 + 1e-6

    def test_trump_cap(self) -> None:
        v = self._make_validator()
        weights = {"TRUMP": 0.05}
        fixed, events = v.validate_and_fix(weights, max_deployment=0.80)
        assert fixed["TRUMP"] <= 0.02 + 1e-6

    def test_paxg_cap(self) -> None:
        v = self._make_validator()
        weights = {"PAXG": 0.20}
        fixed, events = v.validate_and_fix(weights, max_deployment=0.80)
        assert fixed["PAXG"] <= 0.15 + 1e-6

    def test_total_exposure_scaling(self) -> None:
        v = self._make_validator()
        # Total = 0.95, exceeds 0.90 hard cap
        weights = {"BTC": 0.08, "ETH": 0.08, "LINK": 0.08,
                   "AAVE": 0.06, "DOGE": 0.05}
        # Sum is 0.35, within limits. But if max_deployment = 0.30:
        fixed, events = v.validate_and_fix(weights, max_deployment=0.30)
        total = sum(fixed.values())
        assert total <= 0.30 + 1e-6

    def test_tier_3_cap(self) -> None:
        v = self._make_validator()
        weights = {"AAVE": 0.08}  # Tier 3 cap is 0.06
        fixed, events = v.validate_and_fix(weights, max_deployment=0.80)
        assert fixed["AAVE"] <= 0.06 + 1e-6

    def test_tier_4_meme_cap(self) -> None:
        v = self._make_validator()
        weights = {"SHIB": 0.05}  # Tier 4 cap is 0.03
        fixed, events = v.validate_and_fix(weights, max_deployment=0.80)
        assert fixed["SHIB"] <= 0.03 + 1e-6

    def test_tier_5_obscure_cap(self) -> None:
        v = self._make_validator()
        weights = {"SOMI": 0.04}  # Tier 5 cap is 0.02
        fixed, events = v.validate_and_fix(weights, max_deployment=0.80)
        assert fixed["SOMI"] <= 0.02 + 1e-6

    def test_validate_only_reports_without_fixing(self) -> None:
        v = self._make_validator()
        weights = {"BTC": 0.12, "DOGE": 0.07}
        events = v.validate_only(weights, max_deployment=0.80)
        assert len(events) >= 2  # Both BTC and DOGE breach


# =========================================================================
# RiskManager integration tests
# =========================================================================


class TestRiskManager:
    """Integration tests for the unified RiskManager."""

    def _make_risk_manager(self) -> "RiskManager":
        from src.risk.manager import RiskManager

        stops = TrailingStopManager(
            base_stops={"tier_1_3": 0.06, "tier_4_5": 0.08, "trump": 0.10},
            min_stop_floor=0.02,
        )
        breakers = CircuitBreakerManager(
            drawdown_soft_warning=0.05,
            drawdown_hard_halt=0.08,
            daily_loss_soft_warning=0.03,
            daily_loss_hard_reduce=0.05,
            single_asset_loss_soft=0.04,
            single_asset_loss_hard=0.06,
        )
        contagion = ContagionMonitor(
            ratio_threshold=0.80, avg_loss_threshold=0.01
        )
        validator = PreTradeValidator(
            tier_caps={"DOGE": 0.05, "TRUMP": 0.02},
            asset_tier_map={"BTC": "tier_1_2", "ETH": "tier_1_2", "DOGE": "tier_1_2"},
            tier_cap_defaults={"tier_1_2": 0.08, "tier_3": 0.06},
            max_crypto_exposure=0.90,
        )
        asset_tier_map = {"BTC": "tier_1_2", "ETH": "tier_1_2", "DOGE": "tier_4_meme"}
        stop_distances = {"tier_1_3": 0.06, "tier_4_5": 0.08, "trump": 0.10}

        mgr = RiskManager(
            stops=stops,
            breakers=breakers,
            contagion=contagion,
            validator=validator,
            asset_tier_map=asset_tier_map,
            stop_distances=stop_distances,
        )
        mgr.initialize_nav(1_000_000.0)
        return mgr

    def _make_tracker(self) -> "PositionTracker":
        from src.execution.position_tracker import PositionTracker

        tracker = PositionTracker(starting_capital=1_000_000.0)
        tracker.on_buy_fill("BTC", 1.0, 50000.0)
        tracker.on_buy_fill("ETH", 10.0, 3000.0)
        return tracker

    def test_tick_no_events_normal_conditions(self) -> None:
        """No risk events when prices are near entry."""
        rm = self._make_risk_manager()
        tracker = self._make_tracker()

        events = rm.tick(
            current_prices={"BTC": 50000.0, "ETH": 3000.0},
            tracker=tracker,
        )
        # Prices at entry — no trailing stop or drawdown events
        assert len(events) == 0

    def test_tick_trailing_stop_triggers(self) -> None:
        """Trailing stop fires when price drops 7% from peak."""
        rm = self._make_risk_manager()
        tracker = self._make_tracker()

        # BTC drops 7% from entry (entry = peak = 50000)
        events = rm.tick(
            current_prices={"BTC": 46000.0, "ETH": 3000.0},
            tracker=tracker,
        )
        stop_events = [e for e in events if e.event_type == EventType.TRAILING_STOP]
        assert len(stop_events) == 1
        assert stop_events[0].asset == "BTC"
        assert stop_events[0].severity == Severity.CRITICAL

    def test_tick_sync_opens_stops_for_new_positions(self) -> None:
        """_sync_stops auto-registers stops for new tracker positions."""
        rm = self._make_risk_manager()
        tracker = self._make_tracker()

        assert "BTC" not in rm.stops.positions
        rm.tick({"BTC": 50000.0, "ETH": 3000.0}, tracker)
        assert "BTC" in rm.stops.positions
        assert "ETH" in rm.stops.positions

    def test_tick_sync_closes_stops_for_exited_positions(self) -> None:
        """_sync_stops removes stops when position is sold."""
        rm = self._make_risk_manager()
        tracker = self._make_tracker()

        # First tick — register stops
        rm.tick({"BTC": 50000.0, "ETH": 3000.0}, tracker)
        assert "ETH" in rm.stops.positions

        # Sell ETH completely
        tracker.on_sell_fill("ETH", 10.0, 3000.0)
        rm.tick({"BTC": 50000.0}, tracker)
        assert "ETH" not in rm.stops.positions

    def test_tick_syncs_peak_from_tracker(self) -> None:
        """Peak price from PositionTracker is synced to stop manager."""
        rm = self._make_risk_manager()
        tracker = self._make_tracker()

        # Update tracker price to a higher peak
        tracker.update_prices({"BTC": 55000.0, "ETH": 3000.0})

        rm.tick({"BTC": 55000.0, "ETH": 3000.0}, tracker)
        assert rm.stops.positions["BTC"].peak_price == 55000.0

    def test_on_position_opened(self) -> None:
        """Manual position open sets correct stop distance."""
        rm = self._make_risk_manager()
        rm.on_position_opened("BTC", entry_price=50000.0)
        assert "BTC" in rm.stops.positions
        assert rm.stops.positions["BTC"].base_stop_pct == 0.06  # tier_1_3

    def test_on_position_closed(self) -> None:
        """Manual position close removes stop tracking."""
        rm = self._make_risk_manager()
        rm.on_position_opened("BTC", entry_price=50000.0)
        rm.on_position_closed("BTC")
        assert "BTC" not in rm.stops.positions

    def test_get_risk_exit_assets(self) -> None:
        """Extracts CRITICAL asset-level events as exit set."""
        rm = self._make_risk_manager()

        events = [
            RiskEvent(
                event_type=EventType.TRAILING_STOP,
                severity=Severity.CRITICAL,
                triggered_value=0.07,
                limit_value=0.06,
                action_required="SELL",
                asset="BTC",
            ),
            RiskEvent(
                event_type=EventType.CIRCUIT_BREAKER_DD,
                severity=Severity.CRITICAL,
                triggered_value=0.09,
                limit_value=0.08,
                action_required="HALT",
                # No asset — portfolio-level
            ),
            RiskEvent(
                event_type=EventType.SINGLE_ASSET_LOSS,
                severity=Severity.MEDIUM,
                triggered_value=0.045,
                limit_value=0.04,
                action_required="LOG",
                asset="ETH",
            ),
        ]
        exits = rm.get_risk_exit_assets(events)
        assert exits == {"BTC"}  # Only CRITICAL + asset-level

    def test_pre_trade_check_delegates(self) -> None:
        """pre_trade_check passes through to validator."""
        rm = self._make_risk_manager()
        weights = {"BTC": 0.12, "ETH": 0.05}
        fixed, events = rm.pre_trade_check(weights, max_deployment=0.80)
        assert fixed["BTC"] <= 0.08 + 1e-6

    def test_is_halted_delegates(self) -> None:
        """is_halted reflects circuit breaker state."""
        from src.execution.position_tracker import PositionTracker

        rm = self._make_risk_manager()
        assert not rm.is_halted

        # Need large positions relative to NAV for drawdown to hit 8%
        tracker = PositionTracker(starting_capital=100_000.0)
        tracker.on_buy_fill("BTC", 1.0, 50000.0)   # 50% of NAV
        tracker.on_buy_fill("ETH", 10.0, 3000.0)    # 30% of NAV
        rm.initialize_nav(100_000.0)

        # Update tracker prices so tracker.nav reflects drawdown
        prices = {"BTC": 41000.0, "ETH": 2400.0}
        tracker.update_prices(prices)
        # NAV = 20k cash + 41k + 24k = 85k → 15% DD from 100k
        rm.tick(prices, tracker)
        assert rm.is_halted

    def test_stop_distance_lookup(self) -> None:
        """_get_stop_distance returns correct distance per tier."""
        rm = self._make_risk_manager()
        assert rm._get_stop_distance("BTC") == 0.06     # tier_1_2 → tier_1_3
        assert rm._get_stop_distance("DOGE") == 0.08    # tier_4_meme → tier_4_5
        assert rm._get_stop_distance("TRUMP") == 0.10   # special

    def test_tick_events_sorted_by_priority(self) -> None:
        """Events returned by tick() are sorted highest priority first."""
        rm = self._make_risk_manager()
        tracker = self._make_tracker()

        # Both BTC and ETH hit trailing stops, plus drawdown
        events = rm.tick(
            current_prices={"BTC": 45000.0, "ETH": 2700.0},
            tracker=tracker,
        )
        # Events should be sorted (highest priority first — already sorted)
        for i in range(len(events) - 1):
            assert not (events[i + 1] < events[i])

    def test_contagion_triggers_on_tick(self) -> None:
        """Contagion event fires when ratio and avg_loss are high."""
        rm = self._make_risk_manager()
        tracker = self._make_tracker()

        events = rm.tick(
            current_prices={"BTC": 50000.0, "ETH": 3000.0},
            tracker=tracker,
            contagion_ratio=0.85,
            avg_loss=0.015,
        )
        contagion_events = [
            e for e in events if e.event_type == EventType.CONTAGION_CRISIS
        ]
        assert len(contagion_events) == 1


# =========================================================================
# Factory integration tests
# =========================================================================


class TestRiskFactory:
    """Tests for risk factory functions (config-driven creation)."""

    def _make_mock_config(self) -> "Config":
        """Create a mock Config with risk parameters."""
        from apex.core.config import Config

        return Config(config_path=(
            Path(__file__).resolve().parent.parent / "config.yaml"
        ))

    def test_create_trailing_stop_manager(self) -> None:
        from src.risk.factory import create_trailing_stop_manager

        config = self._make_mock_config()
        mgr = create_trailing_stop_manager(config, phase=1)
        assert mgr is not None
        # Phase 1: no tightening
        assert mgr._tightening_config is None

    def test_create_trailing_stop_manager_phase2(self) -> None:
        from src.risk.factory import create_trailing_stop_manager

        config = self._make_mock_config()
        mgr = create_trailing_stop_manager(config, phase=2)
        assert mgr._tightening_config is not None

    def test_create_circuit_breaker_manager(self) -> None:
        from src.risk.factory import create_circuit_breaker_manager

        config = self._make_mock_config()
        mgr = create_circuit_breaker_manager(config, starting_nav=500_000.0)
        assert mgr is not None
        assert mgr.drawdown.peak_nav == 500_000.0

    def test_create_contagion_monitor(self) -> None:
        from src.risk.factory import create_contagion_monitor

        config = self._make_mock_config()
        mon = create_contagion_monitor(config)
        assert mon is not None

    def test_create_pre_trade_validator(self) -> None:
        from src.risk.factory import create_pre_trade_validator

        config = self._make_mock_config()
        validator = create_pre_trade_validator(config)
        assert validator is not None
        # DOGE and TRUMP should have special caps
        assert validator.get_cap_for_asset("DOGE") <= 0.05 + 1e-6
        assert validator.get_cap_for_asset("TRUMP") <= 0.02 + 1e-6

    def test_create_risk_manager(self) -> None:
        from src.risk.factory import create_risk_manager

        config = self._make_mock_config()
        rm = create_risk_manager(config, starting_nav=1_000_000.0, phase=1)
        assert rm is not None
        assert not rm.is_halted


# =========================================================================
# Run with pytest
# =========================================================================

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
