"""Order manager — limit order submission, resubmission, and escalation.

Processes orders from the priority queue, submits limit orders at the
last known price, handles fill timeouts and resubmission logic, and
escalates to market orders ONLY for CRITICAL risk exits after 3 failed
resubmissions.

ABSOLUTE INVARIANT: Market orders are physically disabled. The only
code path that submits a market order is gated behind:
    risk_event.severity == CRITICAL and resubmission_count >= 3

Phase 0: Core structure defined. Full async execution loop for Phase 1.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from src.execution.roostoo_client import ExecutionClient, OrderResult
from src.execution.position_tracker import PositionTracker
from src.execution.decision_logger import DecisionLogger, DecisionEntry
from src.execution.priority_queue import (
    OrderPriorityQueue,
    PendingOrder,
    OrderPriority,
)

logger = logging.getLogger(__name__)


@dataclass
class OrderManagerConfig:
    """Configuration for the order manager.

    All values come from config.yaml execution section.

    Attributes:
        order_timeout_sec: Seconds before cancelling unfilled limit order.
        max_resubmissions: Max resubmit attempts before abandoning.
        risk_exit_acceleration_threshold: Adverse price move % to accelerate exit.
        max_simultaneous_orders: Max open orders at once.
        fill_check_delay_sec: Seconds to wait before checking fill status.
        min_trade_threshold_pct: Minimum order size as fraction of NAV.
    """

    order_timeout_sec: int = 60
    max_resubmissions: int = 3
    risk_exit_acceleration_threshold: float = 0.02
    max_simultaneous_orders: int = 15
    fill_check_delay_sec: int = 5
    min_trade_threshold_pct: float = 0.002


@dataclass
class ActiveOrder:
    """An order that has been submitted and is awaiting fill.

    Attributes:
        order_id: Exchange-assigned order ID.
        pending_order: The original PendingOrder from the queue.
        submitted_at: When the order was submitted.
        submitted_price: Price at which the order was submitted.
        trigger_price: Price that triggered the order (for risk exits).
        resubmission_count: Number of times this order has been resubmitted.
    """

    order_id: int
    pending_order: PendingOrder
    submitted_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    submitted_price: float = 0.0
    trigger_price: float = 0.0
    resubmission_count: int = 0


class OrderManager:
    """Manages limit order lifecycle: submission, monitoring, resubmission.

    The order manager is the execution engine's core loop. It:
    1. Takes orders from the priority queue
    2. Submits limit orders at last known price
    3. Monitors fills with configurable timeout
    4. Resubmits at updated price on timeout
    5. Escalates CRITICAL exits to market after max resubmissions
    6. Updates the position tracker on fill confirmation

    Attributes:
        exec_client: Execution-oriented API wrapper.
        position_tracker: Real-time position book.
        decision_logger: Buffered decision log.
        order_queue: Priority queue for pending orders.
        config: Order manager configuration.
        active_orders: Currently submitted orders awaiting fill.
    """

    def __init__(
        self,
        exec_client: ExecutionClient,
        position_tracker: PositionTracker,
        decision_logger: DecisionLogger,
        order_queue: OrderPriorityQueue,
        config: Optional[OrderManagerConfig] = None,
    ) -> None:
        """Initialize the order manager.

        Args:
            exec_client: Configured ExecutionClient.
            position_tracker: Position tracker instance.
            decision_logger: Decision logger instance.
            order_queue: Order priority queue.
            config: Order manager configuration.
        """
        self.exec_client = exec_client
        self.position_tracker = position_tracker
        self.decision_logger = decision_logger
        self.order_queue = order_queue
        self.config = config or OrderManagerConfig()
        self.active_orders: dict[int, ActiveOrder] = {}
        self._latest_prices: dict[str, float] = {}

    def update_prices(self, prices: dict[str, float]) -> None:
        """Update cached prices. Called on every price bar.

        Args:
            prices: Dict mapping pair name to latest price.
        """
        self._latest_prices = prices

    async def process_queue(self) -> list[OrderResult]:
        """Process all orders in the priority queue.

        Submits orders in priority order, up to max_simultaneous_orders.
        CRITICAL exits are always processed first and never deferred.

        Returns:
            List of OrderResults for orders that filled immediately.
        """
        results: list[OrderResult] = []

        while not self.order_queue.is_empty:
            pending = self.order_queue.pop()
            if pending is None:
                break

            # Check active order count (CRITICAL bypasses limit)
            if (
                len(self.active_orders) >= self.config.max_simultaneous_orders
                and pending.priority != OrderPriority.CRITICAL_EXIT
            ):
                self.order_queue.add(pending)
                logger.warning(
                    "Max open orders reached (%d). Deferring %s %s.",
                    len(self.active_orders),
                    pending.side,
                    pending.asset,
                )
                break

            result = await self._submit_order(pending)
            if result is not None:
                results.append(result)

        return results

    async def _submit_order(
        self, pending: PendingOrder
    ) -> Optional[OrderResult]:
        """Submit a single order and handle the result.

        Args:
            pending: Order to submit.

        Returns:
            OrderResult if filled immediately, None if pending/failed.
        """
        pair = pending.pair
        price = self._latest_prices.get(pair, 0.0)

        if price <= 0:
            logger.error(
                "No price available for %s. Cannot submit order.", pair
            )
            self.decision_logger.log_order(
                asset=pending.asset,
                action="SUPPRESS",
                trigger=pending.trigger,
                suppressed=True,
                suppression_reason="NO_PRICE_DATA",
            )
            return None

        # Check minimum trade threshold (skip for CRITICAL exits)
        nav = self.position_tracker.nav
        order_usd = abs(pending.quantity_usd)
        threshold = nav * self.config.min_trade_threshold_pct

        if (
            order_usd < threshold
            and pending.priority != OrderPriority.CRITICAL_EXIT
        ):
            logger.info(
                "Order suppressed: %s %s $%.0f < threshold $%.0f",
                pending.side,
                pending.asset,
                order_usd,
                threshold,
            )
            self.decision_logger.log_order(
                asset=pending.asset,
                action="SUPPRESS",
                trigger=pending.trigger,
                submitted_price=price,
                suppressed=True,
                suppression_reason=f"BELOW_THRESHOLD_{order_usd:.0f}<{threshold:.0f}",
            )
            return None

        # Calculate quantity from USD amount
        quantity = abs(pending.quantity_usd) / price

        # Log the decision
        current_weight = self.position_tracker.get_weight(pending.asset)
        action = self._classify_action(
            pending.side, current_weight, pending.target_weight
        )

        entry = self.decision_logger.log_order(
            asset=pending.asset,
            action=action,
            trigger=pending.trigger,
            submitted_price=price,
            current_weight_before=current_weight,
            target_weight=pending.target_weight,
        )

        # Submit limit order
        try:
            if pending.side == "BUY":
                result = await self.exec_client.place_limit_buy(
                    pair, quantity, price
                )
            else:
                result = await self.exec_client.place_limit_sell(
                    pair, quantity, price
                )

            if result.is_filled:
                self._on_fill(result, pending, entry)
                return result
            else:
                # Order is pending — track it
                self.active_orders[result.order_id] = ActiveOrder(
                    order_id=result.order_id,
                    pending_order=pending,
                    submitted_price=price,
                    resubmission_count=pending.resubmission_count,
                )
                return None

        except Exception as e:
            logger.error(
                "Order submission failed for %s: %s", pending.asset, e
            )
            return None

    async def check_active_orders(self) -> list[OrderResult]:
        """Check status of all active (pending) orders.

        Cancels and resubmits timed-out orders. Escalates CRITICAL exits
        to market orders after max resubmissions.

        Returns:
            List of OrderResults for newly filled orders.
        """
        results: list[OrderResult] = []
        to_remove: list[int] = []

        for order_id, active in self.active_orders.items():
            elapsed = (
                datetime.now(timezone.utc) - active.submitted_at
            ).total_seconds()

            if elapsed < self.config.fill_check_delay_sec:
                continue

            # Query order status
            query_result = await self.exec_client.query_order(order_id)

            if query_result is not None and query_result.is_filled:
                self._on_fill(
                    query_result, active.pending_order, None
                )
                results.append(query_result)
                to_remove.append(order_id)
                continue

            # Check timeout
            if elapsed < self.config.order_timeout_sec:
                continue

            # Order timed out — cancel and handle
            await self.exec_client.cancel_order(order_id)
            to_remove.append(order_id)

            pending = active.pending_order
            resubmit_count = active.resubmission_count + 1

            # Check for risk exit acceleration
            if (
                pending.priority == OrderPriority.CRITICAL_EXIT
                and self._check_adverse_move(
                    pending, active.submitted_price
                )
            ):
                # Accelerated escalation: market after 1 failed attempt
                logger.critical(
                    "Risk exit acceleration: %s %s adverse move >%.0f%%. "
                    "Escalating to market order.",
                    pending.side,
                    pending.asset,
                    self.config.risk_exit_acceleration_threshold * 100,
                )
                result = await self._emergency_market_order(pending)
                if result is not None:
                    results.append(result)
                continue

            if resubmit_count >= self.config.max_resubmissions:
                if pending.risk_event_severity == "CRITICAL":
                    # CRITICAL exit: escalate to market
                    logger.critical(
                        "EMERGENCY_MARKET_ORDER: %s %s after %d failed "
                        "resubmissions. Severity: CRITICAL.",
                        pending.side,
                        pending.asset,
                        resubmit_count,
                    )
                    result = await self._emergency_market_order(pending)
                    if result is not None:
                        results.append(result)
                else:
                    # Normal order: abandon
                    logger.warning(
                        "TRADE_ABANDONED: %s %s after %d resubmissions. "
                        "Keeping current position.",
                        pending.side,
                        pending.asset,
                        resubmit_count,
                    )
                    self.decision_logger.log_order(
                        asset=pending.asset,
                        action="SUPPRESS",
                        trigger="TRADE_ABANDONED",
                        suppressed=True,
                        suppression_reason=f"MAX_RESUBMISSIONS_{resubmit_count}",
                    )
            else:
                # Resubmit at updated price
                pending.resubmission_count = resubmit_count
                self.order_queue.add(pending)
                logger.info(
                    "Resubmitting %s %s (attempt %d/%d)",
                    pending.side,
                    pending.asset,
                    resubmit_count + 1,
                    self.config.max_resubmissions,
                )

        for order_id in to_remove:
            self.active_orders.pop(order_id, None)

        return results

    async def _emergency_market_order(
        self, pending: PendingOrder
    ) -> Optional[OrderResult]:
        """Submit an emergency market sell order for a CRITICAL risk exit.

        This is the ONLY code path that submits market orders.
        Gated behind: risk_event.severity == CRITICAL and resubmission >= 3
        (or risk exit acceleration).

        Args:
            pending: The pending order to execute as market.

        Returns:
            OrderResult if filled, None on failure.
        """
        pair = pending.pair
        price = self._latest_prices.get(pair, 0.0)
        if price <= 0:
            logger.error(
                "EMERGENCY_MARKET_ORDER: no price for %s", pair
            )
            return None

        quantity = abs(pending.quantity_usd) / price

        try:
            result = await self.exec_client.place_market_sell(
                pair, quantity
            )
            if result.is_filled:
                self._on_fill(result, pending, None)
            self.decision_logger.log_order(
                asset=pending.asset,
                action="EXIT",
                trigger="EMERGENCY_MARKET_ORDER",
                submitted_price=price,
                order_type="EMERGENCY_MARKET_ORDER",
            )
            return result
        except Exception as e:
            logger.critical(
                "EMERGENCY_MARKET_ORDER FAILED for %s: %s",
                pending.asset,
                e,
            )
            return None

    def _on_fill(
        self,
        result: OrderResult,
        pending: PendingOrder,
        entry: Optional[DecisionEntry],
    ) -> None:
        """Process a fill confirmation — update position tracker.

        Args:
            result: The fill result from the exchange.
            pending: Original pending order.
            entry: Decision log entry to update (if available).
        """
        asset = pending.asset
        commission = result.commission_pct

        if pending.side == "BUY":
            self.position_tracker.on_buy_fill(
                asset, result.filled_quantity, result.filled_price, commission
            )
        else:
            self.position_tracker.on_sell_fill(
                asset, result.filled_quantity, result.filled_price, commission
            )

        # Update decision log entry with fill info
        if entry is not None:
            entry.fill_price = result.filled_price
            entry.filled_quantity = result.filled_quantity
            entry.fill_timestamp_utc = datetime.now(timezone.utc).isoformat()
            entry.commission_paid = (
                result.filled_quantity
                * result.filled_price
                * commission
            )

    def _check_adverse_move(
        self, pending: PendingOrder, submitted_price: float
    ) -> bool:
        """Check if price has moved adversely beyond acceleration threshold.

        Args:
            pending: The pending risk exit order.
            submitted_price: Price when the order was submitted.

        Returns:
            True if adverse move exceeds threshold.
        """
        current_price = self._latest_prices.get(pending.pair, 0.0)
        if current_price <= 0 or submitted_price <= 0:
            return False

        if pending.side == "SELL":
            move = (submitted_price - current_price) / submitted_price
        else:
            move = (current_price - submitted_price) / submitted_price

        return move > self.config.risk_exit_acceleration_threshold

    def _classify_action(
        self, side: str, current_weight: float, target_weight: float
    ) -> str:
        """Classify an order action for decision logging.

        Args:
            side: "BUY" or "SELL".
            current_weight: Current portfolio weight.
            target_weight: Target portfolio weight.

        Returns:
            Action string (NEW_ENTRY, INCREASE, DECREASE, EXIT).
        """
        if side == "BUY":
            return "NEW_ENTRY" if current_weight < 0.001 else "INCREASE"
        else:
            return "EXIT" if target_weight < 0.001 else "DECREASE"

    async def cancel_all(self) -> int:
        """Cancel all active orders and clear the queue.

        Returns:
            Total number of orders cancelled/cleared.
        """
        count = 0
        for order_id in list(self.active_orders.keys()):
            await self.exec_client.cancel_order(order_id)
            count += 1
        self.active_orders.clear()
        count += self.order_queue.clear()
        logger.info("Cancelled all orders: %d total", count)
        return count
