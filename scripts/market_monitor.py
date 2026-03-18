"""Live market data monitor — streams Binance prices to a log file.

Uses a single Binance batch call (all ~2000 symbols, weight=2) every
POLL_INTERVAL seconds. Writes formatted snapshots to logs/market_data.log.

Tail it with:
    tail -f logs/market_data.log

Usage:
    PYTHONPATH=. python scripts/market_monitor.py
    PYTHONPATH=. python scripts/market_monitor.py --interval 2
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src.data.binance_client import BinancePriceClient, asset_to_binance_symbol

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
LOG_PATH = PROJECT_ROOT / "logs" / "market_data.log"

DEFAULT_POLL_INTERVAL = 2  # seconds (safe: uses only 2 weight per call)


# ---------------------------------------------------------------------------
# Logging — dual: file for tail, stdout for live view
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(message)s")

    file_handler = logging.FileHandler(LOG_PATH)
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)

    log = logging.getLogger("market_monitor")
    log.setLevel(logging.INFO)
    log.addHandler(file_handler)
    log.addHandler(stream_handler)
    log.propagate = False
    return log


logger = setup_logging()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_all_assets(config: dict) -> list[str]:
    universe = config.get("universe", {})
    assets: list[str] = []
    for key in ["tier_1_majors", "tier_2_large_alts", "tier_3_defi",
                "tier_4_meme", "tier_5_obscure"]:
        assets.extend(universe.get(key, []))
    special = universe.get("special", {})
    if "paxg" in special:
        assets.append(special["paxg"])
    if "trump" in special:
        assets.append(special["trump"])
    return assets


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
def format_snapshot(
    prices: dict[str, float],
    prev_prices: dict[str, float],
    assets: list[str],
    poll_num: int,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "",
        f"┌─── BINANCE #{poll_num}  {now}  ({len(prices)}/{len(assets)} assets) ───",
    ]

    for asset in assets:
        if asset not in prices:
            continue
        price = prices[asset]
        prev = prev_prices.get(asset)

        if prev is None or prev == 0:
            arrow, pct_str = " ", "      "
        else:
            pct = (price - prev) / prev * 100
            arrow = "▲" if pct > 0 else ("▼" if pct < 0 else "─")
            pct_str = f"{pct:+.2f}%"

        if price >= 1000:
            price_str = f"{price:>12,.2f}"
        elif price >= 1:
            price_str = f"{price:>12.4f}"
        elif price >= 0.01:
            price_str = f"{price:>12.6f}"
        else:
            price_str = f"{price:>12.8f}"

        lines.append(f"│  {asset:<12} {price_str}  {arrow} {pct_str}")

    lines.append("└" + "─" * 55)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
async def run(poll_interval: int) -> None:
    config = load_config()
    assets = get_all_assets(config)
    valid_assets = [a for a in assets if asset_to_binance_symbol(a) is not None]

    client = BinancePriceClient()
    client.build_symbol_map(valid_assets)

    logger.info(f"Market monitor starting — {len(valid_assets)} assets, polling every {poll_interval}s")
    logger.info(f"Source: Binance batch ticker (1 call = all prices, weight=2)")
    logger.info(f"Log: {LOG_PATH}")
    logger.info("Press Ctrl+C to stop.\n")

    prev_prices: dict[str, float] = {}
    poll_num = 0

    try:
        while True:
            poll_start = asyncio.get_event_loop().time()
            poll_num += 1

            prices = await client.fetch_prices()

            if prices:
                snapshot = format_snapshot(prices, prev_prices, valid_assets, poll_num)
                logger.info(snapshot)
                prev_prices = prices
            else:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                logger.info(f"[{ts}] No prices received (poll #{poll_num})")

            elapsed = asyncio.get_event_loop().time() - poll_start
            await asyncio.sleep(max(0.1, poll_interval - elapsed))
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Live Binance market data monitor")
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_POLL_INTERVAL,
        help=f"Poll interval in seconds (default: {DEFAULT_POLL_INTERVAL})",
    )
    args = parser.parse_args()

    try:
        asyncio.run(run(args.interval))
    except KeyboardInterrupt:
        logger.info("\nMarket monitor stopped.")


if __name__ == "__main__":
    main()
