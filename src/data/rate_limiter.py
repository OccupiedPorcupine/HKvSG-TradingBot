"""Shared API rate limiter using token bucket algorithm.

All API calls across all endpoints go through this single limiter.
Rate limit is configured in config.yaml (default 30 calls/min until
confirmed in Phase 0).
"""

import asyncio
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """Token bucket rate limiter for API calls.

    Supports a configurable calls-per-minute limit with headroom
    reservation for order placement and retries.

    Attributes:
        max_tokens: Maximum tokens (calls) in the bucket.
        refill_rate: Tokens added per second.
        tokens: Current available tokens.
        last_refill: Timestamp of last refill.
        consecutive_429s: Count of consecutive 429 responses.
        safe_mode: Whether safe mode is active (no new trades).
    """

    def __init__(
        self,
        calls_per_minute: int = 30,
        reserve_headroom: int = 10,
        safe_mode_threshold: int = 5,
    ) -> None:
        """Initialize the rate limiter.

        Args:
            calls_per_minute: Maximum API calls allowed per minute.
            reserve_headroom: Calls reserved for orders and retries.
            safe_mode_threshold: Enter safe mode after this many consecutive 429s.
        """
        self.calls_per_minute = calls_per_minute
        self.reserve_headroom = reserve_headroom
        self.safe_mode_threshold = safe_mode_threshold

        # Effective limit for data calls (total - reserved headroom)
        self.max_tokens = calls_per_minute
        self.refill_rate = calls_per_minute / 60.0  # tokens per second

        self.tokens = float(self.max_tokens)
        self.last_refill = time.monotonic()

        self.consecutive_429s = 0
        self.safe_mode = False

        # Tracking for empirical rate limit testing
        self._call_timestamps: list[float] = []
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.max_tokens, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

    async def acquire(self, priority: bool = False) -> None:
        """Acquire a token, waiting if necessary.

        Args:
            priority: If True, use tokens from the reserve headroom
                (for order placement and risk exits). If False, only
                use tokens above the reserve threshold.
        """
        async with self._lock:
            self._refill()

            # Non-priority calls must leave headroom for orders
            min_tokens = 0.0 if priority else self.reserve_headroom

            if self.tokens > min_tokens:
                self.tokens -= 1.0
                self._call_timestamps.append(time.time())
                return

            # Need to wait for a token
            wait_time = (1.0 - (self.tokens - min_tokens)) / self.refill_rate
            if wait_time < 0:
                wait_time = 1.0 / self.refill_rate

        # Wait outside the lock so other coroutines can proceed
        logger.debug("Rate limiter: waiting %.2fs for token", wait_time)
        await asyncio.sleep(wait_time)

        # Retry acquisition
        async with self._lock:
            self._refill()
            self.tokens -= 1.0
            self._call_timestamps.append(time.time())

    def record_429(self) -> bool:
        """Record a 429 response. Returns True if safe mode should activate.

        Returns:
            True if consecutive 429s exceed threshold and safe mode activates.
        """
        self.consecutive_429s += 1
        logger.warning(
            "Rate limit 429 response #%d (threshold: %d)",
            self.consecutive_429s,
            self.safe_mode_threshold,
        )

        if self.consecutive_429s >= self.safe_mode_threshold:
            self.safe_mode = True
            logger.critical(
                "SAFE_MODE activated after %d consecutive 429s. "
                "Holding positions, no new trades.",
                self.consecutive_429s,
            )
            return True
        return False

    def record_success(self) -> None:
        """Record a successful API call, resetting 429 counter."""
        if self.consecutive_429s > 0:
            logger.info(
                "API call succeeded, resetting 429 counter from %d",
                self.consecutive_429s,
            )
        self.consecutive_429s = 0

    def exit_safe_mode(self) -> None:
        """Manually exit safe mode after conditions normalize."""
        if self.safe_mode:
            self.safe_mode = False
            self.consecutive_429s = 0
            logger.info("SAFE_MODE deactivated.")

    def get_backoff_delay(self) -> float:
        """Calculate exponential backoff delay based on consecutive 429 count.

        Returns:
            Backoff delay in seconds: 1, 2, 4, 8, 16, capped at 30.
        """
        delay = min(2 ** (self.consecutive_429s - 1), 30)
        return float(delay)

    def get_calls_last_minute(self) -> int:
        """Count API calls made in the last 60 seconds.

        Useful for empirical rate limit testing in Phase 0.

        Returns:
            Number of calls in the last 60 seconds.
        """
        cutoff = time.time() - 60.0
        # Clean up old timestamps
        self._call_timestamps = [
            ts for ts in self._call_timestamps if ts > cutoff
        ]
        return len(self._call_timestamps)

    def update_rate_limit(self, new_calls_per_minute: int) -> None:
        """Update the rate limit after Phase 0 empirical testing.

        Args:
            new_calls_per_minute: Confirmed rate limit.
        """
        old = self.calls_per_minute
        self.calls_per_minute = new_calls_per_minute
        self.max_tokens = new_calls_per_minute
        self.refill_rate = new_calls_per_minute / 60.0
        logger.info(
            "Rate limit updated: %d -> %d calls/min",
            old,
            new_calls_per_minute,
        )

    @property
    def available_data_budget(self) -> int:
        """Calls available per minute for data fetching (total - reserved)."""
        return max(0, self.calls_per_minute - self.reserve_headroom)

    def __repr__(self) -> str:
        return (
            f"TokenBucketRateLimiter("
            f"cpm={self.calls_per_minute}, "
            f"tokens={self.tokens:.1f}, "
            f"429s={self.consecutive_429s}, "
            f"safe={self.safe_mode})"
        )
