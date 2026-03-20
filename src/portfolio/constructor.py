"""Portfolio construction — regime-conditional target weight computation (Layer 5).

Integrates: regime detection → exposure targeting → momentum ranking →
trend penalty → position sizing → meme pool → PAXG → turnover control.

Each step is a private method. The construct() method reads as a narrative
of the strategy for Screen 4 judges.

Phase 2: inverse-volatility sizing, regime-aware exposure, meme pool,
PAXG allocation from cash buffer, endgame de-risking, turnover constraints.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from src.regime.regime_state import RegimeType
from src.utils.validation import validate_momentum_scores

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ConstructionResult:
    """Output of the portfolio construction pipeline.

    Attributes:
        target_weights: {symbol: target_weight} where weights sum to <= 1.0.
        orders: Simple order dicts describing changes needed.
        metadata: Regime, exposure, decisions for logging and audit.
    """

    target_weights: dict[str, float]
    orders: list[dict]
    metadata: dict


# ---------------------------------------------------------------------------
# Portfolio Constructor
# ---------------------------------------------------------------------------

class PortfolioConstructor:
    """Regime-conditional portfolio construction.

    Integrates: regime detection → exposure targeting → momentum ranking →
    trend penalty → position sizing → meme pool → PAXG → turnover control.

    Each step is a private method. The construct() method reads as a
    narrative of the strategy for Screen 4 judges.
    """

    def __init__(
        self,
        config: dict,
        regime_detector: Any,
        trend_penalty: Any,
        meme_pool: Any,
        endgame: Any,
        paxg: Any,
        risk_manager: Any = None,
    ) -> None:
        """Dependency injection. All Phase 2 components passed by reference.

        Args:
            config: Full config dict (or Config wrapper).
            regime_detector: RegimeDetector instance for current_regime.
            trend_penalty: TrendPenaltyEngine for score adjustments.
            meme_pool: MemePoolManager for meme coin selections.
            endgame: EndgameManager for time-based de-risking.
            paxg: PAXGAllocator for PAXG target weights.
            risk_manager: RiskManager (optional, for risk-exit bypass).
        """
        self.regime_detector = regime_detector
        self.trend_penalty = trend_penalty
        self.meme_pool = meme_pool
        self.endgame = endgame
        self.paxg = paxg
        self.risk_manager = risk_manager

        # Read config (support both raw dict and Config wrapper)
        self._cfg = config
        self._read_config(config)

        # Track last construction for explain()
        self._last_metadata: dict = {}

    def _read_config(self, config: Any) -> None:
        """Load all parameters from config. No magic numbers."""
        _get = self._cfg_get

        # Exposure targets by regime
        self._exposure_targets: dict[str, float] = {
            "TREND_BULL": _get("portfolio.exposure.TREND_BULL", 0.80),
            "TREND_BULL_RISING_VOL": _get("portfolio.exposure.TREND_BULL_RISING_VOL", 0.65),
            "MEAN_REVERT": _get("portfolio.exposure.MEAN_REVERT", 0.55),
            "TREND_BEAR": _get("portfolio.exposure.TREND_BEAR", 0.35),
            "HIGH_VOL_CRISIS": _get("portfolio.exposure.HIGH_VOL_CRISIS", 0.15),
        }

        # Also support Phase 1 config layout as fallback
        regime_targets = _get("portfolio.regime_targets", {})
        if regime_targets and "TREND_BULL" not in self._exposure_targets:
            self._exposure_targets["TREND_BULL"] = regime_targets.get(
                "trend_bull_low_vol", 0.80
            )

        # Max holdings by regime
        self._holdings: dict[str, int] = {
            "TREND_BULL": _get("portfolio.holdings.TREND_BULL", 10),
            "MEAN_REVERT": _get("portfolio.holdings.MEAN_REVERT", 6),
            "TREND_BEAR": _get("portfolio.holdings.TREND_BEAR", 4),
            "HIGH_VOL_CRISIS": _get("portfolio.holdings.HIGH_VOL_CRISIS", 0),
        }

        # Hard cap on total crypto exposure
        self._max_crypto_exposure: float = _get("portfolio.max_crypto_exposure", 0.90)

        # BTC vol threshold for BULL sub-regime
        self._btc_vol_threshold: float = _get(
            "portfolio.btc_vol_high_vol_threshold", 70.0
        )

        # Turnover and trade thresholds
        self._max_turnover: float = _get("portfolio.turnover_max_pct",
                                          _get("portfolio.max_turnover_per_rebalance", 0.25))
        self._min_trade: float = _get("portfolio.min_trade_nav_pct",
                                       _get("portfolio.min_trade_threshold_pct_nav", 0.002))

        # Tier caps
        tier_caps_cfg = _get("portfolio.tier_caps", {})
        self._tier_cap_defaults: dict[str, float] = {
            "tier1": tier_caps_cfg.get("tier1", _get("tier_caps.tier_1_2", 0.08)),
            "tier2": tier_caps_cfg.get("tier2", _get("tier_caps.tier_1_2", 0.08)),
            "tier3": tier_caps_cfg.get("tier3", _get("tier_caps.tier_3", 0.06)),
            "tier4_meme": tier_caps_cfg.get("tier4_meme", _get("tier_caps.tier_4_meme", 0.03)),
            "tier5": tier_caps_cfg.get("tier5", _get("tier_caps.tier_5_obscure", 0.02)),
            # Combined tier_1_2 key for backward compat
            "tier_1_2": _get("tier_caps.tier_1_2", 0.08),
            "tier_3": _get("tier_caps.tier_3", 0.06),
            "tier_4_meme": _get("tier_caps.tier_4_meme", 0.03),
            "tier_5_obscure": _get("tier_caps.tier_5_obscure", 0.02),
        }

        # Per-asset cap overrides
        self._asset_cap_overrides: dict[str, float] = {
            "DOGE": tier_caps_cfg.get("tier1_doge", _get("tier_caps.doge", 0.05)),
            "TRUMP": tier_caps_cfg.get("trump", _get("tier_caps.trump", 0.02)),
            "PAXG": tier_caps_cfg.get("paxg", _get("tier_caps.paxg", 0.15)),
        }

        # Asset-to-tier mapping (built from universe config)
        self._asset_tier_map: dict[str, str] = self._build_tier_map(config)

        # Redistribution iterations
        self._max_redist_iter: int = _get("tier_caps.redistribution_max_iterations", 5)

        # Vol filter multiplier
        self._vol_filter_multiplier: float = _get(
            "signals.vol_exclusion_multiplier", 2.0
        )

    def _cfg_get(self, key: str, default: Any = None) -> Any:
        """Get config value supporting both dict and Config wrapper."""
        # For Config wrapper objects (non-dict) with dot-path .get()
        if not isinstance(self._cfg, dict) and hasattr(self._cfg, 'get'):
            try:
                val = self._cfg.get(key, default)
                if val is not None:
                    return val
            except (KeyError, TypeError):
                pass

        # Raw dict: traverse nested keys via dot-path
        if isinstance(self._cfg, dict):
            parts = key.split(".")
            node = self._cfg
            for part in parts:
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return default
            return node

        return default

    def _build_tier_map(self, config: Any) -> dict[str, str]:
        """Build asset → tier mapping from universe config."""
        tier_map: dict[str, str] = {}
        _get = self._cfg_get

        for asset in _get("universe.tier_1_majors", []):
            tier_map[asset] = "tier_1_2"
        for asset in _get("universe.tier_2_large_alts", []):
            tier_map[asset] = "tier_1_2"
        for asset in _get("universe.tier_3_defi", []):
            tier_map[asset] = "tier_3"
        for asset in _get("universe.tier_4_meme", []):
            tier_map[asset] = "tier_4_meme"
        for asset in _get("universe.tier_5_obscure", []):
            tier_map[asset] = "tier_5_obscure"

        tier_map["PAXG"] = "special"
        tier_map["TRUMP"] = "special"
        return tier_map

    # ==================================================================
    # Main entry point
    # ==================================================================

    def construct(
        self,
        momentum_scores: dict[str, float],
        current_positions: dict[str, float],
        nav: float,
        market_data: Any = None,
    ) -> ConstructionResult:
        """Execute the full construction pipeline.

        Called every rebalance (~60 minutes). Each step is a private
        method with a descriptive name so judges can follow the logic.

        Args:
            momentum_scores: {symbol: composite_score} from signal layer.
            current_positions: {symbol: current_weight} from position tracker.
            nav: Current portfolio NAV in USD.
            market_data: FeatureEngine or equivalent for vol data.

        Returns:
            ConstructionResult with target_weights, orders, and metadata.
        """
        # ---- Guard: zero or negative NAV ----
        if nav <= 0:
            logger.error("PORTFOLIO: NAV=%.2f is non-positive, returning empty portfolio", nav)
            return ConstructionResult(
                target_weights={},
                orders=[],
                metadata={"error": "non_positive_nav", "nav": nav},
            )

        # ---- Guard: no momentum scores (data outage) ----
        if not momentum_scores:
            logger.warning(
                "PORTFOLIO: no momentum scores available, holding current positions"
            )
            return ConstructionResult(
                target_weights=dict(current_positions),
                orders=[],
                metadata={"reason": "no_momentum_scores", "action": "hold_current"},
            )

        # Clean NaN scores
        momentum_scores = validate_momentum_scores(momentum_scores)
        if not momentum_scores:
            logger.warning(
                "PORTFOLIO: all momentum scores invalid after validation, holding current"
            )
            return ConstructionResult(
                target_weights=dict(current_positions),
                orders=[],
                metadata={"reason": "all_scores_invalid", "action": "hold_current"},
            )

        # ----------------------------------------------------------
        # Step 1: Get current regime
        # ----------------------------------------------------------
        regime = self.regime_detector.current_regime
        logger.info("PORTFOLIO Step 1: regime=%s", regime.value)

        # ----------------------------------------------------------
        # Step 2: Determine exposure target for this regime
        # ----------------------------------------------------------
        target_exposure = self._get_target_exposure(regime)
        logger.info(
            "PORTFOLIO Step 2: target_exposure=%.1f%% for %s",
            target_exposure * 100, regime.value,
        )

        # ----------------------------------------------------------
        # Step 3: Apply endgame constraints
        # ----------------------------------------------------------
        endgame_constraints = self.endgame.get_constraints()
        target_exposure = min(target_exposure, endgame_constraints["max_exposure"])

        if endgame_constraints["sell_all"]:
            logger.critical(
                "PORTFOLIO Step 3: ENDGAME SELL_ALL — %.1fh remaining",
                endgame_constraints["hours_remaining"],
            )
            return self._force_liquidation(current_positions, nav)

        logger.info(
            "PORTFOLIO Step 3: endgame max_exposure=%.1f%%, effective=%.1f%%",
            endgame_constraints["max_exposure"] * 100,
            target_exposure * 100,
        )

        # ----------------------------------------------------------
        # Step 4: Apply trend penalties to momentum scores
        # ----------------------------------------------------------
        adjusted_scores = self.trend_penalty.apply_penalties(momentum_scores)
        logger.info(
            "PORTFOLIO Step 4: trend penalties applied to %d scores",
            len(adjusted_scores),
        )

        # ----------------------------------------------------------
        # Step 5: Rank and select top N assets
        # ----------------------------------------------------------
        max_holdings = self._get_max_holdings(regime)
        candidates = self._rank_and_select(adjusted_scores, max_holdings)
        logger.info(
            "PORTFOLIO Step 5: selected %d/%d candidates (max=%d)",
            len(candidates), len(adjusted_scores), max_holdings,
        )

        # ----------------------------------------------------------
        # Step 6: Apply volatility exclusion filter
        # ----------------------------------------------------------
        candidates_pre_filter = list(candidates)
        candidates = self._apply_vol_filter(candidates, market_data)
        filtered_out = len(candidates_pre_filter) - len(candidates)
        if filtered_out > 0:
            logger.info(
                "PORTFOLIO Step 6: vol filter removed %d candidates", filtered_out
            )

        # ----------------------------------------------------------
        # Step 7: Handle edge case — vol filter removed all candidates
        # ----------------------------------------------------------
        if not candidates and target_exposure > 0:
            fallback_n = min(3, max_holdings) if max_holdings > 0 else 0
            if fallback_n > 0:
                candidates = self._rank_and_select(adjusted_scores, fallback_n)
                logger.warning(
                    "PORTFOLIO Step 7: VOL_FILTER removed all candidates, "
                    "falling back to top %d by raw momentum",
                    len(candidates),
                )

        # ----------------------------------------------------------
        # Step 8: Size positions (inverse-vol with equal-weight fallback)
        # ----------------------------------------------------------
        crypto_weights = self._size_positions(
            candidates, target_exposure, market_data
        )
        logger.info(
            "PORTFOLIO Step 8: sized %d positions, total=%.1f%%",
            len(crypto_weights), sum(crypto_weights.values()) * 100,
        )

        # ----------------------------------------------------------
        # Step 9: Apply tier caps and redistribute excess
        # ----------------------------------------------------------
        crypto_weights = self._apply_tier_caps(crypto_weights)
        logger.info(
            "PORTFOLIO Step 9: tier caps applied, total=%.1f%%",
            sum(crypto_weights.values()) * 100,
        )

        # ----------------------------------------------------------
        # Step 10: Get meme pool allocations
        # ----------------------------------------------------------
        meme_weights = self._get_meme_allocations(momentum_scores, regime)
        logger.info(
            "PORTFOLIO Step 10: meme allocations=%d coins, total=%.1f%%",
            len(meme_weights), sum(meme_weights.values()) * 100,
        )

        # ----------------------------------------------------------
        # Step 11: Get PAXG target
        # ----------------------------------------------------------
        paxg_weight = self.paxg.get_target_weight(regime)
        logger.info("PORTFOLIO Step 11: PAXG target=%.1f%%", paxg_weight * 100)

        # ----------------------------------------------------------
        # Step 12: Combine and validate total doesn't exceed 1.0
        # ----------------------------------------------------------
        target_weights = self._combine_weights(
            crypto_weights, meme_weights, paxg_weight, target_exposure
        )
        logger.info(
            "PORTFOLIO Step 12: combined total=%.1f%% (%d assets)",
            sum(target_weights.values()) * 100, len(target_weights),
        )

        # ----------------------------------------------------------
        # Step 13: Apply turnover constraint
        # ----------------------------------------------------------
        target_weights = self._apply_turnover_cap(
            target_weights, current_positions
        )
        logger.info(
            "PORTFOLIO Step 13: after turnover cap, total=%.1f%%",
            sum(target_weights.values()) * 100,
        )

        # ----------------------------------------------------------
        # Step 14: Suppress small trades
        # ----------------------------------------------------------
        target_weights = self._suppress_small_trades(
            target_weights, current_positions, nav
        )

        # ----------------------------------------------------------
        # Step 15: Compute orders
        # ----------------------------------------------------------
        orders = self._compute_orders(target_weights, current_positions, nav)

        # Build metadata for logging and audit
        metadata = {
            "regime": regime.value,
            "target_exposure": target_exposure,
            "endgame_tier": endgame_constraints.get("tier", -1),
            "endgame_hours_remaining": endgame_constraints.get("hours_remaining"),
            "max_holdings": max_holdings,
            "candidates_selected": len(candidates),
            "vol_filtered_out": filtered_out,
            "crypto_exposure": sum(
                w for a, w in target_weights.items() if a != "PAXG"
            ),
            "paxg_weight": target_weights.get("PAXG", 0.0),
            "meme_count": len(meme_weights),
            "total_weight": sum(target_weights.values()),
            "order_count": len(orders),
        }
        self._last_metadata = metadata

        logger.info(
            "PORTFOLIO COMPLETE: regime=%s exposure=%.1f%% holdings=%d "
            "orders=%d total=%.1f%%",
            regime.value,
            metadata["crypto_exposure"] * 100,
            len(target_weights),
            len(orders),
            metadata["total_weight"] * 100,
        )

        return ConstructionResult(
            target_weights=target_weights,
            orders=orders,
            metadata=metadata,
        )

    # ==================================================================
    # Private pipeline methods
    # ==================================================================

    def _get_target_exposure(self, regime: RegimeType) -> float:
        """Determine crypto exposure fraction from current regime."""
        regime_key = regime.value

        # Check for rising-vol sub-regime within TREND_BULL
        if regime == RegimeType.TREND_BULL:
            btc_vol = self.regime_detector.state.btc_vol_percentile
            if btc_vol > self._btc_vol_threshold:
                regime_key = "TREND_BULL_RISING_VOL"

        target = self._exposure_targets.get(regime_key, 0.55)
        return min(target, self._max_crypto_exposure)

    def _get_max_holdings(self, regime: RegimeType) -> int:
        """Max number of crypto holdings for this regime."""
        return self._holdings.get(regime.value, 6)

    def _rank_and_select(
        self,
        scores: dict[str, float],
        max_n: int,
    ) -> list[str]:
        """Rank assets by momentum score and select top N."""
        if max_n <= 0:
            return []
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [asset for asset, _ in ranked[:max_n]]

    def _apply_vol_filter(
        self,
        candidates: list[str],
        market_data: Any,
    ) -> list[str]:
        """Remove candidates whose 1h vol exceeds threshold × median."""
        if not candidates or market_data is None:
            return candidates

        # Collect 1h vols for all candidates
        vols: dict[str, float] = {}
        for asset in candidates:
            vol = None
            if hasattr(market_data, "get_asset_volatility"):
                vol = market_data.get_asset_volatility(asset, "1h")
            if vol is not None and vol > 0:
                vols[asset] = vol

        if len(vols) < 2:
            return candidates  # Not enough data to compute meaningful median

        sorted_vols = sorted(vols.values())
        median_vol = sorted_vols[len(sorted_vols) // 2]
        threshold = median_vol * self._vol_filter_multiplier

        filtered = [
            asset for asset in candidates
            if asset not in vols or vols[asset] <= threshold
        ]
        return filtered

    def _size_positions(
        self,
        candidates: list[str],
        deployment: float,
        market_data: Any = None,
    ) -> dict[str, float]:
        """Size positions using inverse-volatility weighting.

        Falls back to equal weight if volatility data is unavailable.
        """
        if not candidates or deployment <= 0:
            return {}

        # Try inverse-vol sizing
        vols: dict[str, float] = {}
        if market_data is not None and hasattr(market_data, "get_asset_volatility"):
            for asset in candidates:
                vol = market_data.get_asset_volatility(asset, "24h")
                if vol is not None and vol > 0:
                    vols[asset] = vol

        if len(vols) == len(candidates) and len(vols) > 0:
            return self._inverse_vol_weights(candidates, deployment, vols)

        # Equal-weight fallback
        n = len(candidates)
        base = deployment / n
        return {asset: base for asset in candidates}

    def _inverse_vol_weights(
        self,
        candidates: list[str],
        deployment: float,
        vols: dict[str, float],
    ) -> dict[str, float]:
        """Compute inverse-volatility weights normalized to deployment."""
        inv_vols = {}
        for asset in candidates:
            vol = vols.get(asset, 0.01)
            if vol <= 0:
                vol = 0.01
            inv_vols[asset] = 1.0 / vol

        total_inv = sum(inv_vols.values())
        if total_inv <= 0:
            n = len(candidates)
            return {asset: deployment / n for asset in candidates}

        return {
            asset: (inv_vols[asset] / total_inv) * deployment
            for asset in candidates
        }

    def _apply_tier_caps(self, weights: dict[str, float]) -> dict[str, float]:
        """Apply per-asset tier caps with iterative redistribution.

        Excess from capped positions is redistributed pro-rata to uncapped.
        """
        capped = dict(weights)

        for _ in range(self._max_redist_iter):
            excess = 0.0
            capped_assets: set[str] = set()

            for asset, weight in capped.items():
                cap = self._get_cap(asset)
                if weight > cap:
                    excess += weight - cap
                    capped[asset] = cap
                    capped_assets.add(asset)

            if excess <= 1e-8:
                break

            uncapped = [
                a for a in capped
                if a not in capped_assets and capped[a] > 0
            ]
            if not uncapped:
                break

            # Pro-rata redistribution
            uncapped_total = sum(capped[a] for a in uncapped)
            for asset in uncapped:
                if uncapped_total > 0:
                    share = capped[asset] / uncapped_total
                    capped[asset] += excess * share
                else:
                    capped[asset] += excess / len(uncapped)

        return capped

    def _get_meme_allocations(
        self,
        momentum_scores: dict[str, float],
        regime: RegimeType,
    ) -> dict[str, float]:
        """Get meme pool allocations. Only active in configured regimes."""
        allocations = self.meme_pool.rank_and_select(
            momentum_scores, regime.value
        )
        return {a["symbol"]: a["weight"] for a in allocations}

    def _combine_weights(
        self,
        crypto_weights: dict[str, float],
        meme_weights: dict[str, float],
        paxg_weight: float,
        target_exposure: float,
    ) -> dict[str, float]:
        """Combine crypto, meme, and PAXG weights enforcing total <= 1.0.

        Meme allocations come FROM the crypto exposure budget.
        PAXG comes from the cash buffer (does not count toward crypto).

        Priority if over: reduce crypto first, then meme. PAXG last.
        """
        combined: dict[str, float] = {}

        # Meme comes from crypto budget
        meme_total = sum(meme_weights.values())
        crypto_budget = max(0.0, target_exposure - meme_total)

        # Scale crypto weights to fit within reduced budget
        crypto_total = sum(crypto_weights.values())
        if crypto_total > crypto_budget and crypto_total > 0:
            scale = crypto_budget / crypto_total
            crypto_weights = {a: w * scale for a, w in crypto_weights.items()}

        # Merge crypto weights
        for asset, weight in crypto_weights.items():
            if weight > 1e-6:
                combined[asset] = weight

        # Merge meme weights (no double-counting with crypto)
        for asset, weight in meme_weights.items():
            if weight > 1e-6:
                combined[asset] = weight

        # Add PAXG from cash buffer
        if paxg_weight > 1e-6:
            combined["PAXG"] = paxg_weight

        # Final validation: total must not exceed 1.0
        total = sum(combined.values())
        if total > 1.0:
            # Reduce crypto proportionally first
            crypto_assets = [
                a for a in combined
                if a != "PAXG" and a not in meme_weights
            ]
            overshoot = total - 1.0

            crypto_sum = sum(combined[a] for a in crypto_assets)
            if crypto_sum >= overshoot:
                scale = (crypto_sum - overshoot) / crypto_sum if crypto_sum > 0 else 0
                for asset in crypto_assets:
                    combined[asset] *= scale
            else:
                # Zero out crypto, then reduce meme
                for asset in crypto_assets:
                    combined[asset] = 0.0
                remaining_overshoot = overshoot - crypto_sum
                meme_assets = [a for a in combined if a in meme_weights]
                meme_sum = sum(combined[a] for a in meme_assets)
                if meme_sum > 0 and remaining_overshoot > 0:
                    scale = max(0, (meme_sum - remaining_overshoot) / meme_sum)
                    for asset in meme_assets:
                        combined[asset] *= scale

        # Remove zeroed entries
        combined = {a: w for a, w in combined.items() if w > 1e-6}
        return combined

    def _apply_turnover_cap(
        self,
        target_weights: dict[str, float],
        current_weights: dict[str, float],
    ) -> dict[str, float]:
        """Enforce maximum one-way turnover per rebalance.

        If turnover exceeds max, scale down all weight changes proportionally.
        """
        all_assets = set(target_weights.keys()) | set(current_weights.keys())

        # Compute one-way turnover
        turnover = 0.0
        for asset in all_assets:
            target = target_weights.get(asset, 0.0)
            current = current_weights.get(asset, 0.0)
            turnover += abs(target - current)
        turnover /= 2.0

        if turnover <= self._max_turnover:
            return target_weights

        # Scale down all changes proportionally
        scale = self._max_turnover / turnover
        constrained: dict[str, float] = {}

        for asset in all_assets:
            current = current_weights.get(asset, 0.0)
            target = target_weights.get(asset, 0.0)
            change = target - current
            constrained[asset] = current + change * scale

        logger.info(
            "TURNOVER constrained: raw=%.1f%% -> cap=%.1f%%",
            turnover * 100, self._max_turnover * 100,
        )

        return {a: w for a, w in constrained.items() if w > 1e-6}

    def _suppress_small_trades(
        self,
        target_weights: dict[str, float],
        current_weights: dict[str, float],
        nav: float,
    ) -> dict[str, float]:
        """Suppress trades smaller than minimum threshold."""
        result: dict[str, float] = {}
        all_assets = set(target_weights.keys()) | set(current_weights.keys())

        for asset in all_assets:
            target = target_weights.get(asset, 0.0)
            current = current_weights.get(asset, 0.0)

            if abs(target - current) < self._min_trade:
                result[asset] = current
            else:
                result[asset] = target

        return {a: w for a, w in result.items() if w > 1e-6}

    def _compute_orders(
        self,
        target_weights: dict[str, float],
        current_weights: dict[str, float],
        nav: float,
    ) -> list[dict]:
        """Compute order dicts from weight differences."""
        orders: list[dict] = []
        all_assets = set(target_weights.keys()) | set(current_weights.keys())

        for asset in all_assets:
            target = target_weights.get(asset, 0.0)
            current = current_weights.get(asset, 0.0)
            delta = target - current

            if abs(delta) < 1e-6:
                continue

            orders.append({
                "asset": asset,
                "side": "BUY" if delta > 0 else "SELL",
                "weight_change": delta,
                "usd_amount": abs(delta * nav),
                "target_weight": target,
            })

        return orders

    def _force_liquidation(
        self,
        current_positions: dict[str, float],
        nav: float,
    ) -> ConstructionResult:
        """Return empty portfolio for endgame sell-all."""
        orders = []
        for asset, weight in current_positions.items():
            if weight > 1e-6:
                orders.append({
                    "asset": asset,
                    "side": "SELL",
                    "weight_change": -weight,
                    "usd_amount": weight * nav,
                    "target_weight": 0.0,
                })

        return ConstructionResult(
            target_weights={},
            orders=orders,
            metadata={
                "reason": "endgame_sell_all",
                "regime": self.regime_detector.current_regime.value,
                "liquidated_positions": len(orders),
            },
        )

    def _get_cap(self, asset: str) -> float:
        """Look up tier cap for an asset."""
        if asset in self._asset_cap_overrides:
            return self._asset_cap_overrides[asset]
        tier = self._asset_tier_map.get(asset, "tier_1_2")
        return self._tier_cap_defaults.get(tier, 0.08)

    # ==================================================================
    # Explain (for judges and debugging)
    # ==================================================================

    def explain(self) -> str:
        """Return a human-readable summary of the last construction."""
        m = self._last_metadata
        if not m:
            return "No portfolio construction has been run yet."

        return (
            f"Portfolio Construction Summary\n"
            f"{'=' * 40}\n"
            f"Regime:              {m.get('regime', 'N/A')}\n"
            f"Target Exposure:     {m.get('target_exposure', 0) * 100:.1f}%\n"
            f"Crypto Exposure:     {m.get('crypto_exposure', 0) * 100:.1f}%\n"
            f"PAXG Weight:         {m.get('paxg_weight', 0) * 100:.1f}%\n"
            f"Holdings:            {m.get('candidates_selected', 0)}\n"
            f"Meme Coins:          {m.get('meme_count', 0)}\n"
            f"Vol-Filtered Out:    {m.get('vol_filtered_out', 0)}\n"
            f"Endgame Tier:        {m.get('endgame_tier', -1)}\n"
            f"Hours Remaining:     {m.get('endgame_hours_remaining', 'N/A')}\n"
            f"Total Weight:        {m.get('total_weight', 0) * 100:.1f}%\n"
            f"Orders:              {m.get('order_count', 0)}\n"
        )
