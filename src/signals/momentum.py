"""Signal 1 — Cross-sectional momentum ranking.

Primary signal, active from Phase 1. Ranks Tier 1-3 assets by composite
momentum score and selects top N based on current regime.

Phase 2 adds the trend penalty: EMA(60) < EMA(240) applies a configurable
penalty to the asset's score. This is a rank PENALTY, not an exit override.
Assets with strong momentum survive the penalty. Assets with weak momentum
+ negative trend naturally drop below the top-N threshold.

Signal flow:
    1. Get composite momentum scores from Layer 2.
    2. Filter to eligible assets (Tier 1-3, non-STALE).
    3. Apply trend penalty (Phase 2+, penalty = 0 in Phase 1).
    4. Rank by adjusted score.
    5. Select top N based on regime.
    6. Return: dict[asset, adjusted_score].
"""

import logging
from typing import Any, Callable, Optional

from src.regime.regime_state import RegimeState, RegimeType

logger = logging.getLogger(__name__)


class MomentumSignal:
    """Cross-sectional momentum ranking for Tier 1-3 assets.

    Usage::

        signal = MomentumSignal(config, tier_1_3_assets)
        selections = signal.generate(
            momentum_scores=feature_engine.get_momentum_scores(),
            regime=regime_state,
            get_ema_fn=feature_engine.get_ema_values,  # Phase 2
        )
    """

    def __init__(
        self,
        config: dict[str, Any],
        tier_1_3_assets: set[str],
    ) -> None:
        """Initialize momentum signal.

        Args:
            config: Full config.yaml as dict.
            tier_1_3_assets: Set of asset symbols in Tiers 1-3
                             (eligible for main pool).
        """
        self._config = config
        self._eligible = tier_1_3_assets

        # Top-N selections by regime
        top_n_cfg = config.get("signals", {}).get("top_n_selections", {})
        self._top_n: dict[RegimeType, int] = {
            RegimeType.TREND_BULL: top_n_cfg.get("trend_bull", 10),
            RegimeType.MEAN_REVERT: top_n_cfg.get("mean_revert", 6),
            RegimeType.TREND_BEAR: top_n_cfg.get("trend_bear", 4),
            RegimeType.HIGH_VOL_CRISIS: top_n_cfg.get("crisis", 0),
        }

        # Trend penalty magnitude (Phase 2+, 0 in Phase 1)
        self._trend_penalty = config.get("signals", {}).get(
            "trend_penalty_magnitude", -0.30
        )
        self._trend_penalty_enabled = False  # Enable in Phase 2

    def enable_trend_penalty(self) -> None:
        """Enable the EMA trend penalty (call when entering Phase 2)."""
        self._trend_penalty_enabled = True
        logger.info(
            "Trend penalty enabled: %.2f sigma", self._trend_penalty
        )

    def generate(
        self,
        momentum_scores: dict[str, float],
        regime: RegimeState,
        get_ema_fn: Optional[
            Callable[[str], tuple[Optional[float], Optional[float]]]
        ] = None,
    ) -> dict[str, float]:
        """Generate momentum signal: rank and select top N assets.

        Args:
            momentum_scores: Dict of asset → composite momentum score
                             from Layer 2 (FeatureEngine.get_momentum_scores).
            regime: Current RegimeState.
            get_ema_fn: Callable(asset) → (ema_60, ema_240). Used for
                        trend penalty in Phase 2. None in Phase 1.

        Returns:
            Dict of selected asset → adjusted momentum score.
            Only top-N assets are included. Assets not selected are
            excluded (NOT given score 0 — simply absent).
        """
        # Step 1: Filter to eligible Tier 1-3 assets
        eligible_scores = {
            asset: score
            for asset, score in momentum_scores.items()
            if asset in self._eligible
        }

        if not eligible_scores:
            return {}

        # Step 2: Apply trend penalty (Phase 2+)
        if self._trend_penalty_enabled and get_ema_fn is not None:
            adjusted = self._apply_trend_penalty(eligible_scores, get_ema_fn)
        else:
            adjusted = dict(eligible_scores)

        # Step 3: Determine top N from regime
        n = self._top_n.get(regime.current_regime, 10)

        if n <= 0:
            # CRISIS: exit all
            return {}

        # Step 4: Rank and select top N
        ranked = sorted(adjusted.items(), key=lambda x: x[1], reverse=True)
        selections = dict(ranked[:n])

        if selections:
            logger.debug(
                "MOMENTUM: regime=%s top_%d selected: %s",
                regime.current_regime.value,
                n,
                list(selections.keys()),
            )

        return selections

    def _apply_trend_penalty(
        self,
        scores: dict[str, float],
        get_ema_fn: Callable[
            [str], tuple[Optional[float], Optional[float]]
        ],
    ) -> dict[str, float]:
        """Apply trend penalty to assets where EMA(60) < EMA(240).

        Scales penalty to -0.15 if >70% of the universe is in a downtrend
        to preserve ranking discrimination during broad market sell-offs.
        """
        adjusted: dict[str, float] = {}
        
        # Step 1: Pre-calculate EMA states to determine broad market trend
        ema_states = {}
        downtrend_count = 0
        
        for asset in scores.keys():
            ema_60, ema_240 = get_ema_fn(asset)
            ema_states[asset] = (ema_60, ema_240)
            if ema_60 is not None and ema_240 is not None and ema_60 < ema_240:
                downtrend_count += 1
                
        # Step 2: Scale penalty if >70% of eligible universe is in downtrend
        total_assets = len(scores)
        current_penalty = self._trend_penalty
        
        if total_assets > 0 and (downtrend_count / total_assets) > 0.70:
            current_penalty = -0.15  # Scaled penalty for broad downturns
            logger.debug(
                "TREND_PENALTY SCALED: %d/%d assets (%.1f%%) in downtrend. "
                "Penalty reduced to %.2f",
                downtrend_count, total_assets, 
                (downtrend_count / total_assets) * 100, current_penalty
            )

        # Step 3: Apply the calculated penalty
        for asset, score in scores.items():
            ema_60, ema_240 = ema_states[asset]

            if (
                ema_60 is not None
                and ema_240 is not None
                and ema_60 < ema_240
            ):
                adjusted_score = score + current_penalty
                logger.debug(
                    "TREND_PENALTY: %s score %.4f → %.4f "
                    "(EMA60=%.2f < EMA240=%.2f)",
                    asset, score, adjusted_score, ema_60, ema_240,
                )
                adjusted[asset] = adjusted_score
            else:
                adjusted[asset] = score

        return adjusted
