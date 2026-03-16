#!/usr/bin/env python3
"""APEX Phase 0 — Roostoo API Validation Script

Systematically tests every Phase 0 question before strategy implementation:

  Test 0: Server time and clock synchronization
  Test 1: Exchange info — discover pairs, precision, min orders
  Test 2: Batch ticker — all prices in one call?
  Test 3: Single ticker — field analysis, volume data?
  Test 4: Timestamp format — seconds vs milliseconds
  Test 5: Account balance — verify starting capital
  Test 6: Limit order fill mechanics — immediate fill?
  Test 7: Cancel/pending order endpoints
  Test 8: Rate limit — empirical discovery

Usage::

    export ROOSTOO_API_KEY=your_key
    export ROOSTOO_API_SECRET=your_secret
    python scripts/api_test.py

Results printed to stdout and saved to logs/phase0_results.json.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiohttp

# ---------------------------------------------------------------------------
# Project setup
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from apex.core.config import Config  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("phase0")


# ---------------------------------------------------------------------------
# Minimal async API client for testing
# ---------------------------------------------------------------------------
class TestClient:
    """Thin async Roostoo API client for Phase 0 validation.

    Mirrors the signing logic from the official python_demo.py.
    Auth levels: "none", "ts" (timestamp only), "signed" (full HMAC).
    """

    def __init__(self, base_url: str, api_key: str, api_secret: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self) -> "TestClient":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()

    # -- helpers --

    @staticmethod
    def _timestamp_ms() -> int:
        """13-digit millisecond timestamp."""
        return int(time.time() * 1000)

    def _sign(self, params: dict[str, Any]) -> str:
        """HMAC SHA256 signature per Roostoo spec.

        1. Sort params alphabetically by key.
        2. Join as key=value&key=value.
        3. HMAC-SHA256 with secret key.
        """
        query = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return hmac.new(
            self.api_secret.encode(), query.encode(), hashlib.sha256,
        ).hexdigest()

    def _signed_headers(self, params: dict[str, Any]) -> dict[str, str]:
        """Headers for RCL_TopLevelCheck endpoints."""
        return {
            "RST-API-KEY": self.api_key,
            "MSG-SIGNATURE": self._sign(params),
        }

    # -- HTTP verbs --

    async def get(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        auth: str = "none",
    ) -> dict:
        """Send GET request with optional auth level."""
        params = dict(params or {})
        if auth in ("ts", "signed"):
            params.setdefault("timestamp", self._timestamp_ms())
        headers = self._signed_headers(params) if auth == "signed" else {}

        t0 = time.monotonic()
        async with self._session.get(
            f"{self.base_url}{path}", params=params, headers=headers,
        ) as resp:
            text = await resp.text()
            elapsed = time.monotonic() - t0
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                body = {"_raw_text": text}
            return {
                "status": resp.status,
                "body": body,
                "elapsed_ms": round(elapsed * 1000, 1),
                "headers": dict(resp.headers),
            }

    async def post(
        self,
        path: str,
        data: Optional[dict[str, Any]] = None,
    ) -> dict:
        """Send POST request with signed auth (form-encoded body)."""
        data = dict(data or {})
        data.setdefault("timestamp", self._timestamp_ms())
        headers = self._signed_headers(data)

        t0 = time.monotonic()
        async with self._session.post(
            f"{self.base_url}{path}", data=data, headers=headers,
        ) as resp:
            text = await resp.text()
            elapsed = time.monotonic() - t0
            try:
                body = json.loads(text)
            except json.JSONDecodeError:
                body = {"_raw_text": text}
            return {
                "status": resp.status,
                "body": body,
                "elapsed_ms": round(elapsed * 1000, 1),
                "headers": dict(resp.headers),
            }


# ---------------------------------------------------------------------------
# Logging helper
# ---------------------------------------------------------------------------
def _header(title: str) -> None:
    log.info("")
    log.info("=" * 64)
    log.info(f"  {title}")
    log.info("=" * 64)


# ---------------------------------------------------------------------------
# Test 0: Server Time
# ---------------------------------------------------------------------------
async def test_server_time(client: TestClient) -> dict:
    """Verify server reachability and clock synchronization."""
    _header("TEST 0: Server Time & Clock Sync")

    local_before_ms = int(time.time() * 1000)
    result = await client.get("/v3/serverTime")
    local_after_ms = int(time.time() * 1000)

    if result["status"] != 200:
        log.error(f"  FAILED — HTTP {result['status']}: {result['body']}")
        return {"passed": False, "error": str(result["body"])}

    body = result["body"]
    log.info(f"  Response: {body}")

    # Try both casing conventions
    server_time = body.get("ServerTime") or body.get("serverTime")
    if server_time is None:
        log.error(f"  Cannot find ServerTime field in: {body}")
        return {"passed": False, "fields": list(body.keys())}

    server_time = int(server_time)
    local_mid = (local_before_ms + local_after_ms) // 2
    offset_ms = server_time - local_mid
    server_dt = datetime.fromtimestamp(server_time / 1000, tz=timezone.utc)

    log.info(f"  Server time : {server_time}  ({server_dt.isoformat()})")
    log.info(f"  Local time  : {local_mid}")
    log.info(f"  Offset      : {offset_ms:+d} ms")
    log.info(f"  Latency     : {result['elapsed_ms']} ms")

    ok = abs(offset_ms) < 60_000
    log.info(f"  {'PASSED' if ok else 'WARNING — offset > 60 s, sync clocks'}")

    return {
        "passed": ok,
        "server_time": server_time,
        "clock_offset_ms": offset_ms,
        "latency_ms": result["elapsed_ms"],
        "response_fields": list(body.keys()),
    }


# ---------------------------------------------------------------------------
# Test 1: Exchange Info
# ---------------------------------------------------------------------------
async def test_exchange_info(client: TestClient, config: Config) -> dict:
    """Discover all trading pairs, precision rules, and min order sizes."""
    _header("TEST 1: Exchange Info (Universe Discovery)")

    result = await client.get("/v3/exchangeInfo")

    if result["status"] != 200:
        log.error(f"  FAILED — HTTP {result['status']}: {result['body']}")
        return {"passed": False, "error": str(result["body"])}

    body = result["body"]
    log.info(f"  Top-level keys: {list(body.keys())}")

    # --- Discover pair data ---
    # The response structure is not fully documented. Try common patterns.
    pairs_data: dict[str, dict] = {}
    wallet_data: dict[str, dict] = {}

    for key, val in body.items():
        if not isinstance(val, dict):
            continue
        sample = next(iter(val.values()), None)
        if not isinstance(sample, dict):
            continue
        # Pair info contains precision fields
        if any(f in sample for f in ("PricePrecision", "pricePrecision",
                                      "AmountPrecision", "amountPrecision")):
            pairs_data = val
            log.info(f"  Pairs found in '{key}': {len(val)} entries")
        # Wallet info contains Free/Lock
        elif any(f in sample for f in ("Free", "free", "Lock", "lock")):
            wallet_data = val
            log.info(f"  Wallet found in '{key}': {len(val)} entries")

    # If pairs_data is still empty, the structure may be flat or a list
    if not pairs_data and isinstance(body, dict):
        for key, val in body.items():
            if isinstance(val, list) and val and isinstance(val[0], dict):
                log.info(f"  List data in '{key}': {len(val)} items")
                log.info(f"    Sample item: {json.dumps(val[0], indent=2)[:300]}")

    # --- Check our universe against discovered pairs ---
    expected = [config.pair_for(a) for a in config.all_assets()]
    found, missing = [], []
    for pair in expected:
        # Try both "BTC/USD" and "BTCUSD" key formats
        if pair in pairs_data or pair.replace("/", "") in pairs_data:
            found.append(pair)
        else:
            missing.append(pair)

    log.info(f"  Expected pairs : {len(expected)}")
    log.info(f"  Found          : {len(found)}")
    if missing:
        log.warning(f"  Missing ({len(missing)}): {missing}")

    # --- Sample pair detail ---
    if pairs_data:
        sample_key = next(iter(pairs_data))
        sample_val = pairs_data[sample_key]
        log.info(f"  Sample '{sample_key}': {json.dumps(sample_val)}")
        log.info(f"  Pair field names: {list(sample_val.keys())}")

    # --- Initial wallet ---
    if wallet_data:
        nonzero = {
            k: v for k, v in wallet_data.items()
            if isinstance(v, dict)
            and (float(v.get("Free", v.get("free", 0))) > 0
                 or float(v.get("Lock", v.get("lock", 0))) > 0)
        }
        if nonzero:
            log.info(f"  Non-zero wallet entries: {len(nonzero)}")
            for asset, bal in sorted(nonzero.items()):
                free = bal.get("Free", bal.get("free", 0))
                lock = bal.get("Lock", bal.get("lock", 0))
                log.info(f"    {asset}: free={free}  locked={lock}")

    return {
        "passed": True,
        "total_api_pairs": len(pairs_data),
        "expected_pairs": len(expected),
        "found": len(found),
        "missing": missing,
        "pair_fields": (
            list(next(iter(pairs_data.values())).keys()) if pairs_data else []
        ),
        "pairs_data": pairs_data,
        "wallet_data": wallet_data,
        "latency_ms": result["elapsed_ms"],
    }


# ---------------------------------------------------------------------------
# Test 2: Batch Ticker
# ---------------------------------------------------------------------------
async def test_batch_ticker(client: TestClient) -> dict:
    """Does /v3/ticker with no pair parameter return ALL prices at once?"""
    _header("TEST 2: Batch Ticker (All Prices in One Call)")

    result = await client.get("/v3/ticker", auth="ts")

    if result["status"] != 200:
        log.error(f"  FAILED — HTTP {result['status']}: {result['body']}")
        return {"passed": False, "error": str(result["body"])}

    body = result["body"]

    # Count tickers — response may be list, dict, or nested
    ticker_count = 0
    ticker_data: dict = {}
    sample_fields: list[str] = []

    if isinstance(body, list):
        ticker_count = len(body)
        for item in body:
            if isinstance(item, dict):
                pair_key = (
                    item.get("Pair") or item.get("pair")
                    or item.get("symbol") or str(body.index(item))
                )
                ticker_data[pair_key] = item
        if body and isinstance(body[0], dict):
            sample_fields = list(body[0].keys())
    elif isinstance(body, dict):
        # Try finding a nested dict/list of tickers
        for key, val in body.items():
            if isinstance(val, dict) and len(val) > 5:
                ticker_count = len(val)
                ticker_data = val
                sample_val = next(iter(val.values()))
                if isinstance(sample_val, dict):
                    sample_fields = list(sample_val.keys())
                log.info(f"  Tickers in '{key}': {ticker_count}")
                break
            if isinstance(val, list) and len(val) > 5:
                ticker_count = len(val)
                log.info(f"  Tickers in '{key}' (list): {ticker_count}")
                if val and isinstance(val[0], dict):
                    sample_fields = list(val[0].keys())
                break
        if ticker_count == 0:
            # Maybe body itself maps pair -> data
            if len(body) > 5 and all(isinstance(v, dict) for v in body.values()):
                ticker_count = len(body)
                ticker_data = body
                sample_fields = list(next(iter(body.values())).keys())

    batch_available = ticker_count > 1

    log.info(f"  Tickers returned : {ticker_count}")
    log.info(f"  Batch pricing    : {'YES' if batch_available else 'NO'}")
    log.info(f"  Latency          : {result['elapsed_ms']} ms")
    if sample_fields:
        log.info(f"  Ticker fields    : {sample_fields}")

    # Show a few samples
    if ticker_data:
        for key in list(ticker_data)[:3]:
            log.info(f"  Sample '{key}': {ticker_data[key]}")
    elif not batch_available:
        log.info(f"  Raw response: {json.dumps(body)[:500]}")

    return {
        "passed": True,
        "batch_available": batch_available,
        "ticker_count": ticker_count,
        "sample_fields": sample_fields,
        "latency_ms": result["elapsed_ms"],
    }


# ---------------------------------------------------------------------------
# Test 3: Single Ticker
# ---------------------------------------------------------------------------
async def test_single_ticker(client: TestClient) -> dict:
    """Fetch BTC/USD ticker and analyze all response fields."""
    _header("TEST 3: Single Ticker (BTC/USD Field Analysis)")

    result = await client.get(
        "/v3/ticker", params={"pair": "BTC/USD"}, auth="ts",
    )

    if result["status"] != 200:
        log.error(f"  FAILED — HTTP {result['status']}: {result['body']}")
        return {"passed": False, "error": str(result["body"])}

    body = result["body"]
    log.info(f"  Full response:\n{json.dumps(body, indent=2)}")

    # Flatten field names (including nested dicts)
    fields: set[str] = set()
    _collect_fields(body, "", fields)

    volume_fields = [f for f in fields if any(
        kw in f.lower() for kw in ("vol", "amount", "quantity", "turnover")
    )]
    bid_ask_fields = [f for f in fields if any(
        kw in f.lower() for kw in ("bid", "ask", "best")
    )]
    price_fields = [f for f in fields if "price" in f.lower()]

    log.info(f"  All fields     : {sorted(fields)}")
    log.info(f"  Price fields   : {sorted(price_fields)}")
    log.info(f"  Volume fields  : {sorted(volume_fields) or 'NONE'}")
    log.info(f"  Bid/Ask fields : {sorted(bid_ask_fields) or 'NONE'}")

    return {
        "passed": True,
        "fields": sorted(fields),
        "has_volume": bool(volume_fields),
        "volume_fields": volume_fields,
        "has_bid_ask": bool(bid_ask_fields),
        "bid_ask_fields": bid_ask_fields,
        "price_fields": price_fields,
        "response": body,
        "latency_ms": result["elapsed_ms"],
    }


def _collect_fields(obj: Any, prefix: str, out: set[str]) -> None:
    """Recursively collect field names from a JSON structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            full = f"{prefix}.{k}" if prefix else k
            out.add(full)
            _collect_fields(v, full, out)


# ---------------------------------------------------------------------------
# Test 4: Timestamp Format
# ---------------------------------------------------------------------------
async def test_timestamp_format(client: TestClient) -> dict:
    """Determine whether the API accepts seconds, milliseconds, or both."""
    _header("TEST 4: Timestamp Format (Seconds vs Milliseconds)")

    # The demo code uses seconds for /v3/ticker but ms for signed endpoints.
    # Test both on /v3/ticker to confirm.
    ts_sec = int(time.time())
    ts_ms = int(time.time() * 1000)

    r_sec = await client.get(
        "/v3/ticker", params={"timestamp": ts_sec, "pair": "BTC/USD"},
    )
    r_ms = await client.get(
        "/v3/ticker", params={"timestamp": ts_ms, "pair": "BTC/USD"},
    )

    sec_ok = r_sec["status"] == 200 and r_sec["body"].get("Success") is not False
    ms_ok = r_ms["status"] == 200 and r_ms["body"].get("Success") is not False

    log.info(f"  Seconds ({ts_sec:>13d}): HTTP {r_sec['status']} "
             f"{'OK' if sec_ok else 'FAILED'}")
    log.info(f"  Millis  ({ts_ms:>13d}): HTTP {r_ms['status']} "
             f"{'OK' if ms_ok else 'FAILED'}")

    if not sec_ok:
        log.info(f"    Sec error: {r_sec['body']}")
    if not ms_ok:
        log.info(f"    Ms error:  {r_ms['body']}")

    if sec_ok and ms_ok:
        log.info("  RESULT: Both formats accepted")
    elif ms_ok:
        log.info("  RESULT: Milliseconds only")
    elif sec_ok:
        log.info("  RESULT: Seconds only")
    else:
        log.warning("  RESULT: Neither format worked — investigate")

    return {
        "passed": sec_ok or ms_ok,
        "seconds_accepted": sec_ok,
        "milliseconds_accepted": ms_ok,
        "recommendation": "milliseconds" if ms_ok else "seconds",
    }


# ---------------------------------------------------------------------------
# Test 5: Account Balance
# ---------------------------------------------------------------------------
async def test_balance(client: TestClient) -> dict:
    """Verify account balances and starting capital."""
    _header("TEST 5: Account Balance")

    result = await client.get("/v3/balance", auth="signed")

    if result["status"] != 200:
        log.error(f"  FAILED — HTTP {result['status']}: {result['body']}")
        return {"passed": False, "error": str(result["body"])}

    body = result["body"]
    log.info(f"  Top-level keys: {list(body.keys())}")

    # Find balances — may be nested under a key
    bal_dict: dict[str, dict] = {}
    for key, val in body.items():
        if isinstance(val, dict):
            sample = next(iter(val.values()), None)
            if isinstance(sample, dict) and any(
                f in sample for f in ("Free", "free", "Lock", "lock")
            ):
                bal_dict = val
                break
    if not bal_dict:
        # Maybe body itself is the balance dict
        sample = next(iter(body.values()), None)
        if isinstance(sample, dict) and any(
            f in sample for f in ("Free", "free")
        ):
            bal_dict = body

    nonzero: dict[str, dict[str, float]] = {}
    for asset, info in bal_dict.items():
        if not isinstance(info, dict):
            continue
        free = float(info.get("Free", info.get("free", 0)))
        locked = float(info.get("Lock", info.get("lock",
                        info.get("locked", 0))))
        if free > 0 or locked > 0:
            nonzero[asset] = {"free": free, "locked": locked}

    log.info(f"  Non-zero assets: {len(nonzero)}")
    for asset, bal in sorted(nonzero.items()):
        log.info(f"    {asset:>10s}: free={bal['free']:>15,.4f}  "
                 f"locked={bal['locked']:>12,.4f}")

    usd = nonzero.get("USD", nonzero.get("USDT", {}))
    log.info(f"  USD balance: {usd.get('free', 'NOT FOUND')}")

    return {
        "passed": True,
        "nonzero_balances": nonzero,
        "usd_free": usd.get("free"),
        "total_assets_with_balance": len(nonzero),
        "latency_ms": result["elapsed_ms"],
    }


# ---------------------------------------------------------------------------
# Test 6: Limit Order Fill Mechanics
# ---------------------------------------------------------------------------
async def test_limit_order(client: TestClient, exchange_info: dict) -> dict:
    """Place a small limit buy at market price and check immediate fill.

    This is the critical Phase 0 question: on the mock exchange, do limit
    orders placed at the current price fill immediately?

    Uses a tiny order (~$200 of BTC) and immediately reverses it.
    """
    _header("TEST 6: Limit Order Fill Mechanics")

    # Step 1 — get current BTC price
    ticker = await client.get(
        "/v3/ticker", params={"pair": "BTC/USD"}, auth="ts",
    )
    if ticker["status"] != 200:
        log.error(f"  Cannot fetch BTC price: {ticker['body']}")
        return {"passed": False, "error": "ticker failed"}

    price = _extract_price(ticker["body"])
    if price is None:
        log.error(f"  Cannot extract price from: {ticker['body']}")
        return {"passed": False, "error": "no price field",
                "ticker_response": ticker["body"]}

    log.info(f"  Current BTC price: ${price:,.2f}")

    # Step 2 — determine precision from exchange info
    pairs_data = exchange_info.get("pairs_data", {})
    btc_pair = pairs_data.get("BTC/USD", pairs_data.get("BTCUSD", {}))
    amt_prec = int(btc_pair.get("AmountPrecision",
                    btc_pair.get("amountPrecision", 6)))
    px_prec = int(btc_pair.get("PricePrecision",
                   btc_pair.get("pricePrecision", 2)))

    qty = round(200.0 / price, amt_prec)  # ~$200 worth
    limit_price = round(price, px_prec)

    log.info(f"  Precision: amount={amt_prec}, price={px_prec}")
    log.info(f"  Order: BUY {qty} BTC @ limit ${limit_price:,.{px_prec}f}")

    # Step 3 — place limit buy
    buy_result = await client.post("/v3/place_order", data={
        "pair": "BTC/USD",
        "side": "BUY",
        "type": "LIMIT",
        "quantity": qty,
        "price": limit_price,
    })

    if buy_result["status"] != 200:
        log.error(f"  BUY FAILED — HTTP {buy_result['status']}: "
                  f"{buy_result['body']}")
        return {"passed": False, "error": buy_result["body"]}

    buy_body = buy_result["body"]
    log.info(f"  Buy response:\n{json.dumps(buy_body, indent=2)}")

    # Extract status, order_id, role, commission
    order_status = _find_field(buy_body, "Status", "status")
    order_id = _find_field(buy_body, "OrderID", "orderId", "order_id")
    role = _find_field(buy_body, "Role", "role")
    commission = _find_field(buy_body, "Commission", "commission",
                              "CommissionAmount")

    fills_immediately = (
        isinstance(order_status, str)
        and order_status.upper() in ("FILLED", "COMPLETE", "COMPLETED")
    )

    log.info(f"  Status           : {order_status}")
    log.info(f"  Fills immediately: {'YES' if fills_immediately else 'NO'}")
    log.info(f"  Order ID         : {order_id}")
    log.info(f"  Role (maker/taker): {role}")
    log.info(f"  Commission       : {commission}")
    log.info(f"  Fill latency     : {buy_result['elapsed_ms']} ms")
    log.info(f"  Order fields     : {list(buy_body.keys())}")

    # Step 4 — query the order for full details
    query_body = None
    if order_id is not None:
        log.info(f"  Querying order {order_id}...")
        qr = await client.post("/v3/query_order", data={"order_id": order_id})
        if qr["status"] == 200:
            query_body = qr["body"]
            log.info(f"  Query response:\n"
                     f"{json.dumps(query_body, indent=2)[:600]}")

    # Step 5 — reverse position (sell same quantity)
    sell_body = None
    if fills_immediately:
        log.info(f"  Reversing: SELL {qty} BTC @ ${limit_price:,.{px_prec}f}")
        sell_result = await client.post("/v3/place_order", data={
            "pair": "BTC/USD",
            "side": "SELL",
            "type": "LIMIT",
            "quantity": qty,
            "price": limit_price,
        })
        sell_body = sell_result["body"]
        sell_status = _find_field(sell_body, "Status", "status")
        log.info(f"  Sell status: {sell_status}")

    return {
        "passed": True,
        "fills_immediately": fills_immediately,
        "order_status": order_status,
        "order_id": order_id,
        "role": role,
        "commission": commission,
        "fill_latency_ms": buy_result["elapsed_ms"],
        "order_fields": list(buy_body.keys()),
        "query_response": query_body,
        "sell_response": sell_body,
    }


# ---------------------------------------------------------------------------
# Test 7: Cancel Order & Pending Count
# ---------------------------------------------------------------------------
async def test_cancel_and_pending(client: TestClient) -> dict:
    """Verify cancel_order and pending_count endpoints work."""
    _header("TEST 7: Cancel Order & Pending Count")

    pending = await client.get("/v3/pending_count", auth="signed")
    log.info(f"  Pending count response: {pending['body']}")

    # Cancel for a specific pair (safe — won't cancel unrelated orders)
    cancel = await client.post("/v3/cancel_order", data={"pair": "BTC/USD"})
    log.info(f"  Cancel BTC/USD response: {cancel['body']}")

    return {
        "passed": pending["status"] == 200,
        "pending_response": pending["body"],
        "cancel_response": cancel["body"],
    }


# ---------------------------------------------------------------------------
# Test 8: Rate Limit Discovery
# ---------------------------------------------------------------------------
async def test_rate_limit(client: TestClient) -> dict:
    """Empirically discover the API rate limit.

    Sends rapid /v3/serverTime requests (unauthenticated, cheapest endpoint)
    until hitting a 429 or reaching 70 requests.
    """
    _header("TEST 8: Rate Limit Discovery")

    log.info("  Sending rapid /v3/serverTime requests...")
    log.info("  Will stop at first 429 or after 70 requests")

    statuses: list[int] = []
    hit_429 = False
    rate_limit_headers: dict = {}
    start = time.monotonic()

    for i in range(70):
        resp = await client.get("/v3/serverTime")
        statuses.append(resp["status"])

        if resp["status"] == 429:
            hit_429 = True
            rate_limit_headers = {
                k: v for k, v in resp["headers"].items()
                if "rate" in k.lower() or "retry" in k.lower()
                or "limit" in k.lower()
            }
            log.info(f"  429 at request #{i + 1}  "
                     f"({time.monotonic() - start:.1f}s elapsed)")
            if rate_limit_headers:
                log.info(f"  Rate-limit headers: {rate_limit_headers}")
            break

        # Progress every 10 requests
        if (i + 1) % 10 == 0:
            elapsed = time.monotonic() - start
            rate = (i + 1) / elapsed
            log.info(f"  #{i + 1}: {resp['status']}  "
                     f"({rate:.1f} req/s, {elapsed:.1f}s)")

    total_time = time.monotonic() - start
    total_reqs = len(statuses)
    rate_per_min = total_reqs / total_time * 60 if total_time > 0 else 0

    log.info(f"  Total requests : {total_reqs}")
    log.info(f"  Total time     : {total_time:.1f}s")
    log.info(f"  Effective rate  : {rate_per_min:.0f} req/min")
    log.info(f"  Hit 429        : {'YES' if hit_429 else 'NO'}")

    if hit_429:
        limit = total_reqs - 1
        log.info(f"  Estimated limit: ~{limit} calls/min")
    else:
        log.info(f"  No 429 hit — limit is > {rate_per_min:.0f} req/min")
        log.info("  Consider a longer test or the limit may be per-endpoint")

    return {
        "passed": True,
        "total_requests": total_reqs,
        "total_time_sec": round(total_time, 1),
        "hit_429": hit_429,
        "effective_rate_per_min": round(rate_per_min),
        "estimated_limit": total_reqs - 1 if hit_429 else None,
        "rate_limit_headers": rate_limit_headers,
    }


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------
def _extract_price(body: dict) -> Optional[float]:
    """Extract a usable price from a ticker response (unknown structure)."""
    # Try common field names at top level and one level deep
    price_keys = [
        "LastPrice", "lastPrice", "last_price", "Price", "price",
        "AskPrice", "askPrice", "ask_price",
        "BidPrice", "bidPrice", "bid_price",
    ]
    for key in price_keys:
        if key in body:
            try:
                return float(body[key])
            except (ValueError, TypeError):
                continue
    # One level deep
    for _, val in body.items():
        if isinstance(val, dict):
            for key in price_keys:
                if key in val:
                    try:
                        return float(val[key])
                    except (ValueError, TypeError):
                        continue
    return None


def _find_field(body: dict, *candidates: str) -> Any:
    """Return the first matching field value from a response dict."""
    for key in candidates:
        if key in body:
            return body[key]
    # One level deep
    for _, val in body.items():
        if isinstance(val, dict):
            for key in candidates:
                if key in val:
                    return val[key]
    return None


# ---------------------------------------------------------------------------
# JSON serialization helper
# ---------------------------------------------------------------------------
def _make_serializable(obj: Any) -> Any:
    """Recursively convert non-serializable values for JSON output."""
    if isinstance(obj, dict):
        return {str(k): _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if isinstance(obj, (int, float, str, bool, type(None))):
        return obj
    return str(obj)


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
async def run_all_tests() -> dict:
    """Run all Phase 0 tests sequentially and produce a summary."""
    config = Config()
    results: dict[str, Any] = {}

    log.info("=" * 64)
    log.info("  APEX Phase 0 — Roostoo API Validation")
    log.info(f"  Base URL : {config.base_url}")
    log.info(f"  Time     : {datetime.now(timezone.utc).isoformat()}")
    log.info("=" * 64)

    # --- Credential check ---
    has_creds = True
    try:
        api_key = config.api_key
        api_secret = config.api_secret
        log.info(f"  API Key  : {api_key[:6]}...{api_key[-4:]}")
    except EnvironmentError as e:
        log.warning(f"  {e}")
        log.warning("  Authenticated tests will be skipped.")
        has_creds = False
        api_key, api_secret = "", ""

    async with TestClient(config.base_url, api_key, api_secret) as client:

        # ---- Unauthenticated tests ----
        results["test0_server_time"] = await test_server_time(client)
        results["test1_exchange_info"] = await test_exchange_info(
            client, config,
        )

        # ---- Timestamp-authenticated ----
        results["test2_batch_ticker"] = await test_batch_ticker(client)
        results["test3_single_ticker"] = await test_single_ticker(client)
        results["test4_timestamp_fmt"] = await test_timestamp_format(client)

        # ---- Fully authenticated ----
        if has_creds:
            results["test5_balance"] = await test_balance(client)
            results["test6_limit_order"] = await test_limit_order(
                client, results["test1_exchange_info"],
            )
            results["test7_cancel_pending"] = await test_cancel_and_pending(
                client,
            )
        else:
            log.warning("")
            log.warning("  SKIPPING tests 5-7 (no API credentials).")
            log.warning("  Set ROOSTOO_API_KEY and ROOSTOO_API_SECRET.")

        # ---- Rate limit (last — may trigger throttling) ----
        results["test8_rate_limit"] = await test_rate_limit(client)

    # ------------------------------------------------------------------
    # Config update recommendations
    # ------------------------------------------------------------------
    _header("CONFIG.YAML UPDATE RECOMMENDATIONS")
    updates: dict[str, Any] = {}

    # batch pricing
    batch = results.get("test2_batch_ticker", {})
    batch_val = batch.get("batch_available", None)
    if batch_val is not None:
        updates["api.batch_pricing_available"] = batch_val
        status = "CONFIRMED" if batch_val else "NOT available"
        log.info(f"  api.batch_pricing_available: {str(batch_val).lower()}"
                 f"  ({status})")

    # volume data
    ticker = results.get("test3_single_ticker", {})
    vol_val = ticker.get("has_volume", None)
    if vol_val is not None:
        updates["api.volume_data_available"] = vol_val
        log.info(f"  api.volume_data_available:   {str(vol_val).lower()}")

    # limit fills immediately
    order = results.get("test6_limit_order", {})
    fill_val = order.get("fills_immediately", None)
    if fill_val is not None:
        updates["api.limit_fills_immediately"] = fill_val
        log.info(f"  api.limit_fills_immediately: {str(fill_val).lower()}")

    # rate limit
    rate = results.get("test8_rate_limit", {})
    if rate.get("hit_429"):
        limit = rate["estimated_limit"]
        updates["api.rate_limit_calls_per_min"] = limit
        log.info(f"  api.rate_limit_calls_per_min: {limit}")
    else:
        eff = rate.get("effective_rate_per_min", "?")
        log.info(f"  api.rate_limit_calls_per_min: >{eff} (no 429 hit)")

    # timestamp format
    ts = results.get("test4_timestamp_fmt", {})
    log.info(f"  Timestamp format: {ts.get('recommendation', '?')}")

    results["config_updates"] = updates

    # ------------------------------------------------------------------
    # Save results to file
    # ------------------------------------------------------------------
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    results_path = log_dir / "phase0_results.json"

    with open(results_path, "w") as f:
        json.dump(_make_serializable(results), f, indent=2, default=str)
    log.info(f"\n  Full results saved to: {results_path}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    _header("SUMMARY")
    passed = sum(
        1 for v in results.values()
        if isinstance(v, dict) and v.get("passed") is True
    )
    total = sum(
        1 for v in results.values()
        if isinstance(v, dict) and "passed" in v
    )
    log.info(f"  Tests passed: {passed}/{total}")
    if results.get("test1_exchange_info", {}).get("missing"):
        n = len(results["test1_exchange_info"]["missing"])
        log.info(f"  WARNING: {n} expected pairs not found on exchange")
    log.info(f"  Update config.yaml with the confirmed values above.")
    log.info("")

    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(run_all_tests())
