"""PAXG allocation as portfolio volatility reducer.

PAXG is allocated from the cash buffer, not from crypto signal allocations.
Its weight scales with regime defensiveness — more PAXG in bearish conditions
provides a Sharpe denominator benefit (lower total portfolio volatility).

This is NOT a Treynor instrument (Treynor is not scored). It is purely a
volatility reducer for Sharpe (0.3 weight) and a potential positive-return
source during crypto selloffs for Sortino (0.4 weight).
"""
import logging
from src.regime.regime_state import RegimeType

logger = logging.getLogger(__name__)

_FALLBACK_WEIGHT = 0.05


class PAXGAllocator:
    def __init__(self, config: dict):
        """Load allocation targets from config['paxg']['allocation'].
        Map string regime names to RegimeType enum values.
        """
        raw = config.get("paxg", {}).get("allocation", {})
        self._allocation: dict[RegimeType, float] = {
            RegimeType(k): float(v) for k, v in raw.items()
        }
        self._tolerance: float = float(
            config.get("paxg", {}).get("rebalance_tolerance", 0.01)
        )

    def get_target_weight(self, regime: RegimeType) -> float:
        """Target PAXG weight as fraction of NAV for given regime.

        Returns config value directly (already midpoint floats).
        Falls back to 0.05 if regime not in config (defensive default).
        """
        return self._allocation.get(regime, _FALLBACK_WEIGHT)

    def compute_order(
        self,
        current_paxg_weight: float,
        regime: RegimeType,
        nav: float,
    ) -> dict | None:
        """Compute rebalance order if PAXG weight differs from target.

        Returns None if |current - target| < rebalance_tolerance (1% NAV).
        Otherwise returns:
            {"symbol": "PAXG", "side": "buy" | "sell", "amount_usd": float}
        """
        target = self.get_target_weight(regime)
        delta = target - current_paxg_weight

        if abs(delta) < self._tolerance:
            return None

        side = "buy" if delta > 0 else "sell"
        amount_usd = abs(delta) * nav

        logger.info(
            "PAXG REBALANCE: %s $%.0f (current=%.1f%%, target=%.1f%%, regime=%s)",
            side,
            amount_usd,
            current_paxg_weight * 100,
            target * 100,
            regime.value,
        )
        return {"symbol": "PAXG", "side": side, "amount_usd": amount_usd}
