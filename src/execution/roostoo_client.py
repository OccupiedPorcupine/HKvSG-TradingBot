"""Execution-oriented wrapper around the base Roostoo API client.

Adds exchange info caching, price/quantity formatting to correct precision,
and order response parsing. The execution engine uses this instead of the
raw API client directly.

Phase 0: Validates exchange info loading, precision mapping, and order
field parsing. Full execution flow tested via scripts/phase0_execution_test.py.
"""

import logging
from dataclasses import dataclass
from typing import Any, Optional

from src.data.api_client import RoostooClient, RoostooAPIError

logger = logging.getLogger(__name__)

# Re-export for convenience
__all__ = ["ExecutionClient", "PairInfo", "OrderResult", "RoostooAPIError"]


@dataclass(frozen=True)
class PairInfo:
    """Trading pair metadata from exchange info.

    Attributes:
        pair: Trading pair string (e.g., "BTC/USD").
        coin: Base asset symbol (e.g., "BTC").
        price_precision: Decimal places for price formatting.
        amount_precision: Decimal places for quantity formatting.
        min_order: Minimum order quantity.
        can_trade: Whether the pair is currently tradeable.
    """

    pair: str
    coin: str
    price_precision: int
    amount_precision: int
    min_order: float
    can_trade: bool


@dataclass
class OrderResult:
    """Parsed result from an order placement or query.

    Attributes:
        order_id: Exchange-assigned order ID.
        status: Order status string (e.g., "FILLED", "PENDING").
        pair: Trading pair.
        side: "BUY" or "SELL".
        requested_price: Price submitted with the order.
        filled_price: Average fill price (0 if not filled).
        requested_quantity: Quantity submitted.
        filled_quantity: Quantity filled (0 if not filled).
        commission_pct: Commission as a decimal (e.g., 0.0005 = 0.05%).
        role: Maker/taker role string.
        is_filled: Whether the order is fully filled.
        raw: Full raw response dict for debugging.
    """

    order_id: int
    status: str
    pair: str
    side: str
    requested_price: float
    filled_price: float
    requested_quantity: float
    filled_quantity: float
    commission_pct: float
    role: str
    is_filled: bool
    raw: dict


class ExecutionClient:
    """Execution-layer wrapper around the Roostoo API client.

    Caches exchange info (pair precision, min orders) and provides
    formatted order submission with correct decimal precision.

    Attributes:
        client: Underlying Roostoo API client.
        pair_info: Cached mapping of pair name to PairInfo.
    """

    def __init__(self, client: RoostooClient) -> None:
        """Initialize with an existing API client.

        Args:
            client: Configured RoostooClient instance.
        """
        self.client = client
        self.pair_info: dict[str, PairInfo] = {}

    async def load_exchange_info(self) -> dict[str, PairInfo]:
        """Fetch and cache exchange info for all trading pairs.

        Must be called before placing orders. Populates self.pair_info
        with precision and min order data for every pair.

        Returns:
            Dict mapping pair name to PairInfo.
        """
        resp = await self.client.get_exchange_info()
        trade_pairs = resp.get("TradePairs", {})

        self.pair_info.clear()
        for pair_name, info in trade_pairs.items():
            self.pair_info[pair_name] = PairInfo(
                pair=pair_name,
                coin=info.get("Coin", pair_name.split("/")[0]),
                price_precision=int(info.get("PricePrecision", 2)),
                amount_precision=int(info.get("AmountPrecision", 6)),
                min_order=float(info.get("MiniOrder", 1)),
                can_trade=bool(info.get("CanTrade", False)),
            )

        logger.info(
            "Loaded exchange info: %d pairs (%d tradeable)",
            len(self.pair_info),
            sum(1 for p in self.pair_info.values() if p.can_trade),
        )
        return self.pair_info

    def get_pair_info(self, pair: str) -> PairInfo:
        """Get cached pair info. Raises KeyError if not loaded.

        Args:
            pair: Trading pair string (e.g., "BTC/USD").

        Returns:
            PairInfo for the requested pair.

        Raises:
            KeyError: If pair not found in cached exchange info.
        """
        if pair not in self.pair_info:
            raise KeyError(
                f"Pair {pair!r} not in exchange info. "
                f"Call load_exchange_info() first. "
                f"Available: {len(self.pair_info)} pairs."
            )
        return self.pair_info[pair]

    def format_price(self, pair: str, price: float) -> str:
        """Format a price to the correct precision for a pair.

        Args:
            pair: Trading pair string.
            price: Raw price value.

        Returns:
            Price as a string with correct decimal places.
        """
        info = self.get_pair_info(pair)
        return f"{price:.{info.price_precision}f}"

    def format_quantity(self, pair: str, quantity: float) -> str:
        """Format a quantity to the correct precision for a pair.

        Args:
            pair: Trading pair string.
            quantity: Raw quantity value.

        Returns:
            Quantity as a string with correct decimal places.
        """
        info = self.get_pair_info(pair)
        return f"{quantity:.{info.amount_precision}f}"

    async def get_last_price(self, pair: str) -> float:
        """Fetch the last traded price for a single pair.

        Args:
            pair: Trading pair string.

        Returns:
            Last price as float.

        Raises:
            RoostooAPIError: If the ticker request fails.
            ValueError: If price cannot be extracted.
        """
        resp = await self.client.get_ticker(pair)
        data = resp.get("Data", {})
        pair_data = data.get(pair, {})
        price = pair_data.get("LastPrice")
        if price is None:
            raise ValueError(f"No LastPrice in ticker for {pair}: {data}")
        return float(price)

    async def get_all_prices(self) -> dict[str, float]:
        """Fetch last prices for all pairs in a single batch call.

        Returns:
            Dict mapping pair name to last price.
        """
        resp = await self.client.get_all_tickers()
        data = resp.get("Data", {})
        prices: dict[str, float] = {}
        for pair_name, ticker in data.items():
            last = ticker.get("LastPrice")
            if last is not None:
                prices[pair_name] = float(last)
        return prices

    async def place_limit_buy(
        self, pair: str, quantity: float, price: float
    ) -> OrderResult:
        """Place a limit buy order with correct precision formatting.

        Args:
            pair: Trading pair string.
            quantity: Quantity to buy.
            price: Limit price.

        Returns:
            Parsed OrderResult.
        """
        return await self._place_limit(pair, "BUY", quantity, price)

    async def place_limit_sell(
        self, pair: str, quantity: float, price: float
    ) -> OrderResult:
        """Place a limit sell order with correct precision formatting.

        Args:
            pair: Trading pair string.
            quantity: Quantity to sell.
            price: Limit price.

        Returns:
            Parsed OrderResult.
        """
        return await self._place_limit(pair, "SELL", quantity, price)

    async def _place_limit(
        self, pair: str, side: str, quantity: float, price: float
    ) -> OrderResult:
        """Internal: place a limit order with precision formatting.

        Args:
            pair: Trading pair string.
            side: "BUY" or "SELL".
            quantity: Raw quantity.
            price: Raw price.

        Returns:
            Parsed OrderResult.
        """
        qty_str = self.format_quantity(pair, quantity)
        price_str = self.format_price(pair, price)

        logger.info(
            "Placing LIMIT %s: %s %s @ %s", side, qty_str, pair, price_str
        )

        resp = await self.client.place_limit_order(
            pair=pair, side=side, quantity=qty_str, price=price_str
        )
        return self._parse_order_result(resp, pair, side, price, quantity)

    async def place_market_sell(
        self, pair: str, quantity: float
    ) -> OrderResult:
        """Place an emergency market sell order.

        ONLY for CRITICAL risk exits after 3 failed limit resubmissions.
        This method must only be called when:
            risk_event.severity == CRITICAL and resubmission_count >= 3

        Args:
            pair: Trading pair string.
            quantity: Quantity to sell.

        Returns:
            Parsed OrderResult.
        """
        qty_str = self.format_quantity(pair, quantity)

        logger.critical(
            "EMERGENCY_MARKET_ORDER: SELL %s %s", qty_str, pair
        )

        resp = await self.client.place_order(
            pair=pair,
            side="SELL",
            order_type="MARKET",
            quantity=qty_str,
        )
        return self._parse_order_result(resp, pair, "SELL", 0.0, quantity)

    async def query_order(self, order_id: int) -> Optional[OrderResult]:
        """Query an order by ID.

        Args:
            order_id: Exchange order ID.

        Returns:
            OrderResult if found, None otherwise.
        """
        resp = await self.client.query_order(order_id=str(order_id))
        matched = resp.get("OrderMatched", [])
        if not matched:
            return None

        order = matched[0]
        return OrderResult(
            order_id=int(order.get("OrderID", order_id)),
            status=str(order.get("Status", "UNKNOWN")),
            pair=str(order.get("Pair", "")),
            side=str(order.get("Side", "")),
            requested_price=float(order.get("Price", 0)),
            filled_price=float(order.get("FilledAverPrice", 0)),
            requested_quantity=float(order.get("Quantity", 0)),
            filled_quantity=float(order.get("FilledQuantity", 0)),
            commission_pct=float(order.get("CommissionPercent", 0)),
            role=str(order.get("Role", "")),
            is_filled=str(order.get("Status", "")).upper() == "FILLED",
            raw=order,
        )

    async def cancel_order(self, order_id: int) -> bool:
        """Cancel a specific order.

        Args:
            order_id: Exchange order ID to cancel.

        Returns:
            True if the order was successfully cancelled.
        """
        try:
            resp = await self.client.cancel_order(order_id=str(order_id))
            canceled = resp.get("CanceledList", [])
            success = order_id in canceled
            if success:
                logger.info("Cancelled order %d", order_id)
            else:
                logger.warning(
                    "Cancel request for order %d returned: %s",
                    order_id,
                    canceled,
                )
            return success
        except RoostooAPIError as e:
            logger.warning("Cancel order %d failed: %s", order_id, e)
            return False

    async def cancel_pair_orders(self, pair: str) -> list[int]:
        """Cancel all pending orders for a pair.

        Args:
            pair: Trading pair string.

        Returns:
            List of cancelled order IDs.
        """
        try:
            resp = await self.client.cancel_order(pair=pair)
            canceled = resp.get("CanceledList", [])
            if canceled:
                logger.info("Cancelled %d orders for %s", len(canceled), pair)
            return canceled
        except RoostooAPIError as e:
            logger.warning("Cancel orders for %s failed: %s", pair, e)
            return []

    async def get_balance(self) -> dict[str, dict[str, float]]:
        """Fetch account balances.

        Returns:
            Dict mapping currency to {"free": float, "locked": float}.
        """
        resp = await self.client.get_balance()
        wallet = resp.get("Wallet", {})
        balances: dict[str, dict[str, float]] = {}
        for currency, info in wallet.items():
            if isinstance(info, dict):
                balances[currency] = {
                    "free": float(info.get("Free", 0)),
                    "locked": float(info.get("Lock", 0)),
                }
        return balances

    async def close(self) -> None:
        """Close the underlying API client session."""
        await self.client.close()

    def _parse_order_result(
        self,
        resp: dict[str, Any],
        pair: str,
        side: str,
        requested_price: float,
        requested_quantity: float,
    ) -> OrderResult:
        """Parse an order placement response into OrderResult.

        Args:
            resp: Raw API response.
            pair: Trading pair.
            side: Order side.
            requested_price: Price submitted.
            requested_quantity: Quantity submitted.

        Returns:
            Parsed OrderResult.
        """
        detail = resp.get("OrderDetail", {})

        order_id = int(detail.get("OrderID", 0))
        status = str(detail.get("Status", "UNKNOWN"))
        filled_price = float(detail.get("FilledAverPrice", 0))
        filled_qty = float(detail.get("FilledQuantity", 0))
        commission = float(detail.get("CommissionPercent", 0))
        role = str(detail.get("Role", ""))

        is_filled = status.upper() == "FILLED"

        result = OrderResult(
            order_id=order_id,
            status=status,
            pair=pair,
            side=side,
            requested_price=requested_price,
            filled_price=filled_price,
            requested_quantity=requested_quantity,
            filled_quantity=filled_qty,
            commission_pct=commission,
            role=role,
            is_filled=is_filled,
            raw=detail,
        )

        if is_filled:
            logger.info(
                "Order %d FILLED: %s %s %.6f @ %.4f (commission: %.4f%%)",
                order_id,
                side,
                pair,
                filled_qty,
                filled_price,
                commission * 100,
            )
        else:
            logger.info(
                "Order %d %s: %s %s (pending)", order_id, status, side, pair
            )

        return result
