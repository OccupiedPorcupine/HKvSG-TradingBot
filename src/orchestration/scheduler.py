"""APEX Asyncio Job Scheduler.

Manages the cadence of all system tasks using cooperative multitasking.
Prevents job overlap via locks and handles heartbeat signaling.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from src.orchestration.safe_state import SystemState

logger = logging.getLogger(__name__)


class Scheduler:
    """Asyncio task scheduler with overlap prevention and error handling.

    Attributes:
        system_state: Global health tracker.
        heartbeat_path: Path to the heartbeat file.
        locks: Mapping of job name to asyncio.Lock.
    """

    def __init__(
        self, system_state: SystemState, heartbeat_path: Path
    ) -> None:
        self.system_state = system_state
        self.heartbeat_path = heartbeat_path
        self.locks: dict[str, asyncio.Lock] = {}
        self._tasks: list[asyncio.Task] = []

    def _get_lock(self, name: str) -> asyncio.Lock:
        """Get or create a lock for a job."""
        if name not in self.locks:
            self.locks[name] = asyncio.Lock()
        return self.locks[name]

    async def schedule_job(
        self,
        name: str,
        interval_sec: float,
        func: Callable[[], Awaitable[None]],
        immediate: bool = True,
    ) -> None:
        """Schedule a recurring job.

        Args:
            name: Unique name for the job.
            interval_sec: Seconds between triggers.
            func: Async function to execute.
            immediate: If True, run once immediately before the first sleep.
        """
        lock = self._get_lock(name)
        self.system_state.register_job(name)

        async def _job_wrapper():
            if not immediate:
                await asyncio.sleep(interval_sec)

            while True:
                start_time = time.monotonic()
                
                if lock.locked():
                    logger.warning("JOB_OVERLAP: %s still running, skipping tick.", name)
                else:
                    async with lock:
                        try:
                            await func()
                            self.system_state.report_success(name)
                            
                            # Update heartbeat if this is the ingestion job
                            if name == "data_ingestion":
                                self._update_heartbeat()
                                
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            self.system_state.report_failure(name, str(e))
                            logger.exception("Unhandled exception in job %s: %s", name, e)

                # Calculate remaining sleep time to maintain cadence
                elapsed = time.monotonic() - start_time
                sleep_time = max(0.1, interval_sec - elapsed)
                await asyncio.sleep(sleep_time)

        task = asyncio.create_task(_job_wrapper(), name=name)
        self._tasks.append(task)
        logger.info("Scheduled job '%s' every %gs", name, interval_sec)

    def _update_heartbeat(self) -> None:
        """Write current timestamp to the heartbeat file."""
        try:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.heartbeat_path, "w") as f:
                f.write(datetime.now(timezone.utc).isoformat())
        except Exception as e:
            logger.error("Failed to write heartbeat: %s", e)

    async def stop_all(self) -> None:
        """Cancel all scheduled tasks."""
        logger.info("Stopping all scheduled tasks...")
        for task in self._tasks:
            task.cancel()
        
        # Wait for all tasks to complete cancellation
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("All tasks stopped.")
