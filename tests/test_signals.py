"""Tests for Layer 4 — Signal Generation.

Covers:
- Momentum signal ranking and top-N selection
- Top-N varies by regime
- Trend penalty application (Phase 2)
- Meme pool selection (Phase 2 stub)
- Tier 5 pool with activation threshold (Phase 2 stub)
- ML overlay stub returns 1.0
- SignalOutput structure
- Edge cases (empty scores, ties, single asset)
"""

import sys
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.regime.regime_state import RegimeState, RegimeType
from src.signals.momentum import MomentumSignal
from src.signals.meme_pool import MemePoolManager
from src.signals.tier5_pool import Tier5PoolSignal
from src.signals.ml_overlay import MLOverlay
from src.signals.signal_output import SignalOutput


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(**overrides) -> dict:
    """Create a minimal test config for signals."""
    config = {
        "signals": {
            "top_n_selections": {
                "trend_bull": 10,
                "mean_revert": 6,
                "trend_bear": 4,
                "crisis": 0,
            },
            "trend_penalty_magnitude": -0.30,
            "meme_pool": {
                "allocation_pct": 0.03,
                "max_selections": 2,
                "trailing_stop_pct": 0.08,
                "active_regimes": ["TREND_BULL"],
            },
            "tier5_pool": {
                "activation_4h_return_threshold": 0.10,
                "activation_rank_top_n": 3,
                "allocation_pct_min": 0.01,
                "allocation_pct_max": 0.02,
                "max_selections": 2,
                "trailing_stop_pct": 0.08,
                "active_regimes": ["TREND_BULL"],
            },
            "ml": {
                "enabled": False,
                "multiplier_high": 1.3,
                "multiplier_low": 0.5,
                "multiplier_default": 1.0,
                "probability_threshold_high": 0.65,
                "probability_threshold_low": 0.45,
                "ic_halt_threshold": 0.02,
                "ic_resume_threshold": 0.04,
                "ic_resume_window_hr": 12,
            },
        },
    }
    for key, val in overrides.items():
        keys = key.split(".")
        d = config
        for k in keys[:-1]:
            d = d.setdefault(k, {})
        d[keys[-1]] = val
    return config


TIER_1_3 = {
    "BTC", "ETH", "BNB", "LTC", "ADA", "DOGE", "TRX",  # T1
    "LINK", "DOT", "NEAR", "TON", "SUI", "APT", "ARB",  # T2 (partial)
    "AAVE", "UNI", "CRV", "PENDLE", "SOL", "HBAR",       # T3 (partial)
}

TIER_4 = {"SHIB", "PEPE", "FLOKI", "WIF", "BONK", "PUMP", "PENGU"}
TIER_5 = {"SOMI", "AVNT", "MIRA", "EDEN", "FORM"}


def make_momentum_scores(n: int = 20) -> dict[str, float]:
    """Generate synthetic momentum scores for testing."""
    all_assets = list(TIER_1_3) + list(TIER_4) + list(TIER_5)
    return {
        asset: round(0.9 - i * 0.03, 4)
        for i, asset in enumerate(all_assets[:n])
    }


def bull_regime() -> RegimeState:
    return RegimeState(current_regime=RegimeType.TREND_BULL)


def bear_regime() -> RegimeState:
    return RegimeState(current_regime=RegimeType.TREND_BEAR)


def crisis_regime() -> RegimeState:
    return RegimeState(current_regime=RegimeType.HIGH_VOL_CRISIS)


def mean_revert_regime() -> RegimeState:
    return RegimeState(current_regime=RegimeType.MEAN_REVERT)


# ---------------------------------------------------------------------------
# MomentumSignal tests
# ---------------------------------------------------------------------------

class TestMomentumSignal:
    def test_selects_top_n_in_bull(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        scores = make_momentum_scores(20)
        result = signal.generate(scores, bull_regime(), get_vol_fn=None)
        assert len(result) == 10

    def test_selects_fewer_in_bear(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        scores = make_momentum_scores(20)
        result = signal.generate(scores, bear_regime(), get_vol_fn=None)
        assert len(result) == 4

    def test_selects_fewer_in_mean_revert(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        scores = make_momentum_scores(20)
        result = signal.generate(scores, mean_revert_regime(), get_vol_fn=None)
        assert len(result) == 6

    def test_crisis_selects_nothing(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        scores = make_momentum_scores(20)
        result = signal.generate(scores, crisis_regime(), get_vol_fn=None)
        assert len(result) == 0

    def test_only_tier_1_3_eligible(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        # Give high scores to Tier 4/5 assets
        scores = {"SHIB": 0.99, "PEPE": 0.98, "BTC": 0.50, "ETH": 0.49}
        result = signal.generate(scores, bull_regime(), get_vol_fn=None)
        # Only BTC and ETH should be selected (Tier 1-3)
        assert "SHIB" not in result
        assert "PEPE" not in result
        assert "BTC" in result
        assert "ETH" in result

    def test_highest_scores_selected(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        scores = {
            "BTC": 0.90, "ETH": 0.85, "BNB": 0.80,
            "LTC": 0.20, "ADA": 0.10,
        }
        config = make_config()
        config["signals"]["top_n_selections"]["trend_bull"] = 3
        signal = MomentumSignal(config, TIER_1_3)
        result = signal.generate(scores, bull_regime(), get_vol_fn=None)
        assert set(result.keys()) == {"BTC", "ETH", "BNB"}

    def test_volatility_exclusion_filter(self):
        config = make_config()
        config["signals"]["vol_exclusion_multiplier"] = 2.0
        signal = MomentumSignal(config, TIER_1_3)
        
        scores = {"BTC": 0.90, "ETH": 0.80, "BNB": 0.70, "SOL": 0.60}
        # BTC and ETH normal vol (0.02), BNB high vol (0.05), SOL extreme vol (0.10)
        # median = 0.035? no, let's pick values carefully.
        # vols: [0.02, 0.02, 0.05, 0.10] -> median = (0.02+0.05)/2 = 0.035
        # threshold = 0.035 * 2 = 0.07. SOL (0.10) should be excluded.
        vols = {"BTC": 0.02, "ETH": 0.02, "BNB": 0.05, "SOL": 0.10}
        
        result = signal.generate(
            scores, bull_regime(), 
            get_vol_fn=lambda a, w: vols.get(a)
        )
        assert "SOL" not in result
        assert "BTC" in result
        assert "ETH" in result
        assert "BNB" in result

    def test_hysteresis_n_plus_3(self):
        config = make_config()
        config["signals"]["top_n_selections"]["trend_bull"] = 3
        config["signals"]["hysteresis_buffer"] = 2  # N+2 for simpler test
        signal = MomentumSignal(config, TIER_1_3)
        
        # Initial rebalance: Top 3
        scores1 = {"BTC": 0.9, "ETH": 0.8, "BNB": 0.7, "SOL": 0.6, "ADA": 0.5, "DOT": 0.4}
        result1 = signal.generate(scores1, bull_regime(), get_vol_fn=None)
        assert set(result1.keys()) == {"BTC", "ETH", "BNB"}
        
        # Second rebalance: BNB drops to rank 5 (N+2)
        # Ranked: SOL(0.85), BTC(0.8), ETH(0.75), ADA(0.7), BNB(0.65), DOT(0.4)
        # Ranks (0-indexed): SOL:0, BTC:1, ETH:2, ADA:3, BNB:4, DOT:5
        # N=3, buffer=2 -> exit_limit = 5. BNB at rank 4 is < 5, so it stays.
        scores2 = {"SOL": 0.85, "BTC": 0.8, "ETH": 0.75, "ADA": 0.7, "BNB": 0.65, "DOT": 0.4}
        result2 = signal.generate(scores2, bull_regime(), get_vol_fn=None)
        
        # Should have SOL (top N), BTC (top N), ETH (top N)
        # Wait, if we prioritize entries, then SOL, BTC, ETH fill the 3 slots.
        # But if we also keep BNB due to hysteresis...
        # My implementation: "If we already have N assets, only add if it's a prev asset."
        # Wait, let's re-read my logic:
        # if rank < n or (is_prev and rank < exit_rank_limit):
        #     if len(final_selections) < n: final_selections[asset] = score
        #     elif is_prev: final_selections[asset] = score
        
        # SOL(0): rank < 3 -> add (len=1)
        # BTC(1): rank < 3 -> add (len=2)
        # ETH(2): rank < 3 -> add (len=3)
        # ADA(3): rank >= 3 and not prev -> skip
        # BNB(4): rank >= 3 but is_prev and rank < 5 -> add (len=4)
        assert set(result2.keys()) == {"SOL", "BTC", "ETH", "BNB"}

    def test_sentiment_overlay(self):
        config = make_config()
        config["signals"]["sentiment_overlay_enabled"] = True
        config["signals"]["sentiment_crowded_penalty"] = -0.2
        config["signals"]["sentiment_reversal_bonus"] = 0.1
        signal = MomentumSignal(config, TIER_1_3)
        
        scores = {"BTC": 0.5, "ETH": 0.5, "BNB": 0.5}
        # BTC crowded (sent=0.95) -> penalty
        # ETH oversold (sent=0.05) -> bonus
        # BNB neutral (sent=0.5) -> no change
        sentiment = {"BTC": 0.95, "ETH": 0.05, "BNB": 0.5}
        
        result = signal.generate(
            scores, bull_regime(),
            sentiment_scores=sentiment
        )
        assert result["BTC"] == pytest.approx(0.5 - 0.2)
        assert result["ETH"] == pytest.approx(0.5 + 0.1)
        assert result["BNB"] == 0.5

    def test_empty_scores(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        result = signal.generate({}, bull_regime(), get_vol_fn=None)
        assert result == {}

    def test_fewer_assets_than_top_n(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        scores = {"BTC": 0.90, "ETH": 0.85}
        result = signal.generate(scores, bull_regime())
        # Only 2 eligible, top-N is 10 — returns all 2
        assert len(result) == 2

    def test_scores_preserved_in_output(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        scores = {"BTC": 0.90, "ETH": 0.85, "BNB": 0.80}
        result = signal.generate(scores, bull_regime())
        assert result["BTC"] == 0.90
        assert result["ETH"] == 0.85

    def test_no_negative_scores_generated(self):
        """Bottom-ranked assets get excluded, never shorted."""
        signal = MomentumSignal(make_config(), TIER_1_3)
        scores = {
            "BTC": 0.90, "ETH": 0.85, "BNB": 0.80,
            "LTC": 0.01, "ADA": 0.005,
        }
        config = make_config()
        config["signals"]["top_n_selections"]["trend_bull"] = 3
        signal = MomentumSignal(config, TIER_1_3)
        result = signal.generate(scores, bull_regime())
        # Bottom assets are simply absent
        assert "LTC" not in result
        assert "ADA" not in result
        # No negative weights anywhere
        assert all(v >= 0 for v in result.values())


class TestTrendPenalty:
    def test_penalty_disabled_by_default(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        scores = {"BTC": 0.90, "ETH": 0.85}
        emas = {"BTC": (100.0, 110.0), "ETH": (200.0, 190.0)}
        result = signal.generate(
            scores, bull_regime(),
            get_ema_fn=lambda a: emas.get(a, (None, None)),
            get_vol_fn=None
        )
        # Penalty not enabled — scores unchanged
        assert result["BTC"] == 0.90

    def test_penalty_reduces_score_when_enabled(self):
        signal = MomentumSignal(make_config(), TIER_1_3)
        signal.enable_trend_penalty()
        scores = {"BTC": 0.90, "ETH": 0.85, "BNB": 0.80}
        # BTC: EMA60 < EMA240 (downtrend) → penalty
        # ETH: EMA60 > EMA240 (uptrend) → no penalty
        emas = {
            "BTC": (100.0, 110.0),  # 60 < 240 → penalty
            "ETH": (200.0, 190.0),  # 60 > 240 → no penalty
            "BNB": (50.0, 50.0),    # equal → no penalty
        }
        result = signal.generate(
            scores, bull_regime(),
            get_ema_fn=lambda a: emas.get(a, (None, None)),
            get_vol_fn=None
        )
        assert result["BTC"] == pytest.approx(0.90 + (-0.30))
        assert result["ETH"] == 0.85
        assert result["BNB"] == 0.80

    def test_penalty_can_change_ranking(self):
        config = make_config()
        config["signals"]["top_n_selections"]["trend_bull"] = 2
        signal = MomentumSignal(config, TIER_1_3)
        signal.enable_trend_penalty()

        # BTC has highest raw score but downtrend penalty drops it
        scores = {"BTC": 0.70, "ETH": 0.65, "BNB": 0.60}
        emas = {
            "BTC": (100.0, 110.0),  # penalty: 0.70 - 0.30 = 0.40
            "ETH": (200.0, 190.0),  # no penalty: 0.65
            "BNB": (50.0, 45.0),    # no penalty: 0.60
        }
        result = signal.generate(
            scores, bull_regime(),
            get_ema_fn=lambda a: emas.get(a, (None, None)),
            get_vol_fn=None
        )
        # ETH(0.65) and BNB(0.60) should beat BTC(0.40)
        assert "ETH" in result
        assert "BNB" in result
        assert "BTC" not in result

    def test_penalty_with_none_emas(self):
        """Assets with missing EMA data should not receive penalty."""
        signal = MomentumSignal(make_config(), TIER_1_3)
        signal.enable_trend_penalty()
        scores = {"BTC": 0.90, "ETH": 0.85}
        emas = {"BTC": (None, None), "ETH": (200.0, 190.0)}
        result = signal.generate(
            scores, bull_regime(),
            get_ema_fn=lambda a: emas.get(a, (None, None)),
            get_vol_fn=None
        )
        assert result["BTC"] == 0.90  # No penalty (EMA not available)


# ---------------------------------------------------------------------------
# MemePoolManager tests
# ---------------------------------------------------------------------------

def _make_meme_config(enabled=True):
    """Create a config dict for MemePoolManager."""
    return {
        "meme_pool": {
            "enabled": enabled,
            "symbols": list(TIER_4),
            "max_allocation_per_coin": 0.03,
            "top_n": 2,
            "active_regimes": ["TREND_BULL"],
            "trailing_stop_pct": 0.08,
        }
    }


class TestMemePool:
    def test_disabled_returns_empty(self):
        mgr = MemePoolManager(_make_meme_config(enabled=False))
        scores = {"SHIB": 0.90, "PEPE": 0.85}
        result = mgr.rank_and_select(scores, "TREND_BULL")
        assert result == []

    def test_enabled_selects_top_2(self):
        mgr = MemePoolManager(_make_meme_config())
        scores = {
            "SHIB": 0.90, "PEPE": 0.85, "FLOKI": 0.80,
            "WIF": 0.75, "BONK": 0.70,
        }
        result = mgr.rank_and_select(scores, "TREND_BULL")
        assert len(result) == 2
        symbols = {a["symbol"] for a in result}
        assert "SHIB" in symbols
        assert "PEPE" in symbols

    def test_empty_in_bear(self):
        mgr = MemePoolManager(_make_meme_config())
        scores = {"SHIB": 0.90, "PEPE": 0.85}
        result = mgr.rank_and_select(scores, "TREND_BEAR")
        assert result == []

    def test_empty_in_crisis(self):
        mgr = MemePoolManager(_make_meme_config())
        scores = {"SHIB": 0.90}
        result = mgr.rank_and_select(scores, "HIGH_VOL_CRISIS")
        assert result == []

    def test_only_meme_symbols_eligible(self):
        mgr = MemePoolManager(_make_meme_config())
        scores = {"BTC": 0.99, "SHIB": 0.50}
        result = mgr.rank_and_select(scores, "TREND_BULL")
        symbols = {a["symbol"] for a in result}
        assert "BTC" not in symbols
        assert "SHIB" in symbols


# ---------------------------------------------------------------------------
# Tier5PoolSignal tests
# ---------------------------------------------------------------------------

class TestTier5Pool:
    def test_disabled_in_phase1(self):
        signal = Tier5PoolSignal(make_config(), TIER_5)
        scores = {"SOMI": 0.90, "AVNT": 0.85}
        result = signal.generate(
            scores, bull_regime(),
            get_return_fn=lambda a, w: 0.15,
        )
        assert result == {}

    def test_enabled_with_high_returns(self):
        signal = Tier5PoolSignal(make_config(), TIER_5)
        signal.enable()
        scores = {"SOMI": 0.90, "AVNT": 0.85, "MIRA": 0.80}
        returns = {"SOMI": 0.15, "AVNT": 0.12, "MIRA": 0.05}
        result = signal.generate(
            scores, bull_regime(),
            get_return_fn=lambda a, w: returns.get(a, 0.0),
        )
        # SOMI and AVNT pass threshold (>10%), MIRA doesn't
        assert "SOMI" in result
        assert "AVNT" in result
        assert "MIRA" not in result

    def test_threshold_filter(self):
        signal = Tier5PoolSignal(make_config(), TIER_5)
        signal.enable()
        scores = {"SOMI": 0.90, "AVNT": 0.85}
        # Both below threshold
        returns = {"SOMI": 0.05, "AVNT": 0.03}
        result = signal.generate(
            scores, bull_regime(),
            get_return_fn=lambda a, w: returns.get(a, 0.0),
        )
        assert result == {}

    def test_empty_in_bear(self):
        signal = Tier5PoolSignal(make_config(), TIER_5)
        signal.enable()
        scores = {"SOMI": 0.90}
        result = signal.generate(
            scores, bear_regime(),
            get_return_fn=lambda a, w: 0.20,
        )
        assert result == {}

    def test_rank_limit(self):
        """Only top 3 of Tier 5 ranking are eligible."""
        signal = Tier5PoolSignal(make_config(), TIER_5)
        signal.enable()
        scores = {
            "SOMI": 0.90, "AVNT": 0.85, "MIRA": 0.80,
            "EDEN": 0.75, "FORM": 0.70,
        }
        # All have high returns, but only top 3 ranked are eligible
        result = signal.generate(
            scores, bull_regime(),
            get_return_fn=lambda a, w: 0.15,
        )
        assert len(result) <= 2
        assert "EDEN" not in result
        assert "FORM" not in result


# ---------------------------------------------------------------------------
# MLOverlay tests
# ---------------------------------------------------------------------------

class TestMLOverlay:
    def test_phase1_returns_all_ones(self):
        overlay = MLOverlay(make_config())
        result = overlay.get_multipliers({"BTC", "ETH", "SOL"})
        assert all(v == 1.0 for v in result.values())

    def test_paxg_always_1(self):
        config = make_config()
        config["signals"]["ml"]["enabled"] = True
        overlay = MLOverlay(config)
        overlay.update_predictions({"PAXG": 0.99, "BTC": 0.70})
        result = overlay.get_multipliers({"PAXG", "BTC"})
        assert result["PAXG"] == 1.0
        assert result["BTC"] == 1.3  # P > 0.65

    def test_high_probability_gives_1_3(self):
        config = make_config()
        config["signals"]["ml"]["enabled"] = True
        overlay = MLOverlay(config)
        overlay.update_predictions({"BTC": 0.70})
        result = overlay.get_multipliers({"BTC"})
        assert result["BTC"] == 1.3

    def test_low_probability_gives_0_5(self):
        config = make_config()
        config["signals"]["ml"]["enabled"] = True
        overlay = MLOverlay(config)
        overlay.update_predictions({"BTC": 0.40})
        result = overlay.get_multipliers({"BTC"})
        assert result["BTC"] == 0.5

    def test_mid_probability_gives_1_0(self):
        config = make_config()
        config["signals"]["ml"]["enabled"] = True
        overlay = MLOverlay(config)
        overlay.update_predictions({"BTC": 0.55})
        result = overlay.get_multipliers({"BTC"})
        assert result["BTC"] == 1.0

    def test_ic_halt(self):
        config = make_config()
        config["signals"]["ml"]["enabled"] = True
        overlay = MLOverlay(config)
        overlay.update_predictions({"BTC": 0.70})
        overlay.check_ic(0.01)  # Below halt threshold
        assert overlay.is_halted
        result = overlay.get_multipliers({"BTC"})
        assert result["BTC"] == 1.0  # Halted → default


# ---------------------------------------------------------------------------
# SignalOutput tests
# ---------------------------------------------------------------------------

class TestSignalOutput:
    def test_default_empty(self):
        output = SignalOutput()
        assert output.main_pool_selections == {}
        assert output.meme_pool_selections == {}
        assert output.tier5_pool_selections == {}
        assert len(output.all_selected_assets) == 0

    def test_all_selected_assets(self):
        output = SignalOutput(
            main_pool_selections={"BTC": 0.9, "ETH": 0.8},
            meme_pool_selections={"SHIB": 0.7},
            tier5_pool_selections={"SOMI": 0.6},
        )
        assert output.all_selected_assets == {"BTC", "ETH", "SHIB", "SOMI"}

    def test_to_log_dict(self):
        output = SignalOutput(
            main_pool_selections={"BTC": 0.9},
            regime=RegimeState(current_regime=RegimeType.TREND_BULL),
        )
        d = output.to_log_dict()
        assert d["main_pool"]["BTC"] == 0.9
        assert d["regime"]["current_regime"] == "TREND_BULL"
        assert d["total_selected"] == 1
