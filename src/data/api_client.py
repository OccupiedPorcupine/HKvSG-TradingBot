"""Roostoo mock exchange API client.

Handles authentication (HMAC-SHA256 signing), request construction,
and response parsing for all Roostoo API endpoints. All HTTP calls
are async via aiohttp and go through the shared rate limiter.
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Any, Optional

import aiohttp

from src.data.rate_limiter import TokenBucketRateLimiter

logger = logging.getLogger(__name__)

# Roostoo API security levels
# RCL_NoVerification: no auth needed (serverTime, exchangeInfo)
# RCL_TSCheck: timestamp parameter required (ticker)
# RCL_TopLevelCheck: timestamp + API key header + HMAC signature (balance, orders)


class RoostooAPIError(Exception):
    """Raised when the Roostoo API returns an error response."""

    def __init__(self, message: str, status_code: int = 0, response: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class RoostooClient:
    """Async client for the Roostoo mock exchange API.

    All calls go through the shared rate limiter. Authentication is handled
    automatically based on endpoint security level.

    Attributes:
        base_url: API base URL.
        api_key: API key loaded from environment variable.
        api_secret: API secret loaded from environment variable.
        rate_limiter: Shared token bucket rate limiter.
        session: aiohttp client session (created on first use).
    """

    def __init__(
        self,
        base_url: str,
        api_key_env: str = "ROOSTOO_API_KEY",
        api_secret_env: str = "ROOSTOO_API_SECRET",
        rate_limiter: Optional[TokenBucketRateLimiter] = None,
    ) -> None:
        """Initialize the API client.

        Args:
            base_url: Roostoo API base URL.
            api_key_env: Environment variable name for API key.
            api_secret_env: Environment variable name for API secret.
            rate_limiter: Shared rate limiter instance.
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = os.environ.get(api_key_env, "")
        self.api_secret = os.environ.get(api_secret_env, "")
        self.rate_limiter = rate_limiter or TokenBucketRateLimiter()
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _timestamp_ms(self) -> str:
        """Generate 13-digit millisecond timestamp as string."""
        return str(int(time.time() * 1000))

    def _sign(self, params: dict[str, Any]) -> str:
        """Generate HMAC-SHA256 signature for request parameters.

        Parameters are sorted alphabetically by key, joined as
        key=value&key=value, then signed with the API secret.

        Args:
            params: Request parameters to sign.

        Returns:
            Hex digest of the HMAC-SHA256 signature.
        """
        query_string = "&".join(
            f"{k}={params[k]}" for k in sorted(params.keys())
        )
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _signed_headers(self, params: dict[str, Any]) -> dict[str, str]:
        """Build headers for signed (RCL_TopLevelCheck) endpoints.

        Args:
            params: Request parameters to sign.

        Returns:
            Dict with RST-API-KEY and MSG-SIGNATURE headers.
        """
        return {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": self._sign(params),
        }

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
        signed: bool = False,
        priority: bool = False,
    ) -> dict[str, Any]:
        """Make a rate-limited API request.

        Args:
            method: HTTP method ("GET" or "POST").
            endpoint: API endpoint path (e.g., "/v3/ticker").
            params: Request parameters.
            signed: Whether to sign the request (RCL_TopLevelCheck).
            priority: Whether to use priority rate limit tokens.

        Returns:
            Parsed JSON response as dict.

        Raises:
            RoostooAPIError: On API error or HTTP error.
        """
        await self.rate_limiter.acquire(priority=priority)

        url = f"{self.base_url}{endpoint}"
        params = params or {}
        headers: dict[str, str] = {}

        if signed:
            headers = self._signed_headers(params)

        session = await self._get_session()

        try:
            if method == "GET":
                async with session.get(url, params=params, headers=headers) as resp:
                    return await self._handle_response(resp, endpoint)
            else:  # POST
                headers["Content-Type"] = "application/x-www-form-urlencoded"
                async with session.post(url, data=params, headers=headers) as resp:
                    return await self._handle_response(resp, endpoint)

        except aiohttp.ClientError as e:
            logger.error("HTTP error on %s: %s", endpoint, e)
            raise RoostooAPIError(f"HTTP error: {e}") from e

    async def _handle_response(
        self, resp: aiohttp.ClientResponse, endpoint: str
    ) -> dict[str, Any]:
        """Parse and validate API response.

        Args:
            resp: aiohttp response object.
            endpoint: Endpoint path for logging.

        Returns:
            Parsed JSON response.

        Raises:
            RoostooAPIError: On 429 (rate limit) or other errors.
        """
        if resp.status == 429:
            self.rate_limiter.record_429()
            delay = self.rate_limiter.get_backoff_delay()
            logger.warning(
                "429 on %s, backing off %.1fs", endpoint, delay
            )
            raise RoostooAPIError(
                f"Rate limited on {endpoint}", status_code=429
            )

        self.rate_limiter.record_success()

        if resp.status != 200:
            text = await resp.text()
            logger.error(
                "API error %d on %s: %s", resp.status, endpoint, text
            )
            raise RoostooAPIError(
                f"HTTP {resp.status}: {text}", status_code=resp.status
            )

        # Roostoo returns Content-Type: text/plain, not application/json.
        # Pass content_type=None to skip aiohttp's content-type check.
        data = await resp.json(content_type=None)

        # Check Roostoo-level success flag
        if isinstance(data, dict) and "Success" in data and not data["Success"]:
            err_msg = data.get("ErrMsg", "Unknown API error")
            logger.warning("API returned Success=false on %s: %s", endpoint, err_msg)
            raise RoostooAPIError(err_msg, response=data)

        return data

    # -------------------------------------------------------------------------
    # Public endpoints (no auth)
    # -------------------------------------------------------------------------

    async def get_server_time(self) -> dict[str, Any]:
        """Fetch server time (RCL_NoVerification).

        Returns:
            Dict with 'ServerTime' key (13-digit ms timestamp).
        """
        return await self._request("GET", "/v3/serverTime")

    async def get_exchange_info(self) -> dict[str, Any]:
        """Fetch exchange info including all trading pairs (RCL_NoVerification).

        Returns:
            Dict with 'IsRunning', 'InitialWallet', 'TradePairs' keys.
        """
        return await self._request("GET", "/v3/exchangeInfo")

    # -------------------------------------------------------------------------
    # Timestamp-only endpoints (RCL_TSCheck)
    # -------------------------------------------------------------------------

    async def get_all_tickers(self) -> dict[str, Any]:
        """Fetch all ticker prices in a single batch call.

        Returns:
            Dict with 'Data' containing all pair tickers.
        """
        params = {"timestamp": self._timestamp_ms()}
        return await self._request("GET", "/v3/ticker", params=params)

    async def get_ticker(self, pair: str) -> dict[str, Any]:
        """Fetch ticker for a single pair.

        Args:
            pair: Trading pair (e.g., "BTC/USD").

        Returns:
            Dict with 'Data' containing the pair's ticker.
        """
        params = {
            "timestamp": self._timestamp_ms(),
            "pair": pair,
        }
        return await self._request("GET", "/v3/ticker", params=params)

    # -------------------------------------------------------------------------
    # Signed endpoints (RCL_TopLevelCheck)
    # -------------------------------------------------------------------------

    async def get_balance(self) -> dict[str, Any]:
        """Fetch account balance.

        Returns:
            Dict with 'Wallet' containing currency balances (Free/Lock).
        """
        params = {"timestamp": self._timestamp_ms()}
        return await self._request(
            "GET", "/v3/balance", params=params, signed=True, priority=True
        )

    async def get_pending_count(self) -> dict[str, Any]:
        """Fetch count of pending orders.

        Returns:
            Dict with 'TotalPending' and 'OrderPairs'.
        """
        params = {"timestamp": self._timestamp_ms()}
        return await self._request(
            "GET", "/v3/pending_count", params=params, signed=True, priority=True
        )

    async def place_order(
        self,
        pair: str,
        side: str,
        order_type: str,
        quantity: str,
        price: Optional[str] = None,
    ) -> dict[str, Any]:
        """Place an order.

        Args:
            pair: Trading pair (e.g., "BTC/USD").
            side: "BUY" or "SELL".
            order_type: "LIMIT" (market orders disabled except emergency).
            quantity: Order quantity as string.
            price: Limit price as string (required for LIMIT orders).

        Returns:
            Dict with 'OrderDetail' containing order info.
        """
        params: dict[str, Any] = {
            "pair": pair,
            "side": side,
            "type": order_type,
            "quantity": quantity,
            "timestamp": self._timestamp_ms(),
        }
        if price is not None:
            params["price"] = price

        return await self._request(
            "POST", "/v3/place_order", params=params, signed=True, priority=True
        )

    async def query_order(
        self,
        order_id: Optional[str] = None,
        pair: Optional[str] = None,
        pending_only: Optional[bool] = None,
        offset: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        """Query orders.

        Args:
            order_id: Specific order ID to query.
            pair: Filter by trading pair.
            pending_only: If True, return only pending orders.
            offset: Pagination offset.
            limit: Results per page.

        Returns:
            Dict with 'OrderMatched' list of order details.
        """
        params: dict[str, Any] = {"timestamp": self._timestamp_ms()}

        if order_id is not None:
            params["order_id"] = str(order_id)
        if pair is not None:
            params["pair"] = pair
        if pending_only is not None:
            params["pending_only"] = "TRUE" if pending_only else "FALSE"
        if offset is not None:
            params["offset"] = str(offset)
        if limit is not None:
            params["limit"] = str(limit)

        return await self._request(
            "POST", "/v3/query_order", params=params, signed=True, priority=True
        )

    async def cancel_order(
        self,
        order_id: Optional[str] = None,
        pair: Optional[str] = None,
    ) -> dict[str, Any]:
        """Cancel order(s).

        Args:
            order_id: Specific order ID to cancel.
            pair: Cancel all pending orders for this pair.

        Returns:
            Dict with 'CanceledList' of canceled order IDs.
        """
        params: dict[str, Any] = {"timestamp": self._timestamp_ms()}

        if order_id is not None:
            params["order_id"] = str(order_id)
        if pair is not None:
            params["pair"] = pair

        return await self._request(
            "POST", "/v3/cancel_order", params=params, signed=True, priority=True
        )

    # -------------------------------------------------------------------------
    # Convenience methods
    # -------------------------------------------------------------------------

    async def place_limit_order(
        self, pair: str, side: str, quantity: str, price: str
    ) -> dict[str, Any]:
        """Place a limit order (convenience wrapper).

        Args:
            pair: Trading pair (e.g., "BTC/USD").
            side: "BUY" or "SELL".
            quantity: Order quantity as string.
            price: Limit price as string.

        Returns:
            Dict with 'OrderDetail'.
        """
        return await self.place_order(
            pair=pair,
            side=side,
            order_type="LIMIT",
            quantity=quantity,
            price=price,
        )
