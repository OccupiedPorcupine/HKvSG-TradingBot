"""Unit tests for feedback and adaptation layer (Layer 8).

Tests signal health monitoring, performance logging, and ML retrain
stub behavior. All tests run without API credentials.
"""

import asyncio
import json
import math
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.adaptation.signal_health import (
    SignalHealthMonitor,
    SignalHealthConfig,
    RebalanceOutcome,
)
from src.adaptation.performance_log import (
    PerformanceLogger,
    compute_sharpe_ratio,
    compute_sortino_ratio,
    compute_calmar_ratio,
    compute_max_drawdown_pct,
)
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

    # -------------------------------------------------------------------------
    # Phase 1 record_close / snapshot / is_signal_healthy tests
    # -------------------------------------------------------------------------

    def test_snapshot_empty_window(self) -> None:
        """Empty rolling window → hit_rate 0.0, win_loss_ratio None."""
        monitor = SignalHealthMonitor()
        result = monitor.snapshot()
        assert result["hit_rate"] == 0.0
        assert result["win_loss_ratio"] is None
        assert result["trades_in_window"] == 0
        assert result["window_hours"] == 24

    def test_snapshot_all_winners(self) -> None:
        """All winning trades → hit_rate 1.0."""
        monitor = SignalHealthMonitor()
        now = datetime.now(timezone.utc)
        for i in range(5):
            monitor.record_close(f"BTC_{i}", net_pnl=100.0 + i, timestamp=now)
        result = monitor.snapshot()
        assert result["hit_rate"] == 1.0
        assert result["trades_in_window"] == 5
        # No losers → win_loss_ratio is None
        assert result["win_loss_ratio"] is None

    def test_snapshot_mixed_trades(self) -> None:
        """Mixed trades → correct hit_rate and win_loss_ratio to 2 d.p."""
        monitor = SignalHealthMonitor()
        now = datetime.now(timezone.utc)
        # 6 winners at +$200 each, 4 losers at -$100 each
        for _ in range(6):
            monitor.record_close("BTC", net_pnl=200.0, timestamp=now)
        for _ in range(4):
            monitor.record_close("ETH", net_pnl=-100.0, timestamp=now)
        result = monitor.snapshot()
        assert result["trades_in_window"] == 10
        assert round(result["hit_rate"], 2) == 0.60
        # win_loss_ratio = mean winner abs_pnl / mean loser abs_pnl
        # = 200.0 / 100.0 = 2.0
        assert result["win_loss_ratio"] is not None
        assert round(result["win_loss_ratio"], 2) == 2.00

    def test_snapshot_excludes_old_entries(self) -> None:
        """Entries older than 24 h are excluded from all metrics."""
        monitor = SignalHealthMonitor()
        now = datetime.now(timezone.utc)
        stale = now - timedelta(hours=25)

        # Two stale losers — should be pruned and not affect metrics
        monitor.record_close("BTC", net_pnl=-500.0, timestamp=stale)
        monitor.record_close("ETH", net_pnl=-300.0, timestamp=stale)

        # One recent winner — triggers pruning of stale entries on append
        monitor.record_close("SOL", net_pnl=50.0, timestamp=now)

        result = monitor.snapshot()
        assert result["trades_in_window"] == 1
        assert result["hit_rate"] == 1.0
        assert result["win_loss_ratio"] is None  # no losers in window


# =========================================================================
# Ratio Function Unit Tests
# =========================================================================


class TestComputeSharpeRatio:
    """Unit tests for compute_sharpe_ratio()."""

    def test_returns_none_with_zero_returns(self) -> None:
        """0 data points → None (standard deviation undefined)."""
        assert compute_sharpe_ratio([]) is None

    def test_returns_none_with_one_return(self) -> None:
        """1 data point → None (sample std requires n ≥ 2)."""
        assert compute_sharpe_ratio([0.05]) is None

    def test_known_five_return_series(self) -> None:
        """Verify against hand-computed value for a 5-element series.

        Series: [+1%, -2%, +3%, -1%, +2%]

        Hand computation:
          mean = (0.01 - 0.02 + 0.03 - 0.01 + 0.02) / 5 = 0.006
          deviations = [0.004, -0.026, 0.024, -0.016, 0.014]
          Σdev² = 0.000016 + 0.000676 + 0.000576 + 0.000256 + 0.000196
                = 0.001720
          sample var = 0.001720 / 4 = 0.000430
          sample std = √0.000430
          Sharpe    = 0.006 × √365 / √0.000430
        """
        returns = [0.01, -0.02, 0.03, -0.01, 0.02]
        n = len(returns)
        mean = sum(returns) / n
        variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
        std = math.sqrt(variance)
        expected = mean * math.sqrt(365) / std

        result = compute_sharpe_ratio(returns)
        assert result is not None
        assert result == pytest.approx(expected, rel=1e-6)

    def test_returns_none_for_zero_variance(self) -> None:
        """Constant return series → std=0 → Sharpe undefined → None."""
        assert compute_sharpe_ratio([0.01, 0.01, 0.01]) is None

    def test_all_positive_returns(self) -> None:
        """All positive returns → positive Sharpe."""
        result = compute_sharpe_ratio([0.01, 0.02, 0.015, 0.01, 0.02])
        assert result is not None
        assert result > 0


class TestComputeSortinoRatio:
    """Unit tests for compute_sortino_ratio()."""

    def test_returns_none_with_zero_returns(self) -> None:
        """0 data points → None."""
        assert compute_sortino_ratio([]) is None

    def test_returns_none_with_one_return(self) -> None:
        """1 data point → None."""
        assert compute_sortino_ratio([0.05]) is None

    def test_returns_none_when_no_negative_returns(self) -> None:
        """No negative returns → downside deviation = 0 → None."""
        assert compute_sortino_ratio([0.01, 0.02, 0.03]) is None

    def test_positive_with_mixed_returns(self) -> None:
        """Mixed returns with a positive mean → positive Sortino."""
        returns = [0.03, -0.01, 0.02, -0.005, 0.04]
        result = compute_sortino_ratio(returns)
        assert result is not None
        assert result > 0

    def test_sortino_exceeds_sharpe_when_upside_skew(self) -> None:
        """With upside-skewed returns, Sortino > Sharpe (less penalized by gains)."""
        returns = [0.10, 0.08, 0.06, -0.01, -0.02]
        sharpe = compute_sharpe_ratio(returns)
        sortino = compute_sortino_ratio(returns)
        assert sharpe is not None and sortino is not None
        assert sortino > sharpe


class TestComputeCalmarRatio:
    """Unit tests for compute_calmar_ratio()."""

    def test_returns_none_with_zero_returns(self) -> None:
        """0 daily returns → None (fewer than 2)."""
        assert compute_calmar_ratio([], []) is None

    def test_returns_none_with_one_return(self) -> None:
        """1 daily return → None (fewer than 2)."""
        assert compute_calmar_ratio([0.01], [1_000_000, 1_010_000]) is None

    def test_returns_none_when_no_drawdown(self) -> None:
        """Monotonically rising NAV → max drawdown = 0 → None."""
        navs = [1_000_000, 1_100_000, 1_200_000, 1_300_000]
        returns = [0.10, 0.0909, 0.0833]  # consecutive up days
        assert compute_calmar_ratio(returns, navs) is None

    def test_uses_max_peak_to_trough_not_final_drawdown(self) -> None:
        """Calmar must use the largest historical drawdown, not the terminal one.

        NAV path: 1.0M → 1.1M → 1.05M → 1.2M → 1.15M

          Peak-to-trough drawdowns observed:
            after 1.05M: (1.1 - 1.05) / 1.1 = 4.545...%   ← MAX
            after 1.15M: (1.2 - 1.15) / 1.2 = 4.166...%   ← final (smaller)

          If the implementation mistakenly uses the final drawdown it will
          produce a different (larger) Calmar ratio than the correct one.
        """
        navs = [1_000_000, 1_100_000, 1_050_000, 1_200_000, 1_150_000]
        returns = [
            (1_100_000 - 1_000_000) / 1_000_000,   # +10.000%
            (1_050_000 - 1_100_000) / 1_100_000,   # -4.545...%
            (1_200_000 - 1_050_000) / 1_050_000,   # +14.285...%
            (1_150_000 - 1_200_000) / 1_200_000,   # -4.166...%
        ]

        max_dd = compute_max_drawdown_pct(navs)
        final_dd = (1_200_000 - 1_150_000) / 1_200_000 * 100.0

        # Confirm the test data has the property we need.
        assert max_dd > final_dd, "Test data does not exhibit expected dd structure"

        mean_r = sum(returns) / len(returns)
        expected_calmar = mean_r * 365 * 100.0 / max_dd
        wrong_calmar    = mean_r * 365 * 100.0 / final_dd

        result = compute_calmar_ratio(returns, navs)
        assert result is not None
        assert result == pytest.approx(expected_calmar, rel=1e-6)
        assert result != pytest.approx(wrong_calmar, rel=1e-6)


class TestComputeMaxDrawdownPct:
    """Unit tests for compute_max_drawdown_pct()."""

    def test_empty_series(self) -> None:
        assert compute_max_drawdown_pct([]) == 0.0

    def test_singleton_series(self) -> None:
        assert compute_max_drawdown_pct([1_000_000]) == 0.0

    def test_monotonically_rising(self) -> None:
        assert compute_max_drawdown_pct([100, 110, 120, 130]) == 0.0

    def test_single_trough(self) -> None:
        # Drops 10% from 1.0M to 0.9M.
        result = compute_max_drawdown_pct([1_000_000, 900_000, 950_000])
        assert result == pytest.approx(10.0, rel=1e-6)

    def test_v_shape_recovery(self) -> None:
        # Falls 20% then fully recovers — max dd still 20%.
        navs = [1_000_000, 800_000, 1_000_000, 1_100_000]
        assert compute_max_drawdown_pct(navs) == pytest.approx(20.0, rel=1e-6)


# =========================================================================
# PerformanceLogger Integration Tests
# =========================================================================


class TestPerformanceLogger:
    """Integration tests for PerformanceLogger.

    Every test uses _make_logger() which routes BOTH log files into the
    provided temp directory so no test ever touches real on-disk logs.
    """

    @staticmethod
    def _make_logger(
        tmpdir: str,
        competition_start_nav: float = 1_000_000.0,
    ) -> PerformanceLogger:
        """Create a fully isolated PerformanceLogger backed by tmpdir."""
        return PerformanceLogger(
            log_path=str(Path(tmpdir) / "snapshots.jsonl"),
            daily_returns_path=str(Path(tmpdir) / "daily_returns.jsonl"),
            competition_start_nav=competition_start_nav,
        )

    # ------------------------------------------------------------------
    # File-system safety
    # ------------------------------------------------------------------

    def test_missing_logs_directory_created_on_init(self) -> None:
        """Constructor creates logs/ without raising — even if it doesn't exist.

        Guards audit Section 4-d: 'logs/ directory does not exist on first run'.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Point both paths into a sub-directory that does not yet exist.
            nested = Path(tmpdir) / "new_run" / "logs"
            assert not nested.exists()

            PerformanceLogger(
                log_path=str(nested / "snapshots.jsonl"),
                daily_returns_path=str(nested / "daily_returns.jsonl"),
            )

            assert nested.exists()

    def test_snapshot_creates_jsonl_file(self) -> None:
        """First snapshot creates the JSONL file and writes one valid line."""
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_log = self._make_logger(tmpdir)
            asyncio.run(perf_log.snapshot(
                nav=1_005_000.0,
                positions=[{
                    "symbol": "BTC",
                    "weight_pct": 8.0,
                    "unrealised_pnl_pct": 2.5,
                    "stop_distance_pct": 4.0,
                }],
                regime="TREND_BULL",
                signal_health={"hit_rate": 0.70},
            ))

            log_path = Path(tmpdir) / "snapshots.jsonl"
            assert log_path.exists()
            lines = log_path.read_text().strip().split("\n")
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["nav"] == 1_005_000.0
            assert entry["regime"] == "TREND_BULL"
            assert "timestamp_utc" in entry

    def test_snapshot_required_fields_present(self) -> None:
        """All Phase 1 spec fields are present in the snapshot dict."""
        required = {
            "timestamp_utc", "nav", "nav_peak", "daily_pnl_pct",
            "drawdown_from_peak_pct", "regime", "positions",
            "sharpe_ratio", "sortino_ratio", "calmar_ratio",
            "signal_health", "btc_beta",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_log = self._make_logger(tmpdir)
            entry = asyncio.run(perf_log.snapshot(
                nav=1_000_000.0, positions=[], regime="UNKNOWN", signal_health={}
            ))
            assert required.issubset(entry.keys())

    # ------------------------------------------------------------------
    # NAV peak tracking
    # ------------------------------------------------------------------

    def test_nav_peak_rises_with_nav(self) -> None:
        """nav_peak tracks the running maximum across multiple snapshots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_log = self._make_logger(tmpdir)
            asyncio.run(perf_log.snapshot(
                nav=1_050_000.0, positions=[], regime="X", signal_health={}
            ))
            asyncio.run(perf_log.snapshot(
                nav=1_030_000.0, positions=[], regime="X", signal_health={}
            ))
            entry = asyncio.run(perf_log.snapshot(
                nav=1_020_000.0, positions=[], regime="X", signal_health={}
            ))
            assert entry["nav_peak"] == 1_050_000.0

    def test_drawdown_from_peak_correct(self) -> None:
        """drawdown_from_peak_pct = (peak - nav) / peak × 100."""
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_log = self._make_logger(tmpdir)
            asyncio.run(perf_log.snapshot(
                nav=1_100_000.0, positions=[], regime="X", signal_health={}
            ))
            entry = asyncio.run(perf_log.snapshot(
                nav=1_045_000.0, positions=[], regime="X", signal_health={}
            ))
            expected_dd = (1_100_000.0 - 1_045_000.0) / 1_100_000.0 * 100.0
            assert entry["drawdown_from_peak_pct"] == pytest.approx(
                expected_dd, rel=1e-4
            )

    def test_drawdown_zero_when_nav_peak_is_zero(self) -> None:
        """Guard: nav_peak == 0 → drawdown returns 0.0, not ZeroDivisionError.

        Guards audit Section 4-b principle applied to the drawdown formula.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_log = self._make_logger(tmpdir, competition_start_nav=0.0)
            perf_log.nav_peak = 0.0  # force peak to zero
            entry = asyncio.run(perf_log.snapshot(
                nav=0.0, positions=[], regime="X", signal_health={}
            ))
            assert entry["drawdown_from_peak_pct"] == 0.0

    # ------------------------------------------------------------------
    # Daily P&L
    # ------------------------------------------------------------------

    def test_daily_pnl_pct_within_same_day(self) -> None:
        """daily_pnl_pct is relative to the day's opening NAV (competition_start_nav
        on day 1, because no midnight checkpoint has been written yet)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_log = self._make_logger(tmpdir, competition_start_nav=1_000_000.0)
            # First snapshot — _nav_at_midnight stays at 1_000_000 (competition start).
            asyncio.run(perf_log.snapshot(
                nav=1_010_000.0, positions=[], regime="X", signal_health={}
            ))
            # Second snapshot (same day): (1_020_000 - 1_000_000) / 1_000_000 × 100 = 2.0%
            entry = asyncio.run(perf_log.snapshot(
                nav=1_020_000.0, positions=[], regime="X", signal_health={}
            ))
            assert entry["daily_pnl_pct"] == pytest.approx(2.0, rel=1e-4)

    # ------------------------------------------------------------------
    # Ratio None with insufficient data
    # ------------------------------------------------------------------

    def test_ratios_none_with_zero_daily_returns(self) -> None:
        """Sharpe, Sortino, Calmar are None when no daily returns recorded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_log = self._make_logger(tmpdir)
            entry = asyncio.run(perf_log.snapshot(
                nav=1_000_000.0, positions=[], regime="X", signal_health={}
            ))
            assert entry["sharpe_ratio"] is None
            assert entry["sortino_ratio"] is None
            assert entry["calmar_ratio"] is None

    def test_ratios_none_with_one_daily_return(self) -> None:
        """Sharpe, Sortino, Calmar are None when only 1 daily return recorded.

        1 return means 2 midnight NAV records — below the 2-return minimum.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_log = self._make_logger(tmpdir)
            # Inject exactly 1 daily return directly (avoids calendar dependency).
            perf_log._daily_returns = [0.01]
            perf_log._daily_navs = [1_000_000.0, 1_010_000.0]
            entry = asyncio.run(perf_log.snapshot(
                nav=1_010_000.0, positions=[], regime="X", signal_health={}
            ))
            assert entry["sharpe_ratio"] is None
            assert entry["sortino_ratio"] is None
            assert entry["calmar_ratio"] is None

    # ------------------------------------------------------------------
    # Startup recovery from daily_returns.jsonl
    # ------------------------------------------------------------------

    def test_restart_recovers_nav_peak_from_daily_returns(self) -> None:
        """nav_peak is reconstructed from daily_returns.jsonl on restart."""
        with tempfile.TemporaryDirectory() as tmpdir:
            daily_path = Path(tmpdir) / "daily_returns.jsonl"
            records = [
                {"date": "2026-03-01", "nav": 1_000_000},
                {"date": "2026-03-02", "nav": 1_150_000},   # ← historic peak
                {"date": "2026-03-03", "nav": 1_100_000},
            ]
            with open(daily_path, "w") as f:
                for r in records:
                    f.write(json.dumps(r) + "\n")

            perf_log = PerformanceLogger(
                log_path=str(Path(tmpdir) / "snapshots.jsonl"),
                daily_returns_path=str(daily_path),
                competition_start_nav=1_000_000.0,
            )
            # nav_peak must have been recovered from the highest midnight NAV.
            assert perf_log.nav_peak == 1_150_000.0
            # Daily returns: 2 consecutive diffs from 3 records.
            assert len(perf_log._daily_returns) == 2

    def test_restart_skips_malformed_lines(self) -> None:
        """Malformed JSONL lines in daily_returns.jsonl are skipped silently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            daily_path = Path(tmpdir) / "daily_returns.jsonl"
            with open(daily_path, "w") as f:
                f.write('{"date": "2026-03-01", "nav": 1000000}\n')
                f.write('THIS IS NOT JSON\n')
                f.write('{"date": "2026-03-02", "nav": 1050000}\n')

            perf_log = PerformanceLogger(
                log_path=str(Path(tmpdir) / "snapshots.jsonl"),
                daily_returns_path=str(daily_path),
            )
            # 2 valid records → 1 daily return, no crash.
            assert len(perf_log._daily_returns) == 1
            assert perf_log.nav_peak == pytest.approx(1_050_000.0, rel=1e-6)

    # ------------------------------------------------------------------
    # btc_beta pass-through
    # ------------------------------------------------------------------

    def test_btc_beta_passed_through(self) -> None:
        """btc_beta is stored verbatim in the snapshot."""
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_log = self._make_logger(tmpdir)
            entry = asyncio.run(perf_log.snapshot(
                nav=1_000_000.0, positions=[], regime="X",
                signal_health={}, btc_beta=0.85,
            ))
            assert entry["btc_beta"] == pytest.approx(0.85, rel=1e-6)

    def test_btc_beta_none_by_default(self) -> None:
        """btc_beta defaults to None until Wave 3 wiring."""
        with tempfile.TemporaryDirectory() as tmpdir:
            perf_log = self._make_logger(tmpdir)
            entry = asyncio.run(perf_log.snapshot(
                nav=1_000_000.0, positions=[], regime="X", signal_health={}
            ))
            assert entry["btc_beta"] is None


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
