"""Contagion proxy computation — O(n) per bar.

Runs every 1-minute bar. Computes the fraction of held positions with
negative 5-minute returns and the average loss magnitude among those.

Small portfolio override (<6 positions): uses raised thresholds
(ratio 0.90, loss 1.5%) to avoid false positives with few positions.

This is a Layer 3 computation consumed by:
  - Regime detector (classification rule input)
  - Risk layer's ContagionMonitor (circuit breaker trigger)
"""

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from src.utils.validation import validate_contagion_inputs

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ContagionResult:
    """Output of a single contagion probe computation.

    Attributes:
        contagion_ratio: Fraction of held positions with negative 5-min return.
        avg_loss: Mean of abs(5-min return) for declining positions.
        negative_count: Number of positions with negative 5-min return.
        total_positions: Total number of held positions evaluated.
        is_small_portfolio: True if portfolio had < small_portfolio_size positions.
    """

    contagion_ratio: float
    avg_loss: float
    negative_count: int
    total_positions: int
    is_small_portfolio: bool = False


class ContagionProbe:
    """Computes contagion proxy from current positions and recent prices.

    The contagion proxy measures synchronized decline across held positions.
    High contagion (many positions falling together) signals systemic stress.

    Usage::

        probe = ContagionProbe(return_window=5)
        result = probe.compute(held_positions, get_return_fn)
        # result.contagion_ratio → fed to regime detector
        # result.avg_loss → fed to regime detector
    """

    def __init__(
        self,
        return_window: int = 5,
        small_portfolio_size: int = 6,
    ) -> None:
        """Initialize the contagion probe.

        Args:
            return_window: Return window in minutes for measuring decline.
            small_portfolio_size: Portfolios with fewer positions use
                raised thresholds to avoid false crisis triggers.
        """
        self.return_window = return_window
        self.small_portfolio_size = small_portfolio_size

    def compute(
        self,
        held_positions: dict,
        get_return_fn: Callable[[str, int], Optional[float]],
    ) -> ContagionResult:
        """Compute contagion ratio and average loss for held positions.

        Args:
            held_positions: Dict of currently held positions (keys = assets).
            get_return_fn: Callable(asset, window_minutes) -> return or None.
                           Typically FeatureEngine.get_return.

        Returns:
            ContagionResult with ratio and average loss.
        """
        # Validate inputs
        if not validate_contagion_inputs(held_positions, get_return_fn):
            return ContagionResult(
                contagion_ratio=0.0,
                avg_loss=0.0,
                negative_count=0,
                total_positions=0,
            )

        held_assets = list(held_positions.keys())

        if not held_assets:
            return ContagionResult(
                contagion_ratio=0.0,
                avg_loss=0.0,
                negative_count=0,
                total_positions=0,
            )

        negative_count = 0
        loss_sum = 0.0
        evaluated = 0
        is_small = len(held_assets) < self.small_portfolio_size

        for asset in held_assets:
            ret = get_return_fn(asset, self.return_window)
            if ret is None:
                continue

            evaluated += 1
            if ret < 0:
                negative_count += 1
                loss_sum += abs(ret)

        if evaluated == 0:
            return ContagionResult(
                contagion_ratio=0.0,
                avg_loss=0.0,
                negative_count=0,
                total_positions=0,
                is_small_portfolio=is_small,
            )

        ratio = negative_count / evaluated
        avg_loss = loss_sum / negative_count if negative_count > 0 else 0.0

        return ContagionResult(
            contagion_ratio=ratio,
            avg_loss=avg_loss,
            negative_count=negative_count,
            total_positions=evaluated,
            is_small_portfolio=is_small,
        )
