"""Config-driven factory for portfolio construction components.

Builds PortfolioConstructor and helper maps from config.yaml.
Provides the bridge function that converts target weight diffs into
PendingOrder objects for the execution layer.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from apex.core.config import Config
from src.execution.position_tracker import PositionTracker
from src.execution.priority_queue import OrderPriority, PendingOrder
from src.portfolio.constructor import PortfolioConstructor

logger = logging.getLogger(__name__)


def build_asset_tier_map(config: Config) -> dict[str, str]:
    """Build a map of asset symbol -> tier key from config universe.

    Args:
        config: Loaded Config instance.

    Returns:
        Dict mapping every asset to its tier key
        (e.g., {"BTC": "tier_1_2", "AAVE": "tier_3"}).
    """
    tier_map: dict[str, str] = {}

    for asset in config.get("universe.tier_1_majors", []):
        tier_map[asset] = "tier_1_2"
    for asset in config.get("universe.tier_2_large_alts", []):
        tier_map[asset] = "tier_1_2"
    for asset in config.get("universe.tier_3_defi", []):
        tier_map[asset] = "tier_3"
    for asset in config.get("universe.tier_4_meme", []):
        tier_map[asset] = "tier_4_meme"
    for asset in config.get("universe.tier_5_obscure", []):
        tier_map[asset] = "tier_5_obscure"

    # Specials
    tier_map[config.get("universe.special.paxg", "PAXG")] = "special"
    tier_map[config.get("universe.special.trump", "TRUMP")] = "special"

    return tier_map


def build_tier_cap_overrides(config: Config) -> dict[str, float]:
    """Build per-asset cap overrides from config.

    Args:
        config: Loaded Config instance.

    Returns:
        Dict of asset -> cap override (only for assets with special caps).
    """
    caps: dict[str, float] = {}
    caps["DOGE"] = config.get("tier_caps.doge", 0.05)
    caps["TRUMP"] = config.get("tier_caps.trump", 0.02)
    caps["PAXG"] = config.get("tier_caps.paxg", 0.15)
    return caps


def build_tier_cap_defaults(config: Config) -> dict[str, float]:
    """Build tier key -> default cap from config.

    Args:
        config: Loaded Config instance.

    Returns:
        Dict mapping tier keys to their default caps.
    """
    return {
        "tier_1_2": config.get("tier_caps.tier_1_2", 0.08),
        "tier_3": config.get("tier_caps.tier_3", 0.06),
        "tier_4_meme": config.get("tier_caps.tier_4_meme", 0.03),
        "tier_5_obscure": config.get("tier_caps.tier_5_obscure", 0.02),
        "special": config.get("tier_caps.paxg", 0.15),
    }


def create_portfolio_constructor(
    config: Config,
    phase: int = 1,
) -> PortfolioConstructor:
    """Create a PortfolioConstructor from config.yaml.

    Args:
        config: Loaded Config instance.
        phase: Current implementation phase (1, 2, or 3).

    Returns:
        Configured PortfolioConstructor.
    """
    regime_targets = config.get("portfolio.regime_targets", {})

    # Parse competition end time
    end_str = config.get("competition.competition_end_utc")
    comp_end: Optional[datetime] = None
    if end_str:
        comp_end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))

    # Phase 1: simple T-1h sell-all, no full endgame schedule
    endgame_schedule = None
    final_sell_min = 60.0  # sell all at T-1h for Phase 1

    if phase >= 2:
        endgame_schedule = config.get("endgame.schedule", [])
        final_sell_min = config.get("endgame.final_sell_minutes_remaining", 15.0)

    # Phase 1: no PAXG allocation
    paxg_allocation = None
    if phase >= 2:
        paxg_allocation = config.get("portfolio.paxg_allocation", {})

    return PortfolioConstructor(
        regime_targets=regime_targets,
        tier_caps=build_tier_cap_overrides(config),
        asset_tier_map=build_asset_tier_map(config),
        tier_cap_defaults=build_tier_cap_defaults(config),
        btc_vol_high_vol_threshold=config.get(
            "portfolio.btc_vol_high_vol_threshold", 70.0
        ),
        max_crypto_exposure=config.get("portfolio.max_crypto_exposure", 0.90),
        max_turnover=config.get("portfolio.max_turnover_per_rebalance", 0.25),
        min_trade_threshold=config.get(
            "portfolio.min_trade_threshold_pct_nav", 0.002
        ),
        paxg_allocation=paxg_allocation,
        endgame_schedule=endgame_schedule,
        final_sell_minutes=final_sell_min,
        competition_end_utc=comp_end,
        redistribution_max_iterations=config.get(
            "tier_caps.redistribution_max_iterations", 5
        ),
    )


def get_base_stop_for_asset(
    asset: str, asset_tier_map: dict[str, str], config: Config
) -> float:
    """Look up the base trailing stop distance for an asset.

    Args:
        asset: Asset symbol.
        asset_tier_map: Map of asset -> tier key.
        config: Loaded Config instance.

    Returns:
        Base stop distance as a fraction.
    """
    if asset == "TRUMP":
        return config.get("risk.trailing_stops.trump", 0.10)

    tier = asset_tier_map.get(asset, "tier_1_2")
    if tier in ("tier_4_meme", "tier_5_obscure"):
        return config.get("risk.trailing_stops.tier_4_5", 0.08)

    return config.get("risk.trailing_stops.tier_1_3", 0.06)


def build_orders_from_weights(
    target_weights: dict[str, float],
    current_weights: dict[str, float],
    nav: float,
    pair_suffix: str = "/USD",
    risk_exit_assets: Optional[set[str]] = None,
) -> list[PendingOrder]:
    """Convert target weight diffs into PendingOrder objects.

    This is the bridge between Layer 5 (portfolio construction) and
    Layer 7 (execution). The orchestrator calls this after compute().

    Args:
        target_weights: Target portfolio weights from constructor.
        current_weights: Current portfolio weights from PositionTracker.
        nav: Current portfolio NAV.
        pair_suffix: Trading pair suffix (default "/USD").
        risk_exit_assets: Assets with pending risk exits (CRITICAL priority).

    Returns:
        List of PendingOrders ready to be added to the OrderPriorityQueue.
    """
    risk_exit_assets = risk_exit_assets or set()
    orders: list[PendingOrder] = []

    all_assets = set(target_weights.keys()) | set(current_weights.keys())

    for asset in all_assets:
        target_w = target_weights.get(asset, 0.0)
        current_w = current_weights.get(asset, 0.0)
        delta_w = target_w - current_w

        if abs(delta_w) < 1e-6:
            continue

        quantity_usd = delta_w * nav

        # Determine side and priority
        if delta_w > 0:
            side = "BUY"
            if asset in risk_exit_assets:
                priority = OrderPriority.CRITICAL_EXIT
                trigger = "RISK_EXIT"
            elif current_w < 0.001:
                priority = OrderPriority.NEW_ENTRY
                trigger = "REBALANCE"
            else:
                priority = OrderPriority.SIZE_ADJUSTMENT
                trigger = "REBALANCE"
        else:
            side = "SELL"
            if asset in risk_exit_assets:
                priority = OrderPriority.CRITICAL_EXIT
                trigger = "RISK_EXIT"
            elif target_w < 0.001:
                priority = OrderPriority.POSITION_REDUCTION
                trigger = "EXIT"
            else:
                priority = OrderPriority.SIZE_ADJUSTMENT
                trigger = "REBALANCE"

        risk_severity = "CRITICAL" if asset in risk_exit_assets else None

        orders.append(PendingOrder(
            priority=priority,
            asset=asset,
            pair=f"{asset}{pair_suffix}",
            side=side,
            quantity_usd=abs(quantity_usd),
            target_weight=target_w,
            trigger=trigger,
            risk_event_severity=risk_severity,
        ))

    return orders


def get_current_weights(tracker: PositionTracker) -> dict[str, float]:
    """Extract current weights from PositionTracker.

    Args:
        tracker: PositionTracker instance.

    Returns:
        Dict of asset -> current weight.
    """
    weights: dict[str, float] = {}
    nav = tracker.nav
    if nav <= 0:
        return weights
    for asset, pos in tracker.positions.items():
        weights[asset] = pos.market_value / nav
    return weights
