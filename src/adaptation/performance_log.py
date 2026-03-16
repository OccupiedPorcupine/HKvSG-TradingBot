"""Hourly performance snapshot logger (Layer 8, Component 2).

Writes an hourly snapshot to a JSON-lines file for debugging and
competition code review compliance.

Phase 0-1: Stub implementation — core structure ready, activated in Phase 2.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PerformanceLogger:
    """Logs hourly performance snapshots to a JSON-lines file.

    Attributes:
        log_path: Path to the JSONL output file.
        competition_start_nav: NAV at competition start (for cumulative return).
        last_snapshot: Timestamp of last snapshot.
    """

    def __init__(
        self,
        log_path: str = "logs/snapshots.jsonl",
        competition_start_nav: float = 1_000_000.0,
    ) -> None:
        """Initialize the performance logger.

        Args:
            log_path: Path to the JSONL snapshot file.
            competition_start_nav: Starting NAV for cumulative return calc.
        """
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.competition_start_nav = competition_start_nav
        self.last_snapshot: Optional[datetime] = None
        self._daily_start_nav: float = competition_start_nav
        self._daily_start_date: Optional[str] = None

    async def snapshot(
        self,
        current_nav: float,
        positions: list[dict],
        current_regime: str = "UNKNOWN",
        portfolio_beta_btc: float = 0.0,
        momentum_hit_rate_24h: float = 0.0,
        contagion_proxy: float = 0.0,
        btc_vol_percentile: float = 0.0,
    ) -> dict[str, Any]:
        """Write an hourly performance snapshot.

        Args:
            current_nav: Current net asset value.
            positions: List of position dicts from PositionTracker.
            current_regime: Current regime state string.
            portfolio_beta_btc: Portfolio beta to BTC.
            momentum_hit_rate_24h: Rolling 24h momentum hit rate.
            contagion_proxy: Current contagion proxy value.
            btc_vol_percentile: BTC volatility percentile.

        Returns:
            The snapshot dict that was logged.
        """
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        # Reset daily P&L at midnight UTC
        if self._daily_start_date != today:
            self._daily_start_nav = current_nav
            self._daily_start_date = today

        daily_pnl = current_nav - self._daily_start_nav
        cumulative_return = (
            (current_nav - self.competition_start_nav)
            / self.competition_start_nav
            if self.competition_start_nav > 0
            else 0.0
        )

        entry = {
            "timestamp_utc": now.isoformat(),
            "current_nav": round(current_nav, 2),
            "daily_pnl": round(daily_pnl, 2),
            "cumulative_return": round(cumulative_return, 6),
            "current_regime": current_regime,
            "all_positions": positions,
            "portfolio_beta_btc": round(portfolio_beta_btc, 4),
            "momentum_hit_rate_24h": round(momentum_hit_rate_24h, 4),
            "contagion_proxy": round(contagion_proxy, 4),
            "btc_vol_percentile": round(btc_vol_percentile, 2),
        }

        try:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
            self.last_snapshot = now
            logger.info(
                "Performance snapshot: NAV=$%.2f daily_pnl=$%.2f cum_ret=%.4f%%",
                current_nav,
                daily_pnl,
                cumulative_return * 100,
            )
        except OSError as e:
            logger.error("Performance snapshot write failed: %s", e)

        return entry
