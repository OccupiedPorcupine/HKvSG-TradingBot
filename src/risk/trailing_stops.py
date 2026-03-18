"""Per-position trailing stop tracking with dynamic tightening.

Runs every 1-minute bar. Tracks peak price (high watermark) since entry
for every open position, and emits TRAILING_STOP RiskEvents when the
current price drops below the stop level.

Stop distance is determined by:
  1. Base stop distance (from config, varies by tier)
  2. Dynamic tightening (based on daily P&L — Phase 2+)
  3. End-game tightening (from end-game schedule — Phase 2+)
  The tightest of (2) and (3) wins; floor of 2% always applies.

Phase 1 implements: base trailing stops only (no dynamic tightening,
simple T-1h sell-all instead of end-game schedule).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src.risk.risk_event import EventType, RiskEvent, Severity

logger = logging.getLogger(__name__)


@dataclass
class PositionStop:
    """Trailing stop state for a single position.

    Attributes:
        asset: Asset symbol (e.g., "BTC").
        entry_price: Price at which the position was entered.
        peak_price: Highest price observed since entry (high watermark).
        base_stop_pct: Base trailing stop distance as a fraction (e.g., 0.06).
        last_updated: Timestamp of last peak price update.
    """

    asset: str
    entry_price: float
    peak_price: float
    base_stop_pct: float
    last_updated: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def update_peak(self, current_price: float) -> None:
        """Update peak price if current price exceeds it.

        Args:
            current_price: Latest market price for this asset.
        """
        if current_price > self.peak_price:
            self.peak_price = current_price
            self.last_updated = datetime.now(timezone.utc)

    def stop_price(self, effective_stop_pct: Optional[float] = None) -> float:
        """Compute the stop trigger price.

        Args:
            effective_stop_pct: Override stop distance (after tightening).
                If None, uses base_stop_pct.

        Returns:
            Price below which the trailing stop triggers.
        """
        pct = effective_stop_pct if effective_stop_pct is not None else self.base_stop_pct
        return self.peak_price * (1.0 - pct)

    def is_triggered(
        self, current_price: float, effective_stop_pct: Optional[float] = None
    ) -> bool:
        """Check if the trailing stop has been hit.

        Args:
            current_price: Latest market price.
            effective_stop_pct: Override stop distance.

        Returns:
            True if current_price is at or below the stop price.
        """
        return current_price <= self.stop_price(effective_stop_pct)


class TrailingStopManager:
    """Manages trailing stops for all open positions.

    Usage (called every 1-minute bar)::

        events = stop_manager.check_all(current_prices, daily_pnl_pct)

    Phase 1: no dynamic tightening. Pass daily_pnl_pct=0.0 or omit.

    Attributes:
        positions: Dict of asset -> PositionStop for all tracked positions.
        min_stop_floor: Minimum stop distance (default 2%).
    """

    def __init__(
        self,
        base_stops: dict[str, float],
        min_stop_floor: float = 0.02,
        tightening_config: Optional[dict] = None,
    ) -> None:
        """Initialize the trailing stop manager.

        Args:
            base_stops: Map of asset -> base stop distance fraction.
                Can also contain tier keys like "tier_1_3", "tier_4_5", "trump".
            min_stop_floor: Absolute minimum stop distance (never go below this).
            tightening_config: Dynamic tightening thresholds from config.
                Keys: threshold_high_pnl, tightening_high,
                      threshold_medium_pnl, tightening_medium.
                None disables dynamic tightening (Phase 1).
        """
        self._base_stops = base_stops
        self.min_stop_floor = min_stop_floor
        self._tightening_config = tightening_config
        self.positions: dict[str, PositionStop] = {}

    def open_position(
        self, asset: str, entry_price: float, base_stop_pct: float
    ) -> None:
        """Start tracking a new position.

        Args:
            asset: Asset symbol.
            entry_price: Price at entry.
            base_stop_pct: Base trailing stop distance for this asset.
        """
        self.positions[asset] = PositionStop(
            asset=asset,
            entry_price=entry_price,
            peak_price=entry_price,
            base_stop_pct=base_stop_pct,
        )
        logger.info(
            "Trailing stop opened: %s entry=%.4f base_stop=%.2f%%",
            asset,
            entry_price,
            base_stop_pct * 100,
        )

    def close_position(self, asset: str) -> None:
        """Stop tracking a closed position.

        Args:
            asset: Asset symbol.
        """
        if asset in self.positions:
            del self.positions[asset]
            logger.info("Trailing stop closed: %s", asset)

    def compute_effective_stop(
        self,
        pos: PositionStop,
        current_price: float,
        endgame_stop_override: Optional[float] = None,
    ) -> float:
        """Compute effective stop distance after tightening based on PER-POSITION P&L."""
        tightened = pos.base_stop_pct
        
        if self._tightening_config is not None and pos.entry_price > 0:
            # Calculate unrealized P&L for THIS specific position
            unrealized_pnl_pct = (current_price - pos.entry_price) / pos.entry_price
            
            high_threshold = self._tightening_config.get("threshold_high_pnl", 0.05)
            high_tighten = self._tightening_config.get("tightening_high", 0.40)
            med_threshold = self._tightening_config.get("threshold_medium_pnl", 0.025)
            med_tighten = self._tightening_config.get("tightening_medium", 0.20)

            if unrealized_pnl_pct > high_threshold:
                tightened = pos.base_stop_pct * (1.0 - high_tighten)
            elif unrealized_pnl_pct > med_threshold:
                tightened = pos.base_stop_pct * (1.0 - med_tighten)

        if endgame_stop_override is not None:
            tightened = min(tightened, endgame_stop_override)

        return max(tightened, self.min_stop_floor)

    def check_all(
        self,
        current_prices: dict[str, float],
        endgame_stop_override: Optional[float] = None,
    ) -> list[RiskEvent]:
        """Check all positions against their trailing stops.

        Called every 1-minute bar. Updates peak prices and checks triggers.

        Args:
            current_prices: Map of asset -> current market price.
            daily_pnl_pct: Current daily P&L as fraction.
            endgame_stop_override: End-game schedule stop override, or None.

        Returns:
            List of TRAILING_STOP RiskEvents for any triggered positions.
        """
        events: list[RiskEvent] = []

        for asset, pos in list(self.positions.items()):
            price = current_prices.get(asset)
            if price is None:
                continue

            # Update high watermark
            pos.update_peak(price)

            # Compute effective stop with all tightening (passing pos and price)
            effective_pct = self.compute_effective_stop(
                pos, price, endgame_stop_override
            )

            if pos.is_triggered(price, effective_pct):
                stop_px = pos.stop_price(effective_pct)
                drop_from_peak = (pos.peak_price - price) / pos.peak_price

                event = RiskEvent(
                    event_type=EventType.TRAILING_STOP,
                    severity=Severity.CRITICAL,
                    triggered_value=drop_from_peak,
                    limit_value=effective_pct,
                    action_required=(
                        f"SELL ALL {asset}: price {price:.4f} breached "
                        f"trailing stop at {stop_px:.4f} "
                        f"(peak {pos.peak_price:.4f}, "
                        f"effective stop {effective_pct:.2%})"
                    ),
                    asset=asset,
                )
                events.append(event)
                logger.warning(
                    "TRAILING_STOP triggered: %s price=%.4f stop=%.4f "
                    "peak=%.4f drop=%.2f%%",
                    asset,
                    price,
                    stop_px,
                    pos.peak_price,
                    drop_from_peak * 100,
                )

        return events

    def get_stop_summary(self) -> dict[str, dict]:
        """Return a summary of all tracked stops for logging/monitoring.

        Returns:
            Dict of asset -> {entry_price, peak_price, base_stop, stop_price}.
        """
        summary: dict[str, dict] = {}
        for asset, pos in self.positions.items():
            summary[asset] = {
                "entry_price": pos.entry_price,
                "peak_price": pos.peak_price,
                "base_stop_pct": pos.base_stop_pct,
                "stop_price": pos.stop_price(),
                "gain_from_entry_pct": (
                    (pos.peak_price - pos.entry_price) / pos.entry_price
                    if pos.entry_price > 0
                    else 0.0
                ),
            }
        return summary

    def restore_position(
        self, asset: str, entry_price: float, peak_price: float, base_stop_pct: float
    ) -> None:
        """Restore a position from crash recovery snapshot.

        Args:
            asset: Asset symbol.
            entry_price: Original entry price.
            peak_price: Last known peak price (high watermark).
            base_stop_pct: Base stop distance.
        """
        self.positions[asset] = PositionStop(
            asset=asset,
            entry_price=entry_price,
            peak_price=peak_price,
            base_stop_pct=base_stop_pct,
        )
        logger.info(
            "Trailing stop restored: %s entry=%.4f peak=%.4f stop=%.2f%%",
            asset,
            entry_price,
            peak_price,
            base_stop_pct * 100,
        )
