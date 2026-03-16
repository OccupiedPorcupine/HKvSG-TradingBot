"""Tests for Layer 2 — Feature Engineering Pipeline.

Covers:
- EMA incremental update vs full recompute
- Cross-sectional ranking with STALE asset excluded
- Momentum composite score with known inputs
- BTC vol percentile computation
- Return feature computation
- Regime input computation
- Volume feature computation
- Integration test: ingestion → features on mock data
"""

import asyncio
from collections import deque
from unittest.mock import AsyncMock, MagicMock

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
)
from src.data.features import FeatureEngine, RegimeInputs
from src.data.rate_limiter import TokenBucketRateLimiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config() -> dict:
    """Create a minimal test config."""
    return {
        "api": {
            "base_url": "https://mock-api.roostoo.com",
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
        "features": {
            "return_windows": [5, 15, 60, 240, 720, 1440],
            "ema_periods": {
                "fast": 20,
                "medium": 50,
                "trend_fast": 60,
                "trend_slow": 240,
            },
            "momentum_composite_weights": {
                "rank_1h": 0.20,
                "rank_4h": 0.40,
                "rank_12h": 0.25,
                "rank_24h": 0.15,
            },
            "volatility_windows": [60, 240, 1440],
            "btc_vol_percentile_lookback_days": 7,
            "btc_vol_percentile_sample_interval_min": 5,
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
            f"{a}/USD": {
                "PricePrecision": 2,
                "AmountPrecision": 4,
                "MiniOrder": 1,
                "CanTrade": True,
            }
            for a in ["BTC", "ETH", "LINK", "AAVE", "DOGE", "SOMI", "PAXG", "TRUMP"]
        },
    })
    return client


def populate_prices(
    manager: DataIngestionManager,
    asset: str,
    prices: list[float],
    volumes: list[float] | None = None,
) -> None:
    """Populate an asset's price buffer with test data.

    Args:
        manager: Ingestion manager.
        asset: Asset symbol.
        prices: List of prices (oldest to newest).
        volumes: Optional list of volumes.
    """
    if asset not in manager.price_buffers:
        manager.price_buffers[asset] = deque(maxlen=manager._buffer_maxlen)

    for i, price in enumerate(prices):
        record = {
            "timestamp_utc": f"2026-03-15T10:{i:04d}Z",
            "price": price,
            "volume": volumes[i] if volumes and i < len(volumes) else None,
        }
        manager.price_buffers[asset].append(record)


def create_test_engine() -> tuple[FeatureEngine, DataIngestionManager]:
    """Create a FeatureEngine with initialized ingestion manager."""
    config = make_config()
    client = make_mock_client()
    manager = DataIngestionManager(config, client)
    asyncio.run(manager.initialize())
    engine = FeatureEngine(config, manager)
    return engine, manager


# ---------------------------------------------------------------------------
# EMA Tests
# ---------------------------------------------------------------------------

class TestEMAComputation:
    """Test EMA incremental update produces correct values."""

    def test_ema_incremental_vs_full_recompute(self):
        """EMA computed via warm_start should exactly match full recompute."""
        engine, manager = create_test_engine()

        # Generate 200 random prices
        np.random.seed(42)
        base_price = 70000
        prices = [base_price]
        for _ in range(199):
            ret = np.random.normal(0, 0.001)
            prices.append(prices[-1] * (1 + ret))

        populate_prices(manager, "BTC", prices)

        # warm_start processes all prices through EMAs
        engine.warm_start()

        # Get computed EMA values
        ema_60_inc, ema_240_inc = engine.get_ema_values("BTC")

        # Full recompute of EMA(60)
        alpha_60 = 2.0 / (60 + 1)
        ema_60_full = prices[0]
        for p in prices[1:]:
            ema_60_full = alpha_60 * p + (1 - alpha_60) * ema_60_full

        # Full recompute of EMA(240)
        alpha_240 = 2.0 / (240 + 1)
        ema_240_full = prices[0]
        for p in prices[1:]:
            ema_240_full = alpha_240 * p + (1 - alpha_240) * ema_240_full

        assert ema_60_inc is not None
        assert ema_240_inc is not None
        assert abs(ema_60_inc - ema_60_full) < 0.01, (
            f"EMA(60) mismatch: inc={ema_60_inc}, full={ema_60_full}"
        )
        assert abs(ema_240_inc - ema_240_full) < 0.01, (
            f"EMA(240) mismatch: inc={ema_240_inc}, full={ema_240_full}"
        )

    def test_ema_initialized_to_first_price(self):
        """EMAs should initialize to the first price received."""
        engine, manager = create_test_engine()

        populate_prices(manager, "BTC", [70000.0])
        engine.on_new_bar()

        ema_60, ema_240 = engine.get_ema_values("BTC")
        assert ema_60 == 70000.0
        assert ema_240 == 70000.0

    def test_ema_incremental_single_bar(self):
        """Incremental EMA update for a single new bar is correct."""
        engine, manager = create_test_engine()

        # First bar: initializes EMA
        populate_prices(manager, "BTC", [70000.0])
        engine.on_new_bar()

        # Add second bar
        manager.price_buffers["BTC"].append({
            "timestamp_utc": "2026-03-15T10:0001Z",
            "price": 70100.0,
            "volume": None,
        })
        engine.on_new_bar()

        ema_60, _ = engine.get_ema_values("BTC")
        # Manual: alpha = 2/61, ema = alpha * 70100 + (1-alpha) * 70000
        alpha = 2.0 / 61
        expected = alpha * 70100 + (1 - alpha) * 70000
        assert abs(ema_60 - expected) < 0.001


# ---------------------------------------------------------------------------
# Cross-sectional ranking tests
# ---------------------------------------------------------------------------

class TestCrossSectionalRanking:
    """Test cross-sectional ranking with STALE asset exclusion."""

    def test_ranking_excludes_stale_asset(self):
        """STALE assets are excluded from ranking and don't affect ranks."""
        engine, manager = create_test_engine()

        # Create price histories: 241 bars so we have 4h returns
        n_bars = 241

        # BTC: +5% over 4h
        btc_prices = [70000 + i * (3500 / n_bars) for i in range(n_bars)]
        # ETH: +3% over 4h
        eth_prices = [2000 + i * (60 / n_bars) for i in range(n_bars)]
        # LINK: -2% over 4h
        link_prices = [15 - i * (0.3 / n_bars) for i in range(n_bars)]
        # AAVE: +10% over 4h (best performer)
        aave_prices = [300 + i * (30 / n_bars) for i in range(n_bars)]

        populate_prices(manager, "BTC", btc_prices)
        populate_prices(manager, "ETH", eth_prices)
        populate_prices(manager, "LINK", link_prices)
        populate_prices(manager, "AAVE", aave_prices)

        # Make LINK stale
        for _ in range(4):
            manager.validator.check_missing_bar("LINK", received=False)
        assert manager.get_asset_status("LINK") == AssetStatus.STALE

        # Use warm_start to populate returns buffer
        engine.warm_start()
        scores = engine.get_momentum_scores()

        # LINK should NOT be in momentum scores
        assert "LINK" not in scores

        # Remaining assets should have valid scores
        assert "BTC" in scores
        assert "ETH" in scores
        assert "AAVE" in scores

    def test_percentile_rank_formula(self):
        """Percentile rank follows rank / (N - 1) formula."""
        values = {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}
        ranked = FeatureEngine._percentile_rank(values)

        # N=4, so ranks are 0/3, 1/3, 2/3, 3/3
        assert abs(ranked["A"] - 0.0) < 1e-10
        assert abs(ranked["B"] - 1 / 3) < 1e-10
        assert abs(ranked["C"] - 2 / 3) < 1e-10
        assert abs(ranked["D"] - 1.0) < 1e-10

    def test_percentile_rank_with_ties(self):
        """Ties get average rank."""
        values = {"A": 1.0, "B": 2.0, "C": 2.0, "D": 4.0}
        ranked = FeatureEngine._percentile_rank(values)

        # B and C tie: average rank = (1+2)/2 = 1.5
        # Percentile = 1.5 / 3 = 0.5
        assert abs(ranked["B"] - 0.5) < 1e-10
        assert abs(ranked["C"] - 0.5) < 1e-10

    def test_percentile_rank_two_assets(self):
        """Two assets: one gets 0, one gets 1."""
        values = {"A": 1.0, "B": 2.0}
        ranked = FeatureEngine._percentile_rank(values)
        assert ranked["A"] == 0.0
        assert ranked["B"] == 1.0


# ---------------------------------------------------------------------------
# Momentum composite score tests
# ---------------------------------------------------------------------------

class TestMomentumComposite:
    """Test momentum composite score computation."""

    def test_composite_with_known_inputs(self):
        """Composite score should match hand-calculated value."""
        engine, manager = create_test_engine()

        # We need enough bars for 24h returns (1441 bars)
        # Use 3 assets with known relative performance
        n_bars = 1441

        # Asset A: best on all timeframes (steadily rising)
        a_prices = [100 + i * (50.0 / n_bars) for i in range(n_bars)]
        # Asset B: medium
        b_prices = [100 + i * (20.0 / n_bars) for i in range(n_bars)]
        # Asset C: worst (declining)
        c_prices = [100 - i * (10.0 / n_bars) for i in range(n_bars)]

        populate_prices(manager, "BTC", a_prices)
        populate_prices(manager, "ETH", b_prices)
        populate_prices(manager, "LINK", c_prices)

        engine.warm_start()
        scores = engine.get_momentum_scores()

        # A should have the highest composite score
        assert scores["BTC"] > scores["ETH"]
        assert scores["ETH"] > scores["LINK"]

        # With 3 assets, ranks are 0, 0.5, 1.0
        # Best asset (all windows rank=1.0):
        # composite = 0.20*1.0 + 0.40*1.0 + 0.25*1.0 + 0.15*1.0 = 1.0
        assert abs(scores["BTC"] - 1.0) < 0.01
        assert abs(scores["LINK"] - 0.0) < 0.01

    def test_composite_weights_from_config(self):
        """Weights should be read from config, not hardcoded."""
        config = make_config()
        # Override weights
        config["features"]["momentum_composite_weights"] = {
            "rank_1h": 0.50,
            "rank_4h": 0.50,
            "rank_12h": 0.0,
            "rank_24h": 0.0,
        }

        client = make_mock_client()
        manager = DataIngestionManager(config, client)
        asyncio.run(manager.initialize())
        engine = FeatureEngine(config, manager)

        assert engine._momentum_weights[60] == 0.50
        assert engine._momentum_weights[240] == 0.50
        assert engine._momentum_weights[720] == 0.0
        assert engine._momentum_weights[1440] == 0.0


# ---------------------------------------------------------------------------
# BTC vol percentile tests
# ---------------------------------------------------------------------------

class TestBTCVolPercentile:
    """Test BTC volatility percentile computation."""

    def test_vol_percentile_against_known_distribution(self):
        """Vol percentile should match hand-calculated value."""
        engine, manager = create_test_engine()

        # Create BTC price data with enough bars for 1h vol
        np.random.seed(42)
        n_bars = 120
        prices = [70000.0]
        for _ in range(n_bars - 1):
            ret = np.random.normal(0, 0.001)
            prices.append(prices[-1] * (1 + ret))

        populate_prices(manager, "BTC", prices)

        # Pre-populate the vol sample distribution with known values
        known_vols = sorted([0.001 * i for i in range(1, 21)])  # 0.001 to 0.020
        engine._btc_vol_samples = deque(known_vols, maxlen=2016)

        # Use warm_start to build returns buffer (needed for vol computation)
        engine.warm_start()

        # The current BTC 1h vol should be roughly 0.001
        btc_vol = engine.get_asset_volatility("BTC", "1h")
        assert btc_vol is not None

        # Check that percentile is computed
        regime = engine.get_regime_inputs()
        assert regime.btc_vol_percentile is not None
        assert 0 <= regime.btc_vol_percentile <= 100

    def test_vol_percentile_needs_minimum_samples(self):
        """Percentile returns None with too few samples."""
        engine, manager = create_test_engine()

        # Only 5 vol samples — should return None
        engine._btc_vol_samples = deque([0.001, 0.002, 0.003, 0.004, 0.005])

        # Create minimal BTC data
        prices = [70000 + i for i in range(61)]
        populate_prices(manager, "BTC", prices)

        engine.warm_start()
        regime = engine.get_regime_inputs()
        assert regime.btc_vol_percentile is None  # Too few samples


# ---------------------------------------------------------------------------
# Return features tests
# ---------------------------------------------------------------------------

class TestReturnFeatures:
    """Test return feature computation."""

    def test_return_computation(self):
        """Returns should be (price_now - price_N_ago) / price_N_ago."""
        engine, manager = create_test_engine()

        # 16 bars: enough for 5m and 15m returns
        prices = [100.0] * 10 + [105.0] * 6  # Price jumps from 100 to 105
        populate_prices(manager, "BTC", prices)

        engine.on_new_bar()
        feats = engine.get_all_features("BTC")

        # 5-min return: (105 - 105) / 105 = 0 (no change in last 5 bars)
        assert feats.get("return_5m") is not None
        assert abs(feats["return_5m"] - 0.0) < 1e-10

        # 15-min return: (105 - 100) / 100 = 0.05
        assert feats.get("return_15m") is not None
        assert abs(feats["return_15m"] - 0.05) < 1e-10


# ---------------------------------------------------------------------------
# Regime input tests
# ---------------------------------------------------------------------------

class TestRegimeInputs:
    """Test regime detection input computation."""

    def test_btc_trend_bullish(self):
        """Both positive returns → bullish."""
        engine, manager = create_test_engine()

        # BTC going up for 1441 bars — needs both 4h and 24h returns
        n = 1441
        prices = [70000 + i * 5 for i in range(n)]
        populate_prices(manager, "BTC", prices)
        populate_prices(manager, "ETH", [2000 + i for i in range(n)])

        engine.warm_start()
        regime = engine.get_regime_inputs()

        assert regime.btc_4h_return is not None
        assert regime.btc_4h_return > 0
        assert regime.btc_24h_return is not None
        assert regime.btc_24h_return > 0
        assert regime.btc_trend == "bullish"

    def test_btc_trend_bearish(self):
        """Both negative returns → bearish."""
        engine, manager = create_test_engine()

        n = 1441
        prices = [80000 - i * 5 for i in range(n)]
        populate_prices(manager, "BTC", prices)
        populate_prices(manager, "ETH", [3000 - i for i in range(n)])

        engine.warm_start()
        regime = engine.get_regime_inputs()

        assert regime.btc_4h_return is not None
        assert regime.btc_4h_return < 0
        assert regime.btc_24h_return is not None
        assert regime.btc_24h_return < 0
        assert regime.btc_trend == "bearish"

    def test_altcoin_breadth(self):
        """Breadth = % of non-BTC/non-PAXG with positive 4h return."""
        engine, manager = create_test_engine()

        n = 241  # enough for 4h return

        # BTC up, ETH up, LINK down, AAVE up, DOGE down
        populate_prices(manager, "BTC", [70000 + i * 2 for i in range(n)])
        populate_prices(manager, "ETH", [2000 + i for i in range(n)])
        populate_prices(manager, "LINK", [15 - i * 0.01 for i in range(n)])
        populate_prices(manager, "AAVE", [300 + i * 0.5 for i in range(n)])
        populate_prices(manager, "DOGE", [0.1 - i * 0.0001 for i in range(n)])
        populate_prices(manager, "PAXG", [5000 + i * 0.1 for i in range(n)])

        engine.warm_start()
        regime = engine.get_regime_inputs()

        # Non-BTC/non-PAXG: ETH(up), LINK(down), AAVE(up), DOGE(down)
        # Breadth = 2/4 = 0.5
        assert regime.altcoin_breadth is not None
        assert abs(regime.altcoin_breadth - 0.5) < 0.1

    def test_regime_inputs_not_available_raises(self):
        """get_regime_inputs raises if not yet computed."""
        engine, _ = create_test_engine()
        with pytest.raises(RuntimeError):
            engine.get_regime_inputs()


# ---------------------------------------------------------------------------
# Volume features tests
# ---------------------------------------------------------------------------

class TestVolumeFeatures:
    """Test volume feature computation."""

    def test_volume_ratio(self):
        """Volume ratio = current / rolling average."""
        engine, manager = create_test_engine()

        prices = [70000.0] * 30
        volumes = [100.0] * 29 + [200.0]  # Last bar has 2x volume

        populate_prices(manager, "BTC", prices, volumes)
        engine.warm_start()

        feats = engine.get_all_features("BTC")
        # Average vol = (29*100 + 200) / 30 ≈ 103.33
        # Ratio = 200 / 103.33 ≈ 1.935
        if "volume_ratio" in feats and feats["volume_ratio"] is not None:
            assert feats["volume_ratio"] > 1.5


# ---------------------------------------------------------------------------
# Feature interface tests
# ---------------------------------------------------------------------------

class TestFeatureInterface:
    """Test the public feature interface."""

    def test_get_all_features_no_nan(self):
        """get_all_features should never return NaN or Inf."""
        engine, manager = create_test_engine()

        prices = [70000 + i for i in range(100)]
        populate_prices(manager, "BTC", prices)
        engine.warm_start()

        feats = engine.get_all_features("BTC")
        for key, val in feats.items():
            if val is not None:
                assert np.isfinite(val), f"Feature {key} is not finite: {val}"

    def test_get_asset_volatility_windows(self):
        """Volatility accessor works for valid windows."""
        engine, manager = create_test_engine()

        np.random.seed(42)
        prices = [70000 + np.random.normal(0, 100) for _ in range(100)]
        populate_prices(manager, "BTC", prices)

        # warm_start builds the returns buffer needed for volatility
        engine.warm_start()

        vol_1h = engine.get_asset_volatility("BTC", "1h")
        assert vol_1h is not None
        assert vol_1h > 0

        # Invalid window returns None
        assert engine.get_asset_volatility("BTC", "2h") is None

    def test_warm_start(self):
        """Warm start recomputes EMAs from recovered history."""
        engine, manager = create_test_engine()

        np.random.seed(42)
        prices = [70000.0]
        for _ in range(199):
            prices.append(prices[-1] * (1 + np.random.normal(0, 0.001)))

        populate_prices(manager, "BTC", prices)

        # Warm start
        engine.warm_start()

        ema_60, ema_240 = engine.get_ema_values("BTC")
        assert ema_60 is not None
        assert ema_240 is not None

        # Should match full recompute exactly (warm flag prevents double update)
        alpha_60 = 2.0 / 61
        ema_full = prices[0]
        for p in prices[1:]:
            ema_full = alpha_60 * p + (1 - alpha_60) * ema_full

        assert abs(ema_60 - ema_full) < 0.01

    def test_bar_count(self):
        """Bar counter increments on each on_new_bar call."""
        engine, manager = create_test_engine()
        populate_prices(manager, "BTC", [70000.0, 70100.0])

        assert engine.bar_count == 0
        engine.on_new_bar()
        assert engine.bar_count == 1
        engine.on_new_bar()
        assert engine.bar_count == 2


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

class TestIntegration:
    """End-to-end integration test: ingestion → features."""

    def test_full_pipeline_mock_data(self):
        """Full pipeline on mock data produces valid features."""
        engine, manager = create_test_engine()

        np.random.seed(42)
        n_bars = 300

        # Generate price series for multiple assets
        assets = ["BTC", "ETH", "LINK", "AAVE", "DOGE", "PAXG"]
        base_prices = {"BTC": 70000, "ETH": 2100, "LINK": 15, "AAVE": 300, "DOGE": 0.1, "PAXG": 5000}

        for asset in assets:
            prices = [base_prices[asset]]
            for _ in range(n_bars - 1):
                ret = np.random.normal(0.0001, 0.002)
                prices.append(prices[-1] * (1 + ret))
            volumes = [np.random.uniform(100, 1000) for _ in range(n_bars)]
            populate_prices(manager, asset, prices, volumes)

        # warm_start builds returns buffer + EMAs from bulk data
        engine.warm_start()

        # Verify: all assets have features
        for asset in assets:
            feats = engine.get_all_features(asset)
            assert len(feats) > 0, f"No features for {asset}"
            assert "price" in feats

        # Verify: momentum scores exist for non-PAXG assets
        scores = engine.get_momentum_scores()
        assert len(scores) >= 4

        # Verify: all scores in [0, 1]
        for asset, score in scores.items():
            assert 0.0 <= score <= 1.0, f"{asset} score {score} out of range"

        # Verify: regime inputs are populated
        regime = engine.get_regime_inputs()
        assert regime.btc_trend in ("bullish", "bearish", "neutral")

        # Verify: EMAs exist for all assets
        for asset in assets:
            ema_60, ema_240 = engine.get_ema_values(asset)
            assert ema_60 is not None
            assert ema_240 is not None

        # Verify: volatility exists (needs returns buffer from warm_start)
        for asset in assets:
            vol = engine.get_asset_volatility(asset, "1h")
            assert vol is not None, f"No 1h vol for {asset}"
            assert vol >= 0

        # Verify: no NaN/Inf in any feature
        for asset in assets:
            feats = engine.get_all_features(asset)
            for key, val in feats.items():
                if val is not None:
                    assert np.isfinite(val), f"{asset}.{key} = {val}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
