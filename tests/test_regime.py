"""Tests for Layer 3 — Regime Detection.

Covers:
- RegimeState creation and transitions
- Minimal vol guard (Phase 1)
- Full regime classification rules (Phase 2)
- Asymmetric transition logic (immediate downgrade, gradual upgrade)
- Crisis exit conditions
- Contagion proxy computation
- Small portfolio overrides
- Cold-start behavior
- Input validation (NaN handling)
"""

import logging
import math
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


def no_positions() -> dict:
    """Empty positions dict."""
    return {}


def make_positions(n: int, prefix: str = "ASSET") -> dict:
    """Create n mock positions."""
    return {f"{prefix}{i}": {"qty": 100} for i in range(n)}


def make_return_fn(returns: dict):
    """Create a get_return_fn from a returns dict."""
    def fn(asset, window):
        return returns.get(asset)
    return fn


def all_negative_returns(positions: dict, loss: float = -0.02) -> dict:
    """Returns dict where every position has the given negative return."""
    return {asset: loss for asset in positions}


def all_positive_returns(positions: dict, gain: float = 0.02) -> dict:
    """Returns dict where every position has the given positive return."""
    return {asset: gain for asset in positions}


# ---------------------------------------------------------------------------
# RegimeState unit tests
# ---------------------------------------------------------------------------

class TestRegimeState:
    def test_default_state(self):
        state = RegimeState()
        assert state.current_regime == RegimeType.MEAN_REVERT
        assert state.previous_regime == RegimeType.MEAN_REVERT
        assert state.bars_in_current_regime == 0
        assert not state.transition_pending

    def test_confirm_transition(self):
        state = RegimeState()
        state.bars_in_current_regime = 100
        state.confirm_transition(RegimeType.TREND_BEAR)
        assert state.current_regime == RegimeType.TREND_BEAR
        assert state.previous_regime == RegimeType.MEAN_REVERT
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
        assert d["current_regime"] == "MEAN_REVERT"
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
        result = probe.compute({}, lambda a, w: 0.0)
        assert result.contagion_ratio == 0.0
        assert result.total_positions == 0

    def test_all_positive(self):
        probe = ContagionProbe(return_window=5)
        positions = {"BTC": {}, "ETH": {}, "SOL": {}}
        returns = {"BTC": 0.02, "ETH": 0.01, "SOL": 0.005}
        result = probe.compute(positions, make_return_fn(returns))
        assert result.contagion_ratio == 0.0
        assert result.negative_count == 0

    def test_all_negative(self):
        probe = ContagionProbe(return_window=5)
        positions = {"BTC": {}, "ETH": {}, "SOL": {}}
        returns = {"BTC": -0.02, "ETH": -0.01, "SOL": -0.005}
        result = probe.compute(positions, make_return_fn(returns))
        assert result.contagion_ratio == 1.0
        assert result.negative_count == 3
        assert result.avg_loss == pytest.approx(
            (0.02 + 0.01 + 0.005) / 3, abs=1e-8
        )

    def test_mixed_positions(self):
        probe = ContagionProbe(return_window=5)
        positions = {"A": {}, "B": {}, "C": {}, "D": {}, "E": {}}
        returns = {"A": 0.01, "B": -0.02, "C": 0.005, "D": -0.01, "E": -0.03}
        result = probe.compute(positions, make_return_fn(returns))
        assert result.contagion_ratio == pytest.approx(3 / 5)
        assert result.negative_count == 3
        assert result.avg_loss == pytest.approx(
            (0.02 + 0.01 + 0.03) / 3, abs=1e-8
        )

    def test_none_returns_skipped(self):
        probe = ContagionProbe(return_window=5)
        positions = {"A": {}, "B": {}, "C": {}}
        returns = {"A": -0.01, "B": None, "C": 0.01}
        result = probe.compute(positions, make_return_fn(returns))
        assert result.total_positions == 2
        assert result.negative_count == 1
        assert result.contagion_ratio == pytest.approx(0.5)

    def test_small_portfolio_flag(self):
        probe = ContagionProbe(return_window=5, small_portfolio_size=6)
        # 3 positions < 6 → small portfolio
        positions = make_positions(3)
        returns = {f"ASSET{i}": -0.02 for i in range(3)}
        result = probe.compute(positions, make_return_fn(returns))
        assert result.is_small_portfolio is True

    def test_large_portfolio_flag(self):
        probe = ContagionProbe(return_window=5, small_portfolio_size=6)
        # 8 positions >= 6 → not small
        positions = make_positions(8)
        returns = {f"ASSET{i}": -0.02 for i in range(8)}
        result = probe.compute(positions, make_return_fn(returns))
        assert result.is_small_portfolio is False


# ---------------------------------------------------------------------------
# Regime detector tests — Phase 1 vol guard
# ---------------------------------------------------------------------------

class TestPhase1VolGuard:
    def test_normal_mode_when_vol_low(self):
        det = RegimeDetector(make_config())
        det._state.confirm_transition(RegimeType.TREND_BULL)
        state = det.update_phase1(btc_24h_vol=0.03)
        assert state.current_regime == RegimeType.TREND_BULL

    def test_crisis_when_vol_high(self):
        det = RegimeDetector(make_config())
        # median=0.02, multiplier=2.0, threshold=0.04
        state = det.update_phase1(btc_24h_vol=0.05)
        assert state.current_regime == RegimeType.HIGH_VOL_CRISIS

    def test_normal_at_exact_threshold(self):
        det = RegimeDetector(make_config())
        det._state.confirm_transition(RegimeType.TREND_BULL)
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
        det._state.confirm_transition(RegimeType.TREND_BULL)
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
        det._state.confirm_transition(RegimeType.TREND_BULL)
        det._state.bars_in_current_regime = 0
        det.update_phase1(btc_24h_vol=0.01)
        assert det.state.bars_in_current_regime == 1
        det.update_phase1(btc_24h_vol=0.01)
        assert det.state.bars_in_current_regime == 2

    def test_bars_reset_on_transition(self):
        det = RegimeDetector(make_config())
        det._state.confirm_transition(RegimeType.TREND_BULL)
        det._state.bars_in_current_regime = 0
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
        """CRISIS triggers immediately on high contagion."""
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.0, btc_24h_return=0.0,
            btc_vol_percentile=50.0, altcoin_breadth=0.50,
        )
        # 10 positions, 9 negative → ratio 0.9 > 0.8, loss 0.015 > 0.01
        positions = make_positions(10)
        returns = {f"ASSET{i}": -0.015 for i in range(9)}
        returns["ASSET9"] = 0.01
        det.update(inputs, positions, make_return_fn(returns))
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS

    def test_btc_vol_crisis(self):
        """CRISIS triggers immediately on BTC vol > 90th percentile."""
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.0, btc_24h_return=0.0,
            btc_vol_percentile=95.0, altcoin_breadth=0.50,
        )
        det.update(inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS

    def test_trend_bull(self):
        # MEAN_REVERT → BULL is an upgrade, needs persistence
        config = make_config()
        config["regime"]["transitions"]["upgrade_confirmation_bars"] = 2
        det = RegimeDetector(config)
        inputs = MockRegimeInputs(
            btc_4h_return=0.02,
            btc_24h_return=0.05,
            altcoin_breadth=0.65,
            btc_vol_percentile=50.0,
        )
        # Bar 1: starts upgrade
        det.update(inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.MEAN_REVERT
        # Bar 2: confirms upgrade
        det.update(inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.TREND_BULL

    def test_trend_bear(self):
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=-0.02,
            btc_24h_return=-0.05,
            altcoin_breadth=0.30,
            btc_vol_percentile=50.0,
        )
        det.update(inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.TREND_BEAR

    def test_mean_revert_default(self):
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.01,
            btc_24h_return=-0.01,
            altcoin_breadth=0.50,
            btc_vol_percentile=50.0,
        )
        det.update(inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.MEAN_REVERT

    def test_contagion_overrides_bull(self):
        """Contagion crisis should override even if BTC trend is bullish."""
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.02,
            btc_24h_return=0.05,
            altcoin_breadth=0.65,
            btc_vol_percentile=50.0,
        )
        # 10 positions all very negative
        positions = make_positions(10)
        returns = {f"ASSET{i}": -0.02 for i in range(10)}
        det.update(inputs, positions, make_return_fn(returns))
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS


# ---------------------------------------------------------------------------
# Regime detector tests — asymmetric transitions
# ---------------------------------------------------------------------------

class TestAsymmetricTransitions:
    def test_downgrade_is_immediate(self):
        """Downgrade BULL → BEAR is immediate (no persistence)."""
        det = RegimeDetector(make_config())
        det._state.confirm_transition(RegimeType.TREND_BULL)

        bear_inputs = MockRegimeInputs(
            btc_4h_return=-0.02, btc_24h_return=-0.05,
            altcoin_breadth=0.30, btc_vol_percentile=50.0,
        )
        det.update(bear_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.TREND_BEAR
        assert not det.state.transition_pending

    def test_crisis_immediate_no_persistence(self):
        """CRISIS triggers immediately on high contagion (no persistence delay)."""
        det = RegimeDetector(make_config())
        det._state.confirm_transition(RegimeType.TREND_BULL)

        inputs = MockRegimeInputs(
            btc_4h_return=0.02, btc_24h_return=0.05,
            altcoin_breadth=0.65, btc_vol_percentile=50.0,
        )
        positions = make_positions(10)
        returns = {f"ASSET{i}": -0.02 for i in range(10)}
        det.update(inputs, positions, make_return_fn(returns))
        # Should be immediate — no 30-bar wait
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS
        assert not det.state.transition_pending

    def test_crisis_immediate_btc_vol(self):
        """CRISIS triggers immediately on BTC vol > 90th percentile."""
        det = RegimeDetector(make_config())
        det._state.confirm_transition(RegimeType.TREND_BULL)

        inputs = MockRegimeInputs(
            btc_4h_return=0.02, btc_24h_return=0.05,
            altcoin_breadth=0.65, btc_vol_percentile=95.0,
        )
        det.update(inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS
        assert not det.state.transition_pending

    def test_upgrade_requires_confirmation(self):
        """Upgrade BEAR → MEAN_REVERT requires 30-min persistence."""
        det = RegimeDetector(make_config())
        det._state.confirm_transition(RegimeType.TREND_BEAR)

        mean_inputs = MockRegimeInputs(
            btc_4h_return=0.01, btc_24h_return=-0.01,
            altcoin_breadth=0.50, btc_vol_percentile=50.0,
        )

        # First update: should start pending upgrade
        det.update(mean_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.TREND_BEAR
        assert det.state.transition_pending
        assert det.state.transition_target == RegimeType.MEAN_REVERT

    def test_upgrade_bear_to_mean_30min(self):
        """Upgrade BEAR → MEAN_REVERT requires 30 consecutive minutes."""
        config = make_config()
        config["regime"]["transitions"]["upgrade_confirmation_bars"] = 3
        det = RegimeDetector(config)
        det._state.confirm_transition(RegimeType.TREND_BEAR)

        mean_inputs = MockRegimeInputs(
            btc_4h_return=0.01, btc_24h_return=-0.01,
            altcoin_breadth=0.50, btc_vol_percentile=50.0,
        )

        # 3 bars of upgrade signal needed
        det.update(mean_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.TREND_BEAR

        det.update(mean_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.TREND_BEAR

        det.update(mean_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.MEAN_REVERT
        assert not det.state.transition_pending

    def test_downgrade_bull_to_bear_immediate(self):
        """Downgrade BULL → BEAR is immediate (no persistence)."""
        det = RegimeDetector(make_config())
        det._state.confirm_transition(RegimeType.TREND_BULL)

        bear_inputs = MockRegimeInputs(
            btc_4h_return=-0.02, btc_24h_return=-0.05,
            altcoin_breadth=0.30, btc_vol_percentile=50.0,
        )
        det.update(bear_inputs, no_positions(), make_return_fn({}))
        # Immediate — no waiting
        assert det.current_regime == RegimeType.TREND_BEAR

    def test_upgrade_cancelled_on_interruption(self):
        config = make_config()
        config["regime"]["transitions"]["upgrade_confirmation_bars"] = 5
        det = RegimeDetector(config)
        det._state.confirm_transition(RegimeType.TREND_BEAR)

        mean_inputs = MockRegimeInputs(
            btc_4h_return=0.01, btc_24h_return=-0.01,
            altcoin_breadth=0.50, btc_vol_percentile=50.0,
        )
        bear_inputs = MockRegimeInputs(
            btc_4h_return=-0.02, btc_24h_return=-0.05,
            altcoin_breadth=0.30, btc_vol_percentile=50.0,
        )

        # Start upgrade, then interrupt
        det.update(mean_inputs, no_positions(), make_return_fn({}))
        assert det.state.transition_pending

        det.update(bear_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.TREND_BEAR

    def test_crisis_exit_requires_sustained_normalization(self):
        config = make_config()
        config["regime"]["transitions"]["crisis_exit_confirmation_bars"] = 3
        det = RegimeDetector(config)
        det._state.confirm_transition(RegimeType.HIGH_VOL_CRISIS)

        normal_inputs = MockRegimeInputs(
            btc_4h_return=0.01, btc_24h_return=0.01,
            altcoin_breadth=0.50, btc_vol_percentile=40.0,
        )

        # 3 bars needed to exit crisis, no contagion
        det.update(normal_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS

        det.update(normal_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS

        det.update(normal_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime != RegimeType.HIGH_VOL_CRISIS

    def test_crisis_exit_resets_on_spike(self):
        config = make_config()
        config["regime"]["transitions"]["crisis_exit_confirmation_bars"] = 3
        det = RegimeDetector(config)
        det._state.confirm_transition(RegimeType.HIGH_VOL_CRISIS)

        normal_inputs = MockRegimeInputs(
            btc_4h_return=0.01, btc_24h_return=0.01,
            altcoin_breadth=0.50, btc_vol_percentile=40.0,
        )
        spike_inputs = MockRegimeInputs(
            btc_4h_return=0.01, btc_24h_return=0.01,
            altcoin_breadth=0.50, btc_vol_percentile=80.0,  # vol above crisis_exit_vol (70)
        )

        # 2 normal bars, then spike resets
        det.update(normal_inputs, no_positions(), make_return_fn({}))
        det.update(normal_inputs, no_positions(), make_return_fn({}))
        det.update(spike_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS

        # Need full 3 bars again
        det.update(normal_inputs, no_positions(), make_return_fn({}))
        det.update(normal_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS
        det.update(normal_inputs, no_positions(), make_return_fn({}))
        assert det.current_regime != RegimeType.HIGH_VOL_CRISIS


# ---------------------------------------------------------------------------
# Small portfolio contagion threshold tests
# ---------------------------------------------------------------------------

class TestSmallPortfolio:
    def test_small_portfolio_uses_raised_thresholds(self):
        """Small portfolio (<6 positions) uses ratio=0.90 and loss=1.5%."""
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.0, btc_24h_return=0.0,
            btc_vol_percentile=50.0, altcoin_breadth=0.50,
        )

        # 4 positions (< 6), 4/4 negative → ratio 1.0 > 0.90
        # but avg_loss = 0.012 < 0.015 threshold → NOT crisis
        positions = make_positions(4)
        returns = {f"ASSET{i}": -0.012 for i in range(4)}
        det.update(inputs, positions, make_return_fn(returns))
        assert det.current_regime != RegimeType.HIGH_VOL_CRISIS

    def test_small_portfolio_crisis_above_raised_thresholds(self):
        """Small portfolio triggers crisis when above raised thresholds."""
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.0, btc_24h_return=0.0,
            btc_vol_percentile=50.0, altcoin_breadth=0.50,
        )

        # 4 positions, all negative with loss > 1.5%
        positions = make_positions(4)
        returns = {f"ASSET{i}": -0.02 for i in range(4)}
        det.update(inputs, positions, make_return_fn(returns))
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS

    def test_large_portfolio_uses_normal_thresholds(self):
        """Large portfolio (>=6 positions) uses normal ratio=0.80 and loss=1.0%."""
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.0, btc_24h_return=0.0,
            btc_vol_percentile=50.0, altcoin_breadth=0.50,
        )

        # 10 positions, 9/10 negative → ratio 0.9 > 0.80
        # avg_loss = 0.012 > 0.01 → CRISIS with normal thresholds
        positions = make_positions(10)
        returns = {f"ASSET{i}": -0.012 for i in range(9)}
        returns["ASSET9"] = 0.01
        det.update(inputs, positions, make_return_fn(returns))
        assert det.current_regime == RegimeType.HIGH_VOL_CRISIS


# ---------------------------------------------------------------------------
# Cold-start and properties tests
# ---------------------------------------------------------------------------

class TestColdStart:
    def test_default_cold_start_is_mean_revert(self):
        """Default/cold-start state is MEAN_REVERT."""
        det = RegimeDetector(make_config())
        assert det.current_regime == RegimeType.MEAN_REVERT

    def test_cold_start_log(self, caplog):
        """Cold start logs a clear message."""
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.0, btc_24h_return=0.0,
            btc_vol_percentile=50.0, altcoin_breadth=0.50,
        )
        with caplog.at_level(logging.INFO):
            det.update(inputs, no_positions(), make_return_fn({}))

        assert any("COLD_START" in msg for msg in caplog.messages)

    def test_minutes_in_current_regime(self):
        det = RegimeDetector(make_config())
        assert det.minutes_in_current_regime == 0

        inputs = MockRegimeInputs(
            btc_4h_return=0.0, btc_24h_return=0.0,
            btc_vol_percentile=50.0, altcoin_breadth=0.50,
        )
        det.update(inputs, no_positions(), make_return_fn({}))
        assert det.minutes_in_current_regime == 1

        det.update(inputs, no_positions(), make_return_fn({}))
        assert det.minutes_in_current_regime == 2

    def test_current_regime_property(self):
        det = RegimeDetector(make_config())
        assert det.current_regime == RegimeType.MEAN_REVERT
        det._state.confirm_transition(RegimeType.TREND_BULL)
        assert det.current_regime == RegimeType.TREND_BULL


# ---------------------------------------------------------------------------
# Input validation tests
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_nan_inputs_regime_unchanged(self, caplog):
        """Invalid inputs (NaN) → regime unchanged, warning logged."""
        det = RegimeDetector(make_config())
        original_regime = det.current_regime

        inputs = MockRegimeInputs(
            btc_4h_return=float("nan"),
            btc_24h_return=0.01,
            btc_vol_percentile=50.0,
            altcoin_breadth=0.50,
        )
        with caplog.at_level(logging.WARNING):
            det.update(inputs, no_positions(), make_return_fn({}))

        assert det.current_regime == original_regime
        assert any("invalid" in msg.lower() or "VALIDATION" in msg for msg in caplog.messages)

    def test_none_inputs_regime_unchanged(self):
        """None inputs → regime unchanged."""
        det = RegimeDetector(make_config())
        original_regime = det.current_regime

        inputs = MockRegimeInputs(
            btc_4h_return=None,
            btc_24h_return=None,
            btc_vol_percentile=None,
            altcoin_breadth=None,
        )
        det.update(inputs, no_positions(), make_return_fn({}))
        assert det.current_regime == original_regime


# ---------------------------------------------------------------------------
# Zero positions edge case
# ---------------------------------------------------------------------------

class TestZeroPositions:
    def test_zero_positions_no_crash(self):
        """Zero positions → contagion returns (0.0, 0.0), no crash."""
        det = RegimeDetector(make_config())
        inputs = MockRegimeInputs(
            btc_4h_return=0.01, btc_24h_return=0.01,
            btc_vol_percentile=50.0, altcoin_breadth=0.50,
        )
        # Should not crash with empty positions
        det.update(inputs, no_positions(), make_return_fn({}))
        assert det.state.contagion_proxy == 0.0
        assert det.state.avg_loss == 0.0

    def test_zero_positions_contagion_probe(self):
        """ContagionProbe with empty dict returns (0.0, 0.0)."""
        probe = ContagionProbe(return_window=5)
        result = probe.compute({}, lambda a, w: 0.0)
        assert result.contagion_ratio == 0.0
        assert result.avg_loss == 0.0
        assert result.negative_count == 0
        assert result.total_positions == 0
