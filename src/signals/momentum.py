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
        get_vol_fn: Optional[
            Callable[[str, str], Optional[float]]
        ] = None,
        sentiment_scores: Optional[dict[str, float]] = None,
    ) -> dict[str, float]:
        """Generate momentum signal: rank and select top N assets.

        Args:
            momentum_scores: Dict of asset → composite momentum score
                             from Layer 2 (FeatureEngine.get_momentum_scores).
            regime: Current RegimeState.
            get_ema_fn: Callable(asset) → (ema_60, ema_240). Used for
                        trend penalty in Phase 2. None in Phase 1.
            get_vol_fn: Callable(asset, window) → float. Used for
                        Phase 1 volatility exclusion filter.
            sentiment_scores: Dict of asset → sentiment rank (0-1).

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
            self._prev_selections = set()
            return {}

        # Step 2: Volatility Exclusion Filter (Phase 1)
        if get_vol_fn is not None:
            eligible_scores = self._apply_vol_filter(eligible_scores, get_vol_fn)

        if not eligible_scores:
            self._prev_selections = set()
            return {}

        # Step 3: Apply trend penalty (Phase 2+)
        if self._trend_penalty_enabled and get_ema_fn is not None:
            adjusted = self._apply_trend_penalty(eligible_scores, get_ema_fn)
        else:
            adjusted = dict(eligible_scores)

        # Step 4: Apply sentiment overlay (from Binance Funding)
        if self._sentiment_enabled and sentiment_scores:
            adjusted = self._apply_sentiment_overlay(adjusted, sentiment_scores)

        # Step 5: Determine top N from regime
        n = self._top_n.get(regime.current_regime, 10)

        if n <= 0:
            # CRISIS: exit all
            self._prev_selections = set()
            return {}

        # Step 5: Filter out negative momentum, then Rank
        positive_adjusted = {asset: score for asset, score in adjusted.items() if score > 0}
        ranked = sorted(positive_adjusted.items(), key=lambda x: x[1], reverse=True)
        ranked_assets = [asset for asset, _ in ranked]
        
        # Step 6: Apply Hysteresis (N+3)
        # 1. New assets must enter top N
        # 2. Existing assets remain unless they drop below rank N + buffer
        final_selections: dict[str, float] = {}
        
        # Determine exit threshold (rank is 0-indexed)
        exit_rank_limit = n + self._hysteresis_buffer
        
        for rank, (asset, score) in enumerate(ranked):
            is_prev = asset in self._prev_selections
            
            # Condition 1: Entry (must be in top N)
            # Condition 2: Stay (must be in top N+buffer)
            if rank < n or (is_prev and rank < exit_rank_limit):
                if len(final_selections) < n or is_prev: # Keep prev even if it pushes total > n temporarily?
                    # ARCH: "Go long the top N assets". 
                    # Usually N is the target size. If we keep more due to hysteresis,
                    # we might exceed target N. But we cap at N for new entries.
                    # Actually, we should probably cap total selections at N for simplicity,
                    # prioritizing old ones if they are still within N+buffer.
                    
                    # Logic: If we already have N assets, only add if it's a prev asset.
                    if len(final_selections) < n:
                        final_selections[asset] = score
                    elif is_prev:
                        final_selections[asset] = score

        # Update tracking for next rebalance
        self._prev_selections = set(final_selections.keys())

        if final_selections:
            logger.debug(
                "MOMENTUM: regime=%s top_%d selected (hysteresis buffer %d): %s",
                regime.current_regime.value,
                n,
                self._hysteresis_buffer,
                list(final_selections.keys()),
            )

        return final_selections

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
