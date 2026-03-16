"""Order priority queue for the execution engine.

Orders are processed in strict priority order:
  1. CRITICAL risk exits (trailing stop, circuit breaker)
  2. Position reductions (risk-motivated)
  3. New entries (momentum signal)
  4. Size adjustments (rebalance tweaks)

CRITICAL risk exits must NEVER be delayed by pending lower-priority orders.
Maximum simultaneous open limit orders: configurable (default 15).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Optional

logger = logging.getLogger(__name__)


class OrderPriority(IntEnum):
    """Order priority levels. Lower number = higher priority."""

    CRITICAL_EXIT = 0
    POSITION_REDUCTION = 1
    NEW_ENTRY = 2
    SIZE_ADJUSTMENT = 3


@dataclass(order=True)
class PendingOrder:
    """An order waiting to be submitted.

    Sorted by (priority, timestamp) so highest-priority oldest orders
    go first.

    Attributes:
        priority: Order priority level.
        timestamp: When the order was queued (for FIFO within priority).
        asset: Asset symbol.
        pair: Trading pair string.
        side: "BUY" or "SELL".
        quantity_usd: Order size in USD.
        target_weight: Target portfolio weight for this asset.
        trigger: What caused this order.
        risk_event_severity: Severity of the risk event, if any.
        resubmission_count: How many times this order has been resubmitted.
    """

    priority: OrderPriority = field(compare=True)
    timestamp: datetime = field(
        compare=True,
        default_factory=lambda: datetime.now(timezone.utc),
    )
    asset: str = field(compare=False, default="")
    pair: str = field(compare=False, default="")
    side: str = field(compare=False, default="")
    quantity_usd: float = field(compare=False, default=0.0)
    target_weight: float = field(compare=False, default=0.0)
    trigger: str = field(compare=False, default="")
    risk_event_severity: Optional[str] = field(compare=False, default=None)
    resubmission_count: int = field(compare=False, default=0)


class OrderPriorityQueue:
    """Priority queue for pending orders.

    Maintains a sorted list of orders. CRITICAL exits are always
    processed first. Queue size is capped at max_simultaneous_orders.

    Attributes:
        max_orders: Maximum simultaneous open limit orders.
        queue: Sorted list of pending orders.
    """

    def __init__(self, max_orders: int = 15) -> None:
        """Initialize the priority queue.

        Args:
            max_orders: Maximum simultaneous open orders.
        """
        self.max_orders = max_orders
        self.queue: list[PendingOrder] = []

    def add(self, order: PendingOrder) -> bool:
        """Add an order to the queue.

        CRITICAL exits are always accepted. Lower-priority orders are
        rejected if the queue is full.

        Args:
            order: Order to add.

        Returns:
            True if the order was accepted, False if rejected.
        """
        if order.priority == OrderPriority.CRITICAL_EXIT:
            # CRITICAL exits are NEVER deferred
            self.queue.append(order)
            self.queue.sort()
            logger.info(
                "CRITICAL exit queued: %s %s %s (trigger: %s)",
                order.side,
                order.asset,
                order.pair,
                order.trigger,
            )
            return True

        if len(self.queue) >= self.max_orders:
            logger.warning(
                "Order queue full (%d/%d). Deferring %s %s %s (priority: %s)",
                len(self.queue),
                self.max_orders,
                order.side,
                order.asset,
                order.pair,
                order.priority.name,
            )
            return False

        self.queue.append(order)
        self.queue.sort()
        return True

    def pop(self) -> Optional[PendingOrder]:
        """Remove and return the highest-priority order.

        Returns:
            Highest-priority order, or None if queue is empty.
        """
        if not self.queue:
            return None
        return self.queue.pop(0)

    def pop_batch(self, max_count: int) -> list[PendingOrder]:
        """Remove and return up to max_count highest-priority orders.

        Args:
            max_count: Maximum number of orders to return.

        Returns:
            List of orders in priority order.
        """
        count = min(max_count, len(self.queue))
        batch = self.queue[:count]
        self.queue = self.queue[count:]
        return batch

    def peek(self) -> Optional[PendingOrder]:
        """View the highest-priority order without removing it.

        Returns:
            Highest-priority order, or None if empty.
        """
        if not self.queue:
            return None
        return self.queue[0]

    def has_critical(self) -> bool:
        """Check if any CRITICAL exits are in the queue."""
        return any(
            o.priority == OrderPriority.CRITICAL_EXIT for o in self.queue
        )

    def critical_count(self) -> int:
        """Count of CRITICAL exit orders in the queue."""
        return sum(
            1 for o in self.queue
            if o.priority == OrderPriority.CRITICAL_EXIT
        )

    def remove_for_asset(self, asset: str) -> int:
        """Remove all pending orders for a specific asset.

        Useful when a position is fully exited and pending entries
        should be cancelled.

        Args:
            asset: Asset symbol to remove orders for.

        Returns:
            Number of orders removed.
        """
        before = len(self.queue)
        self.queue = [o for o in self.queue if o.asset != asset]
        removed = before - len(self.queue)
        if removed > 0:
            logger.info("Removed %d pending orders for %s", removed, asset)
        return removed

    def clear(self) -> int:
        """Clear all orders from the queue.

        Returns:
            Number of orders cleared.
        """
        count = len(self.queue)
        self.queue.clear()
        return count

    @property
    def size(self) -> int:
        """Current number of orders in the queue."""
        return len(self.queue)

    @property
    def is_empty(self) -> bool:
        """Whether the queue is empty."""
        return len(self.queue) == 0

    def __len__(self) -> int:
        return len(self.queue)

    def __repr__(self) -> str:
        return (
            f"OrderPriorityQueue(size={self.size}, "
            f"critical={self.critical_count()}, "
            f"max={self.max_orders})"
        )
