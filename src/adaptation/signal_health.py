"""Signal health monitoring (Layer 8, Component 1).

Tracks whether the momentum signal is producing value by monitoring
hit rate and winner/loser ratio. Runs every 1 hour, never blocks the
main trading loop.

Phase 0-1: Stub implementation — logs metrics but does not act on them.
Phase 2+:  Halts momentum signal if hit rate drops below threshold.
"""

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
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
