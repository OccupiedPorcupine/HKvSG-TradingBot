"""Buffered JSON-lines decision logger for the execution engine.

Every order decision is logged as a JSON object to an append-only file.
Writes are non-blocking — entries buffer in memory and flush to disk
every 10 seconds or 100 entries, whichever comes first.

Required for competition code review compliance.
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class DecisionEntry:
    """A single order decision log entry.

    Attributes:
        timestamp_utc: ISO 8601 timestamp.
        asset: Asset symbol.
        action: NEW_ENTRY, INCREASE, DECREASE, EXIT, SUPPRESS.
        trigger: What caused this decision.
        regime_state: Current regime at time of decision.
        momentum_score: Asset's momentum composite score.
        trend_penalty_applied: Whether trend penalty was applied.
        ml_multiplier: ML size multiplier (1.0 in Phase 1-2).
        current_weight_before: Portfolio weight before this order.
        target_weight: Target weight from portfolio construction.
        order_type: Always "LIMIT" except EMERGENCY_MARKET_ORDER.
        submitted_price: Price submitted with the order.
        fill_price: Actual fill price (null if not yet filled).
        fill_timestamp_utc: When the fill was confirmed (null if pending).
        commission_paid: Commission in USD (null if not yet filled).
        suppressed: True if minimum threshold prevented execution.
        suppression_reason: Why the order was suppressed (null if not).
    """

    timestamp_utc: str
    asset: str
    action: str
    trigger: str
    regime_state: str = "UNKNOWN"
    momentum_score: float = 0.0
    trend_penalty_applied: bool = False
    ml_multiplier: float = 1.0
    current_weight_before: float = 0.0
    target_weight: float = 0.0
    order_type: str = "LIMIT"
    submitted_price: float = 0.0
    fill_price: Optional[float] = None
    filled_quantity: Optional[float] = None
    fill_timestamp_utc: Optional[str] = None
    commission_paid: Optional[float] = None
    suppressed: bool = False
    suppression_reason: Optional[str] = None
    # Screen 1 compliance fields (Section 11.3)
    symbol: Optional[str] = None
    signal_values: Optional[dict] = None
    target_weight_pct: Optional[float] = None
    previous_weight_pct: Optional[float] = None
    limit_price: Optional[float] = None
    size_usd: Optional[float] = None
    fill_confirmation: Optional[dict] = None
    commission_paid_usd: Optional[float] = None


class DecisionLogger:
    """Non-blocking buffered logger for order decisions.

    Buffers entries in memory and flushes to a JSON-lines file
    periodically. Flush triggers:
      - 100 entries accumulated
      - 10 seconds since last flush
      - explicit flush() call

    Attributes:
        log_path: Path to the JSONL output file.
        buffer: In-memory list of pending entries.
        flush_interval_sec: Seconds between automatic flushes.
        flush_threshold: Number of entries that trigger a flush.
    """

    def __init__(
        self,
        log_path: str = "logs/trades.jsonl",
        flush_interval_sec: float = 10.0,
        flush_threshold: int = 100,
    ) -> None:
        """Initialize the decision logger.

        Args:
            log_path: Path to the JSONL output file.
            flush_interval_sec: Seconds between automatic flushes.
            flush_threshold: Number of entries to trigger immediate flush.
        """
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.buffer: list[DecisionEntry] = []
        self.flush_interval_sec = flush_interval_sec
        self.flush_threshold = flush_threshold
        self._last_flush = time.monotonic()
        self._total_logged = 0
        self._flush_task: Optional[asyncio.Task] = None

    def log(self, entry: DecisionEntry) -> None:
        """Add a decision entry to the buffer.

        Non-blocking. Entry will be written to disk on next flush.

        Args:
            entry: Decision entry to log.
        """
        self.buffer.append(entry)
        if len(self.buffer) >= self.flush_threshold:
            self._sync_flush()

    def log_order(
        self,
        asset: str,
        action: str,
        trigger: str,
        submitted_price: float = 0.0,
        **kwargs,
    ) -> DecisionEntry:
        """Convenience method to create and log a decision entry.

        Args:
            asset: Asset symbol.
            action: Order action (NEW_ENTRY, EXIT, etc.).
            trigger: What triggered this order.
            submitted_price: Price submitted with the order.
            **kwargs: Additional DecisionEntry fields.

        Returns:
            The created DecisionEntry.
        """
        entry = DecisionEntry(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            asset=asset,
            action=action,
            trigger=trigger,
            submitted_price=submitted_price,
            **kwargs,
        )
        # Auto-populate Screen 1 compliance fields from existing data.
        if entry.symbol is None:
            entry.symbol = entry.asset
        if entry.limit_price is None and entry.submitted_price > 0:
            entry.limit_price = entry.submitted_price
        self.log(entry)
        return entry

    def check_flush(self) -> None:
        """Check if a time-based flush is needed and perform it.

        Call this periodically from the main loop (e.g., every price bar).
        """
        elapsed = time.monotonic() - self._last_flush
        if elapsed >= self.flush_interval_sec and self.buffer:
            # MUST dispatch to async task to prevent blocking the event loop
            loop = asyncio.get_running_loop()
            loop.create_task(self.flush())

    def _sync_flush(self) -> None:
        """Synchronously flush buffer to disk."""
        if not self.buffer:
            return

        entries = self.buffer
        self.buffer = []
        self._last_flush = time.monotonic()

        try:
            with open(self.log_path, "a") as f:
                for entry in entries:
                    line = json.dumps(asdict(entry), default=str)
                    f.write(line + "\n")
            self._total_logged += len(entries)
            logger.debug(
                "Flushed %d decision entries (total: %d)",
                len(entries),
                self._total_logged,
            )
        except OSError as e:
            # Re-add entries on write failure — don't lose data
            self.buffer = entries + self.buffer
            logger.error("Decision log flush failed: %s", e)

    async def flush(self) -> None:
        """Async flush — runs sync flush in executor to avoid blocking.

        Use this for the periodic flush from the main event loop.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._sync_flush)

    async def start_periodic_flush(self) -> None:
        """Start a background task for periodic flushing.

        Runs until cancelled. Should be started as a task from the orchestrator.
        """
        while True:
            await asyncio.sleep(self.flush_interval_sec)
            self.check_flush()

    def close(self) -> None:
        """Flush remaining entries and close."""
        self._sync_flush()
        logger.info(
            "Decision logger closed. Total entries: %d", self._total_logged
        )

    @property
    def total_logged(self) -> int:
        """Total number of entries flushed to disk."""
        return self._total_logged
