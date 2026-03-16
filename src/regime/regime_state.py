"""RegimeState dataclass and RegimeType enum.

The regime state is the primary output of Layer 3. It tells all downstream
layers (signals, portfolio construction, risk) what market environment
we are currently in.

Four states:
    TREND_BULL       — broad uptrend, momentum reliable
    TREND_BEAR       — broad downtrend, minimize crypto
    MEAN_REVERT      — choppy, momentum partially reliable
    HIGH_VOL_CRISIS  — acute stress, capital preservation
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class RegimeType(str, Enum):
    """Market regime classification.

    Uses str mixin so values serialize cleanly to JSON logs.
    """

    TREND_BULL = "TREND_BULL"
    TREND_BEAR = "TREND_BEAR"
    MEAN_REVERT = "MEAN_REVERT"
    HIGH_VOL_CRISIS = "HIGH_VOL_CRISIS"


# Transitions that count as downgrades (applied immediately)
_DOWNGRADE_TRANSITIONS: set[tuple[RegimeType, RegimeType]] = {
    (RegimeType.TREND_BULL, RegimeType.HIGH_VOL_CRISIS),
    (RegimeType.TREND_BULL, RegimeType.TREND_BEAR),
    (RegimeType.MEAN_REVERT, RegimeType.HIGH_VOL_CRISIS),
    (RegimeType.MEAN_REVERT, RegimeType.TREND_BEAR),
    (RegimeType.TREND_BEAR, RegimeType.HIGH_VOL_CRISIS),
}

# Transitions that count as upgrades (require confirmation window)
_UPGRADE_TRANSITIONS: set[tuple[RegimeType, RegimeType]] = {
    (RegimeType.HIGH_VOL_CRISIS, RegimeType.TREND_BEAR),
    (RegimeType.HIGH_VOL_CRISIS, RegimeType.MEAN_REVERT),
    (RegimeType.HIGH_VOL_CRISIS, RegimeType.TREND_BULL),
    (RegimeType.TREND_BEAR, RegimeType.MEAN_REVERT),
    (RegimeType.TREND_BEAR, RegimeType.TREND_BULL),
    (RegimeType.MEAN_REVERT, RegimeType.TREND_BULL),
}


def is_downgrade(current: RegimeType, candidate: RegimeType) -> bool:
    """Return True if moving from current to candidate is a downgrade."""
    return (current, candidate) in _DOWNGRADE_TRANSITIONS


def is_upgrade(current: RegimeType, candidate: RegimeType) -> bool:
    """Return True if moving from current to candidate is an upgrade."""
    return (current, candidate) in _UPGRADE_TRANSITIONS


@dataclass
class RegimeState:
    """Complete regime classification state.

    Updated by the regime detector every 5 minutes. Read by signal
    generation, portfolio construction, and risk management.

    Attributes:
        current_regime: Active regime classification.
        previous_regime: Regime before the most recent confirmed transition.
        bars_in_current_regime: Count of 1-min bars since last confirmed transition.
        transition_pending: True during the 30-bar upgrade confirmation window.
        transition_target: The regime we are upgrading toward, or None.
        transition_bars_remaining: Countdown bars until upgrade confirms.
        contagion_proxy: Latest contagion ratio (0.0 to 1.0).
        avg_loss: Average 5-min loss of declining positions (positive = loss).
        btc_vol_percentile: Latest BTC 1h vol percentile (0-100).
    """

    current_regime: RegimeType = RegimeType.TREND_BULL
    previous_regime: RegimeType = RegimeType.TREND_BULL
    bars_in_current_regime: int = 0
    transition_pending: bool = False
    transition_target: Optional[RegimeType] = None
    transition_bars_remaining: int = 0
    contagion_proxy: float = 0.0
    avg_loss: float = 0.0
    btc_vol_percentile: float = 0.0

    def confirm_transition(self, new_regime: RegimeType) -> None:
        """Apply a confirmed regime transition.

        Args:
            new_regime: The regime to transition to.
        """
        self.previous_regime = self.current_regime
        self.current_regime = new_regime
        self.bars_in_current_regime = 0
        self.transition_pending = False
        self.transition_target = None
        self.transition_bars_remaining = 0

    def start_upgrade(self, target: RegimeType, confirmation_bars: int) -> None:
        """Begin a gradual upgrade transition.

        Args:
            target: The regime we are upgrading toward.
            confirmation_bars: How many consecutive bars must confirm.
        """
        self.transition_pending = True
        self.transition_target = target
        self.transition_bars_remaining = confirmation_bars

    def cancel_upgrade(self) -> None:
        """Cancel a pending upgrade (condition no longer holds)."""
        self.transition_pending = False
        self.transition_target = None
        self.transition_bars_remaining = 0

    def to_log_dict(self) -> dict:
        """Serialize for JSON logging."""
        return {
            "current_regime": self.current_regime.value,
            "previous_regime": self.previous_regime.value,
            "bars_in_current": self.bars_in_current_regime,
            "transition_pending": self.transition_pending,
            "transition_target": (
                self.transition_target.value if self.transition_target else None
            ),
            "transition_bars_remaining": self.transition_bars_remaining,
            "contagion_proxy": round(self.contagion_proxy, 4),
            "avg_loss": round(self.avg_loss, 6),
            "btc_vol_percentile": round(self.btc_vol_percentile, 2),
        }
