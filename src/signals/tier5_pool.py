"""Signal 4 — Tier 5 opportunistic sub-pool.

Active from Phase 2 only. Selects top 1-2 Tier 5 assets that have:
  - 4h return > 10% (activation threshold)
  - In top 3 of Tier 5 ranking

Active ONLY in TREND_BULL regime. Each selected asset gets 1-2% allocation.
8% trailing stop (same as meme coins).

Rationale: without this, 13 assets (Tier 5) receive zero allocation under
any regime. A 100% pump on a 2% position adds 2% to NAV with max downside
of 2% x 8% = 0.16% of NAV. Convex optionality.
"""

import logging
from typing import Any, Callable, Optional

from src.regime.regime_state import RegimeState, RegimeType

logger = logging.getLogger(__name__)


class Tier5PoolSignal:
    """Tier 5 opportunistic asset selection.

    Phase 1: always returns empty dict (stub).
    Phase 2: selects high-momentum Tier 5 assets meeting activation threshold.

    Usage::

        signal = Tier5PoolSignal(config, tier_5_assets)
        selections = signal.generate(
            momentum_scores, regime, get_return_fn
        )
    """

    def __init__(
        self,
        config: dict[str, Any],
        tier_5_assets: set[str],
    ) -> None:
        """Initialize Tier 5 pool signal.

        Args:
            config: Full config.yaml as dict.
            tier_5_assets: Set of Tier 5 asset symbols.
        """
        t5_cfg = config.get("signals", {}).get("tier5_pool", {})
        self._eligible = tier_5_assets
        self._return_threshold = t5_cfg.get(
            "activation_4h_return_threshold", 0.10
        )
        self._rank_top_n = t5_cfg.get("activation_rank_top_n", 3)
        self._max_selections = t5_cfg.get("max_selections", 2)
        self._active_regimes = set(
            t5_cfg.get("active_regimes", ["TREND_BULL"])
        )
        self._enabled = False  # Enable in Phase 2

    def enable(self) -> None:
        """Enable Tier 5 pool signal (call when entering Phase 2)."""
        self._enabled = True
        logger.info("Tier 5 pool signal enabled")

    def generate(
        self,
        momentum_scores: dict[str, float],
        regime: RegimeState,
        get_return_fn: Optional[Callable[[str, int], Optional[float]]] = None,
    ) -> dict[str, float]:
        """Generate Tier 5 pool selections.

        Args:
            momentum_scores: All asset momentum scores from Layer 2.
            regime: Current RegimeState.
            get_return_fn: Callable(asset, window_min) → return value.
                           Used to check 4h return threshold.

        Returns:
            Dict of selected Tier 5 asset → momentum score.
            Empty dict if not TREND_BULL, Phase 1, or no qualifying assets.
        """
        if not self._enabled:
            return {}

        if regime.current_regime.value not in self._active_regimes:
            return {}

        if get_return_fn is None:
            return {}

        # Filter to Tier 5 assets with momentum scores
        tier5_scores = {
            asset: score
            for asset, score in momentum_scores.items()
            if asset in self._eligible
        }

        if not tier5_scores:
            return {}

        # Sort by momentum to get top N within tier
        ranked = sorted(
            tier5_scores.items(), key=lambda x: x[1], reverse=True
        )
        top_ranked = dict(ranked[: self._rank_top_n])

        # Apply activation threshold: 4h return > 10%
        qualifying: dict[str, float] = {}
        for asset, score in top_ranked.items():
            ret_4h = get_return_fn(asset, 240)
            if ret_4h is not None and ret_4h > self._return_threshold:
                qualifying[asset] = score

        if not qualifying:
            return {}

        # Select top max_selections from qualifying
        final = sorted(
            qualifying.items(), key=lambda x: x[1], reverse=True
        )
        selections = dict(final[: self._max_selections])

        if selections:
            logger.debug(
                "TIER5_POOL: selected %s (4h returns > %.0f%%)",
                list(selections.keys()),
                self._return_threshold * 100,
            )

        return selections
