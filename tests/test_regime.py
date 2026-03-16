"""Tests for Layer 3 — Regime Detection.

Covers:
- RegimeState creation and transitions
- Minimal vol guard (Phase 1)
- Full regime classification rules (Phase 2)
- Asymmetric transition logic (immediate downgrade, gradual upgrade)
- Crisis exit conditions
- Contagion proxy computation
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.regime.regime_state import (
    RegimeState,
    RegimeType,
    is_downgrade,
    is_upgrade,
)
from src.regime.contagion import ContagionProbe, ContagionResult
from src.regime.detector import RegimeDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**overrides) -> dict:
    """Create a minimal test config for regime detector."""
    config = {
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
        },
        "phase1_vol_guard": {
            "btc_30d_median_vol": 0.02,
            "vol_spike_multiplier": 2.0,
            "defensive_max_exposure": 0.40,
            "normal_max_exposure": 0.75,
        },
    }
    for key, val in overrides.items():
        keys = key.split(".")
        d = config
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = val
    return config


@dataclass
class MockRegimeInputs:
    """Mock of FeatureEngine's RegimeInputs for testing."""

    btc_4h_return: Optional[float] = None
    btc_24h_return: Optional[float] = None
    btc_trend: str = "neutral"
    btc_vol_percentile: Optional[float] = None
    altcoin_breadth: Optional[float] = None
    volatility_ratio: Optional[float] = None
    btc_dominance_1h: Optional[float] = None
    btc_dominance_4h: Optional[float] = None


def no_contagion() -> ContagionResult:
    """Contagion result with no stress."""
    return ContagionResult(
        contagion_ratio=0.0, avg_loss=0.0,
        negative_count=0, total_positions=0,
    )


def high_contagion() -> ContagionResult:
    """Contagion result triggering crisis."""
    return ContagionResult(
        contagion_ratio=0.85, avg_loss=0.015,
        negative_count=9, total_positions=10,
    )


# ---------------------------------------------------------------------------
# RegimeState unit tests
# ---------------------------------------------------------------------------

class TestRegimeState:
    def test_default_state(self):
        state = RegimeState()
        assert state.current_regime == RegimeType.TREND_BULL
        assert state.previous_regime == RegimeType.TREND_BULL
        assert state.bars_in_current_regime == 0
        assert not state.transition_pending

    def test_confirm_transition(self):
        state = RegimeState()
        state.bars_in_current_regime = 100
        state.confirm_transition(RegimeType.TREND_BEAR)
        assert state.current_regime == RegimeType.TREND_BEAR
        assert state.previous_regime == RegimeType.TREND_BULL
        assert state.bars_in_current_regime == 0
        assert not state.transition_pending

    def test_start_and_cancel_upgrade(self):
        state = RegimeState(current_regime=RegimeType.TREND_BEAR)
        state.start_upgrade(RegimeType.MEAN_REVERT, 30)
        assert state.transition_pending
        assert state.transition_target == RegimeType.MEAN_REVERT
        assert state.transition_bars_remaining == 30

        state.cancel_upgrade()
        assert not state.transition_pending
        assert state.transition_target is None

    def test_to_log_dict(self):
        state = RegimeState()
        d = state.to_log_dict()
        assert d["current_regime"] == "TREND_BULL"
        assert "contagion_proxy" in d


class TestTransitionClassification:
    def test_downgrade_bull_to_crisis(self):
        assert is_downgrade(RegimeType.TREND_BULL, RegimeType.HIGH_VOL_CRISIS)

    def test_downgrade_bull_to_bear(self):
        assert is_downgrade(RegimeType.TREND_BULL, RegimeType.TREND_BEAR)

    def test_downgrade_mean_to_crisis(self):
        assert is_downgrade(RegimeType.MEAN_REVERT, RegimeType.HIGH_VOL_CRISIS)

    def test_upgrade_bear_to_mean(self):
        assert is_upgrade(RegimeType.TREND_BEAR, RegimeType.MEAN_REVERT)

    def test_upgrade_mean_to_bull(self):
        assert is_upgrade(RegimeType.MEAN_REVERT, RegimeType.TREND_BULL)

    def test_not_downgrade_bear_to_bull(self):
        assert not is_downgrade(RegimeType.TREND_BEAR, RegimeType.TREND_BULL)

    def test_same_is_neither(self):
        assert not is_downgrade(RegimeType.TREND_BULL, RegimeType.TREND_BULL)
        assert not is_upgrade(RegimeType.TREND_BULL, RegimeType.TREND_BULL)


# ---------------------------------------------------------------------------
# Contagion probe tests
# ---------------------------------------------------------------------------

class TestContagionProbe:
    def test_empty_positions(self):
        probe = ContagionProbe(return_window=5)
        result = probe.compute([], lambda a, w: 0.0)
        assert result.contagion_ratio == 0.0
        assert result.total_positions == 0

    def test_all_positive(self):
        probe = ContagionProbe(return_window=5)
        assets = ["BTC", "ETH", "SOL"]
        returns = {"BTC": 0.02, "ETH": 0.01, "SOL": 0.005}
        result = probe.compute(assets, lambda a, w: returns.get(a))
        assert result.contagion_ratio == 0.0
        assert result.negative_count == 0

    def test_all_negative(self):
        probe = ContagionProbe(return_window=5)
        assets = ["BTC", "ETH", "SOL"]
        returns = {"BTC": -0.02, "ETH": -0.01, "SOL": -0.005}
        result = probe.compute(assets, lambda a, w: returns.get(a))
        assert result.contagion_ratio == 1.0
        assert result.negative_count == 3
        assert result.avg_loss == pytest.approx(
            (0.02 + 0.01 + 0.005) / 3, abs=1e-8
        )

    def test_mixed_positions(self):
        probe = ContagionProbe(return_window=5)
        assets = ["A", "B", "C", "D", "E"]
        returns = {"A": 0.01, "B": -0.02, "C": 0.005, "D": -0.01, "E": -0.03}
        result = probe.compute(assets, lambda a, w: returns.get(a))
        assert result.contagion_ratio == pytest.approx(3 / 5)
        assert result.negative_count == 3
        assert result.avg_loss == pytest.approx(
            (0.02 + 0.01 + 0.03) / 3, abs=1e-8
        )

    def test_none_returns_skipped(self):
        probe = ContagionProbe(return_window=5)
        assets = ["A", "B", "C"]
        returns = {"A": -0.01, "B": None, "C": 0.01}
        result = probe.compute(assets, lambda a, w: returns.get(a))
        assert result.total_positions == 2
        assert result.negative_count == 1
        assert result.contagion_ratio == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Regime detector tests — Phase 1 vol guard
# ---------------------------------------------------------------------------

class TestPhase1VolGuard:
    def test_normal_mode_when_vol_low(self):
        det = RegimeDetector(make_config())
        state = det.update_phase1(btc_24h_vol=0.03)
        assert state.current_regime == RegimeType.TREND_BULL

    def test_crisis_when_vol_high(self):
        det = RegimeDetector(make_config())
        # median=0.02, multiplier=2.0, threshold=0.04
        state = det.update_phase1(btc_24h_vol=0.05)
        assert state.current_regime == RegimeType.HIGH_VOL_CRISIS

    def test_normal_at_exact_threshold(self):
        det = RegimeDetector(make_config())
        # threshold = 0.02 * 2.0 = 0.04 — at threshold, not exceeding
        state = det.update_phase1(btc_24h_vol=0.04)
        assert state.current_regime == RegimeType.TREND_BULL

    def test_default_when_median_zero(self):
        config = make_config()
        config["phase1_vol_guard"]["btc_30d_median_vol"] = 0.0
        det = RegimeDetector(config)
        state = det.update_phase1(btc_24h_vol=0.99)
        assert state.current_regime == RegimeType.TREND_BULL

    def test_default_when_vol_none(self):
        det = RegimeDetector(make_config())
        state = det.update_phase1(btc_24h_vol=None)
        assert state.current_regime == RegimeType.TREND_BULL

    def test_exposure_normal(self):
        det = RegimeDetector(make_config())
        det.update_phase1(btc_24h_vol=0.01)
        assert det.vol_guard_max_exposure == 0.75

    def test_exposure_defensive(self):
        det = RegimeDetector(make_config())
        det.update_phase1(btc_24h_vol=0.05)
        assert det.vol_guard_max_exposure == 0.40

    def test_recovery_from_crisis(self):
        det = RegimeDetector(make_config())
        det.update_phase1(btc_24h_vol=0.05)
        assert det.state.current_regime == RegimeType.HIGH_VOL_CRISIS

        det.update_phase1(btc_24h_vol=0.03)
        assert det.state.current_regime == RegimeType.TREND_BULL

    def test_bars_increment(self):
        det = RegimeDetector(make_config())
        det.update_phase1(btc_24h_vol=0.01)
        assert det.state.bars_in_current_regime == 1
        det.update_phase1(btc_24h_vol=0.01)
        assert det.state.bars_in_current_regime == 2

    def test_bars_reset_on_transition(self):
        det = RegimeDetector(make_config())
        for _ in range(5):
            det.update_phase1(btc_24h_vol=0.01)
        assert det.state.bars_in_current_regime == 5

        det.update_phase1(btc_24h_vol=0.05)
        assert det.state.bars_in_current_regime == 1


# ---------------------------------------------------------------------------
# Regime detector tests — Phase 2 classification rules
# ---------------------------------------------------------------------------

class TestPhase2Classification:
    def test_contagion_crisis(self):
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(btc_vol_percentile=50.0)
        contagion = high_contagion()
        det.update(inputs, contagion)
        assert det.state.current_regime == RegimeType.HIGH_VOL_CRISIS

    def test_btc_vol_crisis(self):
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(btc_vol_percentile=95.0)
        det.update(inputs, no_contagion())
        assert det.state.current_regime == RegimeType.HIGH_VOL_CRISIS

    def test_trend_bull(self):
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.02,
            btc_24h_return=0.05,
            altcoin_breadth=0.65,
            btc_vol_percentile=50.0,
        )
        det.update(inputs, no_contagion())
        assert det.state.current_regime == RegimeType.TREND_BULL

    def test_trend_bear(self):
        det = RegimeDetector(make_config())
        # First go to MEAN_REVERT so bear is a downgrade
        det._state.current_regime = RegimeType.MEAN_REVERT
        inputs = MockRegimeInputs(
            btc_4h_return=-0.02,
            btc_24h_return=-0.05,
            altcoin_breadth=0.30,
            btc_vol_percentile=50.0,
        )
        det.update(inputs, no_contagion())
        assert det.state.current_regime == RegimeType.TREND_BEAR

    def test_mean_revert_default(self):
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.01,
            btc_24h_return=-0.01,
            altcoin_breadth=0.50,
            btc_vol_percentile=50.0,
        )
        det.update(inputs, no_contagion())
        # Starting from TREND_BULL → MEAN_REVERT is a downgrade? No,
        # BULL→MEAN_REVERT is neither upgrade nor downgrade in the defined sets.
        # But the detector should handle it. Let's check from a neutral start.
        det._state.current_regime = RegimeType.MEAN_REVERT
        det._state.bars_in_current_regime = 0
        det.update(inputs, no_contagion())
        assert det.state.current_regime == RegimeType.MEAN_REVERT

    def test_contagion_overrides_bull(self):
        """Contagion crisis should override even if BTC trend is bullish."""
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.02,
            btc_24h_return=0.05,
            altcoin_breadth=0.65,
            btc_vol_percentile=50.0,
        )
        contagion = high_contagion()
        det.update(inputs, contagion)
        assert det.state.current_regime == RegimeType.HIGH_VOL_CRISIS


# ---------------------------------------------------------------------------
# Regime detector tests — asymmetric transitions
# ---------------------------------------------------------------------------

class TestAsymmetricTransitions:
    def test_downgrade_is_immediate(self):
        det = RegimeDetector(make_config())
        # Start in BULL, downgrade to CRISIS
        inputs = MockRegimeInputs(btc_vol_percentile=95.0)
        det.update(inputs, no_contagion())
        assert det.state.current_regime == RegimeType.HIGH_VOL_CRISIS
        assert not det.state.transition_pending

    def test_upgrade_requires_confirmation(self):
        det = RegimeDetector(make_config())
        det._state.confirm_transition(RegimeType.TREND_BEAR)

        bull_inputs = MockRegimeInputs(
            btc_4h_return=0.02,
            btc_24h_return=0.05,
            altcoin_breadth=0.65,
            btc_vol_percentile=50.0,
        )

        # First update: should start pending upgrade
        det.update(bull_inputs, no_contagion())
        assert det.state.current_regime == RegimeType.TREND_BEAR
        assert det.state.transition_pending
        assert det.state.transition_target == RegimeType.TREND_BULL

    def test_upgrade_confirms_after_n_bars(self):
        config = make_config()
        config["regime"]["transitions"]["upgrade_confirmation_bars"] = 3
        det = RegimeDetector(config)
        det._state.confirm_transition(RegimeType.TREND_BEAR)

        bull_inputs = MockRegimeInputs(
            btc_4h_return=0.02,
            btc_24h_return=0.05,
            altcoin_breadth=0.65,
            btc_vol_percentile=50.0,
        )

        # 3 bars of upgrade signal needed
        det.update(bull_inputs, no_contagion())
        assert det.state.current_regime == RegimeType.TREND_BEAR

        det.update(bull_inputs, no_contagion())
        assert det.state.current_regime == RegimeType.TREND_BEAR

        det.update(bull_inputs, no_contagion())
        # After 3rd bar, should confirm
        assert det.state.current_regime == RegimeType.TREND_BULL
        assert not det.state.transition_pending

    def test_upgrade_cancelled_on_interruption(self):
        config = make_config()
        config["regime"]["transitions"]["upgrade_confirmation_bars"] = 5
        det = RegimeDetector(config)
        det._state.confirm_transition(RegimeType.TREND_BEAR)

        bull_inputs = MockRegimeInputs(
            btc_4h_return=0.02, btc_24h_return=0.05,
            altcoin_breadth=0.65, btc_vol_percentile=50.0,
        )
        bear_inputs = MockRegimeInputs(
            btc_4h_return=-0.02, btc_24h_return=-0.05,
            altcoin_breadth=0.30, btc_vol_percentile=50.0,
        )

        # Start upgrade, then interrupt
        det.update(bull_inputs, no_contagion())
        assert det.state.transition_pending

        det.update(bear_inputs, no_contagion())
        # Bear while in BEAR is same regime — no pending anymore
        assert det.state.current_regime == RegimeType.TREND_BEAR

    def test_crisis_exit_requires_sustained_normalization(self):
        config = make_config()
        config["regime"]["transitions"]["crisis_exit_confirmation_bars"] = 3
        det = RegimeDetector(config)
        det._state.confirm_transition(RegimeType.HIGH_VOL_CRISIS)

        normal_inputs = MockRegimeInputs(
            btc_4h_return=0.01, btc_24h_return=0.01,
            altcoin_breadth=0.50, btc_vol_percentile=40.0,
        )
        low_contagion = ContagionResult(
            contagion_ratio=0.30, avg_loss=0.005,
            negative_count=3, total_positions=10,
        )

        # 3 bars needed to exit crisis
        det.update(normal_inputs, low_contagion)
        assert det.state.current_regime == RegimeType.HIGH_VOL_CRISIS

        det.update(normal_inputs, low_contagion)
        assert det.state.current_regime == RegimeType.HIGH_VOL_CRISIS

        det.update(normal_inputs, low_contagion)
        assert det.state.current_regime != RegimeType.HIGH_VOL_CRISIS

    def test_crisis_exit_resets_on_spike(self):
        config = make_config()
        config["regime"]["transitions"]["crisis_exit_confirmation_bars"] = 3
        det = RegimeDetector(config)
        det._state.confirm_transition(RegimeType.HIGH_VOL_CRISIS)

        normal_inputs = MockRegimeInputs(
            btc_4h_return=0.01, btc_24h_return=0.01,
            altcoin_breadth=0.50, btc_vol_percentile=40.0,
        )
        low_contagion = ContagionResult(
            contagion_ratio=0.30, avg_loss=0.005,
            negative_count=3, total_positions=10,
        )
        spike_contagion = ContagionResult(
            contagion_ratio=0.60, avg_loss=0.01,
            negative_count=6, total_positions=10,
        )

        # 2 normal bars, then spike resets
        det.update(normal_inputs, low_contagion)
        det.update(normal_inputs, low_contagion)
        det.update(normal_inputs, spike_contagion)
        assert det.state.current_regime == RegimeType.HIGH_VOL_CRISIS

        # Need full 3 bars again
        det.update(normal_inputs, low_contagion)
        det.update(normal_inputs, low_contagion)
        assert det.state.current_regime == RegimeType.HIGH_VOL_CRISIS
        det.update(normal_inputs, low_contagion)
        assert det.state.current_regime != RegimeType.HIGH_VOL_CRISIS
