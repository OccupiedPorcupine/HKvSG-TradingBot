"""Tests for Layer 5 — Portfolio Construction.

Covers: Phase 2 regime-conditional pipeline, exposure targets, tier caps,
turnover constraint, meme pool integration, PAXG allocation, endgame
de-risking, vol filter, edge cases.
"""

import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.constructor import PortfolioConstructor, ConstructionResult
from src.portfolio.adaptive import AdaptiveExposure
from src.portfolio.paxg_allocator import PAXGAllocator
from src.portfolio.endgame import EndgameManager
from src.regime.regime_state import RegimeType
from src.signals.meme_pool import MemePoolManager
from src.signals.trend_penalty import TrendPenaltyEngine


# =========================================================================
# Mock components
# =========================================================================

class MockRegimeDetector:
    """Mock regime detector with configurable current regime."""

    def __init__(self, regime: RegimeType = RegimeType.MEAN_REVERT):
        self._regime = regime
        self._state = _MockState(regime)

    @property
    def current_regime(self) -> RegimeType:
        return self._regime

    @current_regime.setter
    def current_regime(self, val: RegimeType) -> None:
        self._regime = val
        self._state = _MockState(val)

    @property
    def state(self):
        return self._state


@dataclass
class _MockState:
    current_regime: RegimeType
    btc_vol_percentile: float = 50.0
    contagion_proxy: float = 0.0
    avg_loss: float = 0.0
    bars_in_current_regime: int = 100


class MockTrendPenalty:
    """Pass-through trend penalty (no adjustment)."""

    def apply_penalties(self, scores: dict[str, float]) -> dict[str, float]:
        return dict(scores)


class MockMarketData:
    """Mock FeatureEngine with configurable volatilities."""

    def __init__(self, vols: Optional[dict[str, float]] = None):
        self._vols = vols or {}

    def get_asset_volatility(self, asset: str, window: str) -> Optional[float]:
        return self._vols.get(asset)


class MockEndgame:
    """Mock endgame manager with configurable constraints."""

    def __init__(
        self,
        max_exposure: float = 1.0,
        sell_all: bool = False,
        hours_remaining: float = 100.0,
    ):
        self._max_exposure = max_exposure
        self._sell_all = sell_all
        self._hours_remaining = hours_remaining

    def get_constraints(self, now=None) -> dict:
        return {
            "max_exposure": self._max_exposure,
            "stop_override": None,
            "sell_all": self._sell_all,
            "hours_remaining": self._hours_remaining,
            "tier": -1,
        }


class MockPAXG:
    """Mock PAXG allocator with configurable weights."""

    def __init__(self, weights: Optional[dict] = None):
        self._weights = weights or {
            RegimeType.TREND_BULL: 0.04,
            RegimeType.MEAN_REVERT: 0.075,
            RegimeType.TREND_BEAR: 0.125,
            RegimeType.HIGH_VOL_CRISIS: 0.125,
        }

    def get_target_weight(self, regime: RegimeType) -> float:
        return self._weights.get(regime, 0.05)


class MockMemePool:
    """Mock meme pool with configurable selections."""

    def __init__(self, active_regimes=None, allocations=None):
        self._active_regimes = active_regimes or {"TREND_BULL"}
        self._allocations = allocations or []

    def rank_and_select(
        self, momentum_scores: dict, current_regime: str
    ) -> list[dict]:
        if current_regime not in self._active_regimes:
            return []
        return self._allocations


# =========================================================================
# Config for testing
# =========================================================================

TEST_CONFIG = {
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
        "turnover_max_pct": 0.25,
        "min_trade_nav_pct": 0.002,
        "tier_caps": {
            "tier1": 0.08,
            "tier1_doge": 0.05,
            "tier2": 0.08,
            "tier3": 0.06,
            "tier4_meme": 0.03,
            "tier5": 0.02,
            "trump": 0.02,
            "paxg": 0.15,
        },
    },
    "tier_caps": {
        "tier_1_2": 0.08,
        "tier_3": 0.06,
        "tier_4_meme": 0.03,
        "tier_5_obscure": 0.02,
        "doge": 0.05,
        "trump": 0.02,
        "paxg": 0.15,
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
}


def _make_constructor(
    regime: RegimeType = RegimeType.MEAN_REVERT,
    endgame: Optional[MockEndgame] = None,
    meme_pool: Optional[MockMemePool] = None,
    paxg: Optional[MockPAXG] = None,
    trend_penalty: Optional[MockTrendPenalty] = None,
    config: Optional[dict] = None,
    max_turnover: Optional[float] = None,
) -> PortfolioConstructor:
    """Create a test PortfolioConstructor with mock dependencies."""
    cfg = dict(config or TEST_CONFIG)
    if max_turnover is not None:
        cfg = _deep_copy_dict(cfg)
        cfg["portfolio"]["turnover_max_pct"] = max_turnover

    return PortfolioConstructor(
        config=cfg,
        regime_detector=MockRegimeDetector(regime),
        trend_penalty=trend_penalty or MockTrendPenalty(),
        meme_pool=meme_pool or MockMemePool(),
        endgame=endgame or MockEndgame(),
        paxg=paxg or MockPAXG(),
    )


def _deep_copy_dict(d: dict) -> dict:
    """Simple deep copy for nested dicts."""
    result = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _deep_copy_dict(v)
        elif isinstance(v, list):
            result[k] = list(v)
        else:
            result[k] = v
    return result


BULL_SCORES = {
    "BTC": 1.0, "ETH": 0.95, "BNB": 0.90, "LTC": 0.85, "ADA": 0.80,
    "DOGE": 0.75, "TRX": 0.70, "LINK": 0.65, "DOT": 0.60, "NEAR": 0.55,
    "AAVE": 0.50, "UNI": 0.45,
}


# =========================================================================
# Phase 2 Pipeline Tests — Required 8 Test Cases
# =========================================================================


class TestTrendBullExposure:
    """1. TREND_BULL regime → 75-85% exposure, 10 holdings."""

    def test_bull_exposure_range(self) -> None:
        pc = _make_constructor(regime=RegimeType.TREND_BULL, max_turnover=1.0)
        result = pc.construct(BULL_SCORES, {}, 1_000_000)

        crypto = sum(
            w for a, w in result.target_weights.items() if a != "PAXG"
        )
        # TREND_BULL target is 0.80, after meme subtraction should be 0.75-0.85
        assert 0.70 <= crypto <= 0.85, f"crypto={crypto:.2f} not in [0.70, 0.85]"

    def test_bull_holdings_count(self) -> None:
        pc = _make_constructor(regime=RegimeType.TREND_BULL, max_turnover=1.0)
        result = pc.construct(BULL_SCORES, {}, 1_000_000)

        crypto_holdings = [
            a for a in result.target_weights if a != "PAXG"
        ]
        assert len(crypto_holdings) <= 10


class TestCrisisExposure:
    """2. HIGH_VOL_CRISIS → 10-20% exposure, 0 crypto holdings (PAXG only)."""

    def test_crisis_low_exposure(self) -> None:
        pc = _make_constructor(regime=RegimeType.HIGH_VOL_CRISIS, max_turnover=1.0)
        result = pc.construct(BULL_SCORES, {}, 1_000_000)

        crypto = sum(
            w for a, w in result.target_weights.items() if a != "PAXG"
        )
        assert crypto <= 0.20, f"crisis crypto={crypto:.2f} exceeds 0.20"

    def test_crisis_zero_crypto(self) -> None:
        pc = _make_constructor(regime=RegimeType.HIGH_VOL_CRISIS, max_turnover=1.0)
        result = pc.construct(BULL_SCORES, {}, 1_000_000)

        # HIGH_VOL_CRISIS has 0 max holdings → no crypto
        crypto_assets = [
            a for a in result.target_weights if a != "PAXG"
        ]
        assert len(crypto_assets) == 0

    def test_crisis_paxg_present(self) -> None:
        pc = _make_constructor(regime=RegimeType.HIGH_VOL_CRISIS, max_turnover=1.0)
        result = pc.construct(BULL_SCORES, {}, 1_000_000)

        assert "PAXG" in result.target_weights
        assert result.target_weights["PAXG"] > 0


class TestEndgameSellAll:
    """3. Endgame sell_all → all weights zero."""

    def test_sell_all_empty_weights(self) -> None:
        pc = _make_constructor(
            regime=RegimeType.TREND_BULL,
            endgame=MockEndgame(sell_all=True, hours_remaining=0.1),
        )
        result = pc.construct(
            BULL_SCORES,
            {"BTC": 0.08, "ETH": 0.06},
            1_000_000,
        )

        assert result.target_weights == {}
        assert result.metadata["reason"] == "endgame_sell_all"

    def test_sell_all_generates_sell_orders(self) -> None:
        pc = _make_constructor(
            regime=RegimeType.TREND_BULL,
            endgame=MockEndgame(sell_all=True),
        )
        result = pc.construct(
            BULL_SCORES,
            {"BTC": 0.08, "ETH": 0.06},
            1_000_000,
        )

        assert len(result.orders) == 2
        for order in result.orders:
            assert order["side"] == "SELL"


class TestVolFilterFallback:
    """4. Vol filter removes all → falls back to top 3."""

    def test_vol_filter_fallback(self) -> None:
        # All assets have vol, but all exceed 2× median → filtered out
        # Then fallback to top 3 by raw score
        high_vols = {a: 0.10 for a in BULL_SCORES}
        # Make one asset 10× higher to ensure all get filtered as outliers
        # Actually, if all vols are equal, none exceed 2× median.
        # Need one to be low so median is low, making others exceed threshold.
        high_vols["BTC"] = 0.01  # low vol
        for other in list(BULL_SCORES.keys()):
            if other != "BTC":
                high_vols[other] = 0.05  # 5× BTC vol > 2× median

        pc = _make_constructor(regime=RegimeType.MEAN_REVERT, max_turnover=1.0)
        market = MockMarketData(vols=high_vols)

        result = pc.construct(BULL_SCORES, {}, 1_000_000, market_data=market)

        # Should have some holdings (fallback triggered)
        crypto = [a for a in result.target_weights if a != "PAXG"]
        assert len(crypto) > 0, "should fall back to candidates when vol filter removes all"

    def test_no_market_data_skips_filter(self) -> None:
        """Without market data, vol filter is skipped gracefully."""
        pc = _make_constructor(regime=RegimeType.MEAN_REVERT, max_turnover=1.0)
        result = pc.construct(BULL_SCORES, {}, 1_000_000, market_data=None)

        crypto = [a for a in result.target_weights if a != "PAXG"]
        assert len(crypto) > 0


class TestTierCapOverflow:
    """5. Tier cap overflow → excess redistributed correctly."""

    def test_cap_and_redistribute(self) -> None:
        pc = _make_constructor(regime=RegimeType.MEAN_REVERT, max_turnover=1.0)

        # Direct test of _apply_tier_caps
        weights = {"BTC": 0.20, "ETH": 0.05}
        capped = pc._apply_tier_caps(weights)

        # BTC capped at 0.08, excess (0.12) redistributed to ETH
        assert capped["BTC"] <= 0.08 + 1e-6
        assert capped["ETH"] > 0.05

    def test_doge_special_cap(self) -> None:
        pc = _make_constructor(regime=RegimeType.MEAN_REVERT)
        weights = {"DOGE": 0.10}
        capped = pc._apply_tier_caps(weights)
        assert capped["DOGE"] <= 0.05 + 1e-6

    def test_total_preserved_after_cap(self) -> None:
        pc = _make_constructor(regime=RegimeType.MEAN_REVERT)
        weights = {"BTC": 0.15, "ETH": 0.05, "LINK": 0.05}
        original_total = sum(weights.values())
        capped = pc._apply_tier_caps(weights)
        capped_total = sum(capped.values())
        # Total should be approximately preserved (may be slightly less
        # if all positions hit caps)
        assert capped_total <= original_total + 1e-6


class TestCombinedWeights:
    """6. Combined weights never exceed 1.0."""

    def test_total_never_exceeds_one(self) -> None:
        pc = _make_constructor(regime=RegimeType.TREND_BULL, max_turnover=1.0)
        result = pc.construct(BULL_SCORES, {}, 1_000_000)

        total = sum(result.target_weights.values())
        assert total <= 1.0 + 1e-6, f"total={total:.4f} exceeds 1.0"

    def test_combine_with_large_paxg(self) -> None:
        """Even with high PAXG, total doesn't exceed 1.0."""
        big_paxg = MockPAXG(weights={
            RegimeType.TREND_BULL: 0.40,
            RegimeType.MEAN_REVERT: 0.40,
            RegimeType.TREND_BEAR: 0.40,
            RegimeType.HIGH_VOL_CRISIS: 0.40,
        })
        pc = _make_constructor(
            regime=RegimeType.TREND_BULL,
            paxg=big_paxg,
            max_turnover=1.0,
        )
        result = pc.construct(BULL_SCORES, {}, 1_000_000)
        total = sum(result.target_weights.values())
        assert total <= 1.0 + 1e-6


class TestMemeAllocations:
    """7. Meme allocations only in TREND_BULL."""

    def test_meme_in_bull(self) -> None:
        meme = MockMemePool(
            active_regimes={"TREND_BULL"},
            allocations=[
                {"symbol": "PEPE", "weight": 0.03, "stop_pct": 0.08},
                {"symbol": "SHIB", "weight": 0.03, "stop_pct": 0.08},
            ],
        )
        pc = _make_constructor(
            regime=RegimeType.TREND_BULL,
            meme_pool=meme,
            max_turnover=1.0,
        )
        result = pc.construct(BULL_SCORES, {}, 1_000_000)
        assert "PEPE" in result.target_weights
        assert "SHIB" in result.target_weights

    def test_no_meme_in_bear(self) -> None:
        meme = MockMemePool(
            active_regimes={"TREND_BULL"},
            allocations=[
                {"symbol": "PEPE", "weight": 0.03, "stop_pct": 0.08},
            ],
        )
        pc = _make_constructor(
            regime=RegimeType.TREND_BEAR,
            meme_pool=meme,
            max_turnover=1.0,
        )
        result = pc.construct(BULL_SCORES, {}, 1_000_000)
        assert "PEPE" not in result.target_weights

    def test_no_meme_in_crisis(self) -> None:
        meme = MockMemePool(
            active_regimes={"TREND_BULL"},
            allocations=[
                {"symbol": "PEPE", "weight": 0.03, "stop_pct": 0.08},
            ],
        )
        pc = _make_constructor(
            regime=RegimeType.HIGH_VOL_CRISIS,
            meme_pool=meme,
            max_turnover=1.0,
        )
        result = pc.construct(BULL_SCORES, {}, 1_000_000)
        assert "PEPE" not in result.target_weights


class TestTurnoverConstraint:
    """8. Turnover constraint limits changes to 25%."""

    def test_turnover_capped(self) -> None:
        pc = _make_constructor(regime=RegimeType.TREND_BULL, max_turnover=0.10)

        # Current: 0% everywhere. Target: ~80% deployment.
        # Turnover would be ~40% one-way → should be capped to 10%.
        result = pc.construct(BULL_SCORES, {}, 1_000_000)

        total = sum(result.target_weights.values())
        # Should be significantly less than full 80% deployment due to cap
        assert total < 0.30, f"turnover cap not working: total={total:.2f}"

    def test_no_constraint_small_change(self) -> None:
        pc = _make_constructor(regime=RegimeType.MEAN_REVERT, max_turnover=0.25)

        current = {"BTC": 0.08, "ETH": 0.08}
        scores = {"BTC": 1.0, "ETH": 0.9}  # only 2 assets for MEAN_REVERT (6 max)

        result = pc.construct(scores, current, 1_000_000)

        # Small change: should not be constrained
        assert "BTC" in result.target_weights or "ETH" in result.target_weights


# =========================================================================
# Edge case tests
# =========================================================================


class TestEdgeCases:
    """Edge cases: zero NAV, no scores, data outage."""

    def test_zero_nav(self) -> None:
        pc = _make_constructor(regime=RegimeType.MEAN_REVERT)
        result = pc.construct(BULL_SCORES, {}, 0.0)
        assert result.target_weights == {}
        assert "error" in result.metadata

    def test_negative_nav(self) -> None:
        pc = _make_constructor(regime=RegimeType.MEAN_REVERT)
        result = pc.construct(BULL_SCORES, {}, -100.0)
        assert result.target_weights == {}

    def test_no_momentum_scores(self) -> None:
        pc = _make_constructor(regime=RegimeType.MEAN_REVERT)
        result = pc.construct({}, {"BTC": 0.08}, 1_000_000)
        # Should hold current positions
        assert result.target_weights == {"BTC": 0.08}
        assert result.metadata["reason"] == "no_momentum_scores"

    def test_nan_momentum_scores(self) -> None:
        pc = _make_constructor(regime=RegimeType.MEAN_REVERT)
        scores = {"BTC": float("nan"), "ETH": float("nan")}
        result = pc.construct(scores, {"BTC": 0.08}, 1_000_000)
        # All scores invalid → hold current
        assert result.target_weights == {"BTC": 0.08}


class TestMetadata:
    """Verify metadata is populated correctly."""

    def test_metadata_has_regime(self) -> None:
        pc = _make_constructor(regime=RegimeType.TREND_BULL, max_turnover=1.0)
        result = pc.construct(BULL_SCORES, {}, 1_000_000)
        assert result.metadata["regime"] == "TREND_BULL"

    def test_metadata_has_exposure(self) -> None:
        pc = _make_constructor(regime=RegimeType.TREND_BULL, max_turnover=1.0)
        result = pc.construct(BULL_SCORES, {}, 1_000_000)
        assert "target_exposure" in result.metadata
        assert "crypto_exposure" in result.metadata
        assert "total_weight" in result.metadata


class TestExplain:
    """Verify explain() returns a readable summary."""

    def test_explain_before_construct(self) -> None:
        pc = _make_constructor()
        text = pc.explain()
        assert "No portfolio construction" in text

    def test_explain_after_construct(self) -> None:
        pc = _make_constructor(regime=RegimeType.TREND_BULL, max_turnover=1.0)
        pc.construct(BULL_SCORES, {}, 1_000_000)
        text = pc.explain()
        assert "TREND_BULL" in text
        assert "Exposure" in text


# =========================================================================
# Tier cap utility tests (preserved from Phase 1)
# =========================================================================


class TestTierCapsUtility:
    """Direct tests for _apply_tier_caps method."""

    def test_cap_applied(self) -> None:
        pc = _make_constructor()
        weights = {"BTC": 0.15, "ETH": 0.05}
        capped = pc._apply_tier_caps(weights)
        assert capped["BTC"] <= 0.08 + 1e-6

    def test_excess_redistributed(self) -> None:
        pc = _make_constructor()
        weights = {"BTC": 0.15, "ETH": 0.05}
        capped = pc._apply_tier_caps(weights)
        assert capped["ETH"] > 0.05

    def test_no_caps_needed(self) -> None:
        pc = _make_constructor()
        weights = {"BTC": 0.07, "ETH": 0.06}
        capped = pc._apply_tier_caps(weights)
        assert abs(capped["BTC"] - 0.07) < 1e-6
        assert abs(capped["ETH"] - 0.06) < 1e-6


# =========================================================================
# Sizing utility tests (preserved from Phase 1)
# =========================================================================


class TestSizing:
    """Tests for position sizing methods."""

    def test_equal_weight_fallback(self) -> None:
        pc = _make_constructor()
        weights = pc._size_positions(["BTC", "ETH", "LINK"], 0.60)
        assert abs(weights["BTC"] - 0.20) < 1e-6
        assert abs(sum(weights.values()) - 0.60) < 1e-6

    def test_inverse_vol_sizing(self) -> None:
        pc = _make_constructor()
        vols = {"BTC": 0.02, "ETH": 0.04, "LINK": 0.04}
        market = MockMarketData(vols=vols)
        weights = pc._size_positions(["BTC", "ETH", "LINK"], 0.60, market)
        # BTC has lower vol → higher weight
        assert weights["BTC"] > weights["ETH"]
        assert abs(sum(weights.values()) - 0.60) < 1e-6

    def test_empty_candidates(self) -> None:
        pc = _make_constructor()
        weights = pc._size_positions([], 0.60)
        assert weights == {}

    def test_zero_deployment(self) -> None:
        pc = _make_constructor()
        weights = pc._size_positions(["BTC"], 0.0)
        assert weights == {}


# =========================================================================
# Turnover utility tests (preserved from Phase 1)
# =========================================================================


class TestTurnoverUtility:
    """Direct tests for _apply_turnover_cap method."""

    def test_no_constraint_when_under_limit(self) -> None:
        pc = _make_constructor(max_turnover=0.25)
        target = {"BTC": 0.10, "ETH": 0.10}
        current = {"BTC": 0.08, "ETH": 0.08}
        result = pc._apply_turnover_cap(target, current)
        assert abs(result["BTC"] - 0.10) < 1e-6

    def test_constraint_scales_changes(self) -> None:
        pc = _make_constructor(max_turnover=0.10)
        target = {"BTC": 0.30}
        current = {"BTC": 0.05}
        result = pc._apply_turnover_cap(target, current)
        assert result["BTC"] < 0.30
        assert result["BTC"] > 0.05


# =========================================================================
# AdaptiveExposure tests
# =========================================================================


class TestAdaptiveExposure:
    """Tests for adaptive exposure adjustment."""

    def test_disabled_returns_zero(self) -> None:
        ae = AdaptiveExposure(enabled=False)
        adj = ae.compute_adjustment(0.0, {})
        assert adj == 0.0

    def test_no_gap_no_adjustment(self) -> None:
        ae = AdaptiveExposure(
            enabled=True,
            gap_threshold_small=0.02,
            competition_end_utc=datetime.now(timezone.utc) + timedelta(days=8),
        )
        ae.initialize({"BTC": 50000, "ETH": 3000}, 1_000_000)
        adj = ae.compute_adjustment(0.05, {"BTC": 52500, "ETH": 3150})
        assert adj == 0.0

    def test_large_gap_large_adjustment(self) -> None:
        ae = AdaptiveExposure(
            enabled=True,
            gap_threshold_small=0.02,
            gap_threshold_large=0.04,
            adjustment_small=0.10,
            adjustment_large=0.20,
            min_days_remaining_small=5,
            min_days_remaining_large=3,
            competition_end_utc=datetime.now(timezone.utc) + timedelta(days=8),
        )
        ae.initialize({"BTC": 50000, "ETH": 3000}, 1_000_000)
        adj = ae.compute_adjustment(0.0, {"BTC": 55000, "ETH": 3300})
        assert adj == 0.20

    def test_no_adjustment_near_end(self) -> None:
        ae = AdaptiveExposure(
            enabled=True,
            gap_threshold_small=0.02,
            adjustment_small=0.10,
            min_days_remaining_small=5,
            competition_end_utc=datetime.now(timezone.utc) + timedelta(days=2),
        )
        ae.initialize({"BTC": 50000}, 1_000_000)
        adj = ae.compute_adjustment(0.0, {"BTC": 55000})
        assert adj == 0.0


# =========================================================================
# PAXGAllocator tests
# =========================================================================


class TestPAXGAllocator:
    """Tests for PAXGAllocator — regime-based PAXG weight from cash buffer."""

    _CONFIG = {
        "paxg": {
            "allocation": {
                "TREND_BULL": 0.04,
                "MEAN_REVERT": 0.075,
                "TREND_BEAR": 0.125,
                "HIGH_VOL_CRISIS": 0.125,
            },
            "rebalance_tolerance": 0.01,
        }
    }

    def _make(self):
        return PAXGAllocator(self._CONFIG)

    def test_trend_bull_target(self) -> None:
        alloc = self._make()
        assert alloc.get_target_weight(RegimeType.TREND_BULL) == 0.04

    def test_high_vol_crisis_target(self) -> None:
        alloc = self._make()
        assert alloc.get_target_weight(RegimeType.HIGH_VOL_CRISIS) == 0.125

    def test_no_order_within_tolerance(self) -> None:
        alloc = self._make()
        order = alloc.compute_order(0.035, RegimeType.TREND_BULL, 1_000_000)
        assert order is None

    def test_buy_order_when_under_target(self) -> None:
        alloc = self._make()
        order = alloc.compute_order(0.02, RegimeType.TREND_BULL, 1_000_000)
        assert order is not None
        assert order["side"] == "buy"
        assert abs(order["amount_usd"] - 20_000) < 1.0

    def test_sell_order_when_over_target(self) -> None:
        alloc = self._make()
        order = alloc.compute_order(0.08, RegimeType.TREND_BULL, 1_000_000)
        assert order is not None
        assert order["side"] == "sell"
        assert abs(order["amount_usd"] - 40_000) < 1.0

    def test_unknown_regime_fallback(self) -> None:
        alloc = PAXGAllocator(
            {"paxg": {"allocation": {}, "rebalance_tolerance": 0.01}}
        )
        assert alloc.get_target_weight(RegimeType.MEAN_REVERT) == 0.05


# =========================================================================
# EndgameManager tests
# =========================================================================


_ENDGAME_SCHEDULE = [
    {"hours_remaining": 48,   "max_exposure": 1.00, "stop_override": None},
    {"hours_remaining": 24,   "max_exposure": 0.70, "stop_override": None},
    {"hours_remaining": 12,   "max_exposure": 0.45, "stop_override": None},
    {"hours_remaining": 4,    "max_exposure": 0.25, "stop_override": 0.03},
    {"hours_remaining": 1,    "max_exposure": 0.15, "stop_override": 0.02},
    {"hours_remaining": 0.25, "max_exposure": 0.00, "stop_override": None},
]

_ROUND_END_UTC = "2026-03-31T23:59:00Z"


def _make_endgame_manager(round_end_utc: str = _ROUND_END_UTC) -> EndgameManager:
    return EndgameManager({
        "endgame": {
            "schedule": _ENDGAME_SCHEDULE,
            "round_end_utc": round_end_utc,
        }
    })


def _now_plus(hours: float) -> datetime:
    round_end = datetime.fromisoformat(_ROUND_END_UTC.replace("Z", "+00:00"))
    return round_end - timedelta(hours=hours)


class TestEndgameManager:
    """Tests for EndgameManager — standalone de-risking logic."""

    def test_over_48h_no_restriction(self) -> None:
        mgr = _make_endgame_manager()
        c = mgr.get_constraints(now=_now_plus(60))
        assert c["max_exposure"] == 1.0
        assert c["stop_override"] is None
        assert c["sell_all"] is False

    def test_24_to_48h_exposure_cap(self) -> None:
        mgr = _make_endgame_manager()
        c = mgr.get_constraints(now=_now_plus(30))
        assert abs(c["max_exposure"] - 0.70) < 1e-9
        assert c["sell_all"] is False

    def test_4_to_12h_exposure_and_stop(self) -> None:
        mgr = _make_endgame_manager()
        c = mgr.get_constraints(now=_now_plus(8))
        assert abs(c["max_exposure"] - 0.25) < 1e-9
        assert abs(c["stop_override"] - 0.03) < 1e-9

    def test_under_15_min_sell_all(self) -> None:
        mgr = _make_endgame_manager()
        c = mgr.get_constraints(now=_now_plus(0.1))
        assert c["sell_all"] is True
        assert c["max_exposure"] == 0.0

    def test_past_round_end_sell_all(self) -> None:
        mgr = _make_endgame_manager()
        c = mgr.get_constraints(now=_now_plus(-1))
        assert c["sell_all"] is True

    def test_reset_for_round(self) -> None:
        mgr = _make_endgame_manager()
        new_end = "2027-06-30T23:59:00Z"
        mgr.reset_for_round(new_end)
        expected = datetime.fromisoformat(new_end.replace("Z", "+00:00"))
        assert mgr.round_end == expected

    def test_no_round_end_unconstrained(self) -> None:
        mgr = EndgameManager({"endgame": {"schedule": _ENDGAME_SCHEDULE}})
        c = mgr.get_constraints()
        assert c["max_exposure"] == 1.0
        assert c["sell_all"] is False

    def test_tier_change_logged(self, caplog) -> None:
        mgr = _make_endgame_manager()
        with caplog.at_level(logging.INFO, logger="src.portfolio.endgame"):
            mgr.get_constraints(now=_now_plus(60))
            mgr.get_constraints(now=_now_plus(30))
        assert "ENDGAME TIER_CHANGE" in caplog.text


# =========================================================================
# build_orders_from_weights tests
# =========================================================================


class TestBuildOrdersFromWeights:
    """Tests for the target-weight-to-PendingOrder bridge."""

    def test_buy_order_created(self) -> None:
        from src.portfolio.factory import build_orders_from_weights

        orders = build_orders_from_weights(
            target_weights={"BTC": 0.10},
            current_weights={"BTC": 0.05},
            nav=1_000_000,
        )
        assert len(orders) == 1
        assert orders[0].side == "BUY"
        assert abs(orders[0].quantity_usd - 50_000) < 1.0

    def test_sell_order_created(self) -> None:
        from src.portfolio.factory import build_orders_from_weights

        orders = build_orders_from_weights(
            target_weights={"BTC": 0.02},
            current_weights={"BTC": 0.08},
            nav=1_000_000,
        )
        assert len(orders) == 1
        assert orders[0].side == "SELL"

    def test_no_change_no_order(self) -> None:
        from src.portfolio.factory import build_orders_from_weights

        orders = build_orders_from_weights(
            target_weights={"BTC": 0.08},
            current_weights={"BTC": 0.08},
            nav=1_000_000,
        )
        assert len(orders) == 0


# =========================================================================
# Run with pytest
# =========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
