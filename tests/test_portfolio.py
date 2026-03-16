"""Tests for Layer 5 — Portfolio Construction.

Covers: deployment targets, PAXG allocation, equal-weight sizing,
vol-adjusted sizing, tier caps, turnover constraint, minimum trade
threshold, end-game de-risking, adaptive exposure.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio.constructor import PortfolioConstructor
from src.portfolio.adaptive import AdaptiveExposure
from src.portfolio.beta_monitor import BetaMonitor
from src.risk.risk_event import EventType


# =========================================================================
# Helpers
# =========================================================================

REGIME_TARGETS = {
    "trend_bull_low_vol": 0.80,
    "trend_bull_high_vol": 0.65,
    "mean_revert": 0.55,
    "trend_bear": 0.35,
    "crisis": 0.15,
}

TIER_CAPS = {"DOGE": 0.05, "TRUMP": 0.02, "PAXG": 0.15}

ASSET_TIER_MAP = {
    "BTC": "tier_1_2", "ETH": "tier_1_2", "BNB": "tier_1_2",
    "LTC": "tier_1_2", "ADA": "tier_1_2", "DOGE": "tier_1_2",
    "TRX": "tier_1_2", "LINK": "tier_1_2", "DOT": "tier_1_2",
    "NEAR": "tier_1_2",
    "AAVE": "tier_3", "UNI": "tier_3", "CRV": "tier_3",
    "SHIB": "tier_4_meme", "PEPE": "tier_4_meme",
    "SOMI": "tier_5_obscure", "AVNT": "tier_5_obscure",
    "PAXG": "special", "TRUMP": "special",
}

TIER_CAP_DEFAULTS = {
    "tier_1_2": 0.08,
    "tier_3": 0.06,
    "tier_4_meme": 0.03,
    "tier_5_obscure": 0.02,
}

PAXG_ALLOCATION = {
    "trend_bull": 0.05,
    "mean_revert": 0.10,
    "trend_bear": 0.12,
    "crisis": 0.15,
    "hard_cap": 0.15,
}


def _make_constructor(**kwargs) -> PortfolioConstructor:
    defaults = dict(
        regime_targets=REGIME_TARGETS,
        tier_caps=TIER_CAPS,
        asset_tier_map=ASSET_TIER_MAP,
        tier_cap_defaults=TIER_CAP_DEFAULTS,
        paxg_allocation=PAXG_ALLOCATION,
        max_crypto_exposure=0.90,
        max_turnover=0.25,
        min_trade_threshold=0.002,
    )
    defaults.update(kwargs)
    return PortfolioConstructor(**defaults)


# =========================================================================
# Deployment target tests
# =========================================================================


class TestDeploymentTarget:
    """Tests for Step 1 — regime-conditional deployment."""

    def test_trend_bull_low_vol(self) -> None:
        c = _make_constructor()
        assert c.get_deployment_target("TREND_BULL", 50.0) == 0.80

    def test_trend_bull_high_vol(self) -> None:
        c = _make_constructor()
        assert c.get_deployment_target("TREND_BULL", 75.0) == 0.65

    def test_trend_bull_boundary(self) -> None:
        """At exactly the threshold, should use high_vol target."""
        c = _make_constructor(btc_vol_high_vol_threshold=70.0)
        assert c.get_deployment_target("TREND_BULL", 70.0) == 0.80  # not above
        assert c.get_deployment_target("TREND_BULL", 71.0) == 0.65  # above

    def test_mean_revert(self) -> None:
        c = _make_constructor()
        assert c.get_deployment_target("MEAN_REVERT") == 0.55

    def test_trend_bear(self) -> None:
        c = _make_constructor()
        assert c.get_deployment_target("TREND_BEAR") == 0.35

    def test_crisis(self) -> None:
        c = _make_constructor()
        assert c.get_deployment_target("HIGH_VOL_CRISIS") == 0.15

    def test_unknown_regime_defaults(self) -> None:
        c = _make_constructor()
        target = c.get_deployment_target("UNKNOWN_STATE")
        assert target == 0.55  # defaults to mean_revert


# =========================================================================
# PAXG allocation tests
# =========================================================================


class TestPAXGAllocation:
    """Tests for Step 2 — PAXG from cash buffer."""

    def test_paxg_trend_bull(self) -> None:
        c = _make_constructor()
        assert c.get_paxg_weight("TREND_BULL") == 0.05

    def test_paxg_crisis(self) -> None:
        c = _make_constructor()
        assert c.get_paxg_weight("HIGH_VOL_CRISIS") == 0.15

    def test_paxg_hard_cap(self) -> None:
        """PAXG never exceeds 15% even if regime config is higher."""
        paxg_alloc = dict(PAXG_ALLOCATION)
        paxg_alloc["crisis"] = 0.20
        c = _make_constructor(paxg_allocation=paxg_alloc)
        assert c.get_paxg_weight("HIGH_VOL_CRISIS") == 0.15


# =========================================================================
# Equal-weight sizing tests
# =========================================================================


class TestEqualWeights:
    """Tests for Step 3a — equal-weight computation."""

    def test_basic_equal_weight(self) -> None:
        c = _make_constructor()
        weights = c.compute_equal_weights(["BTC", "ETH", "LINK"], 0.60)
        assert abs(weights["BTC"] - 0.20) < 1e-6
        assert abs(sum(weights.values()) - 0.60) < 1e-6

    def test_empty_selection(self) -> None:
        c = _make_constructor()
        weights = c.compute_equal_weights([], 0.60)
        assert weights == {}

    def test_single_asset(self) -> None:
        c = _make_constructor()
        weights = c.compute_equal_weights(["BTC"], 0.60)
        assert abs(weights["BTC"] - 0.60) < 1e-6


# =========================================================================
# Vol-adjusted sizing tests
# =========================================================================


class TestVolAdjustedWeights:
    """Tests for Step 3b — volatility-adjusted weights."""

    def test_lower_vol_gets_higher_weight(self) -> None:
        c = _make_constructor()
        vols = {"BTC": 0.02, "ETH": 0.04, "LINK": 0.04}
        weights = c.compute_vol_adjusted_weights(
            ["BTC", "ETH", "LINK"], 0.60, vols
        )
        # BTC has half the vol → should get roughly double weight
        assert weights["BTC"] > weights["ETH"]
        assert abs(sum(weights.values()) - 0.60) < 1e-6

    def test_equal_vol_equals_equal_weight(self) -> None:
        c = _make_constructor()
        vols = {"BTC": 0.03, "ETH": 0.03, "LINK": 0.03}
        weights = c.compute_vol_adjusted_weights(
            ["BTC", "ETH", "LINK"], 0.60, vols
        )
        for w in weights.values():
            assert abs(w - 0.20) < 1e-6

    def test_zero_vol_floored(self) -> None:
        """Zero vol should not cause division by zero."""
        c = _make_constructor()
        vols = {"BTC": 0.0, "ETH": 0.03}
        weights = c.compute_vol_adjusted_weights(["BTC", "ETH"], 0.60, vols)
        assert sum(weights.values()) > 0


# =========================================================================
# Tier caps tests
# =========================================================================


class TestTierCaps:
    """Tests for Step 3c — tier cap application."""

    def test_cap_applied(self) -> None:
        c = _make_constructor()
        weights = {"BTC": 0.15, "ETH": 0.05}
        capped = c.apply_tier_caps(weights)
        assert capped["BTC"] <= 0.08 + 1e-6

    def test_excess_redistributed(self) -> None:
        c = _make_constructor()
        weights = {"BTC": 0.15, "ETH": 0.05}
        capped = c.apply_tier_caps(weights)
        # BTC excess (0.07) goes to ETH
        assert capped["ETH"] > 0.05

    def test_doge_special_cap(self) -> None:
        c = _make_constructor()
        weights = {"DOGE": 0.08}
        capped = c.apply_tier_caps(weights)
        assert capped["DOGE"] <= 0.05 + 1e-6

    def test_no_caps_needed(self) -> None:
        c = _make_constructor()
        weights = {"BTC": 0.07, "ETH": 0.06}
        capped = c.apply_tier_caps(weights)
        assert abs(capped["BTC"] - 0.07) < 1e-6
        assert abs(capped["ETH"] - 0.06) < 1e-6


# =========================================================================
# Turnover constraint tests
# =========================================================================


class TestTurnoverConstraint:
    """Tests for Step 4 — turnover constraint."""

    def test_no_constraint_when_under_limit(self) -> None:
        c = _make_constructor(max_turnover=0.25)
        target = {"BTC": 0.10, "ETH": 0.10}
        current = {"BTC": 0.08, "ETH": 0.08}
        result = c.apply_turnover_constraint(target, current)
        # Turnover = (|0.02| + |0.02|) / 2 = 0.02 < 0.25
        assert abs(result["BTC"] - 0.10) < 1e-6

    def test_constraint_scales_changes(self) -> None:
        c = _make_constructor(max_turnover=0.10)
        target = {"BTC": 0.30}
        current = {"BTC": 0.05}
        result = c.apply_turnover_constraint(target, current)
        # Turnover = |0.25| / 2 = 0.125 > 0.10
        # Change should be scaled down
        assert result["BTC"] < 0.30
        assert result["BTC"] > 0.05

    def test_risk_exits_bypass_turnover(self) -> None:
        c = _make_constructor(max_turnover=0.05)
        target = {"BTC": 0.0}  # Full exit
        current = {"BTC": 0.20}
        result = c.apply_turnover_constraint(
            target, current, risk_exits={"BTC"}
        )
        assert result.get("BTC", 0.0) == 0.0  # Exit not constrained


# =========================================================================
# Minimum trade threshold tests
# =========================================================================


class TestMinTradeThreshold:
    """Tests for Step 5 — minimum trade threshold."""

    def test_small_change_suppressed(self) -> None:
        c = _make_constructor(min_trade_threshold=0.002)
        target = {"BTC": 0.0805}
        current = {"BTC": 0.08}
        result = c.apply_min_trade_threshold(target, current)
        assert abs(result["BTC"] - 0.08) < 1e-6  # Suppressed

    def test_large_change_passes(self) -> None:
        c = _make_constructor(min_trade_threshold=0.002)
        target = {"BTC": 0.10}
        current = {"BTC": 0.08}
        result = c.apply_min_trade_threshold(target, current)
        assert abs(result["BTC"] - 0.10) < 1e-6  # Not suppressed

    def test_risk_exit_bypasses_threshold(self) -> None:
        c = _make_constructor(min_trade_threshold=0.002)
        target = {"BTC": 0.0799}
        current = {"BTC": 0.08}
        result = c.apply_min_trade_threshold(
            target, current, risk_exits={"BTC"}
        )
        assert abs(result["BTC"] - 0.0799) < 1e-6  # Not suppressed


# =========================================================================
# End-game de-risking tests
# =========================================================================


class TestEndgame:
    """Tests for Step 1c — end-game de-risking schedule."""

    def _make_endgame_constructor(
        self, hours_remaining: float
    ) -> PortfolioConstructor:
        end_time = datetime.now(timezone.utc) + timedelta(hours=hours_remaining)
        return _make_constructor(
            competition_end_utc=end_time,
            endgame_schedule=[
                {"hours_remaining": 48, "max_exposure": 0.70, "stop_override": None},
                {"hours_remaining": 24, "max_exposure": 0.45, "stop_override": None},
                {"hours_remaining": 12, "max_exposure": 0.25, "stop_override": 0.03},
                {"hours_remaining": 4, "max_exposure": 0.15, "stop_override": 0.02},
                {"hours_remaining": 1, "max_exposure": 0.15, "stop_override": 0.02},
            ],
            final_sell_minutes=15.0,
        )

    def test_no_cap_far_from_end(self) -> None:
        c = self._make_endgame_constructor(hours_remaining=100)
        cap, stop, sell = c.get_endgame_cap()
        assert cap is None
        assert stop is None
        assert not sell

    def test_cap_at_48h(self) -> None:
        c = self._make_endgame_constructor(hours_remaining=40)
        cap, stop, sell = c.get_endgame_cap()
        assert cap == 0.70
        assert stop is None
        assert not sell

    def test_cap_at_12h(self) -> None:
        c = self._make_endgame_constructor(hours_remaining=10)
        cap, stop, sell = c.get_endgame_cap()
        assert cap == 0.25
        assert abs(stop - 0.03) < 1e-6
        assert not sell

    def test_sell_all_at_final_minutes(self) -> None:
        c = self._make_endgame_constructor(hours_remaining=0.2)  # 12 min
        cap, stop, sell = c.get_endgame_cap()
        assert sell

    def test_endgame_overrides_regime(self) -> None:
        """End-game cap always wins over regime target."""
        c = self._make_endgame_constructor(hours_remaining=10)
        # TREND_BULL low vol = 0.80, but endgame = 0.25
        target, events = c.compute(
            regime="TREND_BULL",
            btc_vol_percentile=50.0,
            selected_assets=["BTC", "ETH", "LINK", "NEAR"],
            current_weights={},
            current_nav=1_000_000,
        )
        total = sum(w for a, w in target.items() if a != "PAXG")
        assert total <= 0.25 + 0.01  # small tolerance for rounding


# =========================================================================
# Full compute integration test
# =========================================================================


class TestFullCompute:
    """Integration test for the full compute pipeline."""

    def test_basic_compute_with_paxg(self) -> None:
        """Phase 2+ compute includes PAXG."""
        c = _make_constructor()
        target, events = c.compute(
            regime="TREND_BULL",
            btc_vol_percentile=50.0,
            selected_assets=["BTC", "ETH", "LINK", "NEAR", "DOT"],
            current_weights={},
            current_nav=1_000_000,
        )
        assert len(target) > 0
        assert "PAXG" in target
        total = sum(target.values())
        assert total <= 0.90 + 0.01

    def test_phase1_compute_no_paxg(self) -> None:
        """Phase 1 compute skips PAXG (paxg_allocation=None)."""
        c = _make_constructor(paxg_allocation=None)
        target, events = c.compute(
            regime="TREND_BULL",
            btc_vol_percentile=50.0,
            selected_assets=["BTC", "ETH", "LINK", "NEAR", "DOT"],
            current_weights={},
            current_nav=1_000_000,
        )
        assert "PAXG" not in target

    def test_crisis_minimal_exposure(self) -> None:
        c = _make_constructor()
        target, events = c.compute(
            regime="HIGH_VOL_CRISIS",
            btc_vol_percentile=95.0,
            selected_assets=[],  # crisis selects 0 assets
            current_weights={"BTC": 0.08},
            current_nav=1_000_000,
        )
        # Only PAXG should have weight, BTC should go to 0
        crypto = sum(w for a, w in target.items() if a != "PAXG")
        assert crypto < 0.20

    def test_sell_all_endgame(self) -> None:
        end_time = datetime.now(timezone.utc) + timedelta(minutes=10)
        c = _make_constructor(
            competition_end_utc=end_time,
            endgame_schedule=[],
            final_sell_minutes=15.0,
        )
        target, events = c.compute(
            regime="TREND_BULL",
            btc_vol_percentile=50.0,
            selected_assets=["BTC", "ETH"],
            current_weights={"BTC": 0.08, "ETH": 0.08},
            current_nav=1_000_000,
        )
        assert target == {}  # Everything sold
        assert any(e.event_type == EventType.ENDGAME_SELL_ALL for e in events)

    def test_sizing_multiplier_halves_exposure(self) -> None:
        # Use MEAN_REVERT (0.55) with 10 assets, high turnover limit
        # so turnover constraint doesn't interfere with the comparison
        assets = ["BTC", "ETH", "BNB", "LTC", "ADA", "TRX",
                  "LINK", "DOT", "NEAR", "AAVE"]
        c = _make_constructor(max_turnover=1.0)
        target_full, _ = c.compute(
            regime="MEAN_REVERT",
            btc_vol_percentile=50.0,
            selected_assets=assets,
            current_weights={},
            current_nav=1_000_000,
            sizing_multiplier=1.0,
        )
        target_half, _ = c.compute(
            regime="MEAN_REVERT",
            btc_vol_percentile=50.0,
            selected_assets=assets,
            current_weights={},
            current_nav=1_000_000,
            sizing_multiplier=0.5,
        )
        full_crypto = sum(w for a, w in target_full.items() if a != "PAXG")
        half_crypto = sum(w for a, w in target_half.items() if a != "PAXG")
        # Half sizing should produce roughly half the crypto exposure
        assert abs(half_crypto / full_crypto - 0.5) < 0.05


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
        # Portfolio matches benchmark — no gap
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
        # Benchmark up 10%, portfolio flat → gap = 7% > 4%
        adj = ae.compute_adjustment(
            0.0,
            {"BTC": 55000, "ETH": 3300},
        )
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
        assert adj == 0.0  # Too close to end


# =========================================================================
# BetaMonitor tests
# =========================================================================


class TestBetaMonitor:
    """Tests for beta monitoring."""

    def test_disabled_returns_zero(self) -> None:
        bm = BetaMonitor(enabled=False)
        assert bm.estimate_portfolio_beta({"BTC": 0.5}) == 0.0

    def test_needs_adjustment(self) -> None:
        bm = BetaMonitor(
            enabled=True,
            target_bull_max=0.60,
            adjustment_trigger=0.10,
        )
        # Beta 0.75 > 0.60 + 0.10 → needs adjustment
        assert bm.needs_adjustment(0.75, "TREND_BULL")
        # Beta 0.65 < 0.60 + 0.10 → no adjustment
        assert not bm.needs_adjustment(0.65, "TREND_BULL")

    def test_portfolio_beta_estimate(self) -> None:
        bm = BetaMonitor(enabled=True)
        bm.update_asset_betas({"BTC": 1.0, "ETH": 1.2, "PAXG": -0.1})
        beta = bm.estimate_portfolio_beta({"BTC": 0.3, "ETH": 0.3, "PAXG": 0.1})
        # 0.3 × 1.0 + 0.3 × 1.2 + 0.1 × (-0.1) = 0.3 + 0.36 - 0.01 = 0.65
        assert abs(beta - 0.65) < 1e-6


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
        assert orders[0].asset == "BTC"
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
        assert abs(orders[0].quantity_usd - 60_000) < 1.0

    def test_full_exit_order(self) -> None:
        from src.portfolio.factory import build_orders_from_weights

        orders = build_orders_from_weights(
            target_weights={},
            current_weights={"ETH": 0.06},
            nav=1_000_000,
        )
        assert len(orders) == 1
        assert orders[0].side == "SELL"
        assert orders[0].target_weight == 0.0

    def test_no_change_no_order(self) -> None:
        from src.portfolio.factory import build_orders_from_weights

        orders = build_orders_from_weights(
            target_weights={"BTC": 0.08},
            current_weights={"BTC": 0.08},
            nav=1_000_000,
        )
        assert len(orders) == 0

    def test_risk_exit_gets_critical_priority(self) -> None:
        from src.portfolio.factory import build_orders_from_weights
        from src.execution.priority_queue import OrderPriority

        orders = build_orders_from_weights(
            target_weights={"BTC": 0.0},
            current_weights={"BTC": 0.08},
            nav=1_000_000,
            risk_exit_assets={"BTC"},
        )
        assert len(orders) == 1
        assert orders[0].priority == OrderPriority.CRITICAL_EXIT
        assert orders[0].trigger == "RISK_EXIT"
        assert orders[0].risk_event_severity == "CRITICAL"

    def test_new_entry_priority(self) -> None:
        from src.portfolio.factory import build_orders_from_weights
        from src.execution.priority_queue import OrderPriority

        orders = build_orders_from_weights(
            target_weights={"LINK": 0.05},
            current_weights={},
            nav=1_000_000,
        )
        assert orders[0].priority == OrderPriority.NEW_ENTRY

    def test_multiple_orders_mixed(self) -> None:
        from src.portfolio.factory import build_orders_from_weights

        orders = build_orders_from_weights(
            target_weights={"BTC": 0.10, "ETH": 0.02, "LINK": 0.05},
            current_weights={"BTC": 0.05, "ETH": 0.06},
            nav=1_000_000,
        )
        by_asset = {o.asset: o for o in orders}
        assert by_asset["BTC"].side == "BUY"
        assert by_asset["ETH"].side == "SELL"
        assert by_asset["LINK"].side == "BUY"

    def test_pair_suffix(self) -> None:
        from src.portfolio.factory import build_orders_from_weights

        orders = build_orders_from_weights(
            target_weights={"BTC": 0.10},
            current_weights={},
            nav=1_000_000,
            pair_suffix="/USD",
        )
        assert orders[0].pair == "BTC/USD"


# =========================================================================
# Portfolio factory tests
# =========================================================================


class TestPortfolioFactory:
    """Tests for config-driven portfolio factory functions."""

    def _make_config(self) -> "Config":
        from apex.core.config import Config
        return Config(config_path=(
            Path(__file__).resolve().parent.parent / "config.yaml"
        ))

    def test_build_asset_tier_map(self) -> None:
        from src.portfolio.factory import build_asset_tier_map

        config = self._make_config()
        tier_map = build_asset_tier_map(config)

        assert tier_map["BTC"] == "tier_1_2"
        assert tier_map["ETH"] == "tier_1_2"
        assert tier_map["PAXG"] == "special"
        assert tier_map["TRUMP"] == "special"
        assert len(tier_map) > 10  # Should have many assets

    def test_build_tier_cap_overrides(self) -> None:
        from src.portfolio.factory import build_tier_cap_overrides

        config = self._make_config()
        caps = build_tier_cap_overrides(config)

        assert "DOGE" in caps
        assert "TRUMP" in caps
        assert "PAXG" in caps
        assert caps["TRUMP"] <= 0.02 + 1e-6

    def test_build_tier_cap_defaults(self) -> None:
        from src.portfolio.factory import build_tier_cap_defaults

        config = self._make_config()
        defaults = build_tier_cap_defaults(config)

        assert "tier_1_2" in defaults
        assert "tier_3" in defaults
        assert "tier_4_meme" in defaults
        assert "tier_5_obscure" in defaults

    def test_create_portfolio_constructor_phase1(self) -> None:
        from src.portfolio.factory import create_portfolio_constructor

        config = self._make_config()
        pc = create_portfolio_constructor(config, phase=1)
        assert pc is not None
        # Phase 1: no PAXG allocation (empty dict, falsy)
        assert not pc._paxg_alloc
        # Phase 1: simple T-1h sell-all
        assert pc._final_sell_min == 60.0

    def test_create_portfolio_constructor_phase2(self) -> None:
        from src.portfolio.factory import create_portfolio_constructor

        config = self._make_config()
        pc = create_portfolio_constructor(config, phase=2)
        # Phase 2: PAXG allocation enabled
        assert pc._paxg_alloc
        # Phase 2: full endgame schedule
        assert len(pc._endgame) > 0

    def test_get_current_weights(self) -> None:
        from src.execution.position_tracker import PositionTracker
        from src.portfolio.factory import get_current_weights

        tracker = PositionTracker(starting_capital=1_000_000.0)
        tracker.on_buy_fill("BTC", 1.0, 50000.0)

        weights = get_current_weights(tracker)
        assert "BTC" in weights
        assert weights["BTC"] > 0.0
        # BTC value = 50000, cash = 950000, NAV = 1000000
        assert abs(weights["BTC"] - 0.05) < 1e-6

    def test_get_base_stop_for_asset(self) -> None:
        from src.portfolio.factory import get_base_stop_for_asset

        config = self._make_config()
        tier_map = {"BTC": "tier_1_2", "SHIB": "tier_4_meme"}

        assert get_base_stop_for_asset("BTC", tier_map, config) == 0.06
        assert get_base_stop_for_asset("SHIB", tier_map, config) == 0.08
        assert get_base_stop_for_asset("TRUMP", tier_map, config) == 0.10


# =========================================================================
# Config integration test
# =========================================================================


class TestConfigIntegration:
    """Tests that all required config parameters exist."""

    def test_config_has_portfolio_section(self) -> None:
        """Verify config.yaml has all portfolio construction parameters."""
        import yaml
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
        if not config_path.exists():
            return  # Skip if config doesn't exist (CI)

        with open(config_path) as f:
            config = yaml.safe_load(f)

        portfolio = config["portfolio"]
        assert "regime_targets" in portfolio
        assert "btc_vol_high_vol_threshold" in portfolio
        assert "max_crypto_exposure" in portfolio
        assert "paxg_allocation" in portfolio
        assert "max_turnover_per_rebalance" in portfolio
        assert "min_trade_threshold_pct_nav" in portfolio

        regime = portfolio["regime_targets"]
        for key in ["trend_bull_low_vol", "trend_bull_high_vol",
                     "mean_revert", "trend_bear", "crisis"]:
            assert key in regime, f"Missing regime target: {key}"

    def test_config_has_risk_section(self) -> None:
        """Verify config.yaml has all risk management parameters."""
        import yaml
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
        if not config_path.exists():
            return

        with open(config_path) as f:
            config = yaml.safe_load(f)

        risk = config["risk"]
        assert "portfolio_limits" in risk
        assert "trailing_stops" in risk
        assert "stop_tightening" in risk
        assert "drawdown_recovery" in risk
        assert "contagion" in risk

        limits = risk["portfolio_limits"]
        for key in ["drawdown_soft_warning", "drawdown_hard_halt",
                     "daily_loss_soft_warning", "daily_loss_hard_reduce",
                     "single_asset_loss_soft", "single_asset_loss_hard"]:
            assert key in limits, f"Missing limit: {key}"

    def test_config_has_endgame(self) -> None:
        """Verify end-game schedule exists."""
        import yaml
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
        if not config_path.exists():
            return

        with open(config_path) as f:
            config = yaml.safe_load(f)

        endgame = config["endgame"]
        assert "schedule" in endgame
        assert "final_sell_minutes_remaining" in endgame
        assert len(endgame["schedule"]) >= 4


# =========================================================================
# Run with pytest
# =========================================================================

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
