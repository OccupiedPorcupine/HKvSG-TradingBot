import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Any

import aiohttp

logger = logging.getLogger(__name__)

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_FUTURES_PREMIUM_URL = "https://fapi.binance.com/fapi/v1/premiumIndex"

# Assets whose Binance symbol differs from the default {ASSET}USDT pattern,
# or that don't exist on Binance (None = skip).
SYMBOL_OVERRIDES: dict[str, Optional[str]] = {
    "1000CHEEMS": "1000CHEEMSUSDT",
    "BONK": "BONKUSDT",
    "FLOKI": "FLOKIUSDT",
    "PEPE": "PEPEUSDT",
    "SHIB": "SHIBUSDT",
    "WIF": "WIFUSDT",
    "PENGU": "PENGUUSDT",
    "PUMP": None,
    "PAXG": "PAXGUSDT",
    "TRUMP": "TRUMPUSDT",
}


def asset_to_binance_symbol(asset: str) -> Optional[str]:
    """Convert an APEX asset name to its Binance trading symbol.

    Returns None if the asset has no Binance equivalent.
    """
    if asset in SYMBOL_OVERRIDES:
        return SYMBOL_OVERRIDES[asset]
    return f"{asset}USDT"


class BinancePriceClient:
    """Fetches live prices, historical klines, and funding rates from Binance.

    Uses public endpoints (no API key required). Handles symbol mapping
    between APEX and Binance.

    Attributes:
        _symbol_to_asset: Reverse map from Binance symbol to APEX asset name.
        _session: Shared aiohttp session.
    """

    def __init__(self) -> None:
        self._symbol_to_asset: dict[str, str] = {}
        self._session: Optional[aiohttp.ClientSession] = None

    def build_symbol_map(self, assets: list[str]) -> None:
        """Build the Binance-symbol → APEX-asset reverse mapping.

        Must be called after the exchange universe is known (after
        DataIngestionManager.initialize()).

        Args:
            assets: List of all APEX asset symbols.
        """
        self._symbol_to_asset = {}
        skipped = []
        for asset in assets:
            symbol = asset_to_binance_symbol(asset)
            if symbol is None:
                skipped.append(asset)
                continue
            self._symbol_to_asset[symbol] = asset

        logger.info(
            "Binance symbol map built: %d symbols (%d skipped: %s)",
            len(self._symbol_to_asset),
            len(skipped),
            skipped or "none",
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=5)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=10),
            )
        return self._session

    async def fetch_prices(self) -> dict[str, float]:
        """Fetch current prices for all known assets in one batch call.

        Returns:
            Dict mapping APEX asset name to latest price.
            Empty dict on failure.
        """
        if not self._symbol_to_asset:
            logger.warning("Binance symbol map not built — call build_symbol_map() first")
            return {}

        session = await self._get_session()
        try:
            async with session.get(BINANCE_TICKER_URL) as resp:
                if resp.status != 200:
                    logger.error("Binance ticker returned HTTP %d", resp.status)
                    return {}
                data: list[dict] = await resp.json()
        except Exception as e:
            logger.error("Binance fetch_prices failed: %s", e)
            return {}

        prices: dict[str, float] = {}
        for item in data:
            symbol = item.get("symbol", "")
            asset = self._symbol_to_asset.get(symbol)
            if asset is None:
                continue
            try:
                prices[asset] = float(item["price"])
            except (KeyError, ValueError):
                continue

        logger.debug(
            "Binance batch fetch: %d/%d assets priced",
            len(prices),
            len(self._symbol_to_asset),
        )
        return prices

    async def fetch_klines(
        self, asset: str, interval: str = "1m", limit: int = 1440
    ) -> list[dict[str, Any]]:
        """Fetch historical klines (OHLCV) for a single asset.

        Binance limit is 1000 per call. If limit > 1000, multiple calls
        are made and joined.

        Returns:
            List of dicts: {timestamp_utc, price, volume}
        """
        symbol = asset_to_binance_symbol(asset)
        if not symbol:
            return []

        session = await self._get_session()
        all_klines = []
        remaining = limit
        end_time = None

        try:
            while remaining > 0:
                fetch_limit = min(remaining, 1000)
                params: dict[str, Any] = {
                    "symbol": symbol,
                    "interval": interval,
                    "limit": fetch_limit,
                }
                if end_time:
                    params["endTime"] = end_time

                async with session.get(BINANCE_KLINES_URL, params=params) as resp:
                    if resp.status != 200:
                        logger.error("Binance klines HTTP %d for %s", resp.status, asset)
                        break
                    
                    data = await resp.json()
                    if not data:
                        break
                    
                    # Convert to APEX format
                    # data[i] = [open_time, open, high, low, close, volume, ...]
                    current_batch = []
                    for k in data:
                        current_batch.append({
                            "timestamp_utc": datetime.fromtimestamp(
                                k[0] / 1000, tz=timezone.utc
                            ).isoformat(),
                            "price": float(k[4]),  # Close price
                            "volume": float(k[5]), # Volume
                        })
                    
                    # Prepend since we are moving backward in time if end_time is used
                    # But if we don't use startTime, we get the MOST RECENT ones ending at now.
                    # Actually, if no endTime is specified, it returns the LATEST entries.
                    # If we need more, we take the oldest from the current batch and set it as endTime.
                    
                    if not all_klines:
                        all_klines = current_batch
                    else:
                        # Join: current_batch contains older data than all_klines
                        all_klines = current_batch + all_klines
                    
                    remaining -= len(data)
                    if len(data) < fetch_limit:
                        break # No more data available
                    
                    # Set end_time to just before the oldest one in this batch
                    end_time = data[0][0] - 1
                    
                    # Small sleep to respect rate limits if doing many calls
                    if remaining > 0:
                        await asyncio.sleep(0.1)

            return all_klines[-limit:] # Ensure exact limit

        except Exception as e:
            logger.error("Binance fetch_klines failed for %s: %s", asset, e)
            return all_klines

    async def fetch_funding_rates(self) -> dict[str, float]:
        """Fetch current funding rates for all assets from Binance Futures.

        Returns:
            Dict mapping APEX asset name to current funding rate.
        """
        session = await self._get_session()
        try:
            async with session.get(BINANCE_FUTURES_PREMIUM_URL) as resp:
                if resp.status != 200:
                    logger.error("Binance futures premium returned HTTP %d", resp.status)
                    return {}
                data = await resp.json()
        except Exception as e:
            logger.error("Binance fetch_funding_rates failed: %s", e)
            return {}

        # If data is a list (all symbols)
        if isinstance(data, list):
            rates: dict[str, float] = {}
            for item in data:
                symbol = item.get("symbol", "")
                asset = self._symbol_to_asset.get(symbol)
                if asset is None:
                    continue
                try:
                    rates[asset] = float(item["lastFundingRate"])
                except (KeyError, ValueError, TypeError):
                    continue
            return rates
        
        return {}

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
