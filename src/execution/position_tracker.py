"""Real-time position book for the execution engine.

Maintains accurate positions with cost basis, peak prices (for trailing
stops), and unrealized P&L. Updated on every fill confirmation and
every 1-minute price bar.

Phase 0: Position dataclass defined. Full tracking logic for Phase 1.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """A single held position in the portfolio.

    Attributes:
        asset: Asset symbol (e.g., "BTC").
        quantity: Current held quantity.
        cost_basis: Volume-weighted average entry price.
        current_price: Most recent price (updated every bar).
        peak_price_since_entry: Highest price since entry (feeds trailing stops).
        entry_timestamp: UTC time of initial entry.
        current_weight: Position value / current NAV.
        unrealized_pnl: (current_price - cost_basis) * quantity.
        unrealized_pnl_pct: (current_price - cost_basis) / cost_basis.
    """

    asset: str
    quantity: float
    cost_basis: float
    current_price: float
    peak_price_since_entry: float
    entry_timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    current_weight: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_pnl_pct: float = 0.0

    @property
    def market_value(self) -> float:
        """Current market value of the position."""
        return self.quantity * self.current_price

    @property
    def cost_value(self) -> float:
        """Total cost basis of the position."""
        return self.quantity * self.cost_basis

    def update_price(self, price: float) -> None:
        """Update position with a new price bar.

        Args:
            price: Latest price for this asset.
        """
        self.current_price = price
        self.peak_price_since_entry = max(self.peak_price_since_entry, price)
        self._recalc_pnl()

    def update_weight(self, nav: float) -> None:
        """Recalculate position weight against current NAV.

        Args:
            nav: Current net asset value.
        """
        if nav > 0:
            self.current_weight = self.market_value / nav
        else:
            self.current_weight = 0.0

    def add_fill(self, fill_quantity: float, fill_price: float) -> None:
        """Process a buy fill — update quantity and cost basis.

        Cost basis is recalculated as the volume-weighted average price.

        Args:
            fill_quantity: Quantity bought.
            fill_price: Price at which the fill occurred.
        """
        total_cost = (self.quantity * self.cost_basis) + (
            fill_quantity * fill_price
        )
        self.quantity += fill_quantity
        if self.quantity > 0:
            self.cost_basis = total_cost / self.quantity
        self.peak_price_since_entry = max(
            self.peak_price_since_entry, fill_price
        )
        self.current_price = fill_price
        self._recalc_pnl()

    def reduce_fill(self, fill_quantity: float, fill_price: float) -> float:
        """Process a sell fill — reduce quantity and return realized P&L.

        Args:
            fill_quantity: Quantity sold.
            fill_price: Price at which the fill occurred.

        Returns:
            Realized P&L from this sale.
        """
        realized_pnl = (fill_price - self.cost_basis) * fill_quantity
        self.quantity -= fill_quantity
        self.current_price = fill_price
        self._recalc_pnl()
        return realized_pnl

    def _recalc_pnl(self) -> None:
        """Recalculate unrealized P&L fields."""
        self.unrealized_pnl = (
            self.current_price - self.cost_basis
        ) * self.quantity
        if self.cost_basis > 0:
            self.unrealized_pnl_pct = (
                self.current_price - self.cost_basis
            ) / self.cost_basis
        else:
            self.unrealized_pnl_pct = 0.0

    def distance_to_stop(self, stop_pct: float) -> float:
        """Calculate how far current price is from the trailing stop level.

        Args:
            stop_pct: Trailing stop distance as a decimal (e.g., 0.06 = 6%).

        Returns:
            Distance as a decimal. Positive = above stop. Negative = below stop.
        """
        stop_price = self.peak_price_since_entry * (1 - stop_pct)
        if stop_price > 0:
            return (self.current_price - stop_price) / stop_price
        return 0.0

    def to_dict(self) -> dict:
        """Serialize to dict for logging/snapshots."""
        return {
            "asset": self.asset,
            "quantity": self.quantity,
            "cost_basis": round(self.cost_basis, 6),
            "current_price": round(self.current_price, 6),
            "peak_price": round(self.peak_price_since_entry, 6),
            "entry_timestamp": self.entry_timestamp.isoformat(),
            "current_weight": round(self.current_weight, 6),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "unrealized_pnl_pct": round(self.unrealized_pnl_pct, 6),
            "market_value": round(self.market_value, 2),
        }


class PositionTracker:
    """Maintains the real-time position book.

    Attributes:
        positions: Dict mapping asset symbol to Position.
        cash_balance: Current USD cash balance.
        starting_capital: Initial capital at competition start.
        realized_pnl: Cumulative realized P&L.
    """

    def __init__(self, starting_capital: float) -> None:
        """Initialize with starting capital.

        Args:
            starting_capital: Initial USD balance.
        """
        self.positions: dict[str, Position] = {}
        self.cash_balance: float = starting_capital
        self.starting_capital: float = starting_capital
        self.realized_pnl: float = 0.0

    @property
    def nav(self) -> float:
        """Current net asset value (positions + cash)."""
        position_value = sum(p.market_value for p in self.positions.values())
        return position_value + self.cash_balance

    @property
    def crypto_exposure(self) -> float:
        """Fraction of NAV in crypto positions."""
        nav = self.nav
        if nav <= 0:
            return 0.0
        position_value = sum(p.market_value for p in self.positions.values())
        return position_value / nav

    def get_position(self, asset: str) -> Optional[Position]:
        """Get position for an asset, or None if not held.

        Args:
            asset: Asset symbol.

        Returns:
            Position if held, None otherwise.
        """
        return self.positions.get(asset)

    def get_weight(self, asset: str) -> float:
        """Get current weight of an asset in the portfolio.

        Args:
            asset: Asset symbol.

        Returns:
            Weight as fraction of NAV, or 0.0 if not held.
        """
        pos = self.positions.get(asset)
        if pos is None:
            return 0.0
        nav = self.nav
        if nav <= 0:
            return 0.0
        return pos.market_value / nav

    def on_buy_fill(
        self,
        asset: str,
        quantity: float,
        price: float,
        commission_pct: float = 0.0,
    ) -> None:
        """Process a buy fill confirmation.

        Args:
            asset: Asset symbol.
            quantity: Filled quantity.
            price: Fill price.
            commission_pct: Commission rate as decimal.
        """
        commission_cost = quantity * price * commission_pct
        total_cost = quantity * price + commission_cost

        if asset in self.positions:
            self.positions[asset].add_fill(quantity, price)
        else:
            self.positions[asset] = Position(
                asset=asset,
                quantity=quantity,
                cost_basis=price,
                current_price=price,
                peak_price_since_entry=price,
            )

        self.cash_balance -= total_cost
        logger.info(
            "BUY fill: %s %.6f @ %.4f (commission: $%.2f, cash: $%.2f)",
            asset,
            quantity,
            price,
            commission_cost,
            self.cash_balance,
        )

    def on_sell_fill(
        self,
        asset: str,
        quantity: float,
        price: float,
        commission_pct: float = 0.0,
    ) -> None:
        """Process a sell fill confirmation.

        Args:
            asset: Asset symbol.
            quantity: Filled quantity.
            price: Fill price.
            commission_pct: Commission rate as decimal.
        """
        pos = self.positions.get(asset)
        if pos is None:
            logger.error("SELL fill for %s but no position held", asset)
            return

        realized = pos.reduce_fill(quantity, price)
        commission_cost = quantity * price * commission_pct
        proceeds = quantity * price - commission_cost

        self.cash_balance += proceeds
        self.realized_pnl += realized - commission_cost

        if pos.quantity <= 1e-12:
            del self.positions[asset]
            logger.info(
                "SELL fill: %s closed. Realized P&L: $%.2f", asset, realized
            )
        else:
            logger.info(
                "SELL fill: %s reduced to %.6f. Realized P&L: $%.2f",
                asset,
                pos.quantity,
                realized,
            )

    def update_prices(self, prices: dict[str, float]) -> None:
        """Update all positions with latest prices and recalculate weights.

        Args:
            prices: Dict mapping pair name (or asset) to latest price.
        """
        # Note: We must update prices FIRST before calculating NAV
        for asset, pos in self.positions.items():
            # Try exact match (e.g., "BTC")
            if asset in prices:
                pos.update_price(prices[asset])
            # Try pair match (e.g., "BTC/USD" or "BTCUSD")
            elif f"{asset}/USD" in prices:
                pos.update_price(prices[f"{asset}/USD"])
            elif f"{asset}USD" in prices:
                pos.update_price(prices[f"{asset}USD"])

        # Recalculate NAV now that prices are updated
        nav = self.nav
        for pos in self.positions.values():
            pos.update_weight(nav)

    def sync_from_exchange(self, balances: dict[str, dict[str, float]]) -> None:
        """Sync cash balance and crypto positions from exchange balance query.

        Args:
            balances: Balance dict from ExecutionClient.get_balance().
        """
        # 1. Sync USD cash balance
        usd = balances.get("USD", {})
        if "free" in usd or "locked" in usd:
            old_cash = self.cash_balance
            # Sum both free and locked USD to get true cash balance
            self.cash_balance = float(usd.get("free", 0.0)) + float(usd.get("locked", 0.0))
            if abs(old_cash - self.cash_balance) > 1.0:
                logger.warning(
                    "Cash balance synced: $%.2f -> $%.2f (diff: $%.2f)",
                    old_cash,
                    self.cash_balance,
                    self.cash_balance - old_cash,
                )

        # 2. Sync crypto positions
        for asset, info in balances.items():
            if asset == "USD":
                continue
            
            # Combine free and locked balance for total true quantity
            total_qty = float(info.get("free", 0.0)) + float(info.get("locked", 0.0))
            
            # Filter out microscopic dust values
            if total_qty > 1e-8:
                if asset in self.positions:
                    # Adjust if execution drifted from exchange truth
                    old_qty = self.positions[asset].quantity
                    if abs(old_qty - total_qty) > 1e-6:
                        self.positions[asset].quantity = total_qty
                else:
                    # New position discovered (e.g., held before bot started)
                    logger.info("Discovered position from exchange: %s %.6f", asset, total_qty)
                    self.positions[asset] = Position(
                        asset=asset,
                        quantity=total_qty,
                        cost_basis=0.0,      # Will rely on future updates or stay 0
                        current_price=0.0,   # Will be populated on the next update_prices() tick
                        peak_price_since_entry=0.0,
                    )
            elif asset in self.positions and total_qty <= 1e-8:
                # Asset was sold manually on the exchange
                del self.positions[asset]

    def snapshot(self) -> dict:
        """Generate a full portfolio snapshot for logging.

        Returns:
            Dict with NAV, cash, positions, exposure.
        """
        return {
            "nav": round(self.nav, 2),
            "cash_balance": round(self.cash_balance, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "crypto_exposure": round(self.crypto_exposure, 4),
            "position_count": len(self.positions),
            "positions": {
                asset: pos.to_dict()
                for asset, pos in self.positions.items()
            },
        }
