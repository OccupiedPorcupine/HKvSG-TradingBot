"""Input validation for Phase 2 market data.

Prevents silent failures from NaN, None, or out-of-range values in
derived market metrics. Each Phase 2 module calls these before acting
on inputs.
"""
import math
import logging

logger = logging.getLogger(__name__)


def validate_regime_inputs(
    btc_4h_return: float,
    btc_24h_return: float,
    altcoin_breadth: float,
    btc_vol_percentile: float,
) -> bool:
    """Validate all inputs to regime classification.

    Returns True if all inputs are valid. Logs specific failures.
    Called by RegimeDetector.update() before classification.
    """
    if btc_4h_return is None or math.isnan(btc_4h_return):
        logger.error("VALIDATION: btc_4h_return is None or NaN")
        return False
    if btc_24h_return is None or math.isnan(btc_24h_return):
        logger.error("VALIDATION: btc_24h_return is None or NaN")
        return False
    if altcoin_breadth is None or math.isnan(altcoin_breadth) or not (0.0 <= altcoin_breadth <= 1.0):
        logger.error("VALIDATION: altcoin_breadth out of range [0.0, 1.0]")
        return False
    if btc_vol_percentile is None or math.isnan(btc_vol_percentile) or not (0 <= btc_vol_percentile <= 100):
        logger.error("VALIDATION: btc_vol_percentile out of range [0, 100]")
        return False
    return True


def validate_contagion_inputs(
    held_positions: dict,
    get_return_fn=None,
) -> bool:
    """Validate inputs to contagion proxy calculation.

    Returns True if valid. Handles edge case: empty portfolio
    (no positions held) is valid but should skip contagion calc.

    Args:
        held_positions: Dict of currently held positions.
        get_return_fn: Callable(asset, minutes) -> float, or None.
    """
    if held_positions is None:
        logger.error("VALIDATION: held_positions is None")
        return False
    if not isinstance(held_positions, dict):
        logger.error("VALIDATION: held_positions must be a dict")
        return False
    if get_return_fn is not None and not callable(get_return_fn):
        logger.error("VALIDATION: get_return_fn must be callable")
        return False
    return True


def validate_momentum_scores(
    scores: dict[str, float],
) -> dict[str, float]:
    """Filter out assets with NaN or None momentum scores.

    Returns cleaned dict with invalid entries removed.
    Logs count of removed entries at WARNING level.
    """
    if scores is None:
        logger.warning("VALIDATION: scores dict is None")
        return {}

    cleaned = {}
    removed_count = 0
    for asset, score in scores.items():
        if score is None or math.isnan(score):
            removed_count += 1
        else:
            cleaned[asset] = score

    if removed_count > 0:
        logger.warning("VALIDATION: removed %d invalid momentum scores", removed_count)

    return cleaned


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that returns default instead of raising on zero/NaN denominator."""
    if denominator == 0 or math.isnan(denominator) or math.isinf(denominator):
        return default
    result = numerator / denominator
    if math.isnan(result) or math.isinf(result):
        return default
    return result
