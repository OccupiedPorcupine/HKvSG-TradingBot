#!/usr/bin/env python3
"""Phase 0 — Execution Engine Validation Tests.

Tests execution-critical assumptions about the Roostoo API that the
order manager depends on:

  1. Exchange info loading and precision mapping
  2. Limit order fill at LastPrice (immediate fill on mock exchange?)
  3. Order lifecycle: submit → query → fill/cancel
  4. Fill response field validation
  5. Commission rate verification (spec: 0.05% for limit)
  6. Sell order fill mechanics
  7. Multi-order submission (concurrent orders)

These tests use the ExecutionClient wrapper to validate that the
abstraction layer works correctly end-to-end.

Usage:
    export ROOSTOO_API_KEY="your_key"
    export ROOSTOO_API_SECRET="your_secret"

    # Run all tests:
    python scripts/phase0_execution_test.py

    # Run specific test:
    python scripts/phase0_execution_test.py --test exchange_info
    python scripts/phase0_execution_test.py --test fill_mechanics
    python scripts/phase0_execution_test.py --test order_lifecycle
    python scripts/phase0_execution_test.py --test commission
    python scripts/phase0_execution_test.py --test multi_order

Results saved to logs/phase0_execution_results.json.
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
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import yaml  # noqa: E402

from src.data.api_client import RoostooClient, RoostooAPIError  # noqa: E402
from src.data.rate_limiter import TokenBucketRateLimiter  # noqa: E402
from src.execution.roostoo_client import ExecutionClient, PairInfo  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("phase0_exec")


# ---------------------------------------------------------------------------
# Result collector
# ---------------------------------------------------------------------------
class TestResults:
    """Collects and serializes test results."""

    def __init__(self) -> None:
        self.results: dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "tests": {},
        }

    def record(
        self, name: str, passed: bool, data: Optional[dict] = None
    ) -> None:
        """Record a test result."""
        status = "PASS" if passed else "FAIL"
        self.results["tests"][name] = {"status": status, "data": data or {}}
        logger.info("[%s] %s", status, name)
        if data:
            for k, v in data.items():
                v_str = str(v)
                if len(v_str) > 300:
                    v_str = v_str[:300] + "..."
                logger.info("  %s: %s", k, v_str)

    def save(self, path: Path) -> None:
        """Save results to JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(self.results, f, indent=2, default=str)
        logger.info("Results saved to %s", path)


# ---------------------------------------------------------------------------
# Test 1: Exchange Info & Precision Mapping
# ---------------------------------------------------------------------------
async def test_exchange_info(
    exec_client: ExecutionClient, results: TestResults
) -> None:
    """Validate exchange info loading and precision mapping."""
    logger.info("=" * 60)
    logger.info("TEST: Exchange Info & Precision Mapping")
    logger.info("=" * 60)

    try:
        pair_info = await exec_client.load_exchange_info()

        # Check key pairs
        key_pairs = ["BTC/USD", "ETH/USD", "PAXG/USD", "DOGE/USD", "SHIB/USD"]
        pair_details: dict[str, dict] = {}

        for pair in key_pairs:
            if pair in pair_info:
                info = pair_info[pair]
                pair_details[pair] = {
                    "coin": info.coin,
                    "price_precision": info.price_precision,
                    "amount_precision": info.amount_precision,
                    "min_order": info.min_order,
                    "can_trade": info.can_trade,
                }

        # Verify formatting works
        format_tests: dict[str, dict] = {}
        if "BTC/USD" in pair_info:
            format_tests["BTC/USD"] = {
                "format_price(84500.123456)": exec_client.format_price(
                    "BTC/USD", 84500.123456
                ),
                "format_quantity(0.12345678)": exec_client.format_quantity(
                    "BTC/USD", 0.12345678
                ),
            }
        if "SHIB/USD" in pair_info:
            format_tests["SHIB/USD"] = {
                "format_price(0.0000123)": exec_client.format_price(
                    "SHIB/USD", 0.0000123
                ),
                "format_quantity(1000000)": exec_client.format_quantity(
                    "SHIB/USD", 1000000
                ),
            }

        results.record("exchange_info", True, {
            "total_pairs": len(pair_info),
            "tradeable": sum(1 for p in pair_info.values() if p.can_trade),
            "key_pair_details": pair_details,
            "format_tests": format_tests,
        })

    except Exception as e:
        results.record("exchange_info", False, {"error": str(e)})


# ---------------------------------------------------------------------------
# Test 2: Limit Order Fill Mechanics
# ---------------------------------------------------------------------------
async def test_fill_mechanics(
    exec_client: ExecutionClient, results: TestResults
) -> None:
    """Test whether limit orders at LastPrice fill immediately.

    This is THE critical Phase 0 question for the execution engine.
    If limit orders fill immediately at LastPrice on the mock exchange,
    the execution engine simplifies dramatically.

    Places a tiny BTC buy at LastPrice and checks the response status.
    """
    logger.info("=" * 60)
    logger.info("TEST: Limit Order Fill Mechanics")
    logger.info("=" * 60)

    try:
        # Ensure exchange info is loaded
        if not exec_client.pair_info:
            await exec_client.load_exchange_info()

        # Get current BTC price
        btc_price = await exec_client.get_last_price("BTC/USD")
        logger.info("Current BTC price: $%.2f", btc_price)

        # Calculate tiny test quantity (~$200 worth)
        btc_info = exec_client.get_pair_info("BTC/USD")
        test_qty = round(200.0 / btc_price, btc_info.amount_precision)
        test_qty = max(test_qty, 10 ** (-btc_info.amount_precision))

        # Place limit buy at last price
        logger.info("Placing LIMIT BUY: %.6f BTC @ $%.2f", test_qty, btc_price)

        t_start = time.monotonic()
        buy_result = await exec_client.place_limit_buy(
            "BTC/USD", test_qty, btc_price
        )
        t_elapsed = time.monotonic() - t_start

        fill_data = {
            "fills_immediately": buy_result.is_filled,
            "order_status": buy_result.status,
            "order_id": buy_result.order_id,
            "requested_price": btc_price,
            "filled_price": buy_result.filled_price,
            "filled_quantity": buy_result.filled_quantity,
            "commission_pct": buy_result.commission_pct,
            "role": buy_result.role,
            "latency_ms": round(t_elapsed * 1000, 1),
            "raw_fields": list(buy_result.raw.keys()),
        }

        if buy_result.is_filled:
            logger.info("CONFIRMED: Limit orders at LastPrice fill immediately")
            logger.info(
                "  Fill price: $%.4f  Commission: %.4f%%",
                buy_result.filled_price,
                buy_result.commission_pct * 100,
            )

            # Immediately sell back to clean up
            sell_result = await exec_client.place_limit_sell(
                "BTC/USD", buy_result.filled_quantity, btc_price
            )
            fill_data["sell_back"] = {
                "status": sell_result.status,
                "filled": sell_result.is_filled,
                "commission_pct": sell_result.commission_pct,
            }
        else:
            logger.warning("Limit order did NOT fill immediately — status: %s", buy_result.status)
            logger.warning("Execution engine must use timeout/resubmission logic")

            # Cancel the unfilled order
            if buy_result.order_id:
                cancelled = await exec_client.cancel_order(buy_result.order_id)
                fill_data["cancelled"] = cancelled

        results.record("fill_mechanics", True, fill_data)

    except Exception as e:
        results.record("fill_mechanics", False, {"error": str(e)})


# ---------------------------------------------------------------------------
# Test 3: Order Lifecycle (submit → query → cancel)
# ---------------------------------------------------------------------------
async def test_order_lifecycle(
    exec_client: ExecutionClient, results: TestResults
) -> None:
    """Test the full order lifecycle with a non-fillable order.

    Places a limit buy well below market, queries it, then cancels it.
    """
    logger.info("=" * 60)
    logger.info("TEST: Order Lifecycle (pending → query → cancel)")
    logger.info("=" * 60)

    try:
        if not exec_client.pair_info:
            await exec_client.load_exchange_info()

        btc_price = await exec_client.get_last_price("BTC/USD")
        btc_info = exec_client.get_pair_info("BTC/USD")

        # Place order at 50% below market (should stay pending)
        pending_price = round(btc_price * 0.50, btc_info.price_precision)
        test_qty = round(200.0 / btc_price, btc_info.amount_precision)
        test_qty = max(test_qty, 10 ** (-btc_info.amount_precision))

        logger.info(
            "Placing LIMIT BUY: %.6f BTC @ $%.2f (50%% below market)",
            test_qty, pending_price,
        )

        buy_result = await exec_client.place_limit_buy(
            "BTC/USD", test_qty, pending_price
        )

        lifecycle: dict[str, Any] = {
            "submit_status": buy_result.status,
            "order_id": buy_result.order_id,
            "is_pending": not buy_result.is_filled,
        }

        # Query the order
        if buy_result.order_id:
            await asyncio.sleep(1)
            query_result = await exec_client.query_order(buy_result.order_id)
            if query_result:
                lifecycle["query_status"] = query_result.status
                lifecycle["query_fields"] = list(query_result.raw.keys())
            else:
                lifecycle["query_status"] = "NOT_FOUND"

            # Cancel the order
            cancelled = await exec_client.cancel_order(buy_result.order_id)
            lifecycle["cancel_success"] = cancelled

            # Verify cancellation
            await asyncio.sleep(1)
            verify = await exec_client.query_order(buy_result.order_id)
            if verify:
                lifecycle["post_cancel_status"] = verify.status
            else:
                lifecycle["post_cancel_status"] = "NOT_FOUND"

        results.record("order_lifecycle", True, lifecycle)

    except Exception as e:
        results.record("order_lifecycle", False, {"error": str(e)})


# ---------------------------------------------------------------------------
# Test 4: Commission Rate Verification
# ---------------------------------------------------------------------------
async def test_commission(
    exec_client: ExecutionClient, results: TestResults
) -> None:
    """Verify commission rate matches spec (0.05% for limit orders).

    Places a small buy and sell, checks commission on both.
    """
    logger.info("=" * 60)
    logger.info("TEST: Commission Rate Verification")
    logger.info("=" * 60)

    try:
        if not exec_client.pair_info:
            await exec_client.load_exchange_info()

        btc_price = await exec_client.get_last_price("BTC/USD")
        btc_info = exec_client.get_pair_info("BTC/USD")
        test_qty = round(200.0 / btc_price, btc_info.amount_precision)
        test_qty = max(test_qty, 10 ** (-btc_info.amount_precision))

        # Buy
        buy = await exec_client.place_limit_buy("BTC/USD", test_qty, btc_price)
        buy_commission = buy.commission_pct

        commission_data: dict[str, Any] = {
            "buy_commission_pct": buy_commission,
            "buy_commission_bps": round(buy_commission * 10000, 2),
        }

        # Sell back
        if buy.is_filled and buy.filled_quantity > 0:
            sell = await exec_client.place_limit_sell(
                "BTC/USD", buy.filled_quantity, btc_price
            )
            commission_data["sell_commission_pct"] = sell.commission_pct
            commission_data["sell_commission_bps"] = round(
                sell.commission_pct * 10000, 2
            )

        # Verify against spec
        expected = 0.0005  # 0.05% = 5 bps
        matches_spec = abs(buy_commission - expected) < 0.0001

        commission_data["expected_pct"] = expected
        commission_data["matches_spec"] = matches_spec

        if not matches_spec:
            logger.warning(
                "Commission MISMATCH: got %.4f%%, expected %.4f%%",
                buy_commission * 100,
                expected * 100,
            )

        results.record("commission", matches_spec, commission_data)

    except Exception as e:
        results.record("commission", False, {"error": str(e)})


# ---------------------------------------------------------------------------
# Test 5: Multi-Order Submission
# ---------------------------------------------------------------------------
async def test_multi_order(
    exec_client: ExecutionClient, results: TestResults
) -> None:
    """Test submitting multiple orders in sequence.

    Verifies that the rate limiter and API handle rapid sequential
    order submissions correctly.
    """
    logger.info("=" * 60)
    logger.info("TEST: Multi-Order Submission")
    logger.info("=" * 60)

    try:
        if not exec_client.pair_info:
            await exec_client.load_exchange_info()

        prices = await exec_client.get_all_prices()

        # Pick 3 different pairs for testing
        test_pairs = []
        for pair in ["ETH/USD", "BNB/USD", "LTC/USD"]:
            if pair in prices and pair in exec_client.pair_info:
                test_pairs.append(pair)
            if len(test_pairs) >= 3:
                break

        if not test_pairs:
            results.record("multi_order", False, {
                "error": "No suitable test pairs found",
            })
            return

        order_results: list[dict] = []
        t_total_start = time.monotonic()

        for pair in test_pairs:
            price = prices[pair]
            info = exec_client.get_pair_info(pair)
            qty = round(100.0 / price, info.amount_precision)
            qty = max(qty, 10 ** (-info.amount_precision))

            t_start = time.monotonic()
            buy = await exec_client.place_limit_buy(pair, qty, price)
            t_elapsed = time.monotonic() - t_start

            entry: dict[str, Any] = {
                "pair": pair,
                "status": buy.status,
                "filled": buy.is_filled,
                "latency_ms": round(t_elapsed * 1000, 1),
            }

            # Sell back if filled
            if buy.is_filled and buy.filled_quantity > 0:
                sell = await exec_client.place_limit_sell(
                    pair, buy.filled_quantity, price
                )
                entry["sell_status"] = sell.status

            order_results.append(entry)

        t_total = time.monotonic() - t_total_start

        results.record("multi_order", True, {
            "pairs_tested": test_pairs,
            "total_time_sec": round(t_total, 2),
            "all_filled": all(r.get("filled") for r in order_results),
            "order_details": order_results,
        })

    except Exception as e:
        results.record("multi_order", False, {"error": str(e)})


# ---------------------------------------------------------------------------
# Test 6: Balance Sync
# ---------------------------------------------------------------------------
async def test_balance_sync(
    exec_client: ExecutionClient, results: TestResults
) -> None:
    """Verify balance endpoint works and returns expected structure."""
    logger.info("=" * 60)
    logger.info("TEST: Balance Sync")
    logger.info("=" * 60)

    try:
        balances = await exec_client.get_balance()
        usd = balances.get("USD", {})

        # Find non-zero balances
        non_zero = {
            k: v for k, v in balances.items()
            if v.get("free", 0) > 0 or v.get("locked", 0) > 0
        }

        results.record("balance_sync", True, {
            "usd_free": usd.get("free", 0),
            "usd_locked": usd.get("locked", 0),
            "non_zero_count": len(non_zero),
            "non_zero_assets": list(non_zero.keys()),
        })

    except Exception as e:
        results.record("balance_sync", False, {"error": str(e)})


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------
async def run_tests(test_filter: str = "all") -> TestResults:
    """Run Phase 0 execution tests.

    Args:
        test_filter: "all" or a specific test name.

    Returns:
        TestResults with all outcomes.
    """
    with open(PROJECT_ROOT / "config.yaml") as f:
        config = yaml.safe_load(f)

    api_config = config.get("api", {})
    base_url = api_config.get("base_url", "https://mock-api.roostoo.com")
    rate_limit = api_config.get("rate_limit_calls_per_min", 30)

    # Check credentials
    api_key = os.environ.get(
        api_config.get("key_env_var", "ROOSTOO_API_KEY"), ""
    )
    api_secret = os.environ.get(
        api_config.get("secret_env_var", "ROOSTOO_API_SECRET"), ""
    )

    if not api_key or not api_secret:
        logger.error(
            "API credentials not set. Set ROOSTOO_API_KEY and "
            "ROOSTOO_API_SECRET environment variables."
        )
        logger.error("Authenticated tests will fail.")

    rate_limiter = TokenBucketRateLimiter(
        calls_per_minute=rate_limit,
        reserve_headroom=0,
    )

    client = RoostooClient(base_url=base_url, rate_limiter=rate_limiter)
    exec_client = ExecutionClient(client)
    test_results = TestResults()

    test_map = {
        "exchange_info": lambda: test_exchange_info(exec_client, test_results),
        "fill_mechanics": lambda: test_fill_mechanics(exec_client, test_results),
        "order_lifecycle": lambda: test_order_lifecycle(exec_client, test_results),
        "commission": lambda: test_commission(exec_client, test_results),
        "multi_order": lambda: test_multi_order(exec_client, test_results),
        "balance_sync": lambda: test_balance_sync(exec_client, test_results),
    }

    tests_to_run = (
        list(test_map.keys()) if test_filter == "all" else [test_filter]
    )

    for test_name in tests_to_run:
        if test_name not in test_map:
            logger.error(
                "Unknown test: %s. Available: %s",
                test_name,
                ", ".join(test_map.keys()),
            )
            continue

        try:
            await test_map[test_name]()
        except Exception as e:
            logger.error("Unhandled error in %s: %s", test_name, e)
            test_results.record(test_name, False, {"error": str(e)})

        logger.info("")

    await exec_client.close()

    # Summary
    logger.info("=" * 60)
    logger.info("EXECUTION PHASE 0 SUMMARY")
    logger.info("=" * 60)

    for name, result in test_results.results["tests"].items():
        logger.info("  %-20s %s", name, result["status"])

    # Key findings
    fill_test = test_results.results["tests"].get("fill_mechanics", {})
    fill_data = fill_test.get("data", {})
    if "fills_immediately" in fill_data:
        logger.info("")
        logger.info("KEY FINDINGS:")
        logger.info(
            "  Limit fills at LastPrice: %s",
            "YES" if fill_data["fills_immediately"] else "NO",
        )
        if "buy_commission_pct" in fill_data:
            logger.info(
                "  Commission: %.4f%%",
                fill_data.get("commission_pct", 0) * 100,
            )

    comm_test = test_results.results["tests"].get("commission", {})
    comm_data = comm_test.get("data", {})
    if "matches_spec" in comm_data:
        logger.info(
            "  Commission matches spec (0.05%%): %s",
            "YES" if comm_data["matches_spec"] else "NO",
        )

    return test_results


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Phase 0 — Execution Engine Validation Tests"
    )
    parser.add_argument(
        "--test",
        default="all",
        help=(
            "Test to run: all, exchange_info, fill_mechanics, "
            "order_lifecycle, commission, multi_order, balance_sync"
        ),
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "logs" / "phase0_execution_results.json"),
        help="Output path (default: logs/phase0_execution_results.json)",
    )
    args = parser.parse_args()

    test_results = asyncio.run(run_tests(test_filter=args.test))
    test_results.save(Path(args.output))


if __name__ == "__main__":
    main()
