"""Portfolio-level circuit breakers — drawdown and daily loss monitors.

Runs on two cadences:
  - Every 1-minute bar: mark-to-market checks.
  - Every rebalance (60 min): pre-trade validation (see pre_trade_checks.py).

Circuit breakers are hard limits that CANNOT be overridden by signals.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src.risk.risk_event import EventType, RiskEvent, Severity

logger = logging.getLogger(__name__)


@dataclass
class DrawdownState:
    """Tracks portfolio drawdown from peak NAV.

    Attributes:
        peak_nav: Highest NAV observed during the competition.
        halt_active: Whether the drawdown halt is currently active.
        halt_activated_at: When the halt was activated (for cooldown timer).
        resume_sizing_fraction: Fraction of normal sizing after cooldown.
    """

    peak_nav: float = 0.0
    halt_active: bool = False
    halt_activated_at: Optional[datetime] = None
    resume_sizing_fraction: float = 1.0


@dataclass
class DailyPnlState:
    """Tracks daily P&L for circuit breaker and stop tightening.

    Resets at 00:00 UTC.

    Attributes:
        day_start_nav: NAV at the start of the current UTC day.
        current_day: Date of the current tracking day (UTC).
        reduce_active: Whether the daily loss reduction is active.
    """

    day_start_nav: float = 0.0
    current_day: Optional[datetime] = None
    reduce_active: bool = False


class CircuitBreakerManager:
    """Portfolio-level drawdown and daily P&L circuit breakers.

    Usage (called every 1-minute bar)::

        events = breaker_mgr.check(current_nav)

    Attributes:
        drawdown: Drawdown tracking state.
        daily_pnl: Daily P&L tracking state.
    """

    def __init__(
        self,
        drawdown_soft_warning: float = 0.05,
        drawdown_hard_halt: float = 0.08,
        daily_loss_soft_warning: float = 0.03,
        daily_loss_hard_reduce: float = 0.05,
        single_asset_loss_soft: float = 0.04,
        single_asset_loss_hard: float = 0.06,
        halt_cooldown_hr: float = 2.0,
        resume_sizing_fraction: float = 0.50,
        full_sizing_within_pct_of_peak: float = 0.04,
    ) -> None:
        """Initialize circuit breakers from config parameters.

        Args:
            drawdown_soft_warning: Drawdown % for soft warning log.
            drawdown_hard_halt: Drawdown % to halt all new entries.
            daily_loss_soft_warning: Daily loss % for soft warning log.
            daily_loss_hard_reduce: Daily loss % to reduce all to 50%.
            single_asset_loss_soft: Per-asset loss % for warning.
            single_asset_loss_hard: Per-asset loss % to force close.
            halt_cooldown_hr: Hours before resuming after drawdown halt.
            resume_sizing_fraction: Sizing fraction after cooldown resume.
            full_sizing_within_pct_of_peak: Resume full sizing when within
                this % of peak NAV.
        """
        self._dd_soft = drawdown_soft_warning
        self._dd_hard = drawdown_hard_halt
        self._daily_soft = daily_loss_soft_warning
        self._daily_hard = daily_loss_hard_reduce
        self._asset_soft = single_asset_loss_soft
        self._asset_hard = single_asset_loss_hard
        self._halt_cooldown_hr = halt_cooldown_hr
        self._resume_fraction = resume_sizing_fraction
        self._full_sizing_pct = full_sizing_within_pct_of_peak

        self.drawdown = DrawdownState()
        self.daily_pnl = DailyPnlState()

        # Track whether warnings have been logged this cycle
        self._dd_warning_logged = False
        self._daily_warning_logged = False

    def check(self, current_nav: float) -> list[RiskEvent]:
        """Run all portfolio-level circuit breaker checks.

        Called every 1-minute bar.

        Args:
            current_nav: Current portfolio net asset value in USD.

        Returns:
            List of RiskEvents for any breached limits.
        """
        events: list[RiskEvent] = []
        now = datetime.now(timezone.utc)

        # --- Daily P&L day-reset check ---
        self._maybe_reset_daily(current_nav, now)

        # --- Update peak NAV ---
        if current_nav > self.drawdown.peak_nav:
            self.drawdown.peak_nav = current_nav
            # If we're setting new highs, clear the halt if it was active
            if self.drawdown.halt_active:
                self._check_halt_recovery(current_nav)

        # --- Drawdown check ---
        events.extend(self._check_drawdown(current_nav, now))

        # --- Daily P&L check ---
        events.extend(self._check_daily_pnl(current_nav))

        return events

    def check_single_asset_loss(
        self, asset: str, entry_price: float, current_price: float
    ) -> list[RiskEvent]:
        """Check single-asset loss from entry price.

        Args:
            asset: Asset symbol.
            entry_price: Price at which position was entered.
            current_price: Current market price.

        Returns:
            List of RiskEvents if loss thresholds are breached.
        """
        if entry_price <= 0:
            return []

        loss_pct = (entry_price - current_price) / entry_price
        if loss_pct <= 0:
            return []  # position is in profit

        events: list[RiskEvent] = []

        if loss_pct >= self._asset_hard:
            events.append(RiskEvent(
                event_type=EventType.SINGLE_ASSET_LOSS,
                severity=Severity.CRITICAL,
                triggered_value=loss_pct,
                limit_value=self._asset_hard,
                action_required=(
                    f"CLOSE {asset}: loss from entry {loss_pct:.2%} "
                    f"exceeds hard limit {self._asset_hard:.2%}"
                ),
                asset=asset,
            ))
            logger.warning(
                "SINGLE_ASSET_LOSS HARD: %s loss=%.2f%% limit=%.2f%%",
                asset, loss_pct * 100, self._asset_hard * 100,
            )
        elif loss_pct >= self._asset_soft:
            events.append(RiskEvent(
                event_type=EventType.SINGLE_ASSET_LOSS,
                severity=Severity.MEDIUM,
                triggered_value=loss_pct,
                limit_value=self._asset_soft,
                action_required=(
                    f"WARNING {asset}: loss from entry {loss_pct:.2%} "
                    f"exceeds soft limit {self._asset_soft:.2%}"
                ),
                asset=asset,
            ))
            logger.info(
                "SINGLE_ASSET_LOSS SOFT: %s loss=%.2f%%",
                asset, loss_pct * 100,
            )

        return events

    def get_daily_pnl_pct(self, current_nav: float) -> float:
        """Compute current daily P&L as a fraction.

        Args:
            current_nav: Current portfolio NAV.

        Returns:
            Daily P&L fraction (e.g., 0.025 = +2.5%, -0.01 = -1%).
        """
        if self.daily_pnl.day_start_nav <= 0:
            return 0.0
        return (current_nav - self.daily_pnl.day_start_nav) / self.daily_pnl.day_start_nav

    @property
    def is_halted(self) -> bool:
        """Whether the drawdown halt is currently active (no new buys)."""
        return self.drawdown.halt_active

    @property
    def sizing_multiplier(self) -> float:
        """Current sizing multiplier (1.0 = full, 0.5 = half after recovery).

        Returns:
            Fraction of normal position sizes to use.
        """
        return self.drawdown.resume_sizing_fraction

    def initialize_nav(self, starting_nav: float) -> None:
        """Set initial NAV for drawdown and daily tracking.

        Call once at bot startup or crash recovery.

        Args:
            starting_nav: Starting or recovered NAV.
        """
        self.drawdown.peak_nav = starting_nav
        self.daily_pnl.day_start_nav = starting_nav
        self.daily_pnl.current_day = datetime.now(timezone.utc).date()
        self.drawdown.resume_sizing_fraction = 1.0
        logger.info("Circuit breakers initialized: NAV=%.2f", starting_nav)

    # ------------------------------------------------------------------
    # Internal checks
    # ------------------------------------------------------------------

    def _maybe_reset_daily(self, current_nav: float, now: datetime) -> None:
        """Reset daily P&L tracking at 00:00 UTC."""
        today = now.date()
        if self.daily_pnl.current_day != today:
            logger.info(
                "Daily P&L reset: previous day_start=%.2f, "
                "new day_start=%.2f (current NAV)",
                self.daily_pnl.day_start_nav,
                current_nav,
            )
            self.daily_pnl.day_start_nav = current_nav
            self.daily_pnl.current_day = today
            self.daily_pnl.reduce_active = False
            self._daily_warning_logged = False

    def _check_drawdown(
        self, current_nav: float, now: datetime
    ) -> list[RiskEvent]:
        """Check portfolio drawdown from peak."""
        events: list[RiskEvent] = []
        if self.drawdown.peak_nav <= 0:
            return events

        drawdown = (self.drawdown.peak_nav - current_nav) / self.drawdown.peak_nav

        if drawdown >= self._dd_hard:
            if not self.drawdown.halt_active:
                self.drawdown.halt_active = True
                self.drawdown.halt_activated_at = now
                self.drawdown.resume_sizing_fraction = 0.0  # no new entries

                events.append(RiskEvent(
                    event_type=EventType.DRAWDOWN_HALT,
                    severity=Severity.CRITICAL,
                    triggered_value=drawdown,
                    limit_value=self._dd_hard,
                    action_required=(
                        f"HALT all new entries: drawdown {drawdown:.2%} "
                        f"exceeds hard limit {self._dd_hard:.2%}. "
                        f"Cooldown {self._halt_cooldown_hr}h."
                    ),
                ))
                logger.critical(
                    "DRAWDOWN HALT: dd=%.2f%% peak=%.2f current=%.2f",
                    drawdown * 100,
                    self.drawdown.peak_nav,
                    current_nav,
                )
            else:
                # Already halted — check if cooldown has elapsed
                self._check_halt_recovery(current_nav)

        elif drawdown >= self._dd_soft and not self._dd_warning_logged:
            events.append(RiskEvent(
                event_type=EventType.CIRCUIT_BREAKER_DD,
                severity=Severity.MEDIUM,
                triggered_value=drawdown,
                limit_value=self._dd_soft,
                action_required=(
                    f"WARNING: drawdown {drawdown:.2%} exceeds "
                    f"soft limit {self._dd_soft:.2%}"
                ),
            ))
            self._dd_warning_logged = True
            logger.warning(
                "Drawdown warning: dd=%.2f%% limit=%.2f%%",
                drawdown * 100, self._dd_soft * 100,
            )

        return events

    def _check_daily_pnl(self, current_nav: float) -> list[RiskEvent]:
        """Check daily P&L limits."""
        events: list[RiskEvent] = []
        daily_pnl = self.get_daily_pnl_pct(current_nav)

        if daily_pnl >= 0:
            return events  # Only check losses

        daily_loss = -daily_pnl  # Make positive for comparison

        if daily_loss >= self._daily_hard:
            if not self.daily_pnl.reduce_active:
                self.daily_pnl.reduce_active = True
                events.append(RiskEvent(
                    event_type=EventType.CIRCUIT_BREAKER_DAILY,
                    severity=Severity.CRITICAL,
                    triggered_value=daily_loss,
                    limit_value=self._daily_hard,
                    action_required=(
                        f"REDUCE all positions to 50%: daily loss "
                        f"{daily_loss:.2%} exceeds hard limit "
                        f"{self._daily_hard:.2%}"
                    ),
                ))
                logger.critical(
                    "DAILY LOSS HARD: loss=%.2f%% day_start=%.2f current=%.2f",
                    daily_loss * 100,
                    self.daily_pnl.day_start_nav,
                    current_nav,
                )

        elif daily_loss >= self._daily_soft and not self._daily_warning_logged:
            events.append(RiskEvent(
                event_type=EventType.CIRCUIT_BREAKER_DAILY,
                severity=Severity.MEDIUM,
                triggered_value=daily_loss,
                limit_value=self._daily_soft,
                action_required=(
                    f"WARNING: daily loss {daily_loss:.2%} exceeds "
                    f"soft limit {self._daily_soft:.2%}"
                ),
            ))
            self._daily_warning_logged = True
            logger.warning(
                "Daily loss warning: loss=%.2f%% limit=%.2f%%",
                daily_loss * 100, self._daily_soft * 100,
            )

        return events

    def _check_halt_recovery(self, current_nav: float) -> None:
        """Check if drawdown halt can be lifted.

        Recovery rules:
          1. Must wait at least halt_cooldown_hr hours.
          2. After cooldown: resume at resume_sizing_fraction.
          3. Full sizing only when within full_sizing_pct of peak.
        """
        if not self.drawdown.halt_active:
            return

        now = datetime.now(timezone.utc)

        # Check cooldown
        if self.drawdown.halt_activated_at is not None:
            elapsed_hr = (
                (now - self.drawdown.halt_activated_at).total_seconds() / 3600.0
            )
            if elapsed_hr < self._halt_cooldown_hr:
                return  # Still in cooldown

        # Cooldown elapsed — check recovery level
        drawdown = (self.drawdown.peak_nav - current_nav) / self.drawdown.peak_nav

        if drawdown <= self._full_sizing_pct:
            # Fully recovered
            self.drawdown.halt_active = False
            self.drawdown.resume_sizing_fraction = 1.0
            self.drawdown.halt_activated_at = None
            self._dd_warning_logged = False
            logger.info(
                "Drawdown halt LIFTED: NAV recovered to within %.2f%% of peak",
                drawdown * 100,
            )
        elif drawdown < self._dd_hard:
            # Partially recovered — resume at reduced sizing
            if self.drawdown.resume_sizing_fraction == 0.0:
                self.drawdown.resume_sizing_fraction = self._resume_fraction
                self.drawdown.halt_active = False
                logger.info(
                    "Drawdown halt PARTIALLY lifted: resuming at %.0f%% sizing "
                    "(dd=%.2f%%)",
                    self._resume_fraction * 100,
                    drawdown * 100,
                )
