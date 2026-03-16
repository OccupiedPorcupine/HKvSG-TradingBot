"""Tests for Layer 1 — Data Ingestion Pipeline.

Covers:
- Missing bar fill-forward logic (3 bars fill, 4th → STALE)
- Price anomaly detection
- Asset status transitions
- Price buffer storage and retrieval
- Parquet backup/restore
"""

import asyncio
import time
from collections import deque
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.validators import AssetStatus, DataValidator
from src.data.ingestion import (
    DataIngestionManager,
    TIER_MAIN_POOL,
    TIER_4_MEME,
    TIER_5_OBSCURE,
    TIER_SPECIAL_PAXG,
    TIER_SPECIAL_TRUMP,
)
from src.data.rate_limiter import TokenBucketRateLimiter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_config() -> dict:
    """Create a minimal test config."""
    return {
        "api": {
            "base_url": "https://mock-api.roostoo.com",
            "rate_limit_calls_per_min": 30,
            "rate_limit_reserve_headroom": 10,
            "volume_data_available": True,
        },
        "universe": {
            "tier_1_majors": ["BTC", "ETH"],
            "tier_2_large_alts": ["LINK"],
            "tier_3_defi": ["AAVE"],
            "tier_4_meme": ["DOGE"],
            "tier_5_obscure": ["SOMI"],
            "special": {"paxg": "PAXG", "trump": "TRUMP"},
            "pair_suffix": "/USD",
        },
        "data_ingestion": {
            "ring_buffer_maxlen": 1440,
            "missing_bar_fill_forward_limit": 3,
            "price_anomaly_threshold_std": 5.0,
            "stale_data_max_age_sec": 300,
        },
        "logging": {
            "heartbeat_file": "/tmp/apex_test_heartbeat",
            "trade_log": "/tmp/apex_test_trades.jsonl",
        },
        "paths": {
            "parquet_backup": "/tmp/apex_test_backup.parquet",
        },
        "adaptation": {
            "parquet_backup_cadence_hr": 1,
        },
    }


def make_mock_client() -> MagicMock:
    """Create a mock RoostooClient."""
    client = MagicMock()
    client.get_exchange_info = AsyncMock(return_value={
        "IsRunning": True,
        "InitialWallet": {"USD": 1000000},
        "TradePairs": {
            "BTC/USD": {"PricePrecision": 2, "AmountPrecision": 6, "MiniOrder": 10, "CanTrade": True},
            "ETH/USD": {"PricePrecision": 2, "AmountPrecision": 4, "MiniOrder": 10, "CanTrade": True},
            "LINK/USD": {"PricePrecision": 4, "AmountPrecision": 2, "MiniOrder": 1, "CanTrade": True},
            "AAVE/USD": {"PricePrecision": 2, "AmountPrecision": 4, "MiniOrder": 1, "CanTrade": True},
            "DOGE/USD": {"PricePrecision": 5, "AmountPrecision": 0, "MiniOrder": 1, "CanTrade": True},
            "SOMI/USD": {"PricePrecision": 6, "AmountPrecision": 0, "MiniOrder": 1, "CanTrade": True},
            "PAXG/USD": {"PricePrecision": 2, "AmountPrecision": 4, "MiniOrder": 1, "CanTrade": True},
            "TRUMP/USD": {"PricePrecision": 2, "AmountPrecision": 2, "MiniOrder": 1, "CanTrade": True},
        },
    })
    return client


# ---------------------------------------------------------------------------
# DataValidator tests
# ---------------------------------------------------------------------------

class TestDataValidator:
    """Tests for the DataValidator class."""

    def test_fill_forward_within_limit(self):
        """Missing bars within limit (<=3) should fill forward, stay ACTIVE."""
        v = DataValidator(fill_forward_limit=3)
        v.register_asset("BTC")

        # 1 missing bar
        status = v.check_missing_bar("BTC", received=False)
        assert status == AssetStatus.ACTIVE
        assert v.get_missing_count("BTC") == 1

        # 2 missing bars
        status = v.check_missing_bar("BTC", received=False)
        assert status == AssetStatus.ACTIVE
        assert v.get_missing_count("BTC") == 2

        # 3 missing bars — still within limit
        status = v.check_missing_bar("BTC", received=False)
        assert status == AssetStatus.ACTIVE
        assert v.get_missing_count("BTC") == 3

    def test_stale_on_4th_missing_bar(self):
        """4th consecutive missing bar should trigger STALE status."""
        v = DataValidator(fill_forward_limit=3)
        v.register_asset("BTC")

        for _ in range(3):
            v.check_missing_bar("BTC", received=False)

        # 4th miss → STALE
        status = v.check_missing_bar("BTC", received=False)
        assert status == AssetStatus.STALE
        assert v.get_status("BTC") == AssetStatus.STALE

    def test_stale_recovers_on_new_data(self):
        """STALE asset becomes ACTIVE when fresh data arrives."""
        v = DataValidator(fill_forward_limit=3)
        v.register_asset("BTC")

        # Go STALE
        for _ in range(4):
            v.check_missing_bar("BTC", received=False)
        assert v.get_status("BTC") == AssetStatus.STALE

        # Receive data → ACTIVE
        status = v.check_missing_bar("BTC", received=True)
        assert status == AssetStatus.ACTIVE
        assert v.get_missing_count("BTC") == 0

    def test_missing_bar_counter_resets(self):
        """Receiving data resets the missing bar counter."""
        v = DataValidator(fill_forward_limit=3)
        v.register_asset("BTC")

        v.check_missing_bar("BTC", received=False)
        v.check_missing_bar("BTC", received=False)
        assert v.get_missing_count("BTC") == 2

        # Receive data — counter resets
        v.check_missing_bar("BTC", received=True)
        assert v.get_missing_count("BTC") == 0

        # Miss again — starts from 1
        v.check_missing_bar("BTC", received=False)
        assert v.get_missing_count("BTC") == 1

    def test_anomaly_detection(self):
        """Price anomaly flagged when return > 5σ of 1h vol."""
        v = DataValidator(anomaly_threshold_std=5.0)
        v.register_asset("BTC")

        # Normal move: 1% return with 0.5% vol → 2σ, not anomalous
        assert v.check_anomaly("BTC", 101.0, 100.0, 0.005) is False

        # Anomalous move: 10% return with 0.5% vol → 20σ
        assert v.check_anomaly("BTC", 110.0, 100.0, 0.005) is True
        assert v.get_status("BTC") == AssetStatus.ANOMALY
        assert v.is_anomaly_flagged("BTC") is True

    def test_anomaly_confirmed_next_bar(self):
        """Next bar after anomaly flag confirms the move and clears flag."""
        v = DataValidator(anomaly_threshold_std=5.0)
        v.register_asset("BTC")

        # Flag anomaly
        v.check_anomaly("BTC", 110.0, 100.0, 0.005)
        assert v.is_anomaly_flagged("BTC") is True

        # Next bar confirms — clears flag
        result = v.check_anomaly("BTC", 111.0, 110.0, 0.005)
        assert result is False
        assert v.is_anomaly_flagged("BTC") is False
        assert v.get_status("BTC") == AssetStatus.ACTIVE

    def test_anomaly_skipped_without_vol(self):
        """Anomaly check skipped when vol is None or zero."""
        v = DataValidator(anomaly_threshold_std=5.0)
        v.register_asset("BTC")

        assert v.check_anomaly("BTC", 200.0, 100.0, None) is False
        assert v.check_anomaly("BTC", 200.0, 100.0, 0.0) is False

    def test_get_active_assets(self):
        """Filter active assets correctly."""
        v = DataValidator(fill_forward_limit=3)
        all_assets = ["BTC", "ETH", "DOGE"]
        for a in all_assets:
            v.register_asset(a)

        # Make ETH stale
        for _ in range(4):
            v.check_missing_bar("ETH", received=False)

        active = v.get_active_assets(all_assets)
        assert "BTC" in active
        assert "DOGE" in active
        assert "ETH" not in active

    def test_get_non_stale_includes_anomaly(self):
        """Non-stale filter includes ANOMALY assets (they're temporary)."""
        v = DataValidator(fill_forward_limit=3, anomaly_threshold_std=5.0)
        all_assets = ["BTC", "ETH"]
        for a in all_assets:
            v.register_asset(a)

        # Flag BTC as anomaly
        v.check_anomaly("BTC", 200.0, 100.0, 0.005)

        non_stale = v.get_non_stale_assets(all_assets)
        assert "BTC" in non_stale  # anomaly but not stale


# ---------------------------------------------------------------------------
# DataIngestionManager tests
# ---------------------------------------------------------------------------

class TestDataIngestionManager:
    """Tests for the DataIngestionManager class."""

    @pytest.fixture
    def manager(self):
        """Create an initialized ingestion manager with mock client."""
        config = make_config()
        client = make_mock_client()
        mgr = DataIngestionManager(config, client)
        asyncio.run(mgr.initialize())
        return mgr

    def test_universe_discovery(self, manager):
        """Exchange info builds correct tier map."""
        assert "BTC" in manager.get_all_assets()
        assert "ETH" in manager.get_all_assets()
        assert "DOGE" in manager.get_all_assets()
        assert "PAXG" in manager.get_all_assets()

        assert manager.get_tier("BTC") == TIER_MAIN_POOL
        assert manager.get_tier("DOGE") == TIER_4_MEME
        assert manager.get_tier("SOMI") == TIER_5_OBSCURE
        assert manager.get_tier("PAXG") == TIER_SPECIAL_PAXG
        assert manager.get_tier("TRUMP") == TIER_SPECIAL_TRUMP

    def test_price_storage(self, manager):
        """Prices are stored in deque ring buffers."""
        # Manually add a price
        manager.price_buffers["BTC"].append({
            "timestamp_utc": "2026-03-15T10:00:00Z",
            "price": 70000.0,
            "volume": 100.0,
        })

        assert manager.get_latest_price("BTC") == 70000.0
        assert manager.get_bar_count("BTC") == 1

        prices = manager.get_prices_array("BTC")
        assert len(prices) == 1
        assert prices[0] == 70000.0

    def test_prices_array_empty(self, manager):
        """Empty buffer returns empty numpy array."""
        prices = manager.get_prices_array("BTC")
        assert len(prices) == 0

    def test_data_freshness(self, manager):
        """Data freshness tracking works."""
        assert manager.is_data_fresh is False  # No fetches yet

        manager._last_fetch_utc = time.time()
        manager._global_stale = False
        assert manager.is_data_fresh is True

        manager._last_fetch_utc = time.time() - 600  # 10 min old
        assert manager.is_data_fresh is False

    def test_pair_info(self, manager):
        """Exchange pair info is accessible."""
        info = manager.get_pair_info("BTC")
        assert info["price_precision"] == 2
        assert info["amount_precision"] == 6
        assert info["min_order"] == 10

    def test_extra_asset_discovery(self, manager):
        """Extra assets from exchange are classified as Tier 5."""
        # The mock client returns exactly the configured assets,
        # so let's test the dynamic discovery during fetch
        manager.client.get_all_tickers = AsyncMock(return_value={
            "Data": {
                "BTC/USD": {"LastPrice": 70000, "CoinTradeValue": 100},
                "NEW_COIN/USD": {"LastPrice": 1.5, "CoinTradeValue": 50},
            }
        })

        asyncio.run(manager.fetch_prices())
        assert "NEW_COIN" in manager.get_all_assets()
        assert manager.get_tier("NEW_COIN") == TIER_5_OBSCURE

    def test_fetch_failure_sets_global_stale(self, manager):
        """Total fetch failure sets global stale after timeout."""
        from src.data.api_client import RoostooAPIError

        manager.client.get_all_tickers = AsyncMock(
            side_effect=RoostooAPIError("Connection failed")
        )

        # Set last fetch to 10 minutes ago
        manager._last_fetch_utc = time.time() - 600

        asyncio.run(manager.fetch_prices())
        assert manager._global_stale is True
        assert manager.is_data_fresh is False

    def test_fill_forward_on_missing_asset(self, manager):
        """Assets missing from batch response are filled forward."""
        # Give BTC some history
        manager.price_buffers["BTC"].append({
            "timestamp_utc": "2026-03-15T10:00:00Z",
            "price": 70000.0,
            "volume": 100.0,
        })

        # Fetch returns only ETH, not BTC
        manager.client.get_all_tickers = AsyncMock(return_value={
            "Data": {
                "ETH/USD": {"LastPrice": 2100, "CoinTradeValue": 50},
            }
        })

        asyncio.run(manager.fetch_prices())

        # BTC should have been filled forward
        assert manager.get_bar_count("BTC") == 2
        assert manager.get_latest_price("BTC") == 70000.0  # Fill-forwarded

    def test_heartbeat_write(self, manager):
        """Heartbeat file is written."""
        manager.write_heartbeat()
        path = Path(manager._heartbeat_path)
        assert path.exists()
        content = path.read_text()
        # Should be an ISO timestamp
        assert "2026" in content or "T" in content

    def test_volumes_array(self, manager):
        """Volume data extracted correctly."""
        manager.price_buffers["BTC"].append({
            "timestamp_utc": "2026-03-15T10:00:00Z",
            "price": 70000.0,
            "volume": 150.5,
        })
        manager.price_buffers["BTC"].append({
            "timestamp_utc": "2026-03-15T10:01:00Z",
            "price": 70100.0,
            "volume": None,
        })

        vols = manager.get_volumes_array("BTC")
        assert len(vols) == 2
        assert vols[0] == 150.5
        assert vols[1] == 0.0  # None → 0.0


# ---------------------------------------------------------------------------
# Rate limiter tests
# ---------------------------------------------------------------------------

class TestRateLimiter:
    """Tests for the TokenBucketRateLimiter."""

    def test_initial_state(self):
        rl = TokenBucketRateLimiter(calls_per_minute=30, reserve_headroom=10)
        assert rl.calls_per_minute == 30
        assert rl.available_data_budget == 20
        assert rl.safe_mode is False
        assert rl.consecutive_429s == 0

    def test_backoff_exponential(self):
        rl = TokenBucketRateLimiter()
        rl.consecutive_429s = 1
        assert rl.get_backoff_delay() == 1.0
        rl.consecutive_429s = 2
        assert rl.get_backoff_delay() == 2.0
        rl.consecutive_429s = 3
        assert rl.get_backoff_delay() == 4.0
        rl.consecutive_429s = 10
        assert rl.get_backoff_delay() == 30.0  # capped

    def test_safe_mode_activation(self):
        rl = TokenBucketRateLimiter(safe_mode_threshold=3)
        rl.record_429()
        rl.record_429()
        assert rl.safe_mode is False
        rl.record_429()
        assert rl.safe_mode is True

    def test_success_resets_429_counter(self):
        rl = TokenBucketRateLimiter()
        rl.record_429()
        rl.record_429()
        assert rl.consecutive_429s == 2
        rl.record_success()
        assert rl.consecutive_429s == 0

    def test_update_rate_limit(self):
        rl = TokenBucketRateLimiter(calls_per_minute=30)
        rl.update_rate_limit(60)
        assert rl.calls_per_minute == 60
        assert rl.refill_rate == 1.0  # 60/60


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
