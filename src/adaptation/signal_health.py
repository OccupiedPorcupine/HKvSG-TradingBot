"""Signal health monitoring (Layer 8, Component 1).

Tracks whether the momentum signal is producing value by monitoring
hit rate and winner/loser ratio. Runs every 1 hour, never blocks the
main trading loop.

Design intent:
  Phase 1 (this file): ``record_close`` feeds a 24-hour rolling deque of
  closed-trade outcomes.  ``snapshot`` computes hit rate and win/loss ratio
  for monitoring and display.  ``is_signal_healthy`` always returns True —
  the trading loop continues uninterrupted regardless of metrics.

  Phase 2: ``is_signal_healthy`` will apply the NP Factor halt condition
  (hit_rate < 0.75 sustained for ``hit_rate_halt_sustained_hr`` hours).
  The deque and snapshot interface remain unchanged; only the halt logic
  is layered on top.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SignalHealthConfig:
    """Configuration for signal health monitoring.

    Attributes:
        check_cadence_hr: Hours between health checks.
        hit_rate_window_hr: Rolling window for hit rate calculation.
        hit_rate_warning: Hit rate below this triggers a warning.
        hit_rate_halt: Hit rate below this for halt_sustained_hr halts signal.
        hit_rate_halt_sustained_hr: Hours hit rate must stay below halt.
        halted_exposure_cap: Max exposure when signal is halted.
        halted_keep_top_positions: Keep only this many best positions when halted.
        resume_hit_rate: Hit rate must recover above this to resume.
        resume_window_hr: Hours hit rate must stay above resume threshold.
        winner_loser_warning: Win/loss ratio below this triggers a warning.
        winner_loser_halt: Win/loss ratio below this for halt.
    """

    check_cadence_hr: int = 1
    hit_rate_window_hr: int = 24
    hit_rate_warning: float = 0.45
    hit_rate_halt: float = 0.35
    hit_rate_halt_sustained_hr: int = 12
    halted_exposure_cap: float = 0.30
    halted_keep_top_positions: int = 3
    resume_hit_rate: float = 0.50
    resume_window_hr: int = 6
    winner_loser_warning: float = 0.80
    winner_loser_halt: float = 0.50


@dataclass
class _ClosedTradeEntry:
    """Internal record for one fully-closed position stored in the rolling window.

    Attributes:
        is_winner: True when net P&L (commission already deducted by caller) > 0.
        abs_pnl: Absolute dollar P&L of the trade.
        timestamp: UTC close time; used for 24-hour window pruning.
    """

    is_winner: bool
    abs_pnl: float
    timestamp: datetime


@dataclass
class RebalanceOutcome:
    """Outcome of a position entered at rebalance.

    Attributes:
        asset: Asset symbol.
        entry_time: When the position was entered.
        entry_price: Price at entry.
        exit_price: Price at next rebalance (or current if still held).
        return_pct: Percentage return.
    """

    asset: str
    entry_time: datetime
    entry_price: float
    exit_price: float
    return_pct: float


class SignalHealthMonitor:
    """Monitors momentum signal effectiveness.

    Phase 0-1: Stub — tracks metrics, logs results, but does not
    modify trading behavior. Full halt/resume logic for Phase 2+.

    Attributes:
        config: Signal health configuration.
        outcomes: Rolling window of rebalance outcomes.
        is_halted: Whether the momentum signal is currently halted.
        halted_since: When the signal was halted (None if active).
        last_check: When the last health check ran.
    """

    def __init__(self, config: Optional[SignalHealthConfig] = None) -> None:
        """Initialize the signal health monitor.

        Args:
            config: Configuration. Uses defaults if not provided.
        """
        self.config = config or SignalHealthConfig()
        self.outcomes: deque[RebalanceOutcome] = deque(maxlen=500)
        self.is_halted: bool = False
        self.halted_since: Optional[datetime] = None
        self.last_check: Optional[datetime] = None
        # Rolling 24-hour window of closed-trade outcomes (Phase 1 monitoring).
        self._closed_trades: deque[_ClosedTradeEntry] = deque()

    def record_outcome(self, outcome: RebalanceOutcome) -> None:
        """Record a rebalance outcome for hit rate tracking.

        Args:
            outcome: The rebalance outcome to record.
        """
        self.outcomes.append(outcome)

    async def check(self) -> dict:
        """Run signal health check. Returns metrics dict.

        Phase 0-1: Computes and logs metrics but does not act.

        Returns:
            Dict with hit_rate, win_loss_ratio, and action taken.
        """
        self.last_check = datetime.now(timezone.utc)

        if len(self.outcomes) < 5:
            logger.debug("Signal health: insufficient data (%d outcomes)", len(self.outcomes))
            return {"hit_rate": None, "win_loss_ratio": None, "action": "INSUFFICIENT_DATA"}

        # Calculate hit rate (% positive returns)
        positive = sum(1 for o in self.outcomes if o.return_pct > 0)
        hit_rate = positive / len(self.outcomes)

        # Calculate winner/loser ratio
        winners = [o.return_pct for o in self.outcomes if o.return_pct > 0]
        losers = [abs(o.return_pct) for o in self.outcomes if o.return_pct < 0]

        avg_winner = sum(winners) / len(winners) if winners else 0.0
        avg_loser = sum(losers) / len(losers) if losers else 1.0
        win_loss_ratio = avg_winner / avg_loser if avg_loser > 0 else float("inf")

        metrics = {
            "hit_rate": round(hit_rate, 4),
            "win_loss_ratio": round(win_loss_ratio, 4),
            "total_outcomes": len(self.outcomes),
            "action": "MONITOR_ONLY",
        }

        if hit_rate < self.config.hit_rate_warning:
            logger.warning(
                "Signal health WARNING: hit_rate=%.2f%% (threshold: %.2f%%)",
                hit_rate * 100,
                self.config.hit_rate_warning * 100,
            )
        if win_loss_ratio < self.config.winner_loser_warning:
            logger.warning(
                "Signal health WARNING: win/loss=%.2f (threshold: %.2f)",
                win_loss_ratio,
                self.config.winner_loser_warning,
            )

        logger.info(
            "Signal health check: hit_rate=%.2f%% win/loss=%.2f outcomes=%d",
            hit_rate * 100,
            win_loss_ratio,
            len(self.outcomes),
        )

        return metrics

    # -------------------------------------------------------------------------
    # Phase 1 closed-trade rolling window interface
    # -------------------------------------------------------------------------

    def record_close(self, symbol: str, net_pnl: float, timestamp: datetime) -> None:
        """Append a closed-position outcome to the 24-hour rolling window.

        Commission is already deducted from ``net_pnl`` by the caller — do not
        deduct again.  Prunes entries older than ``hit_rate_window_hr`` hours
        on every call so the deque stays bounded without a fixed maxlen.

        Args:
            symbol: Asset symbol (stored for future per-symbol analytics).
            net_pnl: Net realised P&L in USD after commission.
            timestamp: UTC datetime when the position was fully closed.
        """
        entry = _ClosedTradeEntry(
            is_winner=net_pnl > 0,
            abs_pnl=abs(net_pnl),
            timestamp=timestamp,
        )
        self._closed_trades.append(entry)

        # Prune entries that have fallen outside the rolling window.
        cutoff = timestamp - timedelta(hours=self.config.hit_rate_window_hr)
        while self._closed_trades and self._closed_trades[0].timestamp < cutoff:
            self._closed_trades.popleft()

    def snapshot(self) -> dict:
        """Return signal health metrics for the current 24-hour rolling window.

        Returns:
            Dict with keys: ``hit_rate`` (float, 0.0 when empty),
            ``win_loss_ratio`` (float | None, None when no losers),
            ``trades_in_window`` (int), ``window_hours`` (int, always 24).
        """
        trades = list(self._closed_trades)
        total = len(trades)

        if total == 0:
            return {
                "hit_rate": 0.0,
                "win_loss_ratio": None,
                "trades_in_window": 0,
                "window_hours": 24,
            }

        winners = [t for t in trades if t.is_winner]
        losers = [t for t in trades if not t.is_winner]

        hit_rate = len(winners) / total  # total > 0 guaranteed above

        if not losers:
            win_loss_ratio: Optional[float] = None
        else:
            mean_loser_pnl = sum(t.abs_pnl for t in losers) / len(losers)
            if mean_loser_pnl > 0:
                mean_winner_pnl = (
                    sum(t.abs_pnl for t in winners) / len(winners)
                    if winners else 0.0
                )
                win_loss_ratio = mean_winner_pnl / mean_loser_pnl
            else:
                # All losers had exactly $0 P&L — ratio is indeterminate.
                win_loss_ratio = None

        return {
            "hit_rate": hit_rate,
            "win_loss_ratio": win_loss_ratio,
            "trades_in_window": total,
            "window_hours": 24,
        }

    def is_signal_healthy(self) -> bool:
        """Return True when the signal is healthy enough to trade.

        Phase 1: always returns True (monitoring-only, no halt logic).
        # Phase 2: replace with NP Factor < 0.75 halt condition.
        """
        return True  # Phase 2: replace with NP Factor < 0.75 halt condition
