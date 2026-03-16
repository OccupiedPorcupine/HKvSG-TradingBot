"""BTC beta estimation and logging (Phase 2+).

Estimates portfolio beta against BTC and optionally against an
equal-weighted universe index. Soft adjustment — influences sizing
at next rebalance, does not override signals.

Phase 1: stub only. Phase 2+ implements rolling regression.
"""

import logging
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class BetaMonitor:
    """Estimates and monitors portfolio beta against configurable benchmarks.

    Phase 2+ implementation. Phase 1 returns default values.

    Attributes:
        target_bull_range: (min, max) beta target during TREND_BULL.
        target_other_max: Max beta target for all other regimes.
        adjustment_trigger: Excess beta above target that triggers adjustment.
        regression_window_days: Rolling window for beta regression.
    """

    def __init__(
        self,
        target_bull_min: float = 0.40,
        target_bull_max: float = 0.60,
        target_other_max: float = 0.30,
        adjustment_trigger: float = 0.10,
        regression_window_days: int = 7,
        enabled: bool = False,
    ) -> None:
        """Initialize beta monitor.

        Args:
            target_bull_min: Min acceptable beta in TREND_BULL.
            target_bull_max: Max acceptable beta in TREND_BULL.
            target_other_max: Max acceptable beta in non-BULL regimes.
            adjustment_trigger: Beta excess above max that triggers action.
            regression_window_days: Rolling regression lookback.
            enabled: Whether beta monitoring is active (Phase 2+).
        """
        self._bull_range = (target_bull_min, target_bull_max)
        self._other_max = target_other_max
        self._trigger = adjustment_trigger
        self._window_days = regression_window_days
        self.enabled = enabled

        # Per-asset beta cache (updated periodically)
        self._asset_betas: dict[str, float] = {}

    def estimate_portfolio_beta(
        self,
        weights: dict[str, float],
        asset_betas: Optional[dict[str, float]] = None,
    ) -> float:
        """Estimate portfolio beta as weighted sum of asset betas.

        Args:
            weights: Portfolio weights {asset: weight}.
            asset_betas: Per-asset beta values. If None, uses cached.

        Returns:
            Portfolio beta (weighted sum).
        """
        if not self.enabled:
            return 0.0

        betas = asset_betas or self._asset_betas
        portfolio_beta = sum(
            weights.get(asset, 0.0) * betas.get(asset, 1.0)
            for asset in weights
        )
        return portfolio_beta

    def compute_asset_beta(
        self,
        asset_returns: np.ndarray,
        btc_returns: np.ndarray,
    ) -> float:
        """Compute trailing beta of a single asset against BTC.

        Uses OLS regression: beta = cov(r_asset, r_btc) / var(r_btc).

        Args:
            asset_returns: Array of asset log returns.
            btc_returns: Array of BTC log returns (same length).

        Returns:
            Beta coefficient.
        """
        if len(asset_returns) < 10 or len(btc_returns) < 10:
            return 1.0  # default when insufficient data

        if len(asset_returns) != len(btc_returns):
            min_len = min(len(asset_returns), len(btc_returns))
            asset_returns = asset_returns[-min_len:]
            btc_returns = btc_returns[-min_len:]

        var_btc = np.var(btc_returns)
        if var_btc < 1e-12:
            return 0.0

        cov = np.cov(asset_returns, btc_returns)[0, 1]
        return float(cov / var_btc)

    def update_asset_betas(self, betas: dict[str, float]) -> None:
        """Update cached per-asset beta values.

        Args:
            betas: Map of asset -> beta.
        """
        self._asset_betas = dict(betas)
        logger.debug("Asset betas updated: %d assets", len(betas))

    def get_beta_target(self, regime: str) -> float:
        """Get beta target max for the current regime.

        Args:
            regime: Current regime string.

        Returns:
            Maximum target beta.
        """
        if regime == "TREND_BULL":
            return self._bull_range[1]
        return self._other_max

    def needs_adjustment(
        self,
        portfolio_beta: float,
        regime: str,
    ) -> bool:
        """Check if portfolio beta exceeds target by more than trigger.

        Args:
            portfolio_beta: Current estimated portfolio beta.
            regime: Current regime.

        Returns:
            True if beta needs downward adjustment.
        """
        target_max = self.get_beta_target(regime)
        return portfolio_beta > target_max + self._trigger

    def log_beta_summary(
        self, portfolio_beta: float, regime: str, weights: dict[str, float]
    ) -> None:
        """Log beta monitoring summary.

        Args:
            portfolio_beta: Current portfolio beta.
            regime: Current regime.
            weights: Current portfolio weights.
        """
        target = self.get_beta_target(regime)
        needs_adj = self.needs_adjustment(portfolio_beta, regime)
        logger.info(
            "Beta monitor: portfolio_beta=%.3f target_max=%.3f "
            "regime=%s needs_adjustment=%s",
            portfolio_beta, target, regime, needs_adj,
        )
