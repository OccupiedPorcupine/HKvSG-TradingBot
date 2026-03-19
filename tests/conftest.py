"""Shared fixtures for APEX integration tests.

Provides reusable test infrastructure: minimal config dicts matching
config.yaml structure, mock price feeds, and pre-seeded position trackers.
"""

import pytest
from datetime import datetime, timezone

from src.execution.position_tracker import PositionTracker


# ---------------------------------------------------------------------------
# minimal_config — dict matching config.yaml structure
# ---------------------------------------------------------------------------
@pytest.fixture
def minimal_config() -> dict:
    """Minimal config dict matching config.yaml structure.

    Provides all keys accessed by the production pipeline with safe
    defaults for testing. No real API credentials or URLs.
    """
    return {
        "competition": {
            "starting_capital_usd": 1_000_000,
            "competition_end_utc": "2099-01-01T00:00:00Z",
            "competition_start_utc": "2026-03-22T00:00:00Z",
        },
        "api": {
            "base_url": "https://mock-api.roostoo.com",
            "key_env_var": "ROOSTOO_API_KEY",
            "secret_env_var": "ROOSTOO_API_SECRET",
            "rate_limit_calls_per_min": 30,
        },
        "universe": {
            "tier_1_majors": ["BTC", "ETH", "BNB", "LTC", "ADA", "DOGE", "TRX"],
            "tier_2_large_alts": ["LINK", "DOT", "NEAR", "TON", "SUI"],
            "tier_3_defi": ["AAVE", "UNI", "CRV", "PENDLE", "ONDO"],
            "tier_4_meme": ["SHIB", "PEPE"],
            "tier_5_obscure": ["SOMI", "AVNT"],
            "special": {"paxg": "PAXG", "trump": "TRUMP"},
            "pair_suffix": "/USD",
        },
        "tier_caps": {
            "tier_1_2": 0.08,
            "tier_3": 0.06,
            "tier_4_meme": 0.03,
            "tier_5_obscure": 0.02,
            "doge": 0.05,
            "trump": 0.02,
            "paxg": 0.15,
            "redistribution_max_iterations": 5,
        },
        "data_ingestion": {
            "price_cadence_sec": 60,
            "ring_buffer_maxlen": 1440,
            "missing_bar_fill_forward_limit": 3,
            "price_anomaly_threshold_std": 5.0,
            "stale_data_max_age_sec": 300,
        },
        "features": {
            "return_windows": [5, 15, 60, 240, 720, 1440],
            "ema_periods": {"fast": 20, "medium": 50, "trend_fast": 60, "trend_slow": 240},
            "momentum_composite_weights": {
                "rank_1h": 0.20,
                "rank_4h": 0.40,
                "rank_12h": 0.25,
                "rank_24h": 0.15,
            },
            "volatility_windows": [60, 240, 1440],
        },
        "regime": {
            "update_cadence_sec": 300,
            "thresholds": {
                "contagion_ratio_crisis": 0.80,
                "contagion_avg_loss_pct": 0.01,
                "btc_vol_percentile_crisis": 90,
                "altcoin_breadth_bull": 0.55,
                "altcoin_breadth_bear": 0.40,
            },
            "transitions": {
                "upgrade_confirmation_bars": 30,
                "crisis_exit_contagion_below": 0.50,
                "crisis_exit_vol_below": 70,
                "crisis_exit_confirmation_bars": 30,
            },
            "contagion_return_window_min": 5,
        },
        "signals": {
            "top_n_selections": {
                "trend_bull": 10,
                "mean_revert": 6,
                "trend_bear": 4,
                "crisis": 0,
            },
            "trend_penalty_magnitude": -0.30,
        },
        "portfolio": {
            "regime_targets": {
                "trend_bull_low_vol": 0.80,
                "trend_bull_high_vol": 0.65,
                "mean_revert": 0.55,
                "trend_bear": 0.35,
                "crisis": 0.15,
            },
            "btc_vol_high_vol_threshold": 70,
            "max_crypto_exposure": 0.90,
            "max_turnover_per_rebalance": 0.25,
            "min_trade_threshold_pct_nav": 0.002,
            "rebalance_cadence_sec": 120,
        },
        "endgame": {
            "schedule": [],
            "final_sell_minutes_remaining": 15,
        },
        "risk": {
            "portfolio_limits": {
                "drawdown_soft_warning": 0.05,
                "drawdown_hard_halt": 0.08,
                "daily_loss_soft_warning": 0.03,
                "daily_loss_hard_reduce": 0.05,
                "crypto_exposure_soft_warning": 0.85,
                "crypto_exposure_hard_sell": 0.90,
                "single_asset_loss_soft": 0.04,
                "single_asset_loss_hard": 0.06,
            },
            "trailing_stops": {
                "tier_1_3": 0.06,
                "tier_4_5": 0.08,
                "trump": 0.10,
                "minimum_floor": 0.02,
            },
            "stop_tightening": {
                "threshold_high_pnl": 0.02,
                "tightening_high": 0.40,
                "threshold_medium_pnl": 0.01,
                "tightening_medium": 0.20,
            },
            "drawdown_recovery": {
                "halt_cooldown_hr": 2,
                "resume_sizing_fraction": 0.50,
                "full_sizing_within_pct_of_peak": 0.04,
            },
            "contagion": {
                "ratio_threshold": 0.80,
                "avg_loss_threshold_pct": 0.01,
                "position_reduction_to": 0.20,
            },
        },
        "execution": {
            "order_timeout_sec": 60,
            "max_resubmissions": 3,
            "risk_exit_acceleration_threshold": 0.02,
            "max_simultaneous_orders": 15,
            "fill_check_delay_sec": 5,
        },
        "orchestration": {
            "max_consecutive_failures": 3,
        },
        "logging": {
            "level": "DEBUG",
            "directory": "logs",
            "trade_log": "logs/trades.jsonl",
            "heartbeat_file": "logs/heartbeat",
        },
        "phase1_vol_guard": {
            "btc_30d_median_vol": 0.0,
            "vol_spike_multiplier": 2.0,
            "defensive_max_exposure": 0.40,
            "normal_max_exposure": 0.75,
        },
        "paths": {
            "parquet_backup": "data/price_backup.parquet",
        },
    }


# ---------------------------------------------------------------------------
# mock_price_feed — 10-asset price dict
# ---------------------------------------------------------------------------
@pytest.fixture
def mock_price_feed() -> dict[str, float]:
    """Synthetic price feed for 10 tier-1/2/3 assets.

    Prices are plausible mid-March-2026 values for integration
    testing. Not real market data.
    """
    return {
        "BTC": 87_000.00,
        "ETH": 3_200.00,
        "BNB": 580.00,
        "LTC": 95.00,
        "ADA": 0.45,
        "LINK": 18.50,
        "DOT": 7.20,
        "NEAR": 4.80,
        "AAVE": 210.00,
        "UNI": 11.50,
    }


# ---------------------------------------------------------------------------
# seeded_position_tracker — 3 open positions with known cost basis
# ---------------------------------------------------------------------------
@pytest.fixture
def seeded_position_tracker() -> PositionTracker:
    """PositionTracker with 3 open positions and known cost basis.

    Positions:
      BTC: 0.5 units @ $85,000 entry, current $87,000
      ETH: 10  units @ $3,100  entry, current $3,200
      LINK: 500 units @ $17.00  entry, current $18.50

    Starting capital: $1,000,000. Cash reduced by cost of fills.
    """
    tracker = PositionTracker(starting_capital=1_000_000)

    # BTC: buy 0.5 @ 85,000 → cost = $42,500
    tracker.on_buy_fill("BTC", quantity=0.5, price=85_000.0, commission_pct=0.0005)
    # ETH: buy 10 @ 3,100 → cost = $31,000
    tracker.on_buy_fill("ETH", quantity=10.0, price=3_100.0, commission_pct=0.0005)
    # LINK: buy 500 @ 17.00 → cost = $8,500
    tracker.on_buy_fill("LINK", quantity=500.0, price=17.00, commission_pct=0.0005)

    # Mark-to-market at current prices
    tracker.update_prices({"BTC": 87_000.0, "ETH": 3_200.0, "LINK": 18.50})

    return tracker
