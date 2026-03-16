"""Signal 5 — ML directional overlay (Phase 3 only).

Reads pre-computed LightGBM predictions and converts them to position
size multipliers (0.5x–1.3x) applied to existing momentum positions.

NOT active in Phase 1-2: all multipliers = 1.0.

When enabled (Phase 3):
    P(positive 4h return) > 0.65 → multiplier = 1.3
    P(positive 4h return) < 0.45 → multiplier = 0.5
    else                         → multiplier = 1.0

NEVER applied to PAXG.

IC monitoring: if rolling 48h IC < 0.02, halt (all multipliers = 1.0).
Resume when IC > 0.04 for 12 continuous hours.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MLOverlay:
    """ML-based size multiplier for momentum positions.

    Phase 1-2: stub that returns 1.0 for all assets.
    Phase 3: reads LightGBM predictions and applies multipliers.

    Usage::

        overlay = MLOverlay(config)
        multipliers = overlay.get_multipliers(selected_assets)
        # multipliers["BTC"] → 1.0 (Phase 1-2)
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize ML overlay.

        Args:
            config: Full config.yaml as dict.
        """
        ml_cfg = config.get("signals", {}).get("ml", {})
        self._enabled = ml_cfg.get("enabled", False)
        self._mult_high = ml_cfg.get("multiplier_high", 1.3)
        self._mult_low = ml_cfg.get("multiplier_low", 0.5)
        self._mult_default = ml_cfg.get("multiplier_default", 1.0)
        self._threshold_high = ml_cfg.get("probability_threshold_high", 0.65)
        self._threshold_low = ml_cfg.get("probability_threshold_low", 0.45)
        self._ic_halt_threshold = ml_cfg.get("ic_halt_threshold", 0.02)
        self._ic_resume_threshold = ml_cfg.get("ic_resume_threshold", 0.04)
        self._ic_resume_window_hr = ml_cfg.get("ic_resume_window_hr", 12)

        # State
        self._halted = False
        self._predictions: dict[str, float] = {}

        # PAXG is never given an ML multiplier
        self._excluded = {"PAXG"}

    def get_multipliers(
        self, selected_assets: set[str]
    ) -> dict[str, float]:
        """Get ML size multipliers for selected assets.

        Args:
            selected_assets: Set of asset symbols with momentum allocations.

        Returns:
            Dict mapping each asset → multiplier (0.5, 1.0, or 1.3).
            Returns 1.0 for all in Phase 1-2.
        """
        if not self._enabled or self._halted:
            return {asset: 1.0 for asset in selected_assets}

        multipliers: dict[str, float] = {}
        for asset in selected_assets:
            if asset in self._excluded:
                multipliers[asset] = 1.0
                continue

            prob = self._predictions.get(asset)
            if prob is None:
                multipliers[asset] = self._mult_default
            elif prob > self._threshold_high:
                multipliers[asset] = self._mult_high
            elif prob < self._threshold_low:
                multipliers[asset] = self._mult_low
            else:
                multipliers[asset] = self._mult_default

        return multipliers

    def update_predictions(self, predictions: dict[str, float]) -> None:
        """Update stored ML predictions (Phase 3).

        Args:
            predictions: Dict mapping asset → P(positive 4h return).
        """
        self._predictions = predictions

    def check_ic(self, rolling_48h_ic: float) -> None:
        """Check IC and halt/resume ML overlay (Phase 3).

        Args:
            rolling_48h_ic: Rolling 48-hour information coefficient.
        """
        if not self._enabled:
            return

        if not self._halted and rolling_48h_ic < self._ic_halt_threshold:
            self._halted = True
            logger.warning(
                "ML_OVERLAY halted: IC=%.4f < %.4f threshold",
                rolling_48h_ic,
                self._ic_halt_threshold,
            )
        elif self._halted and rolling_48h_ic > self._ic_resume_threshold:
            # Full resume logic (12h sustained) handled by ml_monitor.py
            # This is a simplified check; the monitor layer handles the
            # sustained window requirement
            self._halted = False
            logger.info(
                "ML_OVERLAY resumed: IC=%.4f > %.4f threshold",
                rolling_48h_ic,
                self._ic_resume_threshold,
            )

    @property
    def is_halted(self) -> bool:
        """Whether ML overlay is currently halted due to low IC."""
        return self._halted
