"""ML retraining pipeline (Layer 8, Component 3).

Walk-forward LightGBM retraining with post-retrain sanity check.
Phase 3 only — this module is a no-op stub until ML is enabled.

When enabled (Phase 3):
  - Retrains every 24 hours on 60+ days of historical OHLCV data
  - Binary classification target: sign(return_4h)
  - Post-retrain sanity check rejects models with divergent distributions
  - IC monitoring halts ML signal when rolling 48h IC < 0.02
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class MLRetrainer:
    """Walk-forward ML retraining pipeline.

    Phase 0-2: Returns immediately (no-op).
    Phase 3:   Full retraining with sanity checks.

    Attributes:
        enabled: Whether ML retraining is active.
        model: Current model (None until Phase 3).
    """

    def __init__(self, enabled: bool = False) -> None:
        """Initialize the ML retrainer.

        Args:
            enabled: Whether to activate ML retraining (Phase 3 only).
        """
        self.enabled = enabled
        self.model: Any = None
        self._last_retrain: Optional[str] = None

    async def retrain(self) -> dict[str, Any]:
        """Run retraining cycle. No-op if ML is disabled.

        Returns:
            Dict with status and metrics. Returns immediately if disabled.
        """
        if not self.enabled:
            return {"status": "DISABLED", "action": "NONE"}

        # Phase 3 implementation placeholder
        logger.info("ML retrain triggered — Phase 3 not yet implemented")
        return {"status": "NOT_IMPLEMENTED", "action": "NONE"}

    def get_multiplier(self, asset: str) -> float:
        """Get ML size multiplier for an asset. Always 1.0 until Phase 3.

        Args:
            asset: Asset symbol.

        Returns:
            Size multiplier (1.0 = no adjustment).
        """
        if not self.enabled or self.model is None:
            return 1.0

        # Phase 3: run inference here
        return 1.0
