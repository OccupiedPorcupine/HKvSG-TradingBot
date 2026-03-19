"""Pre-rebalance validation — runs before passing TargetPortfolio to execution.

Catches any constraint violations that slipped through portfolio construction
and fixes them before orders go out. Every fix is logged.
"""

import logging
from typing import Optional

from src.risk.risk_event import EventType, RiskEvent, Severity

logger = logging.getLogger(__name__)


class PreTradeValidator:
    """Validates and corrects TargetPortfolio before execution.

    Checks:
      1. No single asset exceeds its tier cap.
      2. Total crypto exposure <= deployment target.
      3. TRUMP <= 2%, PAXG <= 15%, DOGE <= 5%.
      4. Fixes violations by capping and redistributing.

    Attributes:
        tier_caps: Map of asset -> max weight.
        max_crypto_exposure: Hard cap on total crypto deployment.
    """

    def __init__(
        self,
        tier_caps: dict[str, float],
        asset_tier_map: dict[str, str],
        tier_cap_defaults: dict[str, float],
        max_crypto_exposure: float = 0.90,
        redistribution_max_iterations: int = 5,
    ) -> None:
        """Initialize the pre-trade validator.

        Args:
            tier_caps: Per-asset cap overrides (e.g., {"DOGE": 0.05, "TRUMP": 0.02}).
            asset_tier_map: Map of asset -> tier key (e.g., {"BTC": "tier_1_2"}).
            tier_cap_defaults: Map of tier key -> default cap
                (e.g., {"tier_1_2": 0.08, "tier_3": 0.06}).
            max_crypto_exposure: Hard cap on total crypto exposure.
            redistribution_max_iterations: Max cap-and-redistribute iterations.
        """
        self._asset_caps = tier_caps
        self._asset_tier_map = asset_tier_map
        self._tier_cap_defaults = tier_cap_defaults
        self._max_exposure = max_crypto_exposure
        self._max_iterations = redistribution_max_iterations

    def get_cap_for_asset(self, asset: str) -> float:
        """Look up the cap for a specific asset.

        Args:
            asset: Asset symbol.

        Returns:
            Maximum weight as fraction of NAV.
        """
        # Check per-asset override first
        if asset in self._asset_caps:
            return self._asset_caps[asset]

        # Fall back to tier default
        tier = self._asset_tier_map.get(asset, "tier_1_2")
        return self._tier_cap_defaults.get(tier, 0.08)

    def validate_and_fix(
        self,
        target_weights: dict[str, float],
        max_deployment: float,
    ) -> tuple[dict[str, float], list[RiskEvent]]:
        """Validate target weights and fix any violations.

        Args:
            target_weights: Proposed target weights {asset: weight}.
            max_deployment: Maximum allowed total crypto deployment
                (from regime + endgame + adaptive).

        Returns:
            Tuple of (corrected_weights, list of RiskEvents for violations).
        """
        weights = dict(target_weights)
        events: list[RiskEvent] = []

        # Cap total exposure at hard limit
        effective_max = min(max_deployment, self._max_exposure)

        # --- Check 1 & 3: Per-asset caps ---
        for iteration in range(self._max_iterations):
            excess = 0.0
            capped_assets: set[str] = set()
            uncapped_assets: list[str] = []

            for asset, weight in weights.items():
                cap = self.get_cap_for_asset(asset)
                if weight > cap:
                    asset_excess = weight - cap
                    excess += asset_excess
                    weights[asset] = cap
                    capped_assets.add(asset)

                    events.append(RiskEvent(
                        event_type=EventType.CONCENTRATION_BREACH,
                        severity=Severity.HIGH,
                        triggered_value=weight,
                        limit_value=cap,
                        action_required=(
                            f"Capped {asset} from {weight:.4f} to {cap:.4f} "
                            f"(iteration {iteration + 1})"
                        ),
                        asset=asset,
                    ))
                    logger.warning(
                        "Pre-trade cap: %s %.4f -> %.4f (iter %d)",
                        asset, weight, cap, iteration + 1,
                    )

            if excess <= 0:
                break  # No caps breached

            # Redistribute excess to uncapped assets
            for asset in weights:
                if asset not in capped_assets and weights[asset] > 0:
                    uncapped_assets.append(asset)

            if not uncapped_assets:
                # All selected assets are capped. Excess cannot be redistributed.
                # The excess naturally becomes cash/PAXG buffer.
                break 

            per_asset_add = excess / len(uncapped_assets)
            for asset in uncapped_assets:
                weights[asset] += per_asset_add

        

        # --- Check 2: Total crypto exposure ---
        total_crypto = sum(w for w in weights.values() if w > 0)
        if total_crypto > effective_max:
            scale_factor = effective_max / total_crypto
            for asset in weights:
                weights[asset] *= scale_factor

            events.append(RiskEvent(
                event_type=EventType.EXPOSURE_BREACH,
                severity=Severity.HIGH,
                triggered_value=total_crypto,
                limit_value=effective_max,
                action_required=(
                    f"Scaled all weights by {scale_factor:.4f}: "
                    f"total {total_crypto:.4f} exceeded max {effective_max:.4f}"
                ),
            ))
            logger.warning(
                "Pre-trade exposure cap: total %.4f -> %.4f (scale %.4f)",
                total_crypto, effective_max, scale_factor,
            )

        # --- Remove near-zero weights ---
        weights = {k: v for k, v in weights.items() if v > 1e-6}

        return weights, events

    def validate_only(
        self,
        target_weights: dict[str, float],
        max_deployment: float,
    ) -> list[RiskEvent]:
        """Check for violations without fixing them.

        Useful for reporting only.

        Args:
            target_weights: Proposed target weights.
            max_deployment: Maximum allowed deployment.

        Returns:
            List of RiskEvents for any violations found.
        """
        events: list[RiskEvent] = []
        effective_max = min(max_deployment, self._max_exposure)

        for asset, weight in target_weights.items():
            cap = self.get_cap_for_asset(asset)
            if weight > cap:
                events.append(RiskEvent(
                    event_type=EventType.CONCENTRATION_BREACH,
                    severity=Severity.HIGH,
                    triggered_value=weight,
                    limit_value=cap,
                    action_required=f"{asset} weight {weight:.4f} > cap {cap:.4f}",
                    asset=asset,
                ))

        total_crypto = sum(w for w in target_weights.values() if w > 0)
        if total_crypto > effective_max:
            events.append(RiskEvent(
                event_type=EventType.EXPOSURE_BREACH,
                severity=Severity.HIGH,
                triggered_value=total_crypto,
                limit_value=effective_max,
                action_required=(
                    f"Total exposure {total_crypto:.4f} > max {effective_max:.4f}"
                ),
            ))

        return events
