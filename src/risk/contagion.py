"""Contagion circuit breaker — reads contagion proxy from Layer 3.

Checks every 1-minute bar. If contagion ratio > threshold AND average
loss > threshold, forces HIGH_VOL_CRISIS regime and reduces all positions.

This is an O(n) check — no pairwise correlation matrix.
"""

import logging
from typing import Optional

from src.risk.risk_event import EventType, RiskEvent, Severity

logger = logging.getLogger(__name__)


class ContagionMonitor:
    """Monitors contagion proxy and triggers crisis if thresholds breached.

    Reads contagion_ratio and avg_loss from Layer 3 RegimeState.

    Attributes:
        ratio_threshold: Fraction of positions that must be down (default 0.80).
        avg_loss_threshold: Average loss that triggers (default 0.01 = 1%).
        position_reduction_to: Reduce all positions to this fraction (default 0.20).
        triggered: Whether the contagion breaker is currently active.
    """

    def __init__(
        self,
        ratio_threshold: float = 0.80,
        avg_loss_threshold: float = 0.01,
        position_reduction_to: float = 0.20,
    ) -> None:
        """Initialize the contagion monitor.

        Args:
            ratio_threshold: Fraction of held positions with negative 5-min return.
            avg_loss_threshold: Average 5-min loss across held positions.
            position_reduction_to: Target position fraction on trigger.
        """
        self.ratio_threshold = ratio_threshold
        self.avg_loss_threshold = avg_loss_threshold
        self.position_reduction_to = position_reduction_to
        self.triggered = False

    def check(
        self,
        contagion_ratio: float,
        avg_loss: float,
    ) -> Optional[RiskEvent]:
        """Check contagion proxy against thresholds.

        Called every 1-minute bar with values from Layer 3 RegimeState.

        Args:
            contagion_ratio: Fraction of held positions with negative 5-min return.
            avg_loss: Average 5-min loss across those positions (positive = loss).

        Returns:
            RiskEvent if contagion threshold breached, None otherwise.
        """
        if contagion_ratio > self.ratio_threshold and avg_loss > self.avg_loss_threshold:
            if not self.triggered:
                self.triggered = True
                logger.critical(
                    "CONTAGION CRISIS: ratio=%.2f%% (limit %.2f%%) "
                    "avg_loss=%.2f%% (limit %.2f%%)",
                    contagion_ratio * 100,
                    self.ratio_threshold * 100,
                    avg_loss * 100,
                    self.avg_loss_threshold * 100,
                )

            return RiskEvent(
                event_type=EventType.CONTAGION_CRISIS,
                severity=Severity.CRITICAL,
                triggered_value=contagion_ratio,
                limit_value=self.ratio_threshold,
                action_required=(
                    f"CONTAGION CRISIS: {contagion_ratio:.0%} positions down "
                    f"with avg loss {avg_loss:.2%}. Force HIGH_VOL_CRISIS regime. "
                    f"Reduce all positions to {self.position_reduction_to:.0%}."
                ),
            )

        # Reset if conditions normalize
        if self.triggered and contagion_ratio <= self.ratio_threshold * 0.6:
            self.triggered = False
            logger.info(
                "Contagion crisis cleared: ratio=%.2f%%",
                contagion_ratio * 100,
            )

        return None
