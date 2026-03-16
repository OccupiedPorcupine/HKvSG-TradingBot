"""SignalOutput dataclass — the complete output of Layer 4.

Consumed by Layer 5 (portfolio construction) to compute target weights.
"""

from dataclasses import dataclass, field

from src.regime.regime_state import RegimeState


@dataclass
class SignalOutput:
    """Complete signal output for one rebalance cycle.

    Attributes:
        main_pool_selections: Top-N Tier 1-3 assets by adjusted momentum
            score. Key = asset symbol, value = score. Assets not selected
            have no entry (score 0 means no position, never short).
        meme_pool_selections: Top 1-2 Tier 4 meme coins (TREND_BULL only).
            Empty dict in other regimes and in Phase 1.
        tier5_pool_selections: Top 1-2 Tier 5 opportunistic picks
            (TREND_BULL only, 4h return > 10%). Empty in Phase 1.
        ml_multipliers: Per-asset ML size multiplier (0.5-1.3x).
            All 1.0 in Phase 1-2.
        regime: Current RegimeState snapshot.
    """

    main_pool_selections: dict[str, float] = field(default_factory=dict)
    meme_pool_selections: dict[str, float] = field(default_factory=dict)
    tier5_pool_selections: dict[str, float] = field(default_factory=dict)
    ml_multipliers: dict[str, float] = field(default_factory=dict)
    regime: RegimeState = field(default_factory=RegimeState)

    @property
    def all_selected_assets(self) -> set[str]:
        """Union of all selected assets across all pools."""
        return (
            set(self.main_pool_selections)
            | set(self.meme_pool_selections)
            | set(self.tier5_pool_selections)
        )

    def to_log_dict(self) -> dict:
        """Serialize for JSON logging."""
        return {
            "main_pool": {
                k: round(v, 4) for k, v in self.main_pool_selections.items()
            },
            "meme_pool": {
                k: round(v, 4) for k, v in self.meme_pool_selections.items()
            },
            "tier5_pool": {
                k: round(v, 4) for k, v in self.tier5_pool_selections.items()
            },
            "ml_multipliers": {
                k: round(v, 2)
                for k, v in self.ml_multipliers.items()
                if v != 1.0
            },
            "regime": self.regime.to_log_dict(),
            "total_selected": len(self.all_selected_assets),
        }
