"""Binance public API client for real-time price data.

Fetches all prices in a single batch call to /api/v3/ticker/price.
One HTTP request → every tradeable symbol's current price.
No API key required.

Weight cost: 2 per call (vs 1 per individual symbol).
Safe polling rate: up to 10 calls/second; 1-2s interval recommended.
"""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

BINANCE_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"

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
    """Fetches live prices from Binance using a single batch call.

    One call to /api/v3/ticker/price returns every symbol simultaneously.
    Call build_symbol_map() after the asset universe is known so incoming
    Binance symbols can be mapped back to APEX asset names.

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

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
