"""Layer 3 — Rule-based regime classification.
regime
Phase 1: Minimal vol guard only (BTC vol spike → defensive mode).
Phase 2: Full four-state regime with asymmetric transitions.

NO HMM. NO GARCH. No statistical models. No libraries beyond NumPy.
Deterministic rules using BTC trend + vol percentile + altcoin breadth +
contagion proxy.

Classification rules (Phase 2, in priority order):
    1. contagion_ratio > 0.80 AND avg_loss > 1% → HIGH_VOL_CRISIS (immediate)
       Small portfolio (<6 positions): ratio > 0.90 AND avg_loss > 1.5%
    2. btc_vol_percentile > 90 → HIGH_VOL_CRISIS (immediate)
    3. btc_4h > 0 AND btc_24h > 0 AND breadth > 55% → TREND_BULL
    4. btc_4h < 0 AND btc_24h < 0 AND breadth < 40% → TREND_BEAR
    5. else → MEAN_REVERT

Transition rules (asymmetric):
    Downgrades: IMMEDIATE (no delay).
    Upgrades: 30-bar confirmation window.
    Crisis exit: contagion < 0.50 AND vol_pct < 70 for 30 consecutive bars.

All threshold values read from config.yaml.
"""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from src.regime.contagion import ContagionProbe, ContagionResult
from src.regime.regime_state import RegimeState, RegimeType, is_downgrade, is_upgrade
from src.utils.validation import validate_regime_inputs

logger = logging.getLogger(__name__)


class RegimeDetector:
    """Rule-based market regime classifier.

    Phase 1: operates in vol-guard mode (simplified binary: normal/defensive).
    Phase 2: full four-state regime with transition logic.

    Usage::

        detector = RegimeDetector(config)
        # Every 1 minute:
        state = detector.update(regime_inputs, held_positions, get_return_fn)
        regime = detector.current_regime
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize regime detector from config.

        Args:
            config: Full config.yaml as dict.
        """
        self._config = config
        self._state = RegimeState()
        self._cold_start = True
        self._contagion_probe = ContagionProbe(
            return_window=config.get("regime", {})
            .get("contagion_return_window_min", 5),
            small_portfolio_size=config.get("regime", {})
            .get("contagion_small_portfolio_size", 6),
        )

        # Classification thresholds (from config)
        regime_cfg = config.get("regime", {})
        thresholds = regime_cfg.get("thresholds", {})
        self._contagion_ratio_crisis = thresholds.get(
            "contagion_ratio_crisis",
            regime_cfg.get("contagion_ratio_threshold", 0.80),
        )
        self._contagion_avg_loss_crisis = thresholds.get(
            "contagion_avg_loss_pct",
            regime_cfg.get("contagion_loss_threshold", 0.01),
        )
        self._btc_vol_pct_crisis = thresholds.get(
            "btc_vol_percentile_crisis",
            regime_cfg.get("btc_vol_crisis_percentile", 90),
        )
        self._breadth_bull = thresholds.get(
            "altcoin_breadth_bull",
            regime_cfg.get("breadth_bull_threshold", 0.55),
        )
        self._breadth_bear = thresholds.get(
            "altcoin_breadth_bear",
            regime_cfg.get("breadth_bear_threshold", 0.40),
        )

        # Small portfolio thresholds
        self._small_portfolio_size = regime_cfg.get(
            "contagion_small_portfolio_size", 6
        )
        self._small_contagion_ratio = regime_cfg.get(
            "contagion_small_ratio_threshold", 0.90
        )
        self._small_contagion_loss = regime_cfg.get(
            "contagion_small_loss_threshold", 0.015
        )

        # Transition thresholds
        transitions = regime_cfg.get("transitions", {})
        self._upgrade_bars = transitions.get(
            "upgrade_confirmation_bars",
            regime_cfg.get("upgrade_persistence_minutes", 30),
        )
        self._crisis_exit_contagion = transitions.get(
            "crisis_exit_contagion_below", 0.50
        )
        self._crisis_exit_vol = transitions.get("crisis_exit_vol_below", 70)
        self._crisis_exit_bars = transitions.get(
            "crisis_exit_confirmation_bars",
            regime_cfg.get("crisis_exit_persistence_minutes", 30),
        )

        # Crisis exit tracking
        self._crisis_exit_streak: int = 0

        # Upgrade candidate tracking (datetime-based)
        self._upgrade_candidate: Optional[RegimeType] = None
        self._upgrade_candidate_since: Optional[datetime] = None

        # Phase 1 vol guard config
        vol_guard = config.get("phase1_vol_guard", {})
        self._vol_guard_median = vol_guard.get("btc_30d_median_vol", 0.0)
        self._vol_guard_multiplier = vol_guard.get("vol_spike_multiplier", 2.0)
        self._vol_guard_defensive_exposure = vol_guard.get(
            "defensive_max_exposure", 0.40
        )
        self._vol_guard_normal_exposure = vol_guard.get(
            "normal_max_exposure", 0.75
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def state(self) -> RegimeState:
        """Current regime state (read-only reference)."""
        return self._state

    @property
    def current_regime(self) -> RegimeType:
        """Read-only access to current state."""
        return self._state.current_regime

    @property
    def minutes_in_current_regime(self) -> int:
        """How long we've been in this regime (1 bar = 1 minute)."""
        return self._state.bars_in_current_regime

    @property
    def contagion_probe(self) -> ContagionProbe:
        """Access to the contagion probe for 1-minute updates."""
        return self._contagion_probe

    def update(
        self,
        regime_inputs: Any,
        held_positions: dict,
        get_return_fn: Callable[[str, int], Optional[float]],
    ) -> RegimeType:
        """Run regime classification on latest inputs.

        Called every 1 minute by the orchestrator.

        Args:
            regime_inputs: Feature inputs from FeatureEngine.get_regime_inputs().
            held_positions: Dict of currently held positions.
            get_return_fn: Callable(asset, minutes) -> float for contagion.

        Returns:
            Current RegimeState after transition logic applied.
        """
        # Cold-start logging
        if self._cold_start:
            logger.info(
                "REGIME COLD_START: defaulting to MEAN_REVERT, "
                "upgrades require %dmin persistence",
                self._upgrade_bars,
            )
            self._cold_start = False

        # Input validation
        if not validate_regime_inputs(
            regime_inputs.btc_4h_return if regime_inputs.btc_4h_return is not None else float("nan"),
            regime_inputs.btc_24h_return if regime_inputs.btc_24h_return is not None else float("nan"),
            regime_inputs.altcoin_breadth if regime_inputs.altcoin_breadth is not None else float("nan"),
            regime_inputs.btc_vol_percentile if regime_inputs.btc_vol_percentile is not None else 50.0, # change the bot state during the first 50 minutes to start buying major coins
        ):
            logger.warning(
                "REGIME: invalid inputs, keeping current regime %s",
                self._state.current_regime.value,
            )
            self._state.bars_in_current_regime += 1
            return self._state.current_regime

        # Compute contagion
        contagion = self._contagion_probe.compute(held_positions, get_return_fn)

        # Update contagion values in state
        self._state.contagion_proxy = contagion.contagion_ratio
        self._state.avg_loss = contagion.avg_loss
        self._state.btc_vol_percentile = (
            regime_inputs.btc_vol_percentile
            if regime_inputs.btc_vol_percentile is not None
            else 0.0
        )

        # Classify
        candidate = self._classify(regime_inputs, contagion)

        # Apply transition logic
        self._apply_transition(candidate, regime_inputs, contagion)

        # Increment bar counter
        self._state.bars_in_current_regime += 1

        return self._state.current_regime

    def update_phase1(
        self,
        btc_24h_vol: Optional[float],
    ) -> RegimeState:
        """Phase 1 minimal vol guard — simplified regime check.

        Uses BTC 24h realized volatility vs. historical median.
        If vol > 2x median: HIGH_VOL_CRISIS (defensive mode).
        Otherwise: TREND_BULL (normal mode).

        Args:
            btc_24h_vol: BTC 24-hour realized volatility, or None if
                         not yet computed (insufficient data).

        Returns:
            Updated RegimeState.
        """
        # If median vol is not calibrated (0.0), stay in normal mode
        if self._vol_guard_median <= 0 or btc_24h_vol is None:
            if self._state.current_regime != RegimeType.TREND_BULL:
                self._state.confirm_transition(RegimeType.TREND_BULL)
                logger.info(
                    "VOL_GUARD: median not set or no vol data — defaulting "
                    "to TREND_BULL"
                )
            self._state.bars_in_current_regime += 1
            return self._state

        threshold = self._vol_guard_median * self._vol_guard_multiplier

        if btc_24h_vol > threshold:
            if self._state.current_regime != RegimeType.HIGH_VOL_CRISIS:
                self._state.confirm_transition(RegimeType.HIGH_VOL_CRISIS)
                logger.warning(
                    "VOL_GUARD: BTC 24h vol %.4f > %.4f (%.1fx median) "
                    "— entering CRISIS",
                    btc_24h_vol,
                    threshold,
                    btc_24h_vol / self._vol_guard_median,
                )
        else:
            if self._state.current_regime != RegimeType.TREND_BULL:
                self._state.confirm_transition(RegimeType.TREND_BULL)
                logger.info(
                    "VOL_GUARD: BTC 24h vol %.4f <= %.4f — returning to "
                    "TREND_BULL",
                    btc_24h_vol,
                    threshold,
                )

        self._state.bars_in_current_regime += 1
        return self._state

    @property
    def vol_guard_max_exposure(self) -> float:
        """Phase 1: max crypto exposure based on vol guard state."""
        if self._state.current_regime == RegimeType.HIGH_VOL_CRISIS:
            return self._vol_guard_defensive_exposure
        return self._vol_guard_normal_exposure

    # ------------------------------------------------------------------
    # Classification (Phase 2)
    # ------------------------------------------------------------------

    def _classify(
        self,
        inputs: Any,
        contagion: ContagionResult,
    ) -> RegimeType:
        """Determine candidate regime from current inputs.

        Rules applied in priority order — first match wins.
        Uses raised thresholds for small portfolios (<6 positions).

        Args:
            inputs: Regime inputs from Layer 2.
            contagion: Latest contagion result.

        Returns:
            Candidate RegimeType.
        """
        # Determine contagion thresholds based on portfolio size
        if contagion.is_small_portfolio:
            ratio_threshold = self._small_contagion_ratio
            loss_threshold = self._small_contagion_loss
            
            # FIX: Micro-Portfolio Contagion Trap
            # If the bot only holds 1 or 2 assets, a normal 1.5% dip results in a 1.0 (100%) 
            # contagion ratio, falsely triggering a systemic crisis. 
            # We scale the loss threshold up to require a severe drop before panicking.
            if contagion.contagion_ratio >= 0.99:
                loss_threshold = max(loss_threshold, 0.03) # Require at least a 3% structural drop
        else:
            ratio_threshold = self._contagion_ratio_crisis
            loss_threshold = self._contagion_avg_loss_crisis

        # Rule 1: Contagion crisis
        if (
            contagion.contagion_ratio > ratio_threshold
            and contagion.avg_loss > loss_threshold
        ):
            return RegimeType.HIGH_VOL_CRISIS

        # Rule 2: BTC vol crisis
        btc_vol = inputs.btc_vol_percentile
        if btc_vol is not None and btc_vol > self._btc_vol_pct_crisis:
            return RegimeType.HIGH_VOL_CRISIS

        # Rule 3: Trend bull
        btc_4h = inputs.btc_4h_return
        btc_24h = inputs.btc_24h_return
        breadth = inputs.altcoin_breadth

        if (
            btc_4h is not None
            and btc_24h is not None
            and breadth is not None
            and btc_4h > 0
            and btc_24h > 0
            and breadth > self._breadth_bull
        ):
            return RegimeType.TREND_BULL

        # Rule 4: Trend bear
        if (
            btc_4h is not None
            and btc_24h is not None
            and breadth is not None
            and btc_4h < 0
            and btc_24h < 0
            and breadth < self._breadth_bear
        ):
            return RegimeType.TREND_BEAR

        # Rule 5: Default
        return RegimeType.MEAN_REVERT

    # ------------------------------------------------------------------
    # Transition logic (Phase 2)
    # ------------------------------------------------------------------

    def _apply_transition(
        self,
        candidate: RegimeType,
        inputs: Any,
        contagion: ContagionResult,
    ) -> None:
        """Apply asymmetric transition rules.

        Downgrades: immediate.
        Upgrades: require confirmation_bars consecutive confirming bars.
        Crisis exit: special rule — both contagion AND vol must normalize.

        Args:
            candidate: The regime classification wants to move to.
            inputs: Current regime inputs (for crisis exit check).
            contagion: Current contagion result (for crisis exit check).
        """
        current = self._state.current_regime

        # Same regime — no transition needed
        if candidate == current:
            # Cancel any pending upgrade to a different target
            if (
                self._state.transition_pending
                and self._state.transition_target != candidate
            ):
                self._state.cancel_upgrade()
                self._upgrade_candidate = None
                self._upgrade_candidate_since = None
            # If pending upgrade to same target (shouldn't happen), cancel
            if (
                self._state.transition_pending
                and self._state.transition_target == candidate
            ):
                self._state.cancel_upgrade()
                self._upgrade_candidate = None
                self._upgrade_candidate_since = None
            self._crisis_exit_streak = 0
            return

        # --- Special: exiting CRISIS ---
        if current == RegimeType.HIGH_VOL_CRISIS:
            self._handle_crisis_exit(candidate, inputs, contagion)
            return

        # --- Downgrade: immediate ---
        if is_downgrade(current, candidate):
            logger.warning(
                "REGIME TRANSITION: %s → %s (reason: downgrade, "
                "btc_4h=%.4f, breadth=%.2f)",
                current.value,
                candidate.value,
                inputs.btc_4h_return or 0,
                inputs.altcoin_breadth or 0,
            )
            self._state.confirm_transition(candidate)
            self._crisis_exit_streak = 0
            self._upgrade_candidate = None
            self._upgrade_candidate_since = None
            return

        # --- Upgrade: gradual confirmation ---
        if is_upgrade(current, candidate):
            if not self._state.transition_pending:
                # Start confirmation window
                self._state.start_upgrade(
                    candidate, self._upgrade_bars - 1,
                )
                self._upgrade_candidate = candidate
                self._upgrade_candidate_since = datetime.now(timezone.utc)
                logger.info(
                    "REGIME UPGRADE_PENDING: %s → %s (1/%d min elapsed)",
                    current.value,
                    candidate.value,
                    self._upgrade_bars,
                )
            elif self._state.transition_target == candidate:
                # Same target — tick down
                self._state.transition_bars_remaining -= 1
                elapsed = self._upgrade_bars - self._state.transition_bars_remaining
                if self._state.transition_bars_remaining <= 0:
                    logger.info(
                        "REGIME TRANSITION: %s → %s (reason: upgrade_confirmed, "
                        "btc_4h=%.4f, breadth=%.2f)",
                        current.value,
                        candidate.value,
                        inputs.btc_4h_return or 0,
                        inputs.altcoin_breadth or 0,
                    )
                    self._state.confirm_transition(candidate)
                    self._upgrade_candidate = None
                    self._upgrade_candidate_since = None
                else:
                    logger.info(
                        "REGIME UPGRADE_PENDING: %s → %s (%d/%d min elapsed)",
                        current.value,
                        candidate.value,
                        elapsed,
                        self._upgrade_bars,
                    )
            else:
                # Different upgrade target — restart
                self._state.start_upgrade(candidate, self._upgrade_bars)
                self._upgrade_candidate = candidate
                self._upgrade_candidate_since = datetime.now(timezone.utc)
                logger.info(
                    "REGIME UPGRADE_PENDING: %s → %s retarget "
                    "(was %s, restarting, 0/%d min elapsed)",
                    current.value,
                    candidate.value,
                    self._state.transition_target.value
                    if self._state.transition_target
                    else "None",
                    self._upgrade_bars,
                )
            return

        # Lateral move (shouldn't happen with current rules, but be safe)
        self._state.confirm_transition(candidate)

    def _handle_crisis_exit(
        self,
        candidate: RegimeType,
        inputs: Any,
        contagion: ContagionResult,
    ) -> None:
        """Handle exit from HIGH_VOL_CRISIS.

        Requires BOTH conditions for crisis_exit_confirmation_bars:
            - contagion_ratio < crisis_exit_contagion_below (0.50)
            - btc_vol_percentile < crisis_exit_vol_below (70)

        Args:
            candidate: Where classification wants to go.
            inputs: Current regime inputs.
            contagion: Current contagion result.
        """
        btc_vol = inputs.btc_vol_percentile or 0

        conditions_met = (
            contagion.contagion_ratio < self._crisis_exit_contagion
            and btc_vol < self._crisis_exit_vol
        )

        if conditions_met:
            self._crisis_exit_streak += 1
            if self._crisis_exit_streak >= self._crisis_exit_bars:
                logger.info(
                    "REGIME TRANSITION: HIGH_VOL_CRISIS → %s "
                    "(reason: crisis_exit_confirmed, btc_4h=%.4f, breadth=%.2f)",
                    candidate.value,
                    inputs.btc_4h_return or 0,
                    inputs.altcoin_breadth or 0,
                )
                self._state.confirm_transition(candidate)
                self._crisis_exit_streak = 0
            else:
                # Still waiting — show progress
                if not self._state.transition_pending:
                    self._state.start_upgrade(
                        candidate, self._crisis_exit_bars
                    )
                self._state.transition_bars_remaining = (
                    self._crisis_exit_bars - self._crisis_exit_streak
                )
        else:
            # Reset streak
            if self._crisis_exit_streak > 0:
                logger.info(
                    "CRISIS EXIT reset at %d/%d bars. "
                    "contagion=%.3f (need <%.2f), btc_vol=%.1f (need <%.0f)",
                    self._crisis_exit_streak,
                    self._crisis_exit_bars,
                    contagion.contagion_ratio,
                    self._crisis_exit_contagion,
                    btc_vol,
                    self._crisis_exit_vol,
                )
            self._crisis_exit_streak = 0
            self._state.cancel_upgrade()
