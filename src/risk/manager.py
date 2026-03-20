"""Unified risk manager — coordinates all Layer 6 risk checks.

Provides a single entry point for the orchestrator to call on two cadences:
  1. Every 1-minute bar: tick() — trailing stops, circuit breakers, contagion
  2. Every rebalance: pre_trade_check() — validate target weights before execution

Syncs trailing stop state with PositionTracker on every tick.
"""

import logging
from typing import Optional

from src.execution.position_tracker import Position, PositionTracker
from src.portfolio.factory import get_base_stop_for_asset
from src.risk.circuit_breakers import CircuitBreakerManager
from src.risk.contagion import ContagionMonitor
from src.risk.pre_trade_checks import PreTradeValidator
from src.risk.risk_event import RiskEvent, Severity
from src.risk.trailing_stops import TrailingStopManager

logger = logging.getLogger(__name__)


class RiskManager:
    """Unified risk manager for APEX.

    Coordinates trailing stops, circuit breakers, contagion monitoring,
    and pre-trade validation. Provides a clean interface for the orchestrator.

    Usage::

        # Every 1-minute bar:
        risk_events = risk_mgr.tick(current_prices, position_tracker)

        # Every rebalance:
        fixed_weights, events = risk_mgr.pre_trade_check(
            target_weights, max_deployment
        )

    Attributes:
        stops: Trailing stop manager.
        breakers: Circuit breaker manager.
        contagion: Contagion monitor (Phase 2+).
        validator: Pre-trade validator.
    """

    def __init__(
        self,
        stops: TrailingStopManager,
        breakers: CircuitBreakerManager,
        contagion: ContagionMonitor,
        validator: PreTradeValidator,
        asset_tier_map: dict[str, str],
        stop_distances: dict[str, float],
    ) -> None:
        """Initialize the unified risk manager.

        Args:
            stops: Trailing stop manager instance.
            breakers: Circuit breaker manager instance.
            contagion: Contagion monitor instance.
            validator: Pre-trade validator instance.
            asset_tier_map: Map of asset -> tier key (for stop distance lookup).
            stop_distances: Map of tier key -> stop distance
                (e.g., {"tier_1_3": 0.06, "tier_4_5": 0.08, "trump": 0.10}).
        """
        self.stops = stops
        self.breakers = breakers
        self.contagion = contagion
        self.validator = validator
        self._asset_tier_map = asset_tier_map
        self._stop_distances = stop_distances

    def tick(
        self,
        current_prices: dict[str, float],
        tracker: PositionTracker,
        contagion_ratio: float = 0.0,
        avg_loss: float = 0.0,
        daily_pnl_pct: float = 0.0,
        endgame_stop_override: Optional[float] = None,
    ) -> list[RiskEvent]:
        """Run all 1-minute risk checks.

        Called every bar by the orchestrator. Syncs stop tracking with
        PositionTracker, then checks all risk conditions.

        Args:
            current_prices: Map of asset -> current price.
            tracker: PositionTracker with current positions.
            contagion_ratio: Contagion proxy from Layer 3 (0.0-1.0).
            avg_loss: Average loss of declining positions.
            daily_pnl_pct: Current daily P&L from circuit breakers.
            endgame_stop_override: End-game stop override, or None.

        Returns:
            Sorted list of RiskEvents (highest priority first).
        """
        events: list[RiskEvent] = []

        # --- Sync stop tracking with position tracker ---
        self._sync_stops(tracker, current_prices)

        # --- 1. Trailing stops ---
        stop_events = self.stops.check_all(
            current_prices, endgame_stop_override
        )
        events.extend(stop_events)

        # --- 2. Circuit breakers (portfolio-level) ---
        nav = tracker.nav
        breaker_events = self.breakers.check(nav)
        events.extend(breaker_events)

        # --- 3. Single-asset loss checks ---
        for asset, pos in tracker.positions.items():
            asset_events = self.breakers.check_single_asset_loss(
                asset, pos.cost_basis, pos.current_price
            )
            events.extend(asset_events)

        # --- 4. Contagion (Phase 2+, no-op if ratio is 0) ---
        if contagion_ratio > 0:
            contagion_event = self.contagion.check(contagion_ratio, avg_loss)
            if contagion_event is not None:
                events.extend([contagion_event])

        # Sort by priority (highest first)
        events.sort()

        if events:
            critical = [e for e in events if e.is_critical]
            if critical:
                logger.warning(
                    "Risk tick: %d events (%d CRITICAL)",
                    len(events), len(critical),
                )

        return events

    def pre_trade_check(
        self,
        target_weights: dict[str, float],
        max_deployment: float,
    ) -> tuple[dict[str, float], list[RiskEvent]]:
        """Validate target weights before passing to execution.

        Called every rebalance. Fixes any constraint violations.

        Args:
            target_weights: Proposed target weights from portfolio constructor.
            max_deployment: Maximum allowed crypto deployment.

        Returns:
            Tuple of (corrected weights, list of violation events).
        """
        return self.validator.validate_and_fix(target_weights, max_deployment)

    def get_risk_exit_assets(self, events: list[RiskEvent]) -> set[str]:
        """Extract the set of assets that need risk-triggered exits.

        These assets bypass turnover constraint and minimum trade threshold.

        Args:
            events: List of RiskEvents from tick().

        Returns:
            Set of asset symbols that need immediate exits.
        """
        exits: set[str] = set()
        for event in events:
            if event.is_critical and event.asset is not None:
                exits.add(event.asset)
        return exits

    def on_position_opened(
        self, asset: str, entry_price: float
    ) -> None:
        """Notify risk manager that a new position was opened.

        Opens trailing stop tracking for the asset.

        Args:
            asset: Asset symbol.
            entry_price: Fill price.
        """
        base_stop = self._get_stop_distance(asset)
        self.stops.open_position(asset, entry_price, base_stop)

    def on_position_closed(self, asset: str) -> None:
        """Notify risk manager that a position was fully closed.

        Removes trailing stop tracking for the asset.

        Args:
            asset: Asset symbol.
        """
        self.stops.close_position(asset)

    @property
    def is_halted(self) -> bool:
        """Whether drawdown halt is active (no new buys)."""
        return self.breakers.is_halted

    @property
    def sizing_multiplier(self) -> float:
        """Current sizing multiplier from circuit breakers."""
        return self.breakers.sizing_multiplier

    def get_daily_pnl_pct(self, current_nav: float) -> float:
        """Convenience: get daily P&L from circuit breaker state."""
        return self.breakers.get_daily_pnl_pct(current_nav)

    def initialize_nav(self, starting_nav: float) -> None:
        """Set initial NAV for circuit breakers."""
        self.breakers.initialize_nav(starting_nav)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _sync_stops(
        self,
        tracker: PositionTracker,
        current_prices: dict[str, float],
    ) -> None:
        """Sync trailing stop tracking with PositionTracker.

        Opens stops for new positions, closes stops for exited positions,
        and syncs peak prices from PositionTracker where it has a higher peak.

        Args:
            tracker: Current PositionTracker state.
            current_prices: Latest prices.
        """
        tracked_assets = set(self.stops.positions.keys())
        held_assets = set(tracker.positions.keys())

        # Open stops for new positions not yet tracked
        # Open stops for new positions not yet tracked
        for asset in held_assets - tracked_assets:
            pos = tracker.positions[asset]
            base_stop = self._get_stop_distance(asset)
            
            # Use current market price if cost_basis is missing (e.g., initial startup sync)
            initial_price = pos.cost_basis
            if initial_price <= 0.0 and asset in current_prices:
                initial_price = current_prices[asset]
                
            self.stops.open_position(asset, initial_price, base_stop)
            
            # Sync peak from position tracker (may be higher if we missed bars)
            if pos.peak_price_since_entry > initial_price:
                self.stops.positions[asset].peak_price = pos.peak_price_since_entry

        # Close stops for positions no longer held
        for asset in tracked_assets - held_assets:
            self.stops.close_position(asset)

        # Sync peak prices (position tracker may have higher peaks from fills)
        for asset in held_assets & tracked_assets:
            pos = tracker.positions[asset]
            stop = self.stops.positions[asset]
            if pos.peak_price_since_entry > stop.peak_price:
                stop.peak_price = pos.peak_price_since_entry

    def _get_stop_distance(self, asset: str) -> float:
        """Look up base stop distance for an asset."""
        if asset == "TRUMP":
            return self._stop_distances.get("trump", 0.10)

        tier = self._asset_tier_map.get(asset, "tier_1_2")
        if tier in ("tier_4_meme", "tier_5_obscure"):
            return self._stop_distances.get("tier_4_5", 0.08)

        return self._stop_distances.get("tier_1_3", 0.06)
