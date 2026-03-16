#!/usr/bin/env python3
"""Phase 0 — Roostoo API Integration Tests.

Gated prerequisite before Phase 1 implementation. Tests:
  1. Server connectivity and time sync
  2. Exchange info / universe discovery
  3. Batch ticker pricing (all assets in one call)
  4. Single ticker pricing
  5. Data fields analysis (volume, precision, etc.)
  6. Rate limit empirical measurement
  7. Account balance endpoint
  8. Limit order placement and fill mechanics
  9. Order query and cancellation

Usage:
    # Set API credentials first:
    export ROOSTOO_API_KEY="your_key"
    export ROOSTOO_API_SECRET="your_secret"

    # Run all tests:
    python scripts/phase0_test.py

    # Run specific test:
    python scripts/phase0_test.py --test server_time
    python scripts/phase0_test.py --test exchange_info
    python scripts/phase0_test.py --test batch_ticker
    python scripts/phase0_test.py --test rate_limit
    python scripts/phase0_test.py --test balance
    python scripts/phase0_test.py --test limit_order
    python scripts/phase0_test.py --test all

Results are printed to stdout and saved to logs/phase0_results.json.
Update config.yaml with confirmed values after running.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml

from src.data.api_client import RoostooClient, RoostooAPIError
from src.data.rate_limiter import TokenBucketRateLimiter

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("phase0")

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_config() -> dict[str, Any]:
    """Load config.yaml from project root."""
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Known universe from config for cross-referencing
# ---------------------------------------------------------------------------
def get_expected_universe(config: dict[str, Any]) -> set[str]:
    """Build set of expected trading pairs from config universe."""
    universe = config.get("universe", {})
    suffix = universe.get("pair_suffix", "/USD")
    pairs: set[str] = set()

    for tier_key in [
        "tier_1_majors",
        "tier_2_large_alts",
        "tier_3_defi",
        "tier_4_meme",
        "tier_5_obscure",
    ]:
        for asset in universe.get(tier_key, []):
            pairs.add(f"{asset}{suffix}")

    special = universe.get("special", {})
    if "paxg" in special:
        pairs.add(f"{special['paxg']}{suffix}")
    if "trump" in special:
        pairs.add(f"{special['trump']}{suffix}")

    return pairs


# ---------------------------------------------------------------------------
# Result collector
# ---------------------------------------------------------------------------
class Phase0Results:
    """Collects and formats Phase 0 test results."""

    def __init__(self) -> None:
        self.results: dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "tests": {},
        }

    def record(self, test_name: str, status: str, data: Any = None) -> None:
        """Record a test result."""
        self.results["tests"][test_name] = {
            "status": status,
            "data": data,
        }
        icon = "PASS" if status == "pass" else "FAIL" if status == "fail" else "WARN"
        logger.info("[%s] %s", icon, test_name)
        if data and isinstance(data, dict):
            for k, v in data.items():
                # Truncate long values for console
                v_str = str(v)
                if len(v_str) > 200:
                    v_str = v_str[:200] + "..."
                logger.info("  %s: %s", k, v_str)

    def save(self, path: Path) -> None:
        """Save results to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.results, f, indent=2, default=str)
        logger.info("Results saved to %s", path)

    def summary(self) -> dict[str, Any]:
        """Generate config.yaml update recommendations."""
        recs: dict[str, Any] = {}
        tests = self.results["tests"]

        if "batch_ticker" in tests and tests["batch_ticker"]["status"] == "pass":
            recs["batch_pricing_available"] = True
        elif "batch_ticker" in tests:
            recs["batch_pricing_available"] = False

        if "data_fields" in tests and tests["data_fields"]["status"] == "pass":
            fields_data = tests["data_fields"].get("data", {})
            recs["volume_data_available"] = fields_data.get(
                "has_volume_data", False
            )

        if "rate_limit" in tests and tests["rate_limit"]["status"] == "pass":
            rl_data = tests["rate_limit"].get("data", {})
            if "confirmed_limit" in rl_data:
                recs["rate_limit_calls_per_min"] = rl_data["confirmed_limit"]

        if "limit_order" in tests and tests["limit_order"]["status"] == "pass":
            lo_data = tests["limit_order"].get("data", {})
            recs["limit_fills_immediately"] = lo_data.get(
                "fills_at_market_price", False
            )
            if "maker_commission_pct" in lo_data:
                recs["maker_commission_pct"] = lo_data["maker_commission_pct"]

        return recs


# ---------------------------------------------------------------------------
# Test implementations
# ---------------------------------------------------------------------------

async def test_server_time(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Test 1: Server connectivity and time synchronization."""
    logger.info("=" * 60)
    logger.info("TEST: Server Time & Connectivity")
    logger.info("=" * 60)

    try:
        t_before = time.time() * 1000
        resp = await client.get_server_time()
        t_after = time.time() * 1000

        server_time = resp.get("ServerTime", 0)
        roundtrip_ms = t_after - t_before
        clock_diff_ms = server_time - ((t_before + t_after) / 2)

        results.record("server_time", "pass", {
            "server_time_ms": server_time,
            "server_time_utc": datetime.fromtimestamp(
                server_time / 1000, tz=timezone.utc
            ).isoformat(),
            "roundtrip_ms": round(roundtrip_ms, 1),
            "clock_diff_ms": round(clock_diff_ms, 1),
            "response_keys": list(resp.keys()),
            "full_response": resp,
        })

        # Warn if clock difference is large (timestamp validation is 60s)
        if abs(clock_diff_ms) > 5000:
            logger.warning(
                "Clock difference is %.1fs — may cause timestamp "
                "validation failures (limit is 60s)",
                clock_diff_ms / 1000,
            )

    except Exception as e:
        results.record("server_time", "fail", {"error": str(e)})


async def test_exchange_info(
    client: RoostooClient, results: Phase0Results, config: dict[str, Any]
) -> None:
    """Test 2: Exchange info and universe discovery."""
    logger.info("=" * 60)
    logger.info("TEST: Exchange Info & Universe Discovery")
    logger.info("=" * 60)

    try:
        resp = await client.get_exchange_info()

        is_running = resp.get("IsRunning", False)
        initial_wallet = resp.get("InitialWallet", {})
        trade_pairs = resp.get("TradePairs", {})

        # Extract pair info
        discovered_pairs: dict[str, dict[str, Any]] = {}
        for pair_name, pair_info in trade_pairs.items():
            discovered_pairs[pair_name] = {
                "price_precision": pair_info.get("PricePrecision"),
                "amount_precision": pair_info.get("AmountPrecision"),
                "min_order": pair_info.get("MiniOrder"),
                "can_trade": pair_info.get("CanTrade"),
            }

        expected = get_expected_universe(config)
        discovered_set = set(discovered_pairs.keys())

        missing = expected - discovered_set
        extra = discovered_set - expected

        results.record("exchange_info", "pass", {
            "is_running": is_running,
            "initial_wallet": initial_wallet,
            "total_pairs_discovered": len(discovered_pairs),
            "expected_pairs": len(expected),
            "missing_pairs": sorted(missing) if missing else [],
            "extra_pairs": sorted(extra) if extra else [],
            "tradeable_pairs": sum(
                1 for p in discovered_pairs.values() if p.get("can_trade")
            ),
            "pair_details": discovered_pairs,
        })

        if missing:
            logger.warning(
                "MISSING %d expected pairs: %s",
                len(missing),
                sorted(missing),
            )
        if extra:
            logger.info(
                "EXTRA %d pairs not in config (classify as Tier 5): %s",
                len(extra),
                sorted(extra),
            )

    except Exception as e:
        results.record("exchange_info", "fail", {"error": str(e)})


async def test_batch_ticker(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Test 3: Batch ticker — fetch ALL prices in one API call."""
    logger.info("=" * 60)
    logger.info("TEST: Batch Ticker (all prices in one call)")
    logger.info("=" * 60)

    try:
        t_start = time.time()
        resp = await client.get_all_tickers()
        t_elapsed = time.time() - t_start

        data = resp.get("Data", {})
        pair_count = len(data)

        # Sample a few prices
        sample_prices: dict[str, float] = {}
        for pair_name in ["BTC/USD", "ETH/USD", "PAXG/USD"]:
            if pair_name in data:
                sample_prices[pair_name] = data[pair_name].get("LastPrice", 0)

        results.record("batch_ticker", "pass", {
            "pairs_returned": pair_count,
            "response_time_sec": round(t_elapsed, 3),
            "sample_prices": sample_prices,
            "api_calls_used": 1,
            "all_pairs": sorted(data.keys()),
        })

    except Exception as e:
        results.record("batch_ticker", "fail", {"error": str(e)})


async def test_single_ticker(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Test 3b: Single ticker — fetch one pair."""
    logger.info("=" * 60)
    logger.info("TEST: Single Ticker (BTC/USD)")
    logger.info("=" * 60)

    try:
        t_start = time.time()
        resp = await client.get_ticker("BTC/USD")
        t_elapsed = time.time() - t_start

        data = resp.get("Data", {})
        btc_data = data.get("BTC/USD", {})

        results.record("single_ticker", "pass", {
            "pair": "BTC/USD",
            "response_time_sec": round(t_elapsed, 3),
            "ticker_data": btc_data,
            "fields_present": list(btc_data.keys()),
        })

    except Exception as e:
        results.record("single_ticker", "fail", {"error": str(e)})


async def test_data_fields(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Test 4: Analyze ticker data fields (volume, precision, etc.)."""
    logger.info("=" * 60)
    logger.info("TEST: Data Fields Analysis")
    logger.info("=" * 60)

    try:
        resp = await client.get_all_tickers()
        data = resp.get("Data", {})

        if not data:
            results.record("data_fields", "fail", {"error": "No ticker data returned"})
            return

        # Analyze first ticker to identify all fields
        first_pair = next(iter(data))
        first_ticker = data[first_pair]
        all_fields = list(first_ticker.keys())

        # Check volume fields
        has_coin_volume = "CoinTradeValue" in first_ticker
        has_usd_volume = "UnitTradeValue" in first_ticker
        has_volume_data = has_coin_volume or has_usd_volume

        # Check if volume is actually populated (non-zero)
        volume_populated = False
        volume_samples: dict[str, dict[str, Any]] = {}
        for pair_name in ["BTC/USD", "ETH/USD", "DOGE/USD"]:
            if pair_name in data:
                ticker = data[pair_name]
                coin_vol = ticker.get("CoinTradeValue", 0)
                usd_vol = ticker.get("UnitTradeValue", 0)
                volume_samples[pair_name] = {
                    "CoinTradeValue": coin_vol,
                    "UnitTradeValue": usd_vol,
                }
                if coin_vol > 0 or usd_vol > 0:
                    volume_populated = True

        # Check price fields
        has_bid_ask = "MaxBid" in first_ticker and "MinAsk" in first_ticker
        has_last_price = "LastPrice" in first_ticker
        has_change = "Change" in first_ticker

        # Check if bid-ask spread is meaningful
        spread_samples: dict[str, dict[str, Any]] = {}
        for pair_name in ["BTC/USD", "ETH/USD"]:
            if pair_name in data:
                ticker = data[pair_name]
                bid = ticker.get("MaxBid", 0)
                ask = ticker.get("MinAsk", 0)
                last = ticker.get("LastPrice", 0)
                if bid > 0 and ask > 0:
                    spread_bps = ((ask - bid) / ((ask + bid) / 2)) * 10000
                    spread_samples[pair_name] = {
                        "bid": bid,
                        "ask": ask,
                        "last": last,
                        "spread_bps": round(spread_bps, 2),
                        "bid_eq_ask": bid == ask,
                        "last_eq_bid": last == bid,
                    }

        results.record("data_fields", "pass", {
            "all_fields": all_fields,
            "has_volume_data": has_volume_data,
            "has_volume_fields": {
                "CoinTradeValue": has_coin_volume,
                "UnitTradeValue": has_usd_volume,
            },
            "volume_populated": volume_populated,
            "volume_samples": volume_samples,
            "has_bid_ask": has_bid_ask,
            "has_last_price": has_last_price,
            "has_change_24h": has_change,
            "spread_analysis": spread_samples,
            "sample_ticker": {first_pair: first_ticker},
        })

    except Exception as e:
        results.record("data_fields", "fail", {"error": str(e)})


async def test_rate_limit(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Test 5: Empirical rate limit measurement.

    Strategy: Make rapid API calls and count how many succeed before
    getting a 429. Uses the lightweight serverTime endpoint.
    """
    logger.info("=" * 60)
    logger.info("TEST: Rate Limit (empirical measurement)")
    logger.info("=" * 60)
    logger.info("Making rapid API calls to find the rate limit...")
    logger.info("This may take 60-120 seconds.")

    # Use a separate rate limiter with no limit for testing
    test_limiter = TokenBucketRateLimiter(
        calls_per_minute=200,  # effectively unlimited
        reserve_headroom=0,
    )
    test_client = RoostooClient(
        base_url=client.base_url,
        rate_limiter=test_limiter,
    )

    successes = 0
    first_429_at = 0
    errors: list[dict[str, Any]] = []
    call_times: list[float] = []

    t_start = time.time()
    target_calls = 80  # Try 80 calls to find the boundary

    try:
        for i in range(target_calls):
            t_call = time.time()
            try:
                await test_client.get_server_time()
                successes += 1
                call_times.append(time.time() - t_call)
            except RoostooAPIError as e:
                if e.status_code == 429:
                    elapsed = time.time() - t_start
                    if first_429_at == 0:
                        first_429_at = successes
                    errors.append({
                        "call_number": i + 1,
                        "elapsed_sec": round(elapsed, 2),
                        "error": "429 Rate Limited",
                    })
                    # Wait a bit and continue to see if more are allowed
                    await asyncio.sleep(2)
                else:
                    errors.append({
                        "call_number": i + 1,
                        "error": str(e),
                    })

            # Brief pause to not completely hammer the API
            await asyncio.sleep(0.05)

        t_total = time.time() - t_start

        # Determine likely rate limit
        if first_429_at > 0:
            confirmed_limit = first_429_at
        else:
            # No 429s received — limit is >= target_calls per period
            confirmed_limit = None

        avg_latency = (
            round(sum(call_times) / len(call_times) * 1000, 1)
            if call_times
            else 0
        )

        result_data: dict[str, Any] = {
            "total_calls_attempted": target_calls,
            "successful_calls": successes,
            "first_429_at_call": first_429_at if first_429_at > 0 else "never",
            "total_time_sec": round(t_total, 2),
            "avg_latency_ms": avg_latency,
            "errors": errors[:10],  # Cap at 10 errors
        }

        if confirmed_limit is not None:
            result_data["confirmed_limit"] = confirmed_limit
            result_data["recommendation"] = (
                f"Set rate_limit_calls_per_min to {confirmed_limit} in config.yaml"
            )
        else:
            result_data["recommendation"] = (
                f"No 429 received in {target_calls} calls over {t_total:.0f}s. "
                "Rate limit may be >=60/min. Keep conservative default of 30."
            )

        results.record("rate_limit", "pass", result_data)

    except Exception as e:
        results.record("rate_limit", "fail", {"error": str(e)})
    finally:
        await test_client.close()


async def test_balance(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Test 6: Account balance endpoint."""
    logger.info("=" * 60)
    logger.info("TEST: Account Balance")
    logger.info("=" * 60)

    try:
        resp = await client.get_balance()
        wallet = resp.get("Wallet", {})

        # Summarize balances
        non_zero: dict[str, dict[str, float]] = {}
        for currency, balance in wallet.items():
            free = balance.get("Free", 0)
            lock = balance.get("Lock", 0)
            if free > 0 or lock > 0:
                non_zero[currency] = {"free": free, "locked": lock}

        usd_balance = wallet.get("USD", {})

        results.record("balance", "pass", {
            "total_currencies": len(wallet),
            "non_zero_balances": non_zero,
            "usd_free": usd_balance.get("Free", 0),
            "usd_locked": usd_balance.get("Lock", 0),
            "wallet_fields_per_currency": list(usd_balance.keys()) if usd_balance else [],
        })

    except Exception as e:
        results.record("balance", "fail", {"error": str(e)})


async def test_limit_order(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Test 7: Limit order placement and fill mechanics.

    Tests:
    - Place a small LIMIT BUY at current market price (should fill immediately
      on mock exchange if fills at current price).
    - Place a LIMIT BUY well below market to test pending behavior.
    - Check order status.
    - Cancel the pending order.
    - Sell back what was bought.

    IMPORTANT: Uses very small quantities to minimize impact.
    """
    logger.info("=" * 60)
    logger.info("TEST: Limit Order Mechanics")
    logger.info("=" * 60)

    order_results: dict[str, Any] = {}

    try:
        # Step 1: Get current BTC price
        ticker_resp = await client.get_ticker("BTC/USD")
        btc_data = ticker_resp.get("Data", {}).get("BTC/USD", {})
        last_price = btc_data.get("LastPrice", 0)
        bid_price = btc_data.get("MaxBid", 0)
        ask_price = btc_data.get("MinAsk", 0)

        if last_price <= 0:
            results.record("limit_order", "fail", {
                "error": "Could not get BTC price",
            })
            return

        order_results["btc_price"] = {
            "last": last_price,
            "bid": bid_price,
            "ask": ask_price,
        }
        logger.info("BTC price: last=%.2f bid=%.2f ask=%.2f", last_price, bid_price, ask_price)

        # Get exchange info for precision
        exchange_info = await client.get_exchange_info()
        btc_pair_info = exchange_info.get("TradePairs", {}).get("BTC/USD", {})
        price_precision = btc_pair_info.get("PricePrecision", 2)
        amount_precision = btc_pair_info.get("AmountPrecision", 6)
        min_order = btc_pair_info.get("MiniOrder", 10)

        order_results["btc_pair_info"] = {
            "price_precision": price_precision,
            "amount_precision": amount_precision,
            "min_order": min_order,
        }

        # Calculate small test quantity (just above minimum)
        # min_order is the minimum order value in USD
        test_qty = round(min_order / last_price * 1.5, amount_precision)
        test_qty = max(test_qty, 10 ** (-amount_precision))

        # Step 2: Place LIMIT BUY at last price (test immediate fill)
        logger.info(
            "Placing LIMIT BUY: %.6f BTC @ %.2f (at last price)",
            test_qty, last_price,
        )

        buy_price_str = f"{last_price:.{price_precision}f}"
        qty_str = f"{test_qty:.{amount_precision}f}"

        buy_resp = await client.place_limit_order(
            pair="BTC/USD",
            side="BUY",
            quantity=qty_str,
            price=buy_price_str,
        )

        buy_detail = buy_resp.get("OrderDetail", {})
        buy_status = buy_detail.get("Status", "UNKNOWN")
        buy_role = buy_detail.get("Role", "UNKNOWN")
        buy_order_id = buy_detail.get("OrderID", 0)
        buy_commission = buy_detail.get("CommissionPercent", 0)
        buy_filled_price = buy_detail.get("FilledAverPrice", 0)

        fills_at_market = buy_status == "FILLED"

        order_results["limit_buy_at_market"] = {
            "order_id": buy_order_id,
            "status": buy_status,
            "role": buy_role,
            "fills_at_market_price": fills_at_market,
            "requested_price": last_price,
            "filled_price": buy_filled_price,
            "commission_pct": buy_commission,
            "full_response": buy_detail,
        }

        logger.info(
            "LIMIT BUY result: status=%s role=%s commission=%.4f%%",
            buy_status, buy_role, buy_commission * 100,
        )

        # Step 3: Place LIMIT BUY well below market (test pending)
        pending_price = round(last_price * 0.5, price_precision)  # 50% below
        pending_price_str = f"{pending_price:.{price_precision}f}"

        logger.info(
            "Placing LIMIT BUY: %s BTC @ %s (50%% below market, expect PENDING)",
            qty_str, pending_price_str,
        )

        pending_resp = await client.place_limit_order(
            pair="BTC/USD",
            side="BUY",
            quantity=qty_str,
            price=pending_price_str,
        )

        pending_detail = pending_resp.get("OrderDetail", {})
        pending_status = pending_detail.get("Status", "UNKNOWN")
        pending_order_id = pending_detail.get("OrderID", 0)

        order_results["limit_buy_below_market"] = {
            "order_id": pending_order_id,
            "status": pending_status,
            "expected_pending": pending_status == "PENDING",
            "price": pending_price,
            "full_response": pending_detail,
        }

        logger.info("Below-market LIMIT BUY: status=%s", pending_status)

        # Step 4: Query the pending order
        if pending_order_id:
            query_resp = await client.query_order(
                order_id=str(pending_order_id)
            )
            matched = query_resp.get("OrderMatched", [])
            order_results["query_pending"] = {
                "found": len(matched) > 0,
                "order_detail": matched[0] if matched else None,
            }

        # Step 5: Cancel the pending order
        if pending_order_id:
            logger.info("Canceling pending order #%d", pending_order_id)
            cancel_resp = await client.cancel_order(
                order_id=str(pending_order_id)
            )
            canceled = cancel_resp.get("CanceledList", [])
            order_results["cancel_order"] = {
                "canceled_ids": canceled,
                "success": pending_order_id in canceled,
            }
            logger.info("Cancel result: %s", canceled)

        # Step 6: If we bought BTC, sell it back
        if fills_at_market:
            filled_qty = buy_detail.get("FilledQuantity", 0)
            if filled_qty > 0:
                sell_qty_str = f"{filled_qty:.{amount_precision}f}"
                logger.info(
                    "Selling back: %s BTC @ %s",
                    sell_qty_str, buy_price_str,
                )
                sell_resp = await client.place_limit_order(
                    pair="BTC/USD",
                    side="SELL",
                    quantity=sell_qty_str,
                    price=buy_price_str,
                )
                sell_detail = sell_resp.get("OrderDetail", {})
                order_results["sell_back"] = {
                    "status": sell_detail.get("Status"),
                    "role": sell_detail.get("Role"),
                    "commission_pct": sell_detail.get("CommissionPercent", 0),
                    "full_response": sell_detail,
                }
                logger.info("Sell-back status: %s", sell_detail.get("Status"))

        # Summary
        maker_commission = buy_commission if fills_at_market else None
        order_results["maker_commission_pct"] = maker_commission

        results.record("limit_order", "pass", order_results)

    except Exception as e:
        order_results["error"] = str(e)
        results.record("limit_order", "fail", order_results)


async def test_pending_count(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Test 8: Pending order count endpoint."""
    logger.info("=" * 60)
    logger.info("TEST: Pending Order Count")
    logger.info("=" * 60)

    try:
        resp = await client.get_pending_count()
        results.record("pending_count", "pass", {
            "total_pending": resp.get("TotalPending", 0),
            "order_pairs": resp.get("OrderPairs", {}),
            "full_response": resp,
        })
    except RoostooAPIError as e:
        # "no pending order" is a valid response
        if "no pending order" in str(e).lower():
            results.record("pending_count", "pass", {
                "total_pending": 0,
                "note": "No pending orders (expected for fresh account)",
            })
        else:
            results.record("pending_count", "fail", {"error": str(e)})
    except Exception as e:
        results.record("pending_count", "fail", {"error": str(e)})


# ---------------------------------------------------------------------------
# Multi-call timing test for consistent latency
# ---------------------------------------------------------------------------
async def test_latency(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Test 9: Measure API latency over multiple calls."""
    logger.info("=" * 60)
    logger.info("TEST: API Latency (10 serverTime calls)")
    logger.info("=" * 60)

    latencies: list[float] = []
    for _ in range(10):
        t_start = time.time()
        try:
            await client.get_server_time()
            latencies.append((time.time() - t_start) * 1000)
        except Exception:
            pass
        await asyncio.sleep(0.1)

    if latencies:
        results.record("latency", "pass", {
            "calls": len(latencies),
            "avg_ms": round(sum(latencies) / len(latencies), 1),
            "min_ms": round(min(latencies), 1),
            "max_ms": round(max(latencies), 1),
            "p50_ms": round(sorted(latencies)[len(latencies) // 2], 1),
        })
    else:
        results.record("latency", "fail", {"error": "All calls failed"})


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
async def run_all_tests(test_filter: str = "all") -> Phase0Results:
    """Run Phase 0 tests.

    Args:
        test_filter: "all" or a specific test name.

    Returns:
        Phase0Results with all test outcomes.
    """
    config = load_config()
    api_config = config.get("api", {})

    base_url = api_config.get("base_url", "https://mock-api.roostoo.com")
    rate_limit = api_config.get("rate_limit_calls_per_min", 30)
    reserve = api_config.get("rate_limit_reserve_headroom", 10)

    # Check for API credentials
    api_key = os.environ.get(api_config.get("key_env_var", "ROOSTOO_API_KEY"), "")
    api_secret = os.environ.get(
        api_config.get("secret_env_var", "ROOSTOO_API_SECRET"), ""
    )

    if not api_key or not api_secret:
        logger.warning(
            "API credentials not set! Set ROOSTOO_API_KEY and ROOSTOO_API_SECRET "
            "environment variables. Signed endpoint tests will fail."
        )

    rate_limiter = TokenBucketRateLimiter(
        calls_per_minute=rate_limit,
        reserve_headroom=0,  # No reservation during testing
    )

    client = RoostooClient(
        base_url=base_url,
        rate_limiter=rate_limiter,
    )

    results = Phase0Results()

    # Define test map
    test_map = {
        "server_time": lambda: test_server_time(client, results),
        "exchange_info": lambda: test_exchange_info(client, results, config),
        "batch_ticker": lambda: test_batch_ticker(client, results),
        "single_ticker": lambda: test_single_ticker(client, results),
        "data_fields": lambda: test_data_fields(client, results),
        "latency": lambda: test_latency(client, results),
        "rate_limit": lambda: test_rate_limit(client, results),
        "balance": lambda: test_balance(client, results),
        "pending_count": lambda: test_pending_count(client, results),
        "limit_order": lambda: test_limit_order(client, results),
    }

    tests_to_run = (
        list(test_map.keys()) if test_filter == "all" else [test_filter]
    )

    for test_name in tests_to_run:
        if test_name not in test_map:
            logger.error("Unknown test: %s", test_name)
            logger.info("Available tests: %s", ", ".join(test_map.keys()))
            continue

        try:
            await test_map[test_name]()
        except Exception as e:
            logger.error("Unhandled error in test %s: %s", test_name, e)
            results.record(test_name, "fail", {"error": str(e)})

        logger.info("")

    await client.close()

    # Print summary
    logger.info("=" * 60)
    logger.info("PHASE 0 SUMMARY")
    logger.info("=" * 60)

    for test_name, result in results.results["tests"].items():
        status = result["status"].upper()
        logger.info("  %-20s %s", test_name, status)

    recs = results.summary()
    if recs:
        logger.info("")
        logger.info("CONFIG.YAML RECOMMENDATIONS:")
        for key, value in recs.items():
            logger.info("  api.%s: %s", key, value)

    return results


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Phase 0 — Roostoo API Integration Tests"
    )
    parser.add_argument(
        "--test",
        default="all",
        help=(
            "Test to run: all, server_time, exchange_info, batch_ticker, "
            "single_ticker, data_fields, latency, rate_limit, balance, "
            "pending_count, limit_order"
        ),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "logs" / "phase0_results.json"),
        help="Output JSON file path (default: logs/phase0_results.json)",
    )
    args = parser.parse_args()

    results = asyncio.run(run_all_tests(test_filter=args.test))
    results.save(Path(args.output))


if __name__ == "__main__":
    main()
