"""Layer 1 — Data Ingestion Pipeline.

Fetches, validates, and stores all market data in real time using
only in-process data structures. No Redis, no TimescaleDB, no
external services.

Data flow:
  1. Batch API call (all tickers, 1 call/min) → raw prices
  2. Validation (missing bars, anomalies) → cleaned prices
  3. Storage in per-asset deque ring buffers (maxlen=1440)
  4. Heartbeat file write
  5. Hourly Parquet backup for crash recovery
"""

import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from src.data.api_client import RoostooClient, RoostooAPIError
from src.data.binance_client import BinancePriceClient
from src.data.rate_limiter import TokenBucketRateLimiter
from src.data.validators import AssetStatus, DataValidator

logger = logging.getLogger(__name__)

# Tier classification constants
TIER_MAIN_POOL = "tier_1_2_3"
TIER_4_MEME = "tier_4_meme"
TIER_5_OBSCURE = "tier_5_obscure"
TIER_SPECIAL_PAXG = "special_paxg"
TIER_SPECIAL_TRUMP = "special_trump"


class DataIngestionManager:
    """Manages all data ingestion for the APEX trading bot.

    Fetches batch prices via the Roostoo API, validates incoming data,
    stores prices in per-asset ring buffers, and handles crash recovery
    via Parquet snapshots.

    Attributes:
        config: Full configuration dict from config.yaml.
        client: Roostoo API client.
        validator: Data quality validator.
        price_buffers: Per-asset deque of price records.
        pair_suffix: Trading pair suffix (e.g., "/USD").
    """

    def __init__(
        self,
        config: dict[str, Any],
        client: RoostooClient,
        validator: Optional[DataValidator] = None,
        binance_client: Optional[BinancePriceClient] = None,
    ) -> None:
        """Initialize the data ingestion manager.

        Args:
            config: Full config.yaml as dict.
            client: Initialized RoostooClient (used for exchange info and balance).
            validator: DataValidator instance (created if not provided).
            binance_client: If provided, use Binance for price data instead of Roostoo.
        """
        self.config = config
        self.client = client
        self._binance_client = binance_client

        di_config = config.get("data_ingestion", {})
        self.validator = validator or DataValidator(
            fill_forward_limit=di_config.get("missing_bar_fill_forward_limit", 3),
            anomaly_threshold_std=di_config.get("price_anomaly_threshold_std", 5.0),
            stale_max_age_sec=di_config.get("stale_data_max_age_sec", 300),
        )

        # +1 for fencepost: computing an N-bar return requires N+1 prices
        self._buffer_maxlen: int = di_config.get("ring_buffer_maxlen", 1440) + 1
        self._pair_suffix: str = config.get("universe", {}).get("pair_suffix", "/USD")

        # Per-asset ring buffers: deque of {timestamp_utc, price, volume}
        self.price_buffers: dict[str, deque] = {}

        # Tier classification: asset symbol → tier string
        self._tier_map: dict[str, str] = {}

        # Exchange info (populated on initialize)
        self._exchange_info: dict[str, Any] = {}
        self._pair_info: dict[str, dict[str, Any]] = {}

        # All known asset symbols (without suffix)
        self._all_assets: list[str] = []

        # Global data freshness
        self._last_fetch_utc: float = 0.0
        self._global_stale: bool = False

        # Heartbeat / backup paths
        log_config = config.get("logging", {})
        self._heartbeat_path = Path(log_config.get("heartbeat_file", "logs/heartbeat"))
        self._trade_log_path = Path(log_config.get("trade_log", "logs/trades.jsonl"))

        paths_config = config.get("paths", {})
        self._parquet_path = Path(paths_config.get("parquet_backup", "data/price_backup.parquet"))

        self._last_backup_utc: float = 0.0
        backup_hr = config.get("adaptation", {}).get("parquet_backup_cadence_hr", 1)
        self._backup_interval_sec: float = backup_hr * 3600

    # -------------------------------------------------------------------------
    # Initialization
    # -------------------------------------------------------------------------

    async def initialize(self) -> None:
        """Initialize by fetching exchange info and discovering the universe.

        Must be called before any data fetching. Populates tier map,
        exchange info, and creates price buffers for all assets.
        """
        logger.info("Initializing data ingestion — fetching exchange info...")

        self._exchange_info = await self.client.get_exchange_info()

        is_running = self._exchange_info.get("IsRunning", False)
        if not is_running:
            logger.warning("Exchange reports IsRunning=False")

        trade_pairs = self._exchange_info.get("TradePairs", {})
        logger.info("Exchange returned %d trading pairs", len(trade_pairs))

        # Store pair info (precision, min order)
        for pair_name, info in trade_pairs.items():
            self._pair_info[pair_name] = {
                "price_precision": info.get("PricePrecision", 2),
                "amount_precision": info.get("AmountPrecision", 6),
                "min_order": info.get("MiniOrder", 1),
                "can_trade": info.get("CanTrade", True),
            }

        # Build tier map from config
        self._build_tier_map(trade_pairs)

        # Create price buffers for all discovered assets
        for asset in self._all_assets:
            if asset not in self.price_buffers:
                self.price_buffers[asset] = deque(maxlen=self._buffer_maxlen)
            self.validator.register_asset(asset)

        # Build Binance symbol map now that the universe is known
        if self._binance_client is not None:
            self._binance_client.build_symbol_map(self._all_assets)
            logger.info("Price source: Binance (batch ticker)")

        logger.info(
            "Universe: %d assets, tiers: %s",
            len(self._all_assets),
            {t: sum(1 for a in self._all_assets if self._tier_map.get(a) == t)
             for t in set(self._tier_map.values())},
        )

        # Attempt crash recovery
        recovered = self._load_parquet_backup()
        if recovered:
            logger.info("Crash recovery: loaded price history from Parquet backup")

    def _build_tier_map(self, exchange_pairs: dict[str, Any]) -> None:
        """Build asset-to-tier mapping from config + discovered pairs.

        Extra pairs not in config are classified as Tier 5.

        Args:
            exchange_pairs: TradePairs dict from exchange info.
        """
        universe = self.config.get("universe", {})

        # Map config tiers
        tier_defs = [
            ("tier_1_majors", TIER_MAIN_POOL),
            ("tier_2_large_alts", TIER_MAIN_POOL),
            ("tier_3_defi", TIER_MAIN_POOL),
            ("tier_4_meme", TIER_4_MEME),
            ("tier_5_obscure", TIER_5_OBSCURE),
        ]

        configured_assets: set[str] = set()

        for config_key, tier_label in tier_defs:
            for asset in universe.get(config_key, []):
                self._tier_map[asset] = tier_label
                configured_assets.add(asset)

        # Special assets
        special = universe.get("special", {})
        paxg = special.get("paxg", "PAXG")
        trump = special.get("trump", "TRUMP")

        self._tier_map[paxg] = TIER_SPECIAL_PAXG
        self._tier_map[trump] = TIER_SPECIAL_TRUMP
        configured_assets.add(paxg)
        configured_assets.add(trump)

        # Discover extra pairs from exchange
        suffix = self._pair_suffix
        all_assets: list[str] = []

        for pair_name in exchange_pairs:
            if pair_name.endswith(suffix):
                asset = pair_name[: -len(suffix)]
            else:
                asset = pair_name.split("/")[0]

            all_assets.append(asset)

            if asset not in configured_assets:
                self._tier_map[asset] = TIER_5_OBSCURE
                logger.info(
                    "UNIVERSE_DISCOVERY extra asset=%s classified as %s",
                    asset,
                    TIER_5_OBSCURE,
                )

        self._all_assets = sorted(all_assets)

    # -------------------------------------------------------------------------
    # Price fetching
    # -------------------------------------------------------------------------

    async def fetch_prices(self) -> bool:
        """Fetch batch prices for all assets.

        Makes a single API call to get all ticker prices. Validates
        each price, fills forward missing bars, and stores in ring
        buffers.

        Returns:
            True if fetch succeeded (even partially), False on total failure.
        """
        now_utc = time.time()
        timestamp_utc = datetime.now(timezone.utc).isoformat()

        # Fetch prices — Binance if configured, else Roostoo
        if self._binance_client is not None:
            raw_prices = await self._binance_client.fetch_prices()
            if not raw_prices:
                logger.error("BATCH_FETCH_FAILED: Binance returned no prices")
                self._handle_fetch_failure(now_utc)
                return False
        else:
            try:
                resp = await self.client.get_all_tickers()
            except RoostooAPIError as e:
                logger.error("BATCH_FETCH_FAILED: %s", e)
                self._handle_fetch_failure(now_utc)
                return False

            data = resp.get("Data", {})
            if not data:
                logger.error("BATCH_FETCH_EMPTY: no ticker data returned")
                self._handle_fetch_failure(now_utc)
                return False

            raw_prices = {
                pair_name.replace(self._pair_suffix, ""): ticker.get("LastPrice", 0)
                for pair_name, ticker in data.items()
                if ticker.get("LastPrice", 0) > 0
            }

        self._last_fetch_utc = now_utc
        self._global_stale = False

        # Process each asset
        received_assets: set[str] = set()
        for asset, price in raw_prices.items():
            volume = None  # Binance batch ticker doesn't include volume

            if asset not in self._tier_map:
                # Unknown asset, add dynamically
                self._tier_map[asset] = TIER_5_OBSCURE
                self._all_assets.append(asset)
                self._all_assets.sort()
                self.price_buffers[asset] = deque(maxlen=self._buffer_maxlen)
                self.validator.register_asset(asset)
                logger.info("RUNTIME_DISCOVERY asset=%s", asset)

            if price <= 0:
                continue

            received_assets.add(asset)

            # Anomaly check (needs vol from feature engine — skip if no history)
            prev_price = self.get_latest_price(asset)
            if prev_price is not None and prev_price > 0:
                vol_1h = self._estimate_simple_vol(asset)
                is_anomaly = self.validator.check_anomaly(
                    asset, price, prev_price, vol_1h
                )
                if is_anomaly:
                    # Store the price but flag the asset
                    logger.debug("Anomaly flagged for %s, storing price anyway", asset)

            # Mark as received
            self.validator.check_missing_bar(asset, received=True)

            # Store in ring buffer
            record = {
                "timestamp_utc": timestamp_utc,
                "price": price,
                "volume": volume,
            }
            self.price_buffers[asset].append(record)

        # Check for missing assets — fill forward
        for asset in self._all_assets:
            if asset not in received_assets:
                status = self.validator.check_missing_bar(asset, received=False)

                if status != AssetStatus.STALE:
                    # Fill forward with last known price
                    last_price = self.get_latest_price(asset)
                    if last_price is not None:
                        record = {
                            "timestamp_utc": timestamp_utc,
                            "price": last_price,
                            "volume": None,
                        }
                        self.price_buffers.setdefault(
                            asset, deque(maxlen=self._buffer_maxlen)
                        ).append(record)

        logger.debug(
            "PRICE_FETCH received=%d/%d",
            len(received_assets),
            len(self._all_assets),
        )

        return True

    def _handle_fetch_failure(self, now_utc: float) -> None:
        """Handle a complete batch fetch failure.

        Sets global STALE flag if data is older than stale_max_age_sec.

        Args:
            now_utc: Current UTC timestamp.
        """
        if self._last_fetch_utc > 0:
            age = now_utc - self._last_fetch_utc
            if age > self.validator.stale_max_age_sec:
                if not self._global_stale:
                    self._global_stale = True
                    logger.critical(
                        "GLOBAL_STALE data age=%.0fs exceeds %ds limit. "
                        "No new trades until fresh data.",
                        age,
                        self.validator.stale_max_age_sec,
                    )
        else:
            self._global_stale = True

    def _estimate_simple_vol(self, asset: str) -> Optional[float]:
        """Estimate 1h volatility from price buffer for anomaly detection.

        Simple calculation using prices in the buffer. The full
        volatility calculation lives in the feature engine.

        Args:
            asset: Asset symbol.

        Returns:
            Standard deviation of 1-min returns over last 60 bars,
            or None if insufficient data.
        """
        buf = self.price_buffers.get(asset)
        if buf is None or len(buf) < 10:
            return None

        prices = [r["price"] for r in buf]
        n = min(60, len(prices))
        recent = prices[-n:]

        returns = []
        for i in range(1, len(recent)):
            if recent[i - 1] > 0:
                returns.append((recent[i] - recent[i - 1]) / recent[i - 1])

        if len(returns) < 5:
            return None

        return float(np.std(returns, ddof=1))

    # -------------------------------------------------------------------------
    # Balance fetching
    # -------------------------------------------------------------------------

    async def fetch_balance(self) -> dict[str, dict[str, float]]:
        """Fetch current account balance.

        Returns:
            Dict mapping currency → {"free": float, "locked": float}.
        """
        try:
            resp = await self.client.get_balance()
            wallet = resp.get("Wallet", {})

            balances: dict[str, dict[str, float]] = {}
            for currency, bal in wallet.items():
                balances[currency] = {
                    "free": float(bal.get("Free", 0)),
                    "locked": float(bal.get("Lock", 0)),
                }

            return balances

        except RoostooAPIError as e:
            logger.error("BALANCE_FETCH_FAILED: %s", e)
            return {}

    # -------------------------------------------------------------------------
    # Data accessors
    # -------------------------------------------------------------------------

    def get_latest_price(self, asset: str) -> Optional[float]:
        """Get the most recent price for an asset.

        Args:
            asset: Asset symbol.

        Returns:
            Latest price, or None if no data.
        """
        buf = self.price_buffers.get(asset)
        if buf and len(buf) > 0:
            return buf[-1]["price"]
        return None

    def get_prices_array(self, asset: str) -> np.ndarray:
        """Get price history as a numpy array.

        Args:
            asset: Asset symbol.

        Returns:
            1D numpy array of prices (oldest to newest).
            Empty array if no data.
        """
        buf = self.price_buffers.get(asset)
        if buf is None or len(buf) == 0:
            return np.array([], dtype=np.float64)
        return np.array([r["price"] for r in buf], dtype=np.float64)

    def get_volumes_array(self, asset: str) -> np.ndarray:
        """Get volume history as a numpy array.

        Args:
            asset: Asset symbol.

        Returns:
            1D numpy array of volumes (oldest to newest).
            None volumes are stored as 0.
        """
        buf = self.price_buffers.get(asset)
        if buf is None or len(buf) == 0:
            return np.array([], dtype=np.float64)
        return np.array(
            [r.get("volume") or 0.0 for r in buf], dtype=np.float64
        )

    def get_bar_count(self, asset: str) -> int:
        """Get the number of bars stored for an asset.

        Args:
            asset: Asset symbol.

        Returns:
            Number of bars in the ring buffer.
        """
        buf = self.price_buffers.get(asset)
        return len(buf) if buf else 0

    def get_asset_status(self, asset: str) -> AssetStatus:
        """Get current trading status of an asset.

        Args:
            asset: Asset symbol.

        Returns:
            AssetStatus (ACTIVE, STALE, or ANOMALY).
        """
        return self.validator.get_status(asset)

    def get_tier(self, asset: str) -> str:
        """Get tier classification for an asset.

        Args:
            asset: Asset symbol.

        Returns:
            Tier string constant.
        """
        return self._tier_map.get(asset, TIER_5_OBSCURE)

    def get_all_assets(self) -> list[str]:
        """Get all known asset symbols.

        Returns:
            Sorted list of all asset symbols.
        """
        return list(self._all_assets)

    def get_active_assets(self) -> list[str]:
        """Get all non-STALE assets.

        Returns:
            List of assets with ACTIVE or ANOMALY status.
        """
        return self.validator.get_non_stale_assets(self._all_assets)

    def get_pair_info(self, asset: str) -> dict[str, Any]:
        """Get exchange pair info (precision, min order) for an asset.

        Args:
            asset: Asset symbol.

        Returns:
            Dict with price_precision, amount_precision, min_order, can_trade.
        """
        pair_name = f"{asset}{self._pair_suffix}"
        return self._pair_info.get(pair_name, {})

    @property
    def is_data_fresh(self) -> bool:
        """Whether data is fresh enough for new trades."""
        if self._global_stale:
            return False
        if self._last_fetch_utc == 0:
            return False
        age = time.time() - self._last_fetch_utc
        return age <= self.validator.stale_max_age_sec

    @property
    def data_age_sec(self) -> float:
        """Age of the most recent data in seconds."""
        if self._last_fetch_utc == 0:
            return float("inf")
        return time.time() - self._last_fetch_utc

    @property
    def pair_suffix(self) -> str:
        """Trading pair suffix (e.g., '/USD')."""
        return self._pair_suffix

    # -------------------------------------------------------------------------
    # Heartbeat
    # -------------------------------------------------------------------------

    def write_heartbeat(self) -> None:
        """Write current UTC timestamp to the heartbeat file."""
        self._heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        self._heartbeat_path.write_text(ts)

    # -------------------------------------------------------------------------
    # Crash recovery — Parquet backup
    # -------------------------------------------------------------------------

    def save_parquet_backup(self) -> None:
        """Save all price buffers to a Parquet file for crash recovery.

        Called every hour (configured by parquet_backup_cadence_hr).
        """
        import pandas as pd

        now = time.time()
        if self._last_backup_utc > 0 and (now - self._last_backup_utc) < self._backup_interval_sec:
            return  # Not time yet

        self._parquet_path.parent.mkdir(parents=True, exist_ok=True)

        rows: list[dict[str, Any]] = []
        for asset, buf in self.price_buffers.items():
            for record in buf:
                rows.append({
                    "asset": asset,
                    "timestamp_utc": record["timestamp_utc"],
                    "price": record["price"],
                    "volume": record.get("volume"),
                })

        if not rows:
            logger.debug("No data to backup")
            return

        df = pd.DataFrame(rows)
        df.to_parquet(self._parquet_path, index=False)
        self._last_backup_utc = now
        logger.info(
            "PARQUET_BACKUP saved %d records for %d assets to %s",
            len(rows),
            len(self.price_buffers),
            self._parquet_path,
        )

    def _load_parquet_backup(self) -> bool:
        """Load price history from Parquet backup if recent enough.

        Returns:
            True if backup was loaded, False otherwise.
        """
        if not self._parquet_path.exists():
            return False

        # Check file age
        file_age = time.time() - self._parquet_path.stat().st_mtime
        max_age = 2 * 3600  # 2 hours
        if file_age > max_age:
            logger.info(
                "Parquet backup too old (%.0f min), skipping recovery",
                file_age / 60,
            )
            return False

        try:
            import pandas as pd

            df = pd.read_parquet(self._parquet_path)
            loaded_count = 0

            for asset, group in df.groupby("asset"):
                asset_str = str(asset)
                if asset_str not in self.price_buffers:
                    self.price_buffers[asset_str] = deque(maxlen=self._buffer_maxlen)

                for _, row in group.iterrows():
                    record = {
                        "timestamp_utc": row["timestamp_utc"],
                        "price": float(row["price"]),
                        "volume": float(row["volume"]) if row.get("volume") is not None else None,
                    }
                    self.price_buffers[asset_str].append(record)
                    loaded_count += 1

            logger.info(
                "PARQUET_RECOVERY loaded %d records for %d assets",
                loaded_count,
                df["asset"].nunique(),
            )
            return loaded_count > 0

        except Exception as e:
            logger.error("PARQUET_RECOVERY_FAILED: %s", e)
            return False

    # -------------------------------------------------------------------------
    # Trade logging
    # -------------------------------------------------------------------------

    def log_trade(self, trade_record: dict[str, Any]) -> None:
        """Append a trade record to the JSON lines trade log.

        Args:
            trade_record: Dict with trade details.
        """
        self._trade_log_path.parent.mkdir(parents=True, exist_ok=True)
        trade_record["logged_at_utc"] = datetime.now(timezone.utc).isoformat()

        with open(self._trade_log_path, "a") as f:
            f.write(json.dumps(trade_record, default=str) + "\n")
