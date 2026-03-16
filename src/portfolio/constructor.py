"""Portfolio construction — target weight computation (Layer 5).

Computes TargetPortfolio: a dict mapping each asset to a target weight
(0.0–1.0) where weights sum to <= 1.0 (remainder is cash).

Steps implemented:
  1. Deployment target (regime-conditional)
  1b. Adaptive exposure adjustment (Phase 2+)
  1c. End-game de-risking cap (hardcoded)
  2. PAXG allocation (from cash buffer)
  3. Per-asset weight computation (arithmetic — no optimizer)
  4. Turnover constraint
  5. Minimum trade threshold
  6. BTC beta monitoring (Phase 2+, logged only)

Phase 1: equal-weight sizing + tier caps. No vol adjustment, no PAXG,
no meme/Tier5 pools, no adaptive exposure. Simple T-1h sell-all.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from src.risk.risk_event import EventType, RiskEvent, Severity

logger = logging.getLogger(__name__)


class PortfolioConstructor:
    """Computes target portfolio weights from signals and regime.

    Usage::

        constructor = PortfolioConstructor(config)
        target_weights, risk_events = constructor.compute(
            regime_state=regime,
            selected_assets=["BTC", "ETH", ...],
            current_weights={"BTC": 0.08, ...},
            current_nav=1_000_000,
        )

    Attributes:
        regime_targets: Deployment targets per regime.
        tier_caps: Per-asset/tier weight caps.
        max_crypto_exposure: Hard cap on total crypto exposure.
    """

    def __init__(
        self,
        regime_targets: dict[str, float],
        tier_caps: dict[str, float],
        asset_tier_map: dict[str, str],
        tier_cap_defaults: dict[str, float],
        btc_vol_high_vol_threshold: float = 70.0,
        max_crypto_exposure: float = 0.90,
        max_turnover: float = 0.25,
        min_trade_threshold: float = 0.002,
        paxg_allocation: Optional[dict[str, float]] = None,
        endgame_schedule: Optional[list[dict]] = None,
        final_sell_minutes: float = 15.0,
        competition_end_utc: Optional[datetime] = None,
        redistribution_max_iterations: int = 5,
    ) -> None:
        """Initialize portfolio constructor from config.

        Args:
            regime_targets: Map of regime key -> deployment fraction.
                Keys: trend_bull_low_vol, trend_bull_high_vol, mean_revert,
                      trend_bear, crisis.
            tier_caps: Per-asset cap overrides (e.g., {"DOGE": 0.05}).
            asset_tier_map: Map of asset -> tier key.
            tier_cap_defaults: Map of tier key -> default cap.
            btc_vol_high_vol_threshold: BTC vol percentile above which
                TREND_BULL uses high_vol target.
            max_crypto_exposure: Hard cap on total crypto exposure.
            max_turnover: Maximum one-way turnover per rebalance.
            min_trade_threshold: Minimum weight change to trigger a trade.
            paxg_allocation: Map of regime -> PAXG allocation fraction.
            endgame_schedule: End-game de-risking schedule from config.
            final_sell_minutes: Minutes before competition end to sell all.
            competition_end_utc: Competition end timestamp.
            redistribution_max_iterations: Max cap redistribution iterations.
        """
        self._regime_targets = regime_targets
        self._tier_caps = tier_caps
        self._asset_tier_map = asset_tier_map
        self._tier_cap_defaults = tier_cap_defaults
        self._vol_threshold = btc_vol_high_vol_threshold
        self._max_exposure = max_crypto_exposure
        self._max_turnover = max_turnover
        self._min_trade = min_trade_threshold
        self._paxg_alloc = paxg_allocation or {}
        self._endgame = endgame_schedule or []
        self._final_sell_min = final_sell_minutes
        self._comp_end = competition_end_utc
        self._max_redist_iter = redistribution_max_iterations

    # ------------------------------------------------------------------
    # Step 1: Deployment target
    # ------------------------------------------------------------------

    def get_deployment_target(
        self,
        regime: str,
        btc_vol_percentile: float = 50.0,
    ) -> float:
        """Determine crypto deployment fraction from regime state.

        Args:
            regime: Current regime string (TREND_BULL, TREND_BEAR,
                MEAN_REVERT, HIGH_VOL_CRISIS).
            btc_vol_percentile: BTC volatility percentile (0-100).

        Returns:
            Target crypto deployment as fraction of NAV.
        """
        if regime == "TREND_BULL":
            if btc_vol_percentile > self._vol_threshold:
                key = "trend_bull_high_vol"
            else:
                key = "trend_bull_low_vol"
        elif regime == "MEAN_REVERT":
            key = "mean_revert"
        elif regime == "TREND_BEAR":
            key = "trend_bear"
        elif regime == "HIGH_VOL_CRISIS":
            key = "crisis"
        else:
            logger.warning("Unknown regime '%s', defaulting to mean_revert", regime)
            key = "mean_revert"

        target = self._regime_targets.get(key, 0.55)
        logger.debug(
            "Deployment target: regime=%s vol_pctile=%.0f -> %.2f%%",
            regime, btc_vol_percentile, target * 100,
        )
        return target

    # ------------------------------------------------------------------
    # Step 1c: End-game de-risking
    # ------------------------------------------------------------------

    def get_endgame_cap(self) -> tuple[Optional[float], Optional[float], bool]:
        """Compute end-game exposure cap based on time remaining.

        Returns:
            Tuple of (max_exposure_cap, stop_override, sell_all_now).
            Any may be None if no end-game restriction applies.
        """
        if self._comp_end is None:
            return None, None, False

        now = datetime.now(timezone.utc)
        remaining = self._comp_end - now
        hours_remaining = remaining.total_seconds() / 3600.0
        minutes_remaining = remaining.total_seconds() / 60.0

        # Final forced sell
        if minutes_remaining <= self._final_sell_min:
            logger.critical(
                "ENDGAME SELL ALL: %.1f minutes remaining", minutes_remaining,
            )
            return 0.0, None, True

        # Check schedule (sorted descending by hours_remaining)
        cap = None
        stop_override = None
        for entry in sorted(self._endgame, key=lambda e: e["hours_remaining"], reverse=True):
            if hours_remaining <= entry["hours_remaining"]:
                cap = entry["max_exposure"]
                stop_override = entry.get("stop_override")

        if cap is not None:
            logger.info(
                "Endgame cap active: %.1f hours remaining -> cap=%.2f%% stop=%s",
                hours_remaining,
                cap * 100,
                f"{stop_override:.2%}" if stop_override else "none",
            )

        return cap, stop_override, False

    # ------------------------------------------------------------------
    # Step 2: PAXG allocation
    # ------------------------------------------------------------------

    def get_paxg_weight(self, regime: str) -> float:
        """Compute PAXG allocation from cash buffer.

        PAXG allocation comes from the cash portion, not from crypto
        deployment. Never apply momentum or ML signals to PAXG.

        Args:
            regime: Current regime string.

        Returns:
            PAXG target weight as fraction of NAV.
        """
        regime_key_map = {
            "TREND_BULL": "trend_bull",
            "MEAN_REVERT": "mean_revert",
            "TREND_BEAR": "trend_bear",
            "HIGH_VOL_CRISIS": "crisis",
        }
        key = regime_key_map.get(regime, "mean_revert")
        weight = self._paxg_alloc.get(key, 0.10)
        hard_cap = self._paxg_alloc.get("hard_cap", 0.15)
        return min(weight, hard_cap)

    # ------------------------------------------------------------------
    # Step 3: Per-asset weights (arithmetic — no optimizer)
    # ------------------------------------------------------------------

    def compute_equal_weights(
        self,
        selected_assets: list[str],
        deployment: float,
    ) -> dict[str, float]:
        """Compute equal-weight allocation for selected assets.

        Phase 1: no volatility adjustment. Just equal weight + tier caps.

        Args:
            selected_assets: Assets selected by momentum signal.
            deployment: Total crypto deployment fraction.

        Returns:
            Dict of asset -> target weight.
        """
        if not selected_assets:
            return {}

        n = len(selected_assets)
        base_weight = deployment / n
        weights = {asset: base_weight for asset in selected_assets}
        return weights

    def compute_vol_adjusted_weights(
        self,
        selected_assets: list[str],
        deployment: float,
        asset_volatilities: dict[str, float],
    ) -> dict[str, float]:
        """Compute volatility-adjusted weights (Phase 2+).

        Higher vol = smaller position. Lower vol = larger position.
        Normalizes so sum = deployment.

        Args:
            selected_assets: Assets selected by momentum signal.
            deployment: Total crypto deployment fraction.
            asset_volatilities: Map of asset -> 24h realized volatility.

        Returns:
            Dict of asset -> vol-adjusted weight.
        """
        if not selected_assets:
            return {}

        inv_vols: dict[str, float] = {}
        for asset in selected_assets:
            vol = asset_volatilities.get(asset, 0.0)
            if vol <= 0:
                vol = 0.01  # floor to prevent division by zero
            inv_vols[asset] = 1.0 / vol

        mean_inv_vol = sum(inv_vols.values()) / len(inv_vols)
        if mean_inv_vol <= 0:
            return self.compute_equal_weights(selected_assets, deployment)

        base_weight = deployment / len(selected_assets)
        weights: dict[str, float] = {}
        for asset in selected_assets:
            weights[asset] = base_weight * (inv_vols[asset] / mean_inv_vol)

        # Normalize to sum exactly to deployment
        total = sum(weights.values())
        if total > 0:
            scale = deployment / total
            weights = {a: w * scale for a, w in weights.items()}

        return weights

    def apply_tier_caps(self, weights: dict[str, float]) -> dict[str, float]:
        """Apply tier caps with iterative redistribution.

        If a position exceeds its cap, the excess is distributed equally
        among uncapped positions. Iterates up to max_iterations.

        Args:
            weights: Input weights.

        Returns:
            Capped weights (may not perfectly sum to deployment due to
            multiple positions hitting caps).
        """
        capped = dict(weights)

        for iteration in range(self._max_redist_iter):
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

            uncapped = [a for a in capped if a not in capped_assets and capped[a] > 0]
            if not uncapped:
                break

            add_each = excess / len(uncapped)
            for asset in uncapped:
                capped[asset] += add_each

        return capped

    def apply_ml_multipliers(
        self,
        weights: dict[str, float],
        ml_multipliers: dict[str, float],
        deployment: float,
    ) -> dict[str, float]:
        """Apply ML sizing multipliers (Phase 3 only).

        Adjusts weights by multiplier, then re-normalizes to deployment.
        Never applies to PAXG.

        Args:
            weights: Current weights.
            ml_multipliers: Map of asset -> multiplier (0.5-1.3).
            deployment: Target total deployment for re-normalization.

        Returns:
            ML-adjusted weights.
        """
        adjusted = {}
        for asset, weight in weights.items():
            if asset == "PAXG":
                adjusted[asset] = weight
                continue
            mult = ml_multipliers.get(asset, 1.0)
            adjusted[asset] = weight * mult

        # Re-normalize non-PAXG to sum to deployment
        paxg_weight = adjusted.get("PAXG", 0.0)
        non_paxg_total = sum(w for a, w in adjusted.items() if a != "PAXG")
        if non_paxg_total > 0:
            target_non_paxg = deployment
            scale = target_non_paxg / non_paxg_total
            for asset in adjusted:
                if asset != "PAXG":
                    adjusted[asset] *= scale

        return adjusted

    # ------------------------------------------------------------------
    # Step 4: Turnover constraint
    # ------------------------------------------------------------------

    def apply_turnover_constraint(
        self,
        target_weights: dict[str, float],
        current_weights: dict[str, float],
        risk_exits: Optional[set[str]] = None,
    ) -> dict[str, float]:
        """Enforce maximum turnover per rebalance.

        If one-way turnover exceeds max_turnover, scale down all weight
        CHANGES proportionally. Risk exits bypass this constraint.

        Args:
            target_weights: Proposed target weights.
            current_weights: Current portfolio weights.
            risk_exits: Set of assets with risk-triggered exits (bypass turnover).

        Returns:
            Turnover-constrained weights.
        """
        risk_exits = risk_exits or set()
        all_assets = set(target_weights.keys()) | set(current_weights.keys())

        # Compute one-way turnover (excluding risk exits)
        turnover = 0.0
        for asset in all_assets:
            if asset in risk_exits:
                continue
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

            if asset in risk_exits:
                constrained[asset] = target  # bypass
            else:
                change = target - current
                constrained[asset] = current + change * scale

        # Remove zero/negative weights
        constrained = {a: w for a, w in constrained.items() if w > 1e-6}

        logger.info(
            "Turnover constrained: raw=%.2f%% -> capped=%.2f%%",
            turnover * 100,
            self._max_turnover * 100,
        )

        return constrained

    # ------------------------------------------------------------------
    # Step 5: Minimum trade threshold
    # ------------------------------------------------------------------

    def apply_min_trade_threshold(
        self,
        target_weights: dict[str, float],
        current_weights: dict[str, float],
        risk_exits: Optional[set[str]] = None,
    ) -> dict[str, float]:
        """Suppress trades smaller than minimum threshold.

        Risk exits always execute regardless of threshold.

        Args:
            target_weights: Proposed target weights.
            current_weights: Current portfolio weights.
            risk_exits: Assets with risk-triggered exits.

        Returns:
            Weights with sub-threshold changes suppressed.
        """
        risk_exits = risk_exits or set()
        result: dict[str, float] = {}

        all_assets = set(target_weights.keys()) | set(current_weights.keys())
        for asset in all_assets:
            target = target_weights.get(asset, 0.0)
            current = current_weights.get(asset, 0.0)

            if asset in risk_exits:
                result[asset] = target
            elif abs(target - current) < self._min_trade:
                result[asset] = current  # suppress
            else:
                result[asset] = target

        return {a: w for a, w in result.items() if w > 1e-6}

    # ------------------------------------------------------------------
    # Main compute entry point
    # ------------------------------------------------------------------

    def compute(
        self,
        regime: str,
        btc_vol_percentile: float,
        selected_assets: list[str],
        current_weights: dict[str, float],
        current_nav: float,
        asset_volatilities: Optional[dict[str, float]] = None,
        meme_selections: Optional[list[str]] = None,
        tier5_selections: Optional[list[str]] = None,
        ml_multipliers: Optional[dict[str, float]] = None,
        risk_exits: Optional[set[str]] = None,
        adaptive_adjustment: float = 0.0,
        sizing_multiplier: float = 1.0,
    ) -> tuple[dict[str, float], list[RiskEvent]]:
        """Compute full target portfolio.

        Main entry point called every rebalance cycle.

        Args:
            regime: Current regime string from Layer 3.
            btc_vol_percentile: BTC vol percentile from Layer 3.
            selected_assets: Main pool assets selected by Layer 4 signals.
            current_weights: Current portfolio weights.
            current_nav: Current NAV in USD.
            asset_volatilities: Asset -> 24h vol (Phase 2+, None = equal weight).
            meme_selections: Meme pool selections (Phase 2+).
            tier5_selections: Tier 5 pool selections (Phase 2+).
            ml_multipliers: ML sizing multipliers (Phase 3).
            risk_exits: Assets with pending risk exits.
            adaptive_adjustment: Additional exposure from adaptive adjustment.
            sizing_multiplier: From circuit breaker (1.0 = full, 0.5 = half).

        Returns:
            Tuple of (target_weights dict, list of RiskEvents).
        """
        events: list[RiskEvent] = []

        # Step 1: Base deployment target
        base_target = self.get_deployment_target(regime, btc_vol_percentile)

        # Step 1b: Adaptive exposure adjustment (Phase 2+)
        effective_target = min(base_target + adaptive_adjustment, self._max_exposure)

        # Step 1c: End-game de-risking
        endgame_cap, endgame_stop, sell_all = self.get_endgame_cap()

        if sell_all:
            # Force sell everything
            events.append(RiskEvent(
                event_type=EventType.ENDGAME_SELL_ALL,
                severity=Severity.CRITICAL,
                triggered_value=0.0,
                limit_value=0.0,
                action_required="SELL ALL: final minutes of competition",
            ))
            return {}, events

        if endgame_cap is not None:
            effective_target = min(effective_target, endgame_cap)

        # Apply circuit breaker sizing multiplier
        final_deployment = effective_target * sizing_multiplier

        # Step 3: Per-asset weights
        if asset_volatilities is not None and len(asset_volatilities) > 0:
            weights = self.compute_vol_adjusted_weights(
                selected_assets, final_deployment, asset_volatilities,
            )
        else:
            weights = self.compute_equal_weights(selected_assets, final_deployment)

        # Step 3c: Apply tier caps
        weights = self.apply_tier_caps(weights)

        # Step 3d: ML multipliers (Phase 3)
        if ml_multipliers:
            weights = self.apply_ml_multipliers(weights, ml_multipliers, final_deployment)
            weights = self.apply_tier_caps(weights)  # re-check caps

        # Step 3e: Add meme pool (Phase 2+)
        if meme_selections:
            for asset in meme_selections:
                cap = self._get_cap(asset)
                weights[asset] = min(0.03, cap)

        # Step 3f: Add Tier 5 pool (Phase 2+)
        if tier5_selections:
            for asset in tier5_selections:
                cap = self._get_cap(asset)
                weights[asset] = min(0.015, cap)  # midpoint of 1-2%

        # Step 2: PAXG (from cash buffer — Phase 2+ only)
        if self._paxg_alloc:
            paxg_weight = self.get_paxg_weight(regime)
            if paxg_weight > 0:
                weights["PAXG"] = paxg_weight

        # Assets not selected get weight 0
        for asset in current_weights:
            if asset not in weights:
                weights[asset] = 0.0

        # Step 4: Turnover constraint
        weights = self.apply_turnover_constraint(weights, current_weights, risk_exits)

        # Step 5: Minimum trade threshold
        weights = self.apply_min_trade_threshold(weights, current_weights, risk_exits)

        # Remove any zero-weight entries (clean output)
        target = {a: w for a, w in weights.items() if w > 1e-6}

        logger.info(
            "Portfolio computed: regime=%s deployment=%.2f%% "
            "assets=%d total_weight=%.4f",
            regime,
            final_deployment * 100,
            len(target),
            sum(target.values()),
        )

        return target, events

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_cap(self, asset: str) -> float:
        """Look up tier cap for an asset."""
        if asset in self._tier_caps:
            return self._tier_caps[asset]
        tier = self._asset_tier_map.get(asset, "tier_1_2")
        return self._tier_cap_defaults.get(tier, 0.08)
