#!/usr/bin/env python3
"""Phase 0 — Roostoo API Testing Script (Gated Prerequisite).

Run this BEFORE any strategy implementation. Answers four critical
architecture questions and updates config.yaml with confirmed values.

Tests (per spec):
  Test 1 — Batch pricing:        Does one call return all 56+ prices?
  Test 2 — Limit order fills:    Do limit orders at LastPrice fill instantly?
  Test 3 — Rate limit:           What is the actual call budget per minute?
  Test 4 — Data fields:          What fields does the ticker endpoint return?

Supplementary:
  Test 0 — Server connectivity:  Is the API reachable? Clock sync OK?
  Test 5 — Exchange info:        Universe discovery, precision, min orders.
  Test 6 — Account balance:      Starting capital confirmation.

Usage::

    export ROOSTOO_API_KEY="your_key"
    export ROOSTOO_API_SECRET="your_secret"

    # Run all tests:
    python scripts/phase0_api_test.py

    # Run a single test:
    python scripts/phase0_api_test.py --test batch_pricing
    python scripts/phase0_api_test.py --test limit_fills
    python scripts/phase0_api_test.py --test rate_limit
    python scripts/phase0_api_test.py --test data_fields

Results saved to logs/phase0_results.json.
Config recommendations printed at the end.
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

# ---------------------------------------------------------------------------
# Project bootstrap
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from src.data.api_client import RoostooClient, RoostooAPIError  # noqa: E402
from src.data.rate_limiter import TokenBucketRateLimiter  # noqa: E402

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("phase0")


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------
def load_config() -> dict[str, Any]:
    """Load config.yaml from project root."""
    config_path = PROJECT_ROOT / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_expected_pairs(config: dict[str, Any]) -> set[str]:
    """Build the set of trading pairs our universe expects."""
    universe = config.get("universe", {})
    suffix = universe.get("pair_suffix", "/USD")
    pairs: set[str] = set()
    for tier_key in (
        "tier_1_majors", "tier_2_large_alts", "tier_3_defi",
        "tier_4_meme", "tier_5_obscure",
    ):
        for asset in universe.get(tier_key, []):
            pairs.add(f"{asset}{suffix}")
    special = universe.get("special", {})
    for key in ("paxg", "trump"):
        if key in special:
            pairs.add(f"{special[key]}{suffix}")
    return pairs


# ---------------------------------------------------------------------------
# Result collector
# ---------------------------------------------------------------------------
class Phase0Results:
    """Collects test outcomes and generates config recommendations."""

    def __init__(self) -> None:
        self.results: dict[str, Any] = {
            "run_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "tests": {},
        }

    def record(
        self, test_name: str, passed: bool, data: dict[str, Any] | None = None
    ) -> None:
        """Record a single test result."""
        status = "PASS" if passed else "FAIL"
        self.results["tests"][test_name] = {"status": status, "data": data or {}}
        logger.info("[%s] %s", status, test_name)
        if data:
            for k, v in data.items():
                v_str = str(v)
                if len(v_str) > 250:
                    v_str = v_str[:250] + "..."
                logger.info("  %s: %s", k, v_str)

    def save(self, path: Path) -> None:
        """Write results to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.results, f, indent=2, default=str)
        logger.info("Results saved to %s", path)

    def config_recommendations(self) -> dict[str, Any]:
        """Derive config.yaml update values from test outcomes."""
        recs: dict[str, Any] = {}
        tests = self.results["tests"]

        # batch_pricing_available
        bp = tests.get("batch_pricing", {}).get("data", {})
        if "batch_available" in bp:
            recs["api_batch_pricing_available"] = bp["batch_available"]
            recs["total_assets_on_exchange"] = bp.get("pairs_returned", 0)

        # volume_data_available
        df = tests.get("data_fields", {}).get("data", {})
        if "has_volume_data" in df:
            recs["api_volume_data_available"] = df["has_volume_data"]

        # limit_fills_immediately
        lf = tests.get("limit_fills", {}).get("data", {})
        if "fills_immediately" in lf:
            recs["api_limit_fills_immediately"] = lf["fills_immediately"]

        # api_rate_limit
        rl = tests.get("rate_limit", {}).get("data", {})
        if rl.get("hit_429"):
            recs["api_rate_limit_calls_per_min"] = rl.get("estimated_limit")
        elif "effective_rate_per_min" in rl:
            recs["api_rate_limit_note"] = (
                f"No 429 hit. Effective rate was {rl['effective_rate_per_min']}"
                " req/min. Keep conservative default."
            )

        return recs


# ===========================================================================
# TEST 0 — Server Connectivity & Clock Sync
# ===========================================================================
async def test_server_connectivity(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Verify the API is reachable and clock offset is within tolerance."""
    logger.info("=" * 64)
    logger.info("TEST 0: Server Connectivity & Clock Sync")
    logger.info("=" * 64)

    try:
        t_before = time.time() * 1000
        resp = await client.get_server_time()
        t_after = time.time() * 1000

        server_time = resp.get("ServerTime", 0)
        roundtrip_ms = t_after - t_before
        clock_diff_ms = server_time - ((t_before + t_after) / 2)
        server_dt = datetime.fromtimestamp(
            server_time / 1000, tz=timezone.utc
        )

        within_tolerance = abs(clock_diff_ms) < 60_000  # 60s

        results.record("server_connectivity", within_tolerance, {
            "server_time_utc": server_dt.isoformat(),
            "roundtrip_ms": round(roundtrip_ms, 1),
            "clock_offset_ms": round(clock_diff_ms, 1),
            "within_60s_tolerance": within_tolerance,
            "response_keys": list(resp.keys()),
        })

        if not within_tolerance:
            logger.warning(
                "Clock offset is %.1fs — may cause timestamp validation "
                "failures. Sync system clock.",
                clock_diff_ms / 1000,
            )

    except Exception as e:
        results.record("server_connectivity", False, {"error": str(e)})


# ===========================================================================
# TEST 1 — Batch Pricing (spec: 15 min budget)
# ===========================================================================
async def test_batch_pricing(
    client: RoostooClient, results: Phase0Results, config: dict[str, Any]
) -> None:
    """Does one /v3/ticker call (no pair param) return ALL prices?

    If yes: momentum signal breadth covers all 56 assets per cycle with 1 call.
    If no: breadth drops to ~20 assets per cycle (rate-limit constrained).
    This is a fundamental signal architecture question.
    """
    logger.info("=" * 64)
    logger.info("TEST 1: Batch Pricing (all prices in one call?)")
    logger.info("=" * 64)

    try:
        t_start = time.time()
        resp = await client.get_all_tickers()
        t_elapsed = time.time() - t_start

        data = resp.get("Data", {})
        pairs_returned = len(data)
        batch_available = pairs_returned > 1

        # Cross-reference with our universe
        expected = get_expected_pairs(config)
        discovered = set(data.keys())
        missing = sorted(expected - discovered)
        extra = sorted(discovered - expected)

        # Sample prices for sanity check
        sample_prices: dict[str, float] = {}
        for pair in ("BTC/USD", "ETH/USD", "PAXG/USD", "DOGE/USD"):
            if pair in data:
                sample_prices[pair] = data[pair].get("LastPrice", 0)

        results.record("batch_pricing", batch_available, {
            "batch_available": batch_available,
            "pairs_returned": pairs_returned,
            "response_time_sec": round(t_elapsed, 3),
            "api_calls_used": 1,
            "expected_pairs_count": len(expected),
            "missing_from_universe": missing if missing else [],
            "extra_not_in_universe": extra if extra else [],
            "sample_prices": sample_prices,
        })

        if missing:
            logger.warning(
                "%d expected pairs NOT on exchange: %s", len(missing), missing
            )
        if extra:
            logger.info(
                "%d extra pairs available (classify for Tier 5): %s",
                len(extra), extra,
            )

    except Exception as e:
        results.record("batch_pricing", False, {"error": str(e)})


# ===========================================================================
# TEST 2 — Limit Order Fill Mechanics (spec: 10 min budget)
# ===========================================================================
async def test_limit_fills(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Place a limit buy at exact current price for a small amount.

    If it fills immediately: execution engine simplifies dramatically.
    No timeout loop needed for normal trades — only risk exits need
    resubmission logic.

    Uses ~$200 of BTC and immediately reverses the position.
    """
    logger.info("=" * 64)
    logger.info("TEST 2: Limit Order Fill Mechanics")
    logger.info("=" * 64)

    fill_data: dict[str, Any] = {}

    try:
        # Step 1: Get current BTC price and pair precision
        ticker_resp = await client.get_ticker("BTC/USD")
        btc_ticker = ticker_resp.get("Data", {}).get("BTC/USD", {})
        last_price = btc_ticker.get("LastPrice", 0)
        bid_price = btc_ticker.get("MaxBid", 0)
        ask_price = btc_ticker.get("MinAsk", 0)

        if last_price <= 0:
            results.record("limit_fills", False, {
                "error": "Could not get BTC price",
                "ticker_response": btc_ticker,
            })
            return

        fill_data["current_price"] = {
            "last": last_price, "bid": bid_price, "ask": ask_price,
        }

        exchange_info = await client.get_exchange_info()
        btc_info = exchange_info.get("TradePairs", {}).get("BTC/USD", {})
        price_prec = int(btc_info.get("PricePrecision", 2))
        amount_prec = int(btc_info.get("AmountPrecision", 6))
        min_order = float(btc_info.get("MiniOrder", 1))

        fill_data["btc_pair_info"] = {
            "price_precision": price_prec,
            "amount_precision": amount_prec,
            "min_order": min_order,
        }

        # Step 2: Calculate test quantity (~$200 worth, above minimum)
        test_qty = round(max(200.0 / last_price, min_order) * 1.1, amount_prec)
        test_qty = max(test_qty, 10 ** (-amount_prec))
        price_str = f"{last_price:.{price_prec}f}"
        qty_str = f"{test_qty:.{amount_prec}f}"

        logger.info(
            "Placing LIMIT BUY: %s BTC @ $%s (at LastPrice)", qty_str, price_str
        )

        # Step 3: Place limit buy at last price — does it fill immediately?
        t_start = time.monotonic()
        buy_resp = await client.place_limit_order(
            pair="BTC/USD", side="BUY", quantity=qty_str, price=price_str,
        )
        t_fill = time.monotonic() - t_start

        buy_detail = buy_resp.get("OrderDetail", {})
        buy_status = buy_detail.get("Status", "UNKNOWN")
        buy_role = buy_detail.get("Role", "UNKNOWN")
        buy_order_id = buy_detail.get("OrderID", 0)
        buy_commission = buy_detail.get("CommissionPercent", 0)
        buy_filled_price = buy_detail.get("FilledAverPrice", 0)
        buy_filled_qty = buy_detail.get("FilledQuantity", 0)

        fills_immediately = buy_status.upper() == "FILLED"

        fill_data["limit_buy_at_lastprice"] = {
            "order_id": buy_order_id,
            "status": buy_status,
            "fills_immediately": fills_immediately,
            "role": buy_role,
            "requested_price": last_price,
            "filled_price": buy_filled_price,
            "filled_quantity": buy_filled_qty,
            "commission_pct": buy_commission,
            "commission_bps": round(buy_commission * 10000, 2),
            "fill_latency_ms": round(t_fill * 1000, 1),
            "response_fields": list(buy_detail.keys()),
        }

        if fills_immediately:
            logger.info(
                "CONFIRMED: Limit buy at LastPrice fills immediately "
                "(status=%s, role=%s, commission=%.4f%%)",
                buy_status, buy_role, buy_commission * 100,
            )
        else:
            logger.warning(
                "Limit buy did NOT fill immediately — status=%s. "
                "Execution engine needs timeout/resubmission logic.",
                buy_status,
            )

        # Step 4: Place a limit buy BELOW market — should stay PENDING
        pending_price = round(last_price * 0.50, price_prec)
        pending_price_str = f"{pending_price:.{price_prec}f}"
        logger.info(
            "Placing LIMIT BUY: %s BTC @ $%s (50%% below — expect PENDING)",
            qty_str, pending_price_str,
        )

        pending_resp = await client.place_limit_order(
            pair="BTC/USD", side="BUY", quantity=qty_str, price=pending_price_str,
        )
        pending_detail = pending_resp.get("OrderDetail", {})
        pending_status = pending_detail.get("Status", "UNKNOWN")
        pending_order_id = pending_detail.get("OrderID", 0)

        fill_data["limit_buy_below_market"] = {
            "order_id": pending_order_id,
            "status": pending_status,
            "expected_pending": pending_status.upper() != "FILLED",
        }

        # Step 5: Cancel the pending order
        if pending_order_id:
            cancel_resp = await client.cancel_order(
                order_id=str(pending_order_id)
            )
            canceled = cancel_resp.get("CanceledList", [])
            fill_data["cancel_pending"] = {
                "success": pending_order_id in canceled,
                "canceled_ids": canceled,
            }

        # Step 6: Sell back what we bought to restore balance
        if fills_immediately and buy_filled_qty > 0:
            sell_qty_str = f"{buy_filled_qty:.{amount_prec}f}"
            logger.info("Selling back: %s BTC @ $%s", sell_qty_str, price_str)
            sell_resp = await client.place_limit_order(
                pair="BTC/USD", side="SELL",
                quantity=sell_qty_str, price=price_str,
            )
            sell_detail = sell_resp.get("OrderDetail", {})
            fill_data["sell_back"] = {
                "status": sell_detail.get("Status"),
                "role": sell_detail.get("Role"),
                "commission_pct": sell_detail.get("CommissionPercent", 0),
            }

        results.record("limit_fills", True, fill_data)

    except Exception as e:
        fill_data["error"] = str(e)
        results.record("limit_fills", False, fill_data)


# ===========================================================================
# TEST 3 — Rate Limit (spec: 10 min budget)
# ===========================================================================
async def test_rate_limit(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Fire rapid API calls and count successes before a 429.

    The actual limit (30 or 60 calls/min) determines:
    - How many assets we can price per cycle
    - How many orders we can place per rebalance
    - At 30: can only execute ~6-8 trades per rebalance cycle

    Uses a separate unlimited-rate client so the main rate limiter
    doesn't interfere with measurement.
    """
    logger.info("=" * 64)
    logger.info("TEST 3: Rate Limit (empirical measurement)")
    logger.info("=" * 64)
    logger.info("Sending rapid /v3/serverTime requests...")
    logger.info("Will stop at first 429 or after 80 requests.")

    # Bypass the normal rate limiter for this test
    test_limiter = TokenBucketRateLimiter(
        calls_per_minute=6000,  # effectively unlimited
        reserve_headroom=0,
    )
    test_client = RoostooClient(
        base_url=client.base_url,
        rate_limiter=test_limiter,
    )

    successes = 0
    first_429_at = 0
    errors_429: list[dict[str, Any]] = []
    call_latencies: list[float] = []
    target_calls = 80

    t_start = time.monotonic()

    try:
        for i in range(target_calls):
            t_call = time.monotonic()
            try:
                await test_client.get_server_time()
                successes += 1
                call_latencies.append((time.monotonic() - t_call) * 1000)
            except RoostooAPIError as e:
                if e.status_code == 429:
                    elapsed = time.monotonic() - t_start
                    if first_429_at == 0:
                        first_429_at = successes
                    errors_429.append({
                        "call_number": i + 1,
                        "elapsed_sec": round(elapsed, 2),
                    })
                    # Brief pause before continuing to see sustained behavior
                    await asyncio.sleep(2)
                else:
                    logger.warning("Non-429 error at call %d: %s", i + 1, e)

            # Minimal delay to not completely overwhelm
            await asyncio.sleep(0.05)

            # Progress log every 20 calls
            if (i + 1) % 20 == 0:
                elapsed = time.monotonic() - t_start
                rate = (i + 1) / elapsed
                logger.info(
                    "  #%d: %d successes, %.1f req/s, %.1fs elapsed",
                    i + 1, successes, rate, elapsed,
                )

        t_total = time.monotonic() - t_start
        effective_rate = successes / t_total * 60 if t_total > 0 else 0

        if first_429_at > 0:
            estimated_limit = first_429_at
            recommendation = (
                f"Set api.rate_limit_calls_per_min to {estimated_limit} "
                f"(first 429 after {first_429_at} calls)"
            )
        else:
            estimated_limit = None
            recommendation = (
                f"No 429 in {target_calls} calls ({effective_rate:.0f} "
                f"effective req/min). Rate limit may be very high. "
                "Keep conservative default of 30 for competition load."
            )

        avg_latency = (
            round(sum(call_latencies) / len(call_latencies), 1)
            if call_latencies else 0
        )

        results.record("rate_limit", True, {
            "total_calls_attempted": target_calls,
            "successful_calls": successes,
            "first_429_at_call": first_429_at if first_429_at > 0 else "never",
            "total_time_sec": round(t_total, 2),
            "effective_rate_per_min": round(effective_rate),
            "avg_latency_ms": avg_latency,
            "min_latency_ms": round(min(call_latencies), 1) if call_latencies else 0,
            "max_latency_ms": round(max(call_latencies), 1) if call_latencies else 0,
            "hit_429": first_429_at > 0,
            "estimated_limit": estimated_limit,
            "total_429s": len(errors_429),
            "recommendation": recommendation,
        })

    except Exception as e:
        results.record("rate_limit", False, {"error": str(e)})
    finally:
        await test_client.close()


# ===========================================================================
# TEST 4 — Data Fields (spec: 5 min budget)
# ===========================================================================
async def test_data_fields(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Print the full ticker response and check for OHLCV, volume, bid/ask.

    If no volume: skip all volume features in Layer 2.
    """
    logger.info("=" * 64)
    logger.info("TEST 4: Data Fields Analysis")
    logger.info("=" * 64)

    try:
        resp = await client.get_all_tickers()
        data = resp.get("Data", {})

        if not data:
            results.record("data_fields", False, {
                "error": "No ticker data returned",
            })
            return

        # Analyze field structure from first ticker
        first_pair = next(iter(data))
        first_ticker = data[first_pair]
        all_fields = sorted(first_ticker.keys())

        # Volume analysis
        has_coin_volume = "CoinTradeValue" in first_ticker
        has_usd_volume = "UnitTradeValue" in first_ticker
        has_volume_data = has_coin_volume or has_usd_volume

        volume_samples: dict[str, dict[str, Any]] = {}
        volume_populated = False
        for pair in ("BTC/USD", "ETH/USD", "DOGE/USD", "PAXG/USD"):
            if pair in data:
                ticker = data[pair]
                coin_vol = ticker.get("CoinTradeValue", 0)
                usd_vol = ticker.get("UnitTradeValue", 0)
                volume_samples[pair] = {
                    "CoinTradeValue": coin_vol,
                    "UnitTradeValue": usd_vol,
                }
                if coin_vol > 0 or usd_vol > 0:
                    volume_populated = True

        # Price and spread analysis
        has_bid_ask = "MaxBid" in first_ticker and "MinAsk" in first_ticker
        has_last_price = "LastPrice" in first_ticker
        has_change = "Change" in first_ticker

        spread_analysis: dict[str, dict[str, Any]] = {}
        for pair in ("BTC/USD", "ETH/USD"):
            if pair in data:
                t = data[pair]
                bid = t.get("MaxBid", 0)
                ask = t.get("MinAsk", 0)
                last = t.get("LastPrice", 0)
                if bid > 0 and ask > 0:
                    mid = (bid + ask) / 2
                    spread_bps = ((ask - bid) / mid) * 10000
                    spread_analysis[pair] = {
                        "bid": bid,
                        "ask": ask,
                        "last": last,
                        "spread_bps": round(spread_bps, 2),
                        "bid_eq_ask": bid == ask,
                    }

        # Check for OHLCV (unlikely on ticker, but check)
        ohlcv_fields = [f for f in all_fields if f.lower() in (
            "open", "high", "low", "close", "volume",
        )]

        results.record("data_fields", True, {
            "all_fields": all_fields,
            "has_volume_data": has_volume_data,
            "volume_fields": {
                "CoinTradeValue": has_coin_volume,
                "UnitTradeValue": has_usd_volume,
            },
            "volume_populated": volume_populated,
            "volume_samples": volume_samples,
            "has_bid_ask": has_bid_ask,
            "has_last_price": has_last_price,
            "has_change_24h": has_change,
            "ohlcv_fields_found": ohlcv_fields if ohlcv_fields else "NONE",
            "spread_analysis": spread_analysis,
            "sample_ticker": {first_pair: first_ticker},
        })

        logger.info("Ticker fields: %s", all_fields)
        if not has_volume_data:
            logger.warning(
                "NO VOLUME DATA — skip all volume features in Layer 2"
            )

    except Exception as e:
        results.record("data_fields", False, {"error": str(e)})


# ===========================================================================
# TEST 5 — Exchange Info (supplementary)
# ===========================================================================
async def test_exchange_info(
    client: RoostooClient, results: Phase0Results, config: dict[str, Any]
) -> None:
    """Discover all trading pairs, precision rules, initial wallet."""
    logger.info("=" * 64)
    logger.info("TEST 5: Exchange Info (Universe Discovery)")
    logger.info("=" * 64)

    try:
        resp = await client.get_exchange_info()

        is_running = resp.get("IsRunning", False)
        initial_wallet = resp.get("InitialWallet", {})
        trade_pairs = resp.get("TradePairs", {})

        # Extract pair info
        pair_details: dict[str, dict[str, Any]] = {}
        for pair_name, info in trade_pairs.items():
            pair_details[pair_name] = {
                "coin": info.get("Coin"),
                "price_precision": info.get("PricePrecision"),
                "amount_precision": info.get("AmountPrecision"),
                "min_order": info.get("MiniOrder"),
                "can_trade": info.get("CanTrade"),
            }

        # Cross-reference with our universe
        expected = get_expected_pairs(config)
        discovered = set(pair_details.keys())
        missing = sorted(expected - discovered)
        extra = sorted(discovered - expected)
        tradeable = sum(1 for p in pair_details.values() if p.get("can_trade"))

        results.record("exchange_info", True, {
            "is_running": is_running,
            "initial_wallet": initial_wallet,
            "total_pairs": len(pair_details),
            "tradeable_pairs": tradeable,
            "expected_pairs_count": len(expected),
            "missing_pairs": missing,
            "extra_pairs": extra,
            "response_keys": list(resp.keys()),
            "pair_fields": list(next(iter(pair_details.values())).keys()) if pair_details else [],
        })

        # Log key findings
        if initial_wallet:
            usd_capital = initial_wallet.get("USD", "NOT FOUND")
            logger.info("Initial wallet USD: %s", usd_capital)
        if missing:
            logger.warning("%d expected pairs MISSING: %s", len(missing), missing)
        if extra:
            logger.info("%d extra pairs available: %s", len(extra), extra)

    except Exception as e:
        results.record("exchange_info", False, {"error": str(e)})


# ===========================================================================
# TEST 6 — Account Balance (supplementary, requires credentials)
# ===========================================================================
async def test_balance(
    client: RoostooClient, results: Phase0Results
) -> None:
    """Verify account balance and starting capital."""
    logger.info("=" * 64)
    logger.info("TEST 6: Account Balance")
    logger.info("=" * 64)

    try:
        resp = await client.get_balance()
        wallet = resp.get("Wallet", {})

        non_zero: dict[str, dict[str, float]] = {}
        for currency, balance in wallet.items():
            if not isinstance(balance, dict):
                continue
            free = float(balance.get("Free", 0))
            locked = float(balance.get("Lock", 0))
            if free > 0 or locked > 0:
                non_zero[currency] = {"free": free, "locked": locked}

        usd = non_zero.get("USD", {})

        results.record("balance", True, {
            "usd_free": usd.get("free", 0),
            "usd_locked": usd.get("locked", 0),
            "total_currencies": len(wallet),
            "non_zero_assets": len(non_zero),
            "non_zero_balances": non_zero,
        })

        logger.info("USD balance: free=%s, locked=%s",
                     usd.get("free", 0), usd.get("locked", 0))

    except Exception as e:
        results.record("balance", False, {"error": str(e)})


# ===========================================================================
# Main runner
# ===========================================================================
async def run_phase0(test_filter: str = "all") -> Phase0Results:
    """Execute Phase 0 tests and produce config recommendations.

    Args:
        test_filter: "all" or a specific test name.

    Returns:
        Phase0Results with all test outcomes.
    """
    config = load_config()
    api_config = config.get("api", {})

    base_url = api_config.get("base_url", "https://mock-api.roostoo.com")
    rate_limit = api_config.get("rate_limit_calls_per_min", 30)

    # Credential check
    key_env = api_config.get("key_env_var", "ROOSTOO_API_KEY")
    secret_env = api_config.get("secret_env_var", "ROOSTOO_API_SECRET")
    has_creds = bool(os.environ.get(key_env)) and bool(os.environ.get(secret_env))

    if not has_creds:
        logger.warning(
            "API credentials not set. Set %s and %s. "
            "Signed endpoint tests (limit_fills, balance) will fail.",
            key_env, secret_env,
        )

    rate_limiter = TokenBucketRateLimiter(
        calls_per_minute=rate_limit,
        reserve_headroom=0,  # No reservation during testing
    )
    client = RoostooClient(base_url=base_url, rate_limiter=rate_limiter)
    results = Phase0Results()

    # Test definitions — ordered to match spec numbering
    test_map: dict[str, Any] = {
        "server_connectivity": lambda: test_server_connectivity(client, results),
        "batch_pricing": lambda: test_batch_pricing(client, results, config),
        "limit_fills": lambda: test_limit_fills(client, results),
        "rate_limit": lambda: test_rate_limit(client, results),
        "data_fields": lambda: test_data_fields(client, results),
        "exchange_info": lambda: test_exchange_info(client, results, config),
        "balance": lambda: test_balance(client, results),
    }

    # Signed tests require credentials
    signed_tests = {"limit_fills", "balance"}

    if test_filter == "all":
        tests_to_run = list(test_map.keys())
    elif test_filter in test_map:
        tests_to_run = [test_filter]
    else:
        logger.error(
            "Unknown test: %s. Available: %s",
            test_filter, ", ".join(test_map.keys()),
        )
        await client.close()
        return results

    logger.info("=" * 64)
    logger.info("  APEX Phase 0 — Roostoo API Testing")
    logger.info("  Base URL: %s", base_url)
    logger.info("  Time: %s", datetime.now(timezone.utc).isoformat())
    logger.info("  Credentials: %s", "SET" if has_creds else "MISSING")
    logger.info("=" * 64)
    logger.info("")

    for test_name in tests_to_run:
        if test_name in signed_tests and not has_creds:
            logger.warning(
                "SKIPPING %s (requires API credentials)", test_name
            )
            results.record(test_name, False, {
                "error": "Skipped — API credentials not set",
            })
            continue

        try:
            await test_map[test_name]()
        except Exception as e:
            logger.error("Unhandled error in %s: %s", test_name, e)
            results.record(test_name, False, {"error": str(e)})

        logger.info("")

    await client.close()

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    logger.info("=" * 64)
    logger.info("PHASE 0 RESULTS SUMMARY")
    logger.info("=" * 64)

    passed = 0
    total = 0
    for test_name, result in results.results["tests"].items():
        total += 1
        status = result["status"]
        if status == "PASS":
            passed += 1
        logger.info("  %-25s %s", test_name, status)

    logger.info("")
    logger.info("  Tests passed: %d/%d", passed, total)

    # Config recommendations
    recs = results.config_recommendations()
    if recs:
        logger.info("")
        logger.info("CONFIG.YAML UPDATE RECOMMENDATIONS:")
        for key, value in recs.items():
            logger.info("  %s: %s", key, value)

    results.results["config_recommendations"] = recs

    return results


# ===========================================================================
# CLI entry point
# ===========================================================================
def main() -> None:
    """Parse arguments and run Phase 0 tests."""
    parser = argparse.ArgumentParser(
        description="APEX Phase 0 — Roostoo API Testing Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Available tests:\n"
            "  server_connectivity  — API reachability and clock sync\n"
            "  batch_pricing        — All prices in one call?\n"
            "  limit_fills          — Limit orders fill immediately?\n"
            "  rate_limit           — Empirical rate limit discovery\n"
            "  data_fields          — Ticker field analysis\n"
            "  exchange_info        — Universe discovery\n"
            "  balance              — Account balance check\n"
        ),
    )
    parser.add_argument(
        "--test",
        default="all",
        help="Run a specific test (default: all)",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "logs" / "phase0_results.json"),
        help="Output JSON path (default: logs/phase0_results.json)",
    )
    args = parser.parse_args()

    results = asyncio.run(run_phase0(test_filter=args.test))
    results.save(Path(args.output))


if __name__ == "__main__":
    main()
