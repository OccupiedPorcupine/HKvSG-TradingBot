"""Layer 3 — Rule-based regime classification.

Phase 1: Minimal vol guard only (BTC vol spike → defensive mode).
Phase 2: Full four-state regime with asymmetric transitions.

NO HMM. NO GARCH. No statistical models. No libraries beyond NumPy.
Deterministic rules using BTC trend + vol percentile + altcoin breadth +
contagion proxy.

Classification rules (Phase 2, in priority order):
    1. contagion_ratio > 0.80 AND avg_loss > 1% → HIGH_VOL_CRISIS (immediate)
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
from typing import Any, Optional

from src.regime.contagion import ContagionProbe, ContagionResult
from src.regime.regime_state import RegimeState, RegimeType, is_downgrade, is_upgrade

logger = logging.getLogger(__name__)


class RegimeDetector:
    """Rule-based market regime classifier.

    Phase 1: operates in vol-guard mode (simplified binary: normal/defensive).
    Phase 2: full four-state regime with transition logic.

    Usage::

        detector = RegimeDetector(config)
        # Every 5 minutes:
        detector.update(regime_inputs, contagion_result)
        state = detector.state
    """

    def __init__(self, config: dict[str, Any]) -> None:
        """Initialize regime detector from config.

        Args:
            config: Full config.yaml as dict.
        """
        self._config = config
        self._state = RegimeState()
        self._contagion_probe = ContagionProbe(
            return_window=config.get("regime", {})
            .get("contagion_return_window_min", 5)
        )

        # Classification thresholds (from config)
        thresholds = config.get("regime", {}).get("thresholds", {})
        self._contagion_ratio_crisis = thresholds.get(
            "contagion_ratio_crisis", 0.80
        )
        self._contagion_avg_loss_crisis = thresholds.get(
            "contagion_avg_loss_pct", 0.01
        )
        self._btc_vol_pct_crisis = thresholds.get(
            "btc_vol_percentile_crisis", 90
        )
        self._breadth_bull = thresholds.get("altcoin_breadth_bull", 0.55)
        self._breadth_bear = thresholds.get("altcoin_breadth_bear", 0.40)

        # Transition thresholds
        transitions = config.get("regime", {}).get("transitions", {})
        self._upgrade_bars = transitions.get("upgrade_confirmation_bars", 30)
        self._crisis_exit_contagion = transitions.get(
            "crisis_exit_contagion_below", 0.50
        )
        self._crisis_exit_vol = transitions.get("crisis_exit_vol_below", 70)
        self._crisis_exit_bars = transitions.get(
            "crisis_exit_confirmation_bars", 30
        )

        # Crisis exit tracking
        self._crisis_exit_streak: int = 0

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
    def contagion_probe(self) -> ContagionProbe:
        """Access to the contagion probe for 1-minute updates."""
        return self._contagion_probe

    def update(
        self,
        regime_inputs: "RegimeInputs",
        contagion: ContagionResult,
    ) -> RegimeState:
        """Run regime classification on latest inputs.

        Called every 5 minutes by the orchestrator. Contagion is computed
        separately every 1 minute and passed in.

        Args:
            regime_inputs: Feature inputs from Layer 2 (BTC returns,
                           vol percentile, altcoin breadth).
            contagion: Latest contagion probe result.

        Returns:
            Updated RegimeState.
        """
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

        return self._state

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
        inputs: "RegimeInputs",
        contagion: ContagionResult,
    ) -> RegimeType:
        """Determine candidate regime from current inputs.

        Rules applied in priority order — first match wins.

        Args:
            inputs: Regime inputs from Layer 2.
            contagion: Latest contagion result.

        Returns:
            Candidate RegimeType.
        """
        # Rule 1: Contagion crisis
        if (
            contagion.contagion_ratio > self._contagion_ratio_crisis
            and contagion.avg_loss > self._contagion_avg_loss_crisis
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
        inputs: "RegimeInputs",
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
            # But if we had a pending upgrade to somewhere else, cancel it
            if (
                self._state.transition_pending
                and self._state.transition_target != candidate
            ):
                self._state.cancel_upgrade()
            # If pending upgrade to same target, tick down
            if (
                self._state.transition_pending
                and self._state.transition_target == candidate
            ):
                # This shouldn't happen (candidate == current), but guard
                self._state.cancel_upgrade()
            self._crisis_exit_streak = 0
            return

        # --- Special: exiting CRISIS ---
        if current == RegimeType.HIGH_VOL_CRISIS:
            self._handle_crisis_exit(candidate, inputs, contagion)
            return

        # --- Downgrade: immediate ---
        if is_downgrade(current, candidate):
            logger.warning(
                "REGIME DOWNGRADE: %s → %s (immediate). "
                "btc_vol_pct=%.1f, breadth=%.3f, contagion=%.3f",
                current.value,
                candidate.value,
                self._state.btc_vol_percentile,
                inputs.altcoin_breadth or 0,
                contagion.contagion_ratio,
            )
            self._state.confirm_transition(candidate)
            self._crisis_exit_streak = 0
            return

        # --- Upgrade: gradual confirmation ---
        if is_upgrade(current, candidate):
            if not self._state.transition_pending:
                # Start confirmation window. This bar counts as the first
                # confirming bar, so remaining = total - 1.
                self._state.start_upgrade(
                    candidate, self._upgrade_bars - 1,
                )
                logger.info(
                    "REGIME UPGRADE pending: %s → %s "
                    "(need %d confirming bars, 1 counted)",
                    current.value,
                    candidate.value,
                    self._upgrade_bars,
                )
            elif self._state.transition_target == candidate:
                # Same target — tick down
                self._state.transition_bars_remaining -= 1
                if self._state.transition_bars_remaining <= 0:
                    logger.info(
                        "REGIME UPGRADE confirmed: %s → %s "
                        "(after %d bars)",
                        current.value,
                        candidate.value,
                        self._upgrade_bars,
                    )
                    self._state.confirm_transition(candidate)
            else:
                # Different upgrade target — restart
                self._state.start_upgrade(candidate, self._upgrade_bars)
                logger.info(
                    "REGIME UPGRADE retarget: %s → %s "
                    "(was targeting %s, restarting)",
                    current.value,
                    candidate.value,
                    self._state.transition_target.value
                    if self._state.transition_target
                    else "None",
                )
            return

        # Lateral move (shouldn't happen with current rules, but be safe)
        self._state.confirm_transition(candidate)

    def _handle_crisis_exit(
        self,
        candidate: RegimeType,
        inputs: "RegimeInputs",
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
                    "CRISIS EXIT confirmed after %d bars. "
                    "contagion=%.3f, btc_vol_pct=%.1f → %s",
                    self._crisis_exit_streak,
                    contagion.contagion_ratio,
                    btc_vol,
                    candidate.value,
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
