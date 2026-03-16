"""RiskEvent dataclass — shared data structure for all risk signals.

Every risk breach detected by Layer 6 is expressed as a RiskEvent. The
execution layer (Layer 7) reads these events and acts on them according
to their severity and priority.

Priority ordering within CRITICAL:
  trailing stops > circuit breakers > contagion events
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class EventType(str, Enum):
    """Types of risk events that Layer 6 can emit."""

    TRAILING_STOP = "TRAILING_STOP"
    CIRCUIT_BREAKER_DD = "CIRCUIT_BREAKER_DD"          # portfolio drawdown
    CIRCUIT_BREAKER_DAILY = "CIRCUIT_BREAKER_DAILY"    # daily P&L limit
    CONTAGION_CRISIS = "CONTAGION_CRISIS"
    CONCENTRATION_BREACH = "CONCENTRATION_BREACH"
    SINGLE_ASSET_LOSS = "SINGLE_ASSET_LOSS"            # single position loss from entry
    EXPOSURE_BREACH = "EXPOSURE_BREACH"                # total crypto exposure exceeded
    ENDGAME_SELL_ALL = "ENDGAME_SELL_ALL"              # final 15-min forced liquidation
    DRAWDOWN_HALT = "DRAWDOWN_HALT"                    # 8% drawdown halt on new entries
    STOP_TIGHTENING = "STOP_TIGHTENING"                # informational: stops tightened


class Severity(str, Enum):
    """Severity determines how quickly the event must be acted on.

    CRITICAL: execute immediately, bypass rebalance cadence.
    HIGH:     execute at next rebalance.
    MEDIUM:   log and review.
    """

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"


# Priority within CRITICAL severity (lower number = higher priority)
CRITICAL_PRIORITY: dict[EventType, int] = {
    EventType.ENDGAME_SELL_ALL: 0,
    EventType.TRAILING_STOP: 1,
    EventType.CIRCUIT_BREAKER_DD: 2,
    EventType.CIRCUIT_BREAKER_DAILY: 3,
    EventType.CONTAGION_CRISIS: 4,
    EventType.SINGLE_ASSET_LOSS: 5,
    EventType.EXPOSURE_BREACH: 6,
}


@dataclass(frozen=True)
class RiskEvent:
    """Immutable record of a detected risk breach.

    Attributes:
        event_type: Category of risk event.
        severity: How urgently the event must be acted on.
        triggered_value: The value that triggered the breach (e.g., drawdown pct).
        limit_value: The threshold that was breached.
        action_required: Human-readable description of what must happen.
        asset: Asset symbol, or None for portfolio-level events.
        timestamp: UTC timestamp when the event was detected.
    """

    event_type: EventType
    severity: Severity
    triggered_value: float
    limit_value: float
    action_required: str
    asset: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_critical(self) -> bool:
        """Whether this event requires immediate execution."""
        return self.severity == Severity.CRITICAL

    @property
    def is_asset_level(self) -> bool:
        """Whether this event targets a specific asset (vs. portfolio-level)."""
        return self.asset is not None

    @property
    def priority(self) -> int:
        """Sort key for ordering events. Lower = higher priority."""
        severity_order = {Severity.CRITICAL: 0, Severity.HIGH: 100, Severity.MEDIUM: 200}
        base = severity_order.get(self.severity, 300)
        if self.severity == Severity.CRITICAL:
            base += CRITICAL_PRIORITY.get(self.event_type, 50)
        return base

    def to_log_dict(self) -> dict:
        """Serialize to dict for JSON logging."""
        return {
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "asset": self.asset,
            "triggered_value": round(self.triggered_value, 6),
            "limit_value": round(self.limit_value, 6),
            "action_required": self.action_required,
            "timestamp": self.timestamp.isoformat(),
        }

    def __lt__(self, other: "RiskEvent") -> bool:
        """Enable sorting by priority (highest priority first)."""
        return self.priority < other.priority
