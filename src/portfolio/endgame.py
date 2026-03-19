"""End-game de-risking for competition rounds.

As time remaining decreases, exposure caps tighten and trailing stops
narrow to lock in accumulated gains. The final 15 minutes force a
complete exit to avoid last-minute drawdowns that would damage Calmar.

The schedule is loaded from config and supports clean reset for Round 2.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_FINAL_SELL_HOURS = 0.25  # 15 minutes


class EndgameManager:
    def __init__(self, config: dict):
        """Parse schedule from config['endgame']['schedule'] and round end time.

        Schedule is a list of dicts sorted by hours_remaining descending:
          [{"hours_remaining": 48, "max_exposure": 1.0, "stop_override": null}, ...]

        Each entry activates when time_left drops below that threshold.
        The entry with the largest hours_remaining <= time_left applies.
        """
        eg = config.get("endgame", {})
        # Sort descending so we can short-circuit on first match
        self._schedule: list[dict] = sorted(
            eg.get("schedule", []),
            key=lambda e: e["hours_remaining"],
            reverse=True,
        )
        end_str: str | None = eg.get("round_end_utc")
        self._round_end: datetime | None = (
            datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            if end_str
            else None
        )
        self._current_tier: int = -1  # sentinel: no tier logged yet

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_constraints(self, now: datetime | None = None) -> dict:
        """Return current endgame constraints.

        Returns:
            {
                "max_exposure": float,        # 0.0–1.0, caps portfolio crypto exposure
                "stop_override": float | None, # overrides dynamic stop distance if set
                "sell_all": bool,              # True if < 15 min remaining (non-negotiable)
                "hours_remaining": float,      # for logging/monitoring
                "tier": int,                   # schedule index (0 = loosest)
            }
        """
        if now is None:
            now = datetime.now(timezone.utc)

        if self._round_end is None:
            return {
                "max_exposure": 1.0,
                "stop_override": None,
                "sell_all": False,
                "hours_remaining": float("inf"),
                "tier": -1,
            }

        hours_remaining = (self._round_end - now).total_seconds() / 3600.0

        # Competition over or final 15 minutes
        if hours_remaining <= _FINAL_SELL_HOURS:
            new_tier = len(self._schedule)
            self._log_tier_change(new_tier, hours_remaining, 0.0)
            return {
                "max_exposure": 0.0,
                "stop_override": None,
                "sell_all": True,
                "hours_remaining": hours_remaining,
                "tier": new_tier,
            }

        # Find the entry with the largest hours_remaining that has already
        # been crossed (i.e., entry.hours_remaining <= time_left).
        applicable: dict | None = None
        tier_idx: int = -1
        for i, entry in enumerate(self._schedule):  # descending order
            if entry["hours_remaining"] <= hours_remaining:
                applicable = entry
                tier_idx = i
                break

        if applicable is None:
            # time_left is below all schedule entries but above sell_all threshold;
            # this only happens if the 0.25h sentinel entry is absent from config.
            max_exposure = 0.0
            stop_override = None
            tier_idx = len(self._schedule) - 1
        else:
            max_exposure = float(applicable["max_exposure"])
            stop_override = applicable.get("stop_override")

        self._log_tier_change(tier_idx, hours_remaining, max_exposure)

        return {
            "max_exposure": max_exposure,
            "stop_override": stop_override,
            "sell_all": False,
            "hours_remaining": hours_remaining,
            "tier": tier_idx,
        }

    def reset_for_round(self, new_end_utc: str) -> None:
        """Reset round end time for Round 2. Parse ISO format string."""
        self._round_end = datetime.fromisoformat(new_end_utc.replace("Z", "+00:00"))
        self._current_tier = -1
        logger.info("ENDGAME RESET: new round end %s", self._round_end)

    @property
    def round_end(self) -> datetime | None:
        """Read-only access to configured round end time."""
        return self._round_end

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _log_tier_change(
        self, new_tier: int, hours_remaining: float, max_exposure: float
    ) -> None:
        if self._current_tier != new_tier:
            logger.info(
                "ENDGAME TIER_CHANGE: tier %d → %d (%.1fh remaining, max_exposure=%.0f%%)",
                self._current_tier,
                new_tier,
                hours_remaining,
                max_exposure * 100,
            )
            self._current_tier = new_tier
