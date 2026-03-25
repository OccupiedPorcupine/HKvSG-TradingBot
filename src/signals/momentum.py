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
            get_ema_fn=feature_engine.get_ema_values,      # Phase 2
            get_vol_fn=feature_engine.get_asset_volatility, # Phase 1 vol filter
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

        # Turnover Buffer (Hysteresis): exit if drops below N + buffer
        self._hysteresis_buffer = config.get("signals", {}).get("hysteresis_buffer", 3)
        self._prev_selections: set[str] = set()

        # Volatility Exclusion Filter: exclude if > multiplier * median
        self._vol_filter_multiplier = config.get("signals", {}).get("vol_exclusion_multiplier", 2.0)

        # Sentiment Overlay (Binance Funding)
        self._sentiment_enabled = config.get("signals", {}).get("sentiment_overlay_enabled", False)
        self._sentiment_penalty = config.get("signals", {}).get("sentiment_crowded_penalty", -0.15)
        self._sentiment_bonus = config.get("signals", {}).get("sentiment_reversal_bonus", 0.10)

        # Trend penalty magnitude (Phase 2+, 0 in Phase 1)
        self._trend_penalty = config.get("signals", {}).get(
            "trend_penalty_magnitude", -0.30
        )
        self._trend_penalty_enabled = False  # Enable in Phase 2

    def generate(
        self,
        momentum_scores: dict[str, float],
        regime: RegimeState,
        get_ema_fn: Optional[
            Callable[[str], tuple[Optional[float], Optional[float]]]
        ] = None, # Kept so the orchestrator doesn't break
        get_vol_fn: Optional[
            Callable[[str, str], Optional[float]]
        ] = None, # Kept so the orchestrator doesn't break
        sentiment_scores: Optional[dict[str, float]] = None,
    ) -> dict[str, float]:
        """Generate momentum signal: apply sentiment and return ALL scores.
        
        Phase 2 Delegation: Trend penalties, Volatility Filtering, and Top-N 
        selection are now handled explicitly by src/portfolio/constructor.py 
        to ensure ranks are calculated accurately AFTER all penalties are applied.
        """
        if not momentum_scores:
            return {}

        # Step 1: Apply sentiment overlay (from Binance Funding) to ALL assets
        if self._sentiment_enabled and sentiment_scores:
            adjusted = self._apply_sentiment_overlay(momentum_scores, sentiment_scores)
        else:
            adjusted = dict(momentum_scores)

        # Step 2: Filter out negative baseline momentum
        positive_adjusted = {
            asset: score for asset, score in adjusted.items() if score > 0
        }

        # Return the FULL list of positive scores across ALL tiers. 
        # constructor.py will apply the TrendPenaltyEngine, route Meme coins 
        # to the Meme pool, and isolate Tier 1-3 for the main pool.
        return positive_adjusted

    def _apply_vol_filter(
        self,
        scores: dict[str, float],
        get_vol_fn: Callable[[str, str], Optional[float]],
    ) -> dict[str, float]:
        """Exclude assets whose 24h vol > multiplier * median of pool."""
        vols = {}
        for asset in scores:
            vol = get_vol_fn(asset, "24h")
            if vol is not None:
                vols[asset] = vol
        
        if not vols:
            return scores
            
        import numpy as np
        median_vol = np.median(list(vols.values()))
        threshold = median_vol * self._vol_filter_multiplier
        
        filtered = {}
        excluded = []
        for asset, score in scores.items():
            vol = vols.get(asset)
            if vol is not None and vol > threshold:
                excluded.append(asset)
                continue
            filtered[asset] = score
            
        if excluded:
            logger.info(
                "VOL_FILTER: excluded %d assets with 24h vol > %.4f (2x median %.4f): %s",
                len(excluded), threshold, median_vol, excluded
            )
            
        return filtered

    def _apply_sentiment_overlay(
        self,
        scores: dict[str, float],
        sentiment_scores: dict[str, float],
    ) -> dict[str, float]:
        """Apply sentiment-based adjustments to momentum scores.
        
        Crowded Longs (high sentiment score) → Penalty.
        Potential Reversals (low sentiment score) → Bonus.
        """
        adjusted = {}
        for asset, score in scores.items():
            sent = sentiment_scores.get(asset, 0.5) # Neutral if missing
            
            if sent > 0.90: # Extremely crowded
                adj_score = score + self._sentiment_penalty
                logger.debug("SENTIMENT_PENALTY: %s crowded (sent=%.2f) %.4f -> %.4f", 
                             asset, sent, score, adj_score)
                adjusted[asset] = adj_score
            elif sent < 0.15: # Potential panic/reversal
                adj_score = score + self._sentiment_bonus
                logger.debug("SENTIMENT_BONUS: %s oversold (sent=%.2f) %.4f -> %.4f", 
                             asset, sent, score, adj_score)
                adjusted[asset] = adj_score
            else:
                adjusted[asset] = score
        return adjusted
