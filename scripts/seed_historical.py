"""Seed historical price data from Binance into the APEX ring buffer format.

Fetches 1440 bars (24h) of 1-minute OHLCV data from Binance public API
for all assets in the universe, then writes a Parquet file that the
DataIngestionManager will load automatically on next startup.

No API key required — Binance klines endpoint is public.

Usage:
    PYTHONPATH=. python scripts/seed_historical.py

The file is written to the path configured in config.yaml under
paths.parquet_backup (default: data/price_backup.parquet).

After running this script, start the bot normally. The feature engine
will have full 24h history from bar 1 — all timeframe returns available.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import pandas as pd
import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("seed_historical")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BARS_TO_FETCH = 1440       # 24h of 1-minute bars
INTERVAL = "1m"
DELAY_BETWEEN_REQUESTS = 0.15   # seconds — stay well under Binance rate limit

# Assets that have non-standard Binance symbols or don't exist on Binance
SYMBOL_OVERRIDES = {
    "1000CHEEMS": "1000CHEEMSUSDT",
    "BONK": "BONKUSDT",
    "FLOKI": "FLOKIUSDT",
    "PEPE": "PEPEUSDT",
    "SHIB": "SHIBUSDT",
    "WIF": "WIFUSDT",
    "PENGU": "PENGUUSDT",
    "PUMP": None,           # does not exist on Binance — skip
    "PAXG": "PAXGUSDT",
    "TRUMP": "TRUMPUSDT",
}


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def get_all_assets(config: dict) -> list[str]:
    """Return flat list of all asset symbols from config universe."""
    universe = config.get("universe", {})
    assets = []
    for key in ["tier_1_majors", "tier_2_large_alts", "tier_3_defi",
                "tier_4_meme", "tier_5_obscure"]:
        assets.extend(universe.get(key, []))
    special = universe.get("special", {})
    assets.append(special.get("paxg", "PAXG"))
    assets.append(special.get("trump", "TRUMP"))
    return assets


def asset_to_binance_symbol(asset: str) -> str | None:
    """Convert APEX asset symbol to Binance trading pair symbol.

    Returns None if the asset should be skipped (not on Binance).
    """
    if asset in SYMBOL_OVERRIDES:
        return SYMBOL_OVERRIDES[asset]
    return f"{asset}USDT"


async def fetch_klines(
    session: aiohttp.ClientSession,
    symbol: str,
    limit: int = BARS_TO_FETCH,
) -> list[dict] | None:
    """Fetch 1-minute klines from Binance for a single symbol.

    Args:
        session: aiohttp session.
        symbol: Binance symbol (e.g., "BTCUSDT").
        limit: Number of bars to fetch (max 1000 per request).

    Returns:
        List of price records or None on failure.
    """
    records = []

    # Binance max per request is 1000 — fetch in two batches if needed
    batches = []
    remaining = limit
    end_time = None

    while remaining > 0:
        batch_limit = min(remaining, 1000)
        params = {
            "symbol": symbol,
            "interval": INTERVAL,
            "limit": batch_limit,
        }
        if end_time is not None:
            params["endTime"] = end_time

        try:
            async with session.get(BINANCE_KLINES_URL, params=params) as resp:
                if resp.status == 400:
                    # Symbol likely doesn't exist
                    return None
                if resp.status != 200:
                    logger.warning("HTTP %d for %s", resp.status, symbol)
                    return None
                data = await resp.json()
        except Exception as e:
            logger.warning("Request failed for %s: %s", symbol, e)
            return None

        if not data:
            break

        batches = data + batches  # prepend older bars
        remaining -= len(data)

        # Move end_time back to get older bars
        end_time = data[0][0] - 1  # open_time of oldest bar minus 1ms

        if len(data) < batch_limit:
            break  # No more history available

        await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

    # Parse kline format: [open_time, open, high, low, close, volume, ...]
    now_utc = datetime.now(timezone.utc)
    for kline in batches:
        open_time_ms = int(kline[0])
        close_price = float(kline[4])
        volume = float(kline[5])  # base asset volume

        ts = datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc).isoformat()
        records.append({
            "timestamp_utc": ts,
            "price": close_price,
            "volume": volume,
        })

    return records


async def seed(config: dict) -> None:
    """Main seeding function."""
    assets = get_all_assets(config)
    parquet_path = Path(config.get("paths", {}).get("parquet_backup", "data/price_backup.parquet"))
    parquet_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Seeding %d assets → %s", len(assets), parquet_path)

    all_rows: list[dict] = []
    skipped: list[str] = []
    failed: list[str] = []

    connector = aiohttp.TCPConnector(limit=5)
    async with aiohttp.ClientSession(connector=connector) as session:
        for i, asset in enumerate(assets):
            binance_symbol = asset_to_binance_symbol(asset)

            if binance_symbol is None:
                logger.info("  [%d/%d] %s — skipped (no Binance symbol)", i + 1, len(assets), asset)
                skipped.append(asset)
                continue

            records = await fetch_klines(session, binance_symbol)

            if records is None:
                logger.warning("  [%d/%d] %s (%s) — not found on Binance", i + 1, len(assets), asset, binance_symbol)
                failed.append(asset)
            else:
                for rec in records:
                    all_rows.append({
                        "asset": asset,
                        "timestamp_utc": rec["timestamp_utc"],
                        "price": rec["price"],
                        "volume": rec["volume"],
                    })
                logger.info(
                    "  [%d/%d] %s — %d bars (latest price: %.4f)",
                    i + 1, len(assets), asset, len(records), records[-1]["price"] if records else 0,
                )

            await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

    if not all_rows:
        logger.error("No data fetched — aborting.")
        return

    df = pd.DataFrame(all_rows)
    df.to_parquet(parquet_path, index=False)

    logger.info("")
    logger.info("=== SEED COMPLETE ===")
    logger.info("Saved %d records for %d assets to %s",
                len(all_rows), df["asset"].nunique(), parquet_path)
    logger.info("Skipped (no Binance symbol): %s", skipped or "none")
    logger.info("Not found on Binance: %s", failed or "none")
    logger.info("")
    logger.info("Start the bot now — feature engine will have full 24h history from bar 1.")


if __name__ == "__main__":
    config = load_config()
    asyncio.run(seed(config))
