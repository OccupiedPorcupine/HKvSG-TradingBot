"""Layer 2 — Feature Engineering Pipeline.

Transforms raw price data from the ingestion pipeline into validated
feature arrays consumed by signals, regime detection, and ML model.

All features are computed incrementally on each new bar. Feature
computation must complete within 5 seconds of bar close.
"""

import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from src.data.ingestion import (
    DataIngestionManager,
    TIER_4_MEME,
    TIER_5_OBSCURE,
    TIER_MAIN_POOL,
    TIER_SPECIAL_PAXG,
)
from src.data.validators import AssetStatus

logger = logging.getLogger(__name__)


@dataclass
class RegimeInputs:
    """Input features consumed by the regime detection layer.

    Attributes:
        btc_4h_return: BTC 4-hour return.
        btc_24h_return: BTC 24-hour return.
        btc_trend: "bullish", "bearish", or "neutral".
        btc_vol_percentile: Current BTC 1h vol percentile (0-100).
        altcoin_breadth: Fraction of non-BTC/non-PAXG assets with positive 4h return.
        volatility_ratio: BTC 1h vol / BTC 24h vol (spike detector).
        btc_dominance_1h: BTC 1h return minus equal-weighted altcoin 1h return.
        btc_dominance_4h: BTC 4h return minus equal-weighted altcoin 4h return.
    """

    btc_4h_return: Optional[float]
    btc_24h_return: Optional[float]
    btc_trend: str
    btc_vol_percentile: Optional[float]
    altcoin_breadth: Optional[float]
    volatility_ratio: Optional[float]
    btc_dominance_1h: Optional[float]
    btc_dominance_4h: Optional[float]


class FeatureEngine:
    """Computes all features from raw price data.

    Features are updated incrementally on each new bar. The engine
    maintains per-asset EMA state, return buffers, and the BTC
    volatility percentile distribution.

    Public interface:
        on_new_bar()           — trigger full feature recomputation
        get_momentum_scores()  — composite momentum scores for ranking
        get_regime_inputs()    — inputs for regime detection
        get_ema_values(asset)  — EMA(60), EMA(240) for trend filter
        get_asset_volatility() — realized vol for a given window
        get_all_features()     — all features for one asset (ML input)
    """

    def __init__(
        self,
        config: dict[str, Any],
        ingestion: DataIngestionManager,
    ) -> None:
        """Initialize the feature engine.

        Args:
            config: Full config.yaml as dict.
            ingestion: Initialized DataIngestionManager.
        """
        self._ingestion = ingestion
        self._config = config

        feat_config = config.get("features", {})

        # Return windows (in 1-min bars)
        self._return_windows: list[int] = feat_config.get(
            "return_windows", [5, 15, 60, 240, 720, 1440]
        )

        # EMA periods
        ema_config = feat_config.get("ema_periods", {})
        self._ema_periods: list[int] = [
            ema_config.get("fast", 20),
            ema_config.get("medium", 50),
            ema_config.get("trend_fast", 60),
            ema_config.get("trend_slow", 240),
        ]

        # Precompute EMA alphas
        self._ema_alphas: dict[int, float] = {
            p: 2.0 / (p + 1) for p in self._ema_periods
        }

        # Volatility windows
        self._vol_windows: list[int] = feat_config.get(
            "volatility_windows", [60, 240, 1440]
        )

        # Momentum composite weights
        momentum_weights = feat_config.get("momentum_composite_weights", {})
        self._momentum_weights = {
            60: momentum_weights.get("rank_1h", 0.20),
            240: momentum_weights.get("rank_4h", 0.40),
            720: momentum_weights.get("rank_12h", 0.25),
            1440: momentum_weights.get("rank_24h", 0.15),
        }

        # BTC vol percentile config
        btc_vol_days = feat_config.get("btc_vol_percentile_lookback_days", 7)
        sample_interval = feat_config.get("btc_vol_percentile_sample_interval_min", 5)
        max_samples = int(btc_vol_days * 24 * 60 / sample_interval)

        # Volume data availability
        self._volume_available: bool = config.get("api", {}).get(
            "volume_data_available", False
        )

        # ---- Per-asset state ----

        # EMA values: asset → {period: value}
        self._emas: dict[str, dict[int, float]] = {}

        # Assets whose EMAs are already current (skip update in next on_new_bar)
        self._ema_warm: set[str] = set()

        # 1-min return buffers: asset → deque of returns
        self._returns_1m: dict[str, deque] = {}

        # All computed features: asset → {feature_name: value}
        self._features: dict[str, dict[str, float]] = {}

        # ---- Cross-sectional state ----

        # Momentum scores: asset → composite score
        self._momentum_scores: dict[str, float] = {}

        # ---- Regime input state ----
        self._regime_inputs: Optional[RegimeInputs] = None

        # BTC vol percentile distribution
        self._btc_vol_samples: deque = deque(maxlen=max_samples)
        self._btc_vol_sample_counter: int = 0
        self._btc_vol_sample_interval: int = sample_interval

        # Bar counter for sampling
        self._bar_count: int = 0

    # -------------------------------------------------------------------------
    # Main entry point
    # -------------------------------------------------------------------------

    def on_new_bar(self) -> None:
        """Compute all features for the latest bar.

        Called by the orchestrator after each price update. Updates
        per-asset features, cross-sectional rankings, and regime inputs.
        """
        self._bar_count += 1

        all_assets = self._ingestion.get_all_assets()

        # Step 1: Per-asset feature computation
        for asset in all_assets:
            self._update_asset_features(asset)

        # Step 2: Cross-sectional ranking (only non-STALE assets)
        self._compute_cross_sectional(all_assets)

        # Step 3: Regime input features
        self._compute_regime_inputs(all_assets)

    # -------------------------------------------------------------------------
    # Per-asset features
    # -------------------------------------------------------------------------

    def _update_asset_features(self, asset: str) -> None:
        """Compute all per-asset features from price history.

        Args:
            asset: Asset symbol.
        """
        prices = self._ingestion.get_prices_array(asset)
        n = len(prices)
        if n < 2:
            if n == 1:
                # Initialize EMAs to first price
                self._init_emas(asset, prices[0])
            return

        current_price = prices[-1]
        prev_price = prices[-2]

        features: dict[str, float] = {}

        # Check if warm_start already processed this asset's full history.
        # If so, skip EMA update and return buffer append (already done).
        is_warm = asset in self._ema_warm
        if is_warm:
            self._ema_warm.discard(asset)
        else:
            self._update_emas(asset, current_price)
        emas = self._emas.get(asset, {})

        # -- 1-min return --
        if not is_warm:
            ret_1m = (current_price - prev_price) / prev_price
            if asset not in self._returns_1m:
                self._returns_1m[asset] = deque(maxlen=1440)
            self._returns_1m[asset].append(ret_1m)

        # -- Return features --
        for window in self._return_windows:
            if n > window:
                ref_price = prices[-(window + 1)]
                if ref_price > 0:
                    features[f"return_{window}m"] = (
                        (current_price - ref_price) / ref_price
                    )

        # -- ROC features (same values, distinct names for signal layer) --
        for window in [60, 240, 720, 1440]:
            ret_key = f"return_{window}m"
            if ret_key in features:
                features[f"roc_{window}m"] = features[ret_key]

        # -- EMA ratios --
        for period in self._ema_periods:
            if period in emas and emas[period] > 0:
                features[f"ema_{period}"] = emas[period]

        if 20 in emas and emas[20] > 0:
            features["price_ema20_ratio"] = current_price / emas[20]
        if 50 in emas and emas[50] > 0:
            features["price_ema50_ratio"] = current_price / emas[50]
        if 20 in emas and 50 in emas and emas[50] > 0:
            features["ema20_ema50_ratio"] = emas[20] / emas[50]

        # -- Volatility (std dev of 1-min returns) --
        returns_buf = self._returns_1m.get(asset, deque())
        returns_arr = np.array(returns_buf, dtype=np.float64)

        for window in self._vol_windows:
            if len(returns_arr) >= window:
                vol = float(np.std(returns_arr[-window:], ddof=1))
                features[f"vol_{window}m"] = vol

        # -- Rolling 24h high/low distance --
        if n >= 2:
            high_24h = float(np.max(prices))
            low_24h = float(np.min(prices))
            if high_24h > 0:
                features["dist_high_24h"] = (current_price - high_24h) / high_24h
            if low_24h > 0:
                features["dist_low_24h"] = (current_price - low_24h) / low_24h

        # -- Volume features (conditional) --
        if self._volume_available:
            volumes = self._ingestion.get_volumes_array(asset)
            if len(volumes) >= 2 and volumes[-1] > 0:
                # Volume ratio: current / rolling 24h average
                vol_window = min(1440, len(volumes))
                avg_vol = float(np.mean(volumes[-vol_window:]))
                if avg_vol > 0:
                    features["volume_ratio"] = volumes[-1] / avg_vol

                # Price-volume correlation (rolling 24-bar Pearson)
                corr_window = min(24, len(volumes), len(prices))
                if corr_window >= 5:
                    p_slice = prices[-corr_window:]
                    v_slice = volumes[-corr_window:]
                    if np.std(p_slice) > 0 and np.std(v_slice) > 0:
                        corr = float(np.corrcoef(p_slice, v_slice)[0, 1])
                        if np.isfinite(corr):
                            features["price_volume_corr"] = corr

        # Store current price for convenience
        features["price"] = current_price

        self._features[asset] = features

    def _init_emas(self, asset: str, price: float) -> None:
        """Initialize all EMAs to the first received price.

        Args:
            asset: Asset symbol.
            price: First price.
        """
        self._emas[asset] = {p: price for p in self._ema_periods}

    def _update_emas(self, asset: str, price: float) -> None:
        """Update EMAs incrementally with new price.

        Formula: ema_new = alpha * price + (1 - alpha) * ema_old

        Args:
            asset: Asset symbol.
            price: Current price.
        """
        if asset not in self._emas:
            self._init_emas(asset, price)
            return

        emas = self._emas[asset]
        for period in self._ema_periods:
            alpha = self._ema_alphas[period]
            old = emas.get(period, price)
            emas[period] = alpha * price + (1 - alpha) * old

    # -------------------------------------------------------------------------
    # Cross-sectional features
    # -------------------------------------------------------------------------

    def _compute_cross_sectional(self, all_assets: list[str]) -> None:
        """Compute cross-sectional rankings and momentum composite scores.

        STALE assets are excluded from ranking. Ranking is done
        separately for main pool (Tier 1-3), meme pool (Tier 4),
        and Tier 5 pool.

        Args:
            all_assets: All known asset symbols.
        """
        # Filter to non-STALE assets with return data
        ranking_assets = [
            a for a in all_assets
            if (
                self._ingestion.get_asset_status(a) != AssetStatus.STALE
                and a in self._features
            )
        ]

        if not ranking_assets:
            return

        # Return windows used in momentum composite
        rank_windows = list(self._momentum_weights.keys())

        # Compute cross-sectional return ranks across ALL non-STALE assets
        for window in rank_windows:
            ret_key = f"return_{window}m"
            returns = {}
            for asset in ranking_assets:
                val = self._features.get(asset, {}).get(ret_key)
                if val is not None:
                    returns[asset] = val

            if len(returns) < 2:
                continue

            # Percentile rank: rank / (N - 1)
            ranked = self._percentile_rank(returns)
            for asset, pct in ranked.items():
                self._features[asset][f"rank_{window}m"] = pct

        # Compute momentum composite scores
        self._momentum_scores.clear()
        for asset in ranking_assets:
            feats = self._features.get(asset, {})
            score = 0.0
            total_weight = 0.0

            for window, weight in self._momentum_weights.items():
                rank_key = f"rank_{window}m"
                rank_val = feats.get(rank_key)
                if rank_val is not None:
                    score += weight * rank_val
                    total_weight += weight

            if total_weight > 0:
                # Normalize if some windows missing (early in session)
                score = score / total_weight
                self._momentum_scores[asset] = score
                self._features[asset]["momentum_composite"] = score

        # Within-tier ranks
        self._compute_within_tier_ranks(ranking_assets)

    def _compute_within_tier_ranks(self, ranking_assets: list[str]) -> None:
        """Compute percentile ranks within each tier pool.

        Tier 1-3 ranked together. Tier 4 ranked separately.
        Tier 5 ranked separately.

        Args:
            ranking_assets: Non-STALE assets with features.
        """
        tier_pools: dict[str, list[str]] = {
            TIER_MAIN_POOL: [],
            TIER_4_MEME: [],
            TIER_5_OBSCURE: [],
        }

        for asset in ranking_assets:
            tier = self._ingestion.get_tier(asset)
            if tier in tier_pools:
                tier_pools[tier].append(asset)

        for pool_name, pool_assets in tier_pools.items():
            if len(pool_assets) < 2:
                continue

            scores = {}
            for asset in pool_assets:
                s = self._momentum_scores.get(asset)
                if s is not None:
                    scores[asset] = s

            if len(scores) < 2:
                continue

            ranked = self._percentile_rank(scores)
            for asset, pct in ranked.items():
                self._features[asset]["within_tier_rank"] = pct

    @staticmethod
    def _percentile_rank(values: dict[str, float]) -> dict[str, float]:
        """Compute percentile ranks for a set of values.

        Formula: rank / (N - 1), producing values in [0, 1].

        Args:
            values: Mapping of asset → numeric value.

        Returns:
            Mapping of asset → percentile rank in [0, 1].
        """
        if len(values) < 2:
            return {a: 0.5 for a in values}

        # Sort by value
        sorted_items = sorted(values.items(), key=lambda x: x[1])
        n = len(sorted_items)

        # Handle ties with average rank
        ranks: dict[str, float] = {}
        i = 0
        while i < n:
            # Find all items with the same value
            j = i
            while j < n and sorted_items[j][1] == sorted_items[i][1]:
                j += 1
            # Average rank for ties
            avg_rank = (i + j - 1) / 2.0
            for k in range(i, j):
                ranks[sorted_items[k][0]] = avg_rank / (n - 1)
            i = j

        return ranks

    # -------------------------------------------------------------------------
    # Regime input features
    # -------------------------------------------------------------------------

    def _compute_regime_inputs(self, all_assets: list[str]) -> None:
        """Compute regime detection input features.

        Args:
            all_assets: All known asset symbols.
        """
        btc_feats = self._features.get("BTC", {})
        btc_4h = btc_feats.get("return_240m")
        btc_24h = btc_feats.get("return_1440m")

        # BTC trend classification
        if btc_4h is not None and btc_24h is not None:
            if btc_4h > 0 and btc_24h > 0:
                btc_trend = "bullish"
            elif btc_4h < 0 and btc_24h < 0:
                btc_trend = "bearish"
            else:
                btc_trend = "neutral"
        else:
            btc_trend = "neutral"

        # Altcoin breadth: % of non-BTC/non-PAXG with positive 4h return
        altcoin_breadth = self._compute_altcoin_breadth(all_assets)

        # BTC volatility percentile
        btc_vol_pct = self._update_btc_vol_percentile()

        # Volatility ratio: BTC 1h vol / BTC 24h vol
        btc_vol_1h = btc_feats.get("vol_60m")
        btc_vol_24h = btc_feats.get("vol_1440m")
        vol_ratio = None
        if btc_vol_1h is not None and btc_vol_24h is not None and btc_vol_24h > 0:
            vol_ratio = btc_vol_1h / btc_vol_24h

        # BTC dominance proxy
        dom_1h, dom_4h = self._compute_btc_dominance(all_assets)

        self._regime_inputs = RegimeInputs(
            btc_4h_return=btc_4h,
            btc_24h_return=btc_24h,
            btc_trend=btc_trend,
            btc_vol_percentile=btc_vol_pct,
            altcoin_breadth=altcoin_breadth,
            volatility_ratio=vol_ratio,
            btc_dominance_1h=dom_1h,
            btc_dominance_4h=dom_4h,
        )

    def _compute_altcoin_breadth(self, all_assets: list[str]) -> Optional[float]:
        """Compute altcoin breadth: % of non-BTC/non-PAXG with positive 4h return.

        Args:
            all_assets: All known asset symbols.

        Returns:
            Breadth as fraction [0, 1], or None if insufficient data.
        """
        positive_count = 0
        total_count = 0

        for asset in all_assets:
            if asset in ("BTC", "PAXG"):
                continue
            if self._ingestion.get_asset_status(asset) == AssetStatus.STALE:
                continue

            ret_4h = self._features.get(asset, {}).get("return_240m")
            if ret_4h is not None:
                total_count += 1
                if ret_4h > 0:
                    positive_count += 1

        if total_count == 0:
            return None

        return positive_count / total_count

    def _update_btc_vol_percentile(self) -> Optional[float]:
        """Update BTC vol percentile distribution and return current percentile.

        Samples BTC 1h vol every btc_vol_sample_interval bars.
        Percentile is computed against the trailing distribution.

        Returns:
            Current percentile (0-100), or None if insufficient samples.
        """
        btc_vol_1h = self._features.get("BTC", {}).get("vol_60m")
        if btc_vol_1h is None:
            return None

        # Sample at configured interval (every N bars)
        if self._bar_count % self._btc_vol_sample_interval == 0:
            self._btc_vol_samples.append(btc_vol_1h)

        # Need at least a few samples for percentile
        if len(self._btc_vol_samples) < 10:
            return None

        # Compute percentile rank
        samples = np.array(self._btc_vol_samples, dtype=np.float64)
        percentile = float(np.sum(samples <= btc_vol_1h) / len(samples) * 100)

        return percentile

    def _compute_btc_dominance(
        self, all_assets: list[str]
    ) -> tuple[Optional[float], Optional[float]]:
        """Compute BTC dominance proxy for 1h and 4h windows.

        BTC return minus equal-weighted altcoin return.

        Args:
            all_assets: All known asset symbols.

        Returns:
            Tuple of (dominance_1h, dominance_4h), either may be None.
        """
        btc_feats = self._features.get("BTC", {})
        btc_1h = btc_feats.get("return_60m")
        btc_4h = btc_feats.get("return_240m")

        alt_returns_1h: list[float] = []
        alt_returns_4h: list[float] = []

        for asset in all_assets:
            if asset in ("BTC", "PAXG"):
                continue
            if self._ingestion.get_asset_status(asset) == AssetStatus.STALE:
                continue

            feats = self._features.get(asset, {})
            r1h = feats.get("return_60m")
            r4h = feats.get("return_240m")
            if r1h is not None:
                alt_returns_1h.append(r1h)
            if r4h is not None:
                alt_returns_4h.append(r4h)

        dom_1h = None
        dom_4h = None

        if btc_1h is not None and alt_returns_1h:
            avg_alt_1h = sum(alt_returns_1h) / len(alt_returns_1h)
            dom_1h = btc_1h - avg_alt_1h

        if btc_4h is not None and alt_returns_4h:
            avg_alt_4h = sum(alt_returns_4h) / len(alt_returns_4h)
            dom_4h = btc_4h - avg_alt_4h

        return dom_1h, dom_4h

    # -------------------------------------------------------------------------
    # Public interface
    # -------------------------------------------------------------------------

    def get_momentum_scores(self) -> dict[str, float]:
        """Get composite momentum scores for all non-STALE assets.

        Returns:
            Dict mapping asset → composite momentum score in [0, 1].
        """
        return dict(self._momentum_scores)

    def get_regime_inputs(self) -> RegimeInputs:
        """Get current regime detection inputs.

        Returns:
            RegimeInputs dataclass.

        Raises:
            RuntimeError: If regime inputs have not been computed yet.
        """
        if self._regime_inputs is None:
            raise RuntimeError(
                "Regime inputs not yet computed — call on_new_bar() first"
            )
        return self._regime_inputs

    def get_ema_values(self, asset: str) -> tuple[Optional[float], Optional[float]]:
        """Get EMA(60) and EMA(240) for the trend filter.

        Args:
            asset: Asset symbol.

        Returns:
            Tuple of (ema_60, ema_240). Either may be None if not computed.
        """
        emas = self._emas.get(asset, {})
        return emas.get(60), emas.get(240)

    def get_asset_volatility(self, asset: str, window: str) -> Optional[float]:
        """Get realized volatility for an asset and window.

        Args:
            asset: Asset symbol.
            window: Volatility window ("1h", "4h", "24h").

        Returns:
            Realized vol (std dev of 1-min returns), or None.
        """
        window_map = {"1h": 60, "4h": 240, "24h": 1440}
        bars = window_map.get(window)
        if bars is None:
            return None

        return self._features.get(asset, {}).get(f"vol_{bars}m")

    def get_all_features(self, asset: str) -> dict[str, Optional[float]]:
        """Get all computed features for one asset.

        Used by the ML model (Phase 3) for inference.

        Args:
            asset: Asset symbol.

        Returns:
            Dict of feature name → value. Values are never NaN or Inf;
            missing features are None.
        """
        raw = self._features.get(asset, {})

        # Sanitize: replace NaN/Inf with None
        clean: dict[str, Optional[float]] = {}
        for key, val in raw.items():
            if val is not None and np.isfinite(val):
                clean[key] = val
            else:
                clean[key] = None

        return clean

    def get_return(self, asset: str, window: int) -> Optional[float]:
        """Get return for a specific window.

        Args:
            asset: Asset symbol.
            window: Window in minutes (5, 15, 60, 240, 720, 1440).

        Returns:
            Return value, or None if not computed.
        """
        return self._features.get(asset, {}).get(f"return_{window}m")

    def get_feature(self, asset: str, feature_name: str) -> Optional[float]:
        """Get a single named feature for an asset.

        Args:
            asset: Asset symbol.
            feature_name: Feature key.

        Returns:
            Feature value, or None.
        """
        val = self._features.get(asset, {}).get(feature_name)
        if val is not None and np.isfinite(val):
            return val
        return None

    @property
    def bar_count(self) -> int:
        """Number of bars processed since startup."""
        return self._bar_count

    def warm_start(self) -> None:
        """Recompute EMAs from recovered price history after crash recovery.

        Call this after loading Parquet backup data into the ingestion
        manager's price buffers.
        """
        for asset in self._ingestion.get_all_assets():
            prices = self._ingestion.get_prices_array(asset)
            if len(prices) == 0:
                continue

            # Reinitialize EMAs from full price history
            self._init_emas(asset, prices[0])
            for price in prices[1:]:
                self._update_emas(asset, price)

            # Rebuild 1-min returns buffer
            self._returns_1m[asset] = deque(maxlen=1440)
            for i in range(1, len(prices)):
                if prices[i - 1] > 0:
                    ret = (prices[i] - prices[i - 1]) / prices[i - 1]
                    self._returns_1m[asset].append(ret)

            # Mark asset so on_new_bar skips redundant EMA/return update
            self._ema_warm.add(asset)

        # Run full feature computation (EMAs + returns already built)
        self.on_new_bar()
        logger.info(
            "WARM_START recomputed EMAs and features for %d assets",
            len(self._ingestion.get_all_assets()),
        )
