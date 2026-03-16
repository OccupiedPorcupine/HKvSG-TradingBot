"""Adaptive exposure adjustment (Phase 2+).

Tracks a naive benchmark (equal-weight 70% exposure to all assets) and
raises exposure floors when the portfolio falls behind. Ensures we stay
competitive on the leaderboard (Gate 1) even in defensive regimes.

Phase 1: disabled (returns 0.0 adjustment).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class AdaptiveExposure:
    """Tracks naive benchmark and computes exposure adjustments.

    The naive benchmark is trivial: equal-weight average of all asset
    returns since competition start. Tracked incrementally.

    Attributes:
        enabled: Whether adaptive adjustment is active.
        benchmark_exposure: Naive benchmark crypto exposure (default 0.70).
        portfolio_cumulative_return: Tracked portfolio return since start.
        benchmark_cumulative_return: Tracked benchmark return since start.
    """

    def __init__(
        self,
        enabled: bool = False,
        benchmark_exposure: float = 0.70,
        gap_threshold_small: float = 0.02,
        gap_threshold_large: float = 0.04,
        adjustment_small: float = 0.10,
        adjustment_large: float = 0.20,
        min_days_remaining_small: int = 5,
        min_days_remaining_large: int = 3,
        competition_end_utc: Optional[datetime] = None,
    ) -> None:
        """Initialize adaptive exposure from config.

        Args:
            enabled: Whether to activate (Phase 2+).
            benchmark_exposure: Naive benchmark's crypto exposure fraction.
            gap_threshold_small: Relative gap for small adjustment.
            gap_threshold_large: Relative gap for large adjustment.
            adjustment_small: Exposure floor increase for small gap.
            adjustment_large: Exposure floor increase for large gap.
            min_days_remaining_small: Only adjust if > this many days left.
            min_days_remaining_large: Only adjust if > this many days left.
            competition_end_utc: Competition end timestamp.
        """
        self.enabled = enabled
        self._benchmark_exposure = benchmark_exposure
        self._gap_small = gap_threshold_small
        self._gap_large = gap_threshold_large
        self._adj_small = adjustment_small
        self._adj_large = adjustment_large
        self._min_days_small = min_days_remaining_small
        self._min_days_large = min_days_remaining_large
        self._comp_end = competition_end_utc

        # Tracked state
        self._start_prices: dict[str, float] = {}
        self._start_nav: float = 0.0

    def initialize(
        self, asset_prices: dict[str, float], starting_nav: float
    ) -> None:
        """Set starting prices and NAV for benchmark tracking.

        Call once at competition start or crash recovery.

        Args:
            asset_prices: Map of asset -> initial price.
            starting_nav: Starting portfolio NAV.
        """
        self._start_prices = dict(asset_prices)
        self._start_nav = starting_nav
        logger.info(
            "Adaptive exposure initialized: %d assets, NAV=%.2f",
            len(asset_prices), starting_nav,
        )

    def compute_benchmark_return(
        self, current_prices: dict[str, float]
    ) -> float:
        """Compute naive benchmark cumulative return.

        Equal-weight average of all asset returns × benchmark_exposure.

        Args:
            current_prices: Map of asset -> current price.

        Returns:
            Benchmark cumulative return as fraction.
        """
        if not self._start_prices:
            return 0.0

        returns: list[float] = []
        for asset, start_price in self._start_prices.items():
            current = current_prices.get(asset, start_price)
            if start_price > 0:
                returns.append((current - start_price) / start_price)

        if not returns:
            return 0.0

        avg_return = sum(returns) / len(returns)
        return avg_return * self._benchmark_exposure

    def compute_adjustment(
        self,
        portfolio_return: float,
        current_prices: dict[str, float],
    ) -> float:
        """Compute exposure adjustment based on benchmark gap.

        Args:
            portfolio_return: Portfolio cumulative return since start.
            current_prices: Current asset prices for benchmark calc.

        Returns:
            Exposure adjustment (0.0, 0.10, or 0.20).
        """
        if not self.enabled:
            return 0.0

        benchmark_return = self.compute_benchmark_return(current_prices)
        relative_gap = benchmark_return - portfolio_return

        days_remaining = self._get_days_remaining()

        if relative_gap > self._gap_large and days_remaining > self._min_days_large:
            logger.info(
                "Adaptive exposure: large gap (%.2f%%), +%.0f%% adjustment",
                relative_gap * 100, self._adj_large * 100,
            )
            return self._adj_large

        if relative_gap > self._gap_small and days_remaining > self._min_days_small:
            logger.info(
                "Adaptive exposure: small gap (%.2f%%), +%.0f%% adjustment",
                relative_gap * 100, self._adj_small * 100,
            )
            return self._adj_small

        return 0.0

    def _get_days_remaining(self) -> float:
        """Compute days remaining in competition."""
        if self._comp_end is None:
            return 999.0  # effectively unlimited
        remaining = self._comp_end - datetime.now(timezone.utc)
        return remaining.total_seconds() / 86400.0
