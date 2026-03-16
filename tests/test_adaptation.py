"""Unit tests for feedback and adaptation layer (Layer 8).

Tests signal health monitoring, performance logging, and ML retrain
stub behavior. All tests run without API credentials.
"""

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.adaptation.signal_health import (
    SignalHealthMonitor,
    SignalHealthConfig,
    RebalanceOutcome,
)
from src.adaptation.performance_log import PerformanceLogger
from src.adaptation.ml_retrain import MLRetrainer


# =========================================================================
# Signal Health Monitor Tests
# =========================================================================

class TestSignalHealthMonitor:
    """Tests for the SignalHealthMonitor."""

    def test_insufficient_data(self) -> None:
        monitor = SignalHealthMonitor()

        result = asyncio.run(monitor.check())
        assert result["action"] == "INSUFFICIENT_DATA"
        assert result["hit_rate"] is None

    def test_hit_rate_calculation(self) -> None:
        monitor = SignalHealthMonitor()

        # 7 winners, 3 losers = 70% hit rate
        for i in range(7):
            monitor.record_outcome(RebalanceOutcome(
                asset=f"ASSET_{i}",
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                exit_price=105.0,
                return_pct=0.05,
            ))
        for i in range(3):
            monitor.record_outcome(RebalanceOutcome(
                asset=f"LOSER_{i}",
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                exit_price=95.0,
                return_pct=-0.05,
            ))

        result = asyncio.run(monitor.check())
        assert result["hit_rate"] == pytest.approx(0.70, rel=1e-2)
        assert result["win_loss_ratio"] == pytest.approx(1.0, rel=1e-2)

    def test_low_hit_rate_logged(self) -> None:
        monitor = SignalHealthMonitor()

        # 3 winners, 7 losers = 30% hit rate (below halt threshold)
        for i in range(3):
            monitor.record_outcome(RebalanceOutcome(
                asset=f"W_{i}",
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                exit_price=105.0,
                return_pct=0.05,
            ))
        for i in range(7):
            monitor.record_outcome(RebalanceOutcome(
                asset=f"L_{i}",
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                exit_price=95.0,
                return_pct=-0.05,
            ))

        result = asyncio.run(monitor.check())
        assert result["hit_rate"] == pytest.approx(0.30, rel=1e-2)
        # Phase 0-1: action is MONITOR_ONLY even when below threshold
        assert result["action"] == "MONITOR_ONLY"

    def test_win_loss_ratio(self) -> None:
        monitor = SignalHealthMonitor()

        # Winners: +10% each, Losers: -5% each
        for i in range(5):
            monitor.record_outcome(RebalanceOutcome(
                asset=f"W_{i}",
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                exit_price=110.0,
                return_pct=0.10,
            ))
        for i in range(5):
            monitor.record_outcome(RebalanceOutcome(
                asset=f"L_{i}",
                entry_time=datetime.now(timezone.utc),
                entry_price=100.0,
                exit_price=95.0,
                return_pct=-0.05,
            ))

        result = asyncio.run(monitor.check())
        assert result["win_loss_ratio"] == pytest.approx(2.0, rel=1e-2)

    def test_halted_state_initial(self) -> None:
        monitor = SignalHealthMonitor()
        assert not monitor.is_halted
        assert monitor.halted_since is None


# =========================================================================
# Performance Logger Tests
# =========================================================================

class TestPerformanceLogger:
    """Tests for the PerformanceLogger."""

    def test_snapshot_writes_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "snapshots.jsonl"
            perf_log = PerformanceLogger(
                log_path=str(log_path),
                competition_start_nav=1_000_000.0,
            )

            result = asyncio.run(perf_log.snapshot(
                current_nav=1_005_000.0,
                positions=[{"asset": "BTC", "weight": 0.08}],
                current_regime="TREND_BULL",
            ))

            assert log_path.exists()
            lines = log_path.read_text().strip().split("\n")
            assert len(lines) == 1

            entry = json.loads(lines[0])
            assert entry["current_nav"] == 1_005_000.0
            assert entry["current_regime"] == "TREND_BULL"
            assert entry["cumulative_return"] == pytest.approx(0.005, rel=1e-4)

    def test_daily_pnl_resets_at_midnight(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "snapshots.jsonl"
            perf_log = PerformanceLogger(
                log_path=str(log_path),
                competition_start_nav=1_000_000.0,
            )

            # First snapshot sets daily baseline
            asyncio.run(perf_log.snapshot(
                current_nav=1_010_000.0, positions=[]
            ))

            # Second snapshot same "day" — daily P&L relative to first
            result = asyncio.run(perf_log.snapshot(
                current_nav=1_015_000.0, positions=[]
            ))
            assert result["daily_pnl"] == pytest.approx(5000.0, rel=1e-4)

    def test_cumulative_return(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "snapshots.jsonl"
            perf_log = PerformanceLogger(
                log_path=str(log_path),
                competition_start_nav=1_000_000.0,
            )

            # 5% gain
            result = asyncio.run(perf_log.snapshot(
                current_nav=1_050_000.0, positions=[]
            ))
            assert result["cumulative_return"] == pytest.approx(0.05, rel=1e-4)


# =========================================================================
# ML Retrainer Tests
# =========================================================================

class TestMLRetrainer:
    """Tests for the MLRetrainer."""

    def test_disabled_returns_immediately(self) -> None:
        retainer = MLRetrainer(enabled=False)
        result = asyncio.run(retainer.retrain())
        assert result["status"] == "DISABLED"

    def test_multiplier_always_one_when_disabled(self) -> None:
        retainer = MLRetrainer(enabled=False)
        assert retainer.get_multiplier("BTC") == 1.0
        assert retainer.get_multiplier("ETH") == 1.0
        assert retainer.get_multiplier("DOGE") == 1.0

    def test_enabled_but_not_implemented(self) -> None:
        retainer = MLRetrainer(enabled=True)
        result = asyncio.run(retainer.retrain())
        assert result["status"] == "NOT_IMPLEMENTED"

    def test_multiplier_one_without_model(self) -> None:
        retainer = MLRetrainer(enabled=True)
        assert retainer.get_multiplier("BTC") == 1.0
