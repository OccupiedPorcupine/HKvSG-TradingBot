"""Config-driven factory for risk management components.

Builds TrailingStopManager, CircuitBreakerManager, ContagionMonitor,
PreTradeValidator, and the unified RiskManager from config.yaml.
"""

import logging
from typing import Optional

from apex.core.config import Config
from src.portfolio.factory import (
    build_asset_tier_map,
    build_tier_cap_defaults,
    build_tier_cap_overrides,
)
from src.risk.circuit_breakers import CircuitBreakerManager
from src.risk.contagion import ContagionMonitor
from src.risk.manager import RiskManager
from src.risk.pre_trade_checks import PreTradeValidator
from src.risk.trailing_stops import TrailingStopManager

logger = logging.getLogger(__name__)


def create_trailing_stop_manager(
    config: Config,
    phase: int = 1,
) -> TrailingStopManager:
    """Create a TrailingStopManager from config.yaml.

    Args:
        config: Loaded Config instance.
        phase: Current implementation phase.

    Returns:
        Configured TrailingStopManager.
    """
    base_stops = {
        "tier_1_3": config.get("risk.trailing_stops.tier_1_3", 0.06),
        "tier_4_5": config.get("risk.trailing_stops.tier_4_5", 0.08),
        "trump": config.get("risk.trailing_stops.trump", 0.10),
    }
    min_floor = config.get("risk.trailing_stops.minimum_floor", 0.02)

    # Phase 1: no dynamic tightening
    tightening_config = None
    if phase >= 2:
        tightening_config = config.get("risk.stop_tightening", {})

    return TrailingStopManager(
        base_stops=base_stops,
        min_stop_floor=min_floor,
        tightening_config=tightening_config,
    )


def create_circuit_breaker_manager(
    config: Config,
    starting_nav: float = 0.0,
) -> CircuitBreakerManager:
    """Create a CircuitBreakerManager from config.yaml.

    Args:
        config: Loaded Config instance.
        starting_nav: Initial NAV for drawdown tracking.

    Returns:
        Configured CircuitBreakerManager.
    """
    limits = config.get("risk.portfolio_limits", {})
    recovery = config.get("risk.drawdown_recovery", {})

    mgr = CircuitBreakerManager(
        drawdown_soft_warning=limits.get("drawdown_soft_warning", 0.05),
        drawdown_hard_halt=limits.get("drawdown_hard_halt", 0.08),
        daily_loss_soft_warning=limits.get("daily_loss_soft_warning", 0.03),
        daily_loss_hard_reduce=limits.get("daily_loss_hard_reduce", 0.05),
        single_asset_loss_soft=limits.get("single_asset_loss_soft", 0.04),
        single_asset_loss_hard=limits.get("single_asset_loss_hard", 0.06),
        halt_cooldown_hr=recovery.get("halt_cooldown_hr", 2.0),
        resume_sizing_fraction=recovery.get("resume_sizing_fraction", 0.50),
        full_sizing_within_pct_of_peak=recovery.get(
            "full_sizing_within_pct_of_peak", 0.04
        ),
    )

    if starting_nav > 0:
        mgr.initialize_nav(starting_nav)

    return mgr


def create_contagion_monitor(config: Config) -> ContagionMonitor:
    """Create a ContagionMonitor from config.yaml.

    Args:
        config: Loaded Config instance.

    Returns:
        Configured ContagionMonitor.
    """
    contagion = config.get("risk.contagion", {})
    return ContagionMonitor(
        ratio_threshold=contagion.get("ratio_threshold", 0.80),
        avg_loss_threshold=contagion.get("avg_loss_threshold_pct", 0.01),
        position_reduction_to=contagion.get("position_reduction_to", 0.20),
    )


def create_pre_trade_validator(config: Config) -> PreTradeValidator:
    """Create a PreTradeValidator from config.yaml.

    Args:
        config: Loaded Config instance.

    Returns:
        Configured PreTradeValidator.
    """
    return PreTradeValidator(
        tier_caps=build_tier_cap_overrides(config),
        asset_tier_map=build_asset_tier_map(config),
        tier_cap_defaults=build_tier_cap_defaults(config),
        max_crypto_exposure=config.get("portfolio.max_crypto_exposure", 0.90),
        redistribution_max_iterations=config.get(
            "tier_caps.redistribution_max_iterations", 5
        ),
    )


def create_risk_manager(
    config: Config,
    starting_nav: float = 0.0,
    phase: int = 1,
) -> RiskManager:
    """Create the unified RiskManager with all sub-components.

    This is the main entry point for the orchestrator to create
    the complete risk management stack.

    Args:
        config: Loaded Config instance.
        starting_nav: Initial NAV for drawdown tracking.
        phase: Current implementation phase.

    Returns:
        Configured RiskManager.
    """
    stops = create_trailing_stop_manager(config, phase=phase)
    breakers = create_circuit_breaker_manager(config, starting_nav=starting_nav)
    contagion = create_contagion_monitor(config)
    validator = create_pre_trade_validator(config)
    asset_tier_map = build_asset_tier_map(config)

    stop_distances = {
        "tier_1_3": config.get("risk.trailing_stops.tier_1_3", 0.06),
        "tier_4_5": config.get("risk.trailing_stops.tier_4_5", 0.08),
        "trump": config.get("risk.trailing_stops.trump", 0.10),
    }

    return RiskManager(
        stops=stops,
        breakers=breakers,
        contagion=contagion,
        validator=validator,
        asset_tier_map=asset_tier_map,
        stop_distances=stop_distances,
    )
