"""Signal 3 — Meme coin sub-pool (Tier 4).

Active from Phase 2 only. Ranks Tier 4 meme coins against each other
and selects top 1-2 by momentum score.

Active ONLY in TREND_BULL regime. All other regimes → empty selection.
TRUMP additional rule: allocation = 0 if regime != TREND_BULL.
DOGE is classified as Tier 1 (not in this pool).

Each selected meme coin gets 3% target allocation (applied by Layer 5).
"""

import logging
from typing import Any

from src.regime.regime_state import RegimeState, RegimeType

logger = logging.getLogger(__name__)


class MemePoolSignal:
    """Tier 4 meme coin ranking and selection.

    Phase 1: always returns empty dict (stub).
    Phase 2: ranks meme coins within-tier, selects top 1-2 in TREND_BULL.

    Usage::

        signal = MemePoolSignal(config, tier_4_assets)
        selections = signal.generate(momentum_scores, regime)
    """

    def __init__(
        self,
        config: dict[str, Any],
        tier_4_assets: set[str],
    ) -> None:
        """Initialize meme pool signal.

        Args:
            config: Full config.yaml as dict.
            tier_4_assets: Set of Tier 4 meme coin symbols (excluding DOGE).
        """
        meme_cfg = config.get("signals", {}).get("meme_pool", {})
        self._eligible = tier_4_assets
        self._max_selections = meme_cfg.get("max_selections", 2)
        self._active_regimes = set(
            meme_cfg.get("active_regimes", ["TREND_BULL"])
        )
        self._enabled = False  # Enable in Phase 2

    def enable(self) -> None:
        """Enable meme pool signal (call when entering Phase 2)."""
        self._enabled = True
        logger.info("Meme pool signal enabled")

    def generate(
        self,
        momentum_scores: dict[str, float],
        regime: RegimeState,
    ) -> dict[str, float]:
        """Generate meme pool selections.

        Args:
            momentum_scores: All asset momentum scores from Layer 2.
            regime: Current RegimeState.

        Returns:
            Dict of selected meme asset → momentum score.
            Empty dict if not TREND_BULL or Phase 1.
        """
        if not self._enabled:
            return {}

        if regime.current_regime.value not in self._active_regimes:
            return {}

        # Filter to Tier 4 assets
        meme_scores = {
            asset: score
            for asset, score in momentum_scores.items()
            if asset in self._eligible
        }

        if not meme_scores:
            return {}

        # Rank within tier and select top N
        ranked = sorted(meme_scores.items(), key=lambda x: x[1], reverse=True)
        selections = dict(ranked[: self._max_selections])

        if selections:
            logger.debug(
                "MEME_POOL: selected %s", list(selections.keys())
            )

        return selections
