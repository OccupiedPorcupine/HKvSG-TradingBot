"""APEX Error Handling & Safe-State Fallback.

Defines the global safe-state logic and per-job failure handling to
ensure the bot never crashes silently and remains in a safe posture
during partial system failures.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class JobStatus:
    """Tracking status for an asynchronous job."""
    name: str
    last_run_utc: Optional[datetime] = None
    consecutive_failures: int = 0
    is_failing: bool = False
    last_error: Optional[str] = None


class SystemState:
    """Manages global system health and safe-state transitions.

    Attributes:
        is_global_safe_mode: If True, only risk exits are allowed.
        is_stale_data: If True, prices are not being updated.
        jobs: Mapping of job name to JobStatus.
        max_consecutive_failures: Failures before entering global safe mode.
    """

    def __init__(self, max_consecutive_failures: int = 3) -> None:
        self.is_global_safe_mode: bool = False
        self.is_stale_data: bool = False
        self.jobs: dict[str, JobStatus] = {}
        self.max_consecutive_failures = max_consecutive_failures
        self._critical_jobs = {"data_ingestion", "risk_check"}

    def register_job(self, name: str) -> None:
        """Register a new job for health tracking."""
        self.jobs[name] = JobStatus(name=name)

    def report_success(self, name: str) -> None:
        """Mark a job as successfully completed."""
        if name not in self.jobs:
            self.register_job(name)
        
        job = self.jobs[name]
        job.last_run_utc = datetime.now(timezone.utc)
        
        if job.is_failing:
            logger.info("Job recovered: %s", name)
            job.is_failing = False
            job.consecutive_failures = 0
            job.last_error = None
            
            # If all critical jobs are OK, we might exit safe mode
            # (though normally safe mode requires manual review or 
            # sustained stability)
            self._check_global_recovery()

    def report_failure(self, name: str, error: str) -> bool:
        """Mark a job as failed and evaluate safe-state entry.

        Args:
            name: Name of the failed job.
            error: Error message or stack trace.

        Returns:
            True if system entered global safe mode.
        """
        if name not in self.jobs:
            self.register_job(name)
            
        job = self.jobs[name]
        job.consecutive_failures += 1
        job.is_failing = True
        job.last_error = error
        
        logger.error(
            "JOB_FAILURE: %s (attempt %d/%d): %s",
            name, job.consecutive_failures, self.max_consecutive_failures, error
        )

        # Per-job fallback logic
        if name == "data_ingestion":
            self.is_stale_data = True
            logger.warning("STALE_DATA_FLAG set — using last known prices.")
        
        if name == "risk_check":
            logger.critical("RISK_CHECK_FAILURE — halting all new entries.")
            # Risk check failure is an immediate safe-state trigger
            self._enter_global_safe_mode(f"Critical job failure: {name}")
            return True

        # Check for global safe-state threshold
        if job.consecutive_failures >= self.max_consecutive_failures:
            self._enter_global_safe_mode(f"Job {name} failed {job.consecutive_failures} times.")
            return True
            
        return self.is_global_safe_mode

    def _enter_global_safe_mode(self, reason: str) -> None:
        """Transition the entire system into safe mode."""
        if not self.is_global_safe_mode:
            self.is_global_safe_mode = True
            logger.critical("SYSTEM_CRITICAL: Entering global SAFE MODE. Reason: %s", reason)
            logger.critical("SAFE MODE POLICY: Hold current positions, allow RISK EXITS only, NO new entries.")

    def _check_global_recovery(self) -> None:
        """Evaluate if the system can exit global safe mode automatically.

        Note: Per spec, global safe mode usually requires manual review,
        but we can allow auto-recovery for minor intermittent issues.
        """
        if not self.is_global_safe_mode:
            return
            
        # Check if all critical jobs are healthy
        all_critical_ok = True
        for name in self._critical_jobs:
            job = self.jobs.get(name)
            if not job or job.is_failing:
                all_critical_ok = False
                break
                
        if all_critical_ok:
            # We don't automatically exit safe mode here per spec,
            # but we could. For now, we stay in safe mode but log.
            logger.info("All critical jobs are healthy, but remaining in SAFE MODE for safety.")

    @property
    def can_rebalance(self) -> bool:
        """Check if rebalancing (new entries) is allowed."""
        if self.is_global_safe_mode:
            return False
        if self.is_stale_data:
            return False
        
        # Check if rebalance job itself is failing
        rebalance_job = self.jobs.get("rebalance")
        if rebalance_job and rebalance_job.is_failing:
            return False
            
        return True

    def get_summary(self) -> dict:
        """Get summary of system health for monitoring."""
        return {
            "is_global_safe_mode": self.is_global_safe_mode,
            "is_stale_data": self.is_stale_data,
            "job_status": {
                name: {
                    "failing": job.is_failing,
                    "consecutive_failures": job.consecutive_failures,
                    "last_run": job.last_run_utc.isoformat() if job.last_run_utc else None
                }
                for name, job in self.jobs.items()
            }
        }
