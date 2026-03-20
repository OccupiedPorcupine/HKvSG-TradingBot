"""Smoke tests — verify Phase 2 bot starts and runs one cycle without crash.

Run on EC2 instance before live trading to catch import errors, config
mismatches, and initialization failures. No network calls.

Two critical assertions in test_one_rebalance_cycle:
  sum(weights) <= 1.0  — catches weight-overflow bugs
  all weights >= 0.0   — catches scaling sign-flip bugs
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


# ===========================================================================
# TestPhase2Smoke
# ===========================================================================


class TestPhase2Smoke:
    """Smoke tests — verify Phase 2 initializes and runs one cycle without crash."""

    def test_config_has_all_phase2_keys(self):
        """All Phase 2 config sections present and parseable.
        Guards against: new config keys missing after a merge or deploy.
        """
        config = _load_config()
        required_keys = [
            "regime", "trend_penalty", "meme_pool",
            "dynamic_stops", "paxg", "endgame", "portfolio",
        ]
        missing = [k for k in required_keys if k not in config]
        assert not missing, (
            f"Phase 2 config keys missing from config.yaml: {missing}"
        )

        # Validate key nested values (config.yaml uses flat regime keys, not sub-dicts)
        regime = config["regime"]
        assert any(
            k in regime for k in ("breadth_bull_threshold", "thresholds")
        ), "regime must have breadth thresholds (flat or nested)"
        assert "schedule" in config["endgame"], "endgame.schedule missing"
        assert "allocation" in config["paxg"], "paxg.allocation missing"
        assert "min_stop_floor" in config["dynamic_stops"], (
            "dynamic_stops.min_stop_floor missing"
        )

    def test_all_phase2_imports(self):
        """All Phase 2 modules import without error.
        Guards against: circular imports, missing __init__.py, bad syntax.
        """
        from src.regime.detector import RegimeDetector
        from src.regime.regime_state import RegimeState, RegimeType
        from src.signals.meme_pool import MemePoolManager
        from src.signals.trend_penalty import TrendPenaltyEngine
        from src.portfolio.constructor import PortfolioConstructor, ConstructionResult
        from src.portfolio.endgame import EndgameManager
        from src.portfolio.paxg_allocator import PAXGAllocator
        from src.risk.trailing_stops import TrailingStopManager
        from src.utils.validation import validate_regime_inputs
        # Reaching here means all imports succeeded
        assert RegimeDetector is not None
        assert MemePoolManager is not None
        assert PortfolioConstructor is not None

    def test_full_component_initialization(self):
        """All Phase 2 components instantiate from config without crash.
        Guards against: config key renames breaking __init__ at startup.
        """
        from src.regime.detector import RegimeDetector
        from src.signals.meme_pool import MemePoolManager
        from src.signals.trend_penalty import TrendPenaltyEngine
        from src.portfolio.constructor import PortfolioConstructor
        from src.portfolio.endgame import EndgameManager
        from src.portfolio.paxg_allocator import PAXGAllocator
        from src.risk.trailing_stops import TrailingStopManager

        config = _load_config()

        regime = RegimeDetector(config)
        meme = MemePoolManager(config)
        endgame = EndgameManager(config)
        paxg = PAXGAllocator(config)

        trend_penalty_config = config.get("trend_penalty")
        assert trend_penalty_config is not None, "trend_penalty config missing"
        trend_penalty = TrendPenaltyEngine(config)

        constructor = PortfolioConstructor(
            config=config,
            regime_detector=regime,
            trend_penalty=trend_penalty,
            meme_pool=meme,
            endgame=endgame,
            paxg=paxg,
        )
        assert constructor is not None

        # Trailing stop with Phase 2 tightening
        mgr = TrailingStopManager(
            base_stops={"tier_1_3": 0.06},
            min_stop_floor=config.get("dynamic_stops", {}).get("min_stop_floor", 0.02),
            tightening_config=config.get("dynamic_stops"),
        )
        assert mgr is not None

    def test_one_rebalance_cycle(self):
        """Synthetic data through one full rebalance. No crash.

        CRITICAL assertions:
          - sum(weights) <= 1.0   catches weight-overflow bugs
          - all weights >= 0.0    catches scaling sign-flip bugs
        """
        from src.regime.detector import RegimeDetector
        from src.signals.meme_pool import MemePoolManager
        from src.signals.trend_penalty import TrendPenaltyEngine
        from src.portfolio.constructor import PortfolioConstructor
        from src.portfolio.endgame import EndgameManager
        from src.portfolio.paxg_allocator import PAXGAllocator

        config = _load_config()

        regime = RegimeDetector(config)
        meme = MemePoolManager(config)
        endgame = EndgameManager(config)
        paxg = PAXGAllocator(config)
        trend_penalty = TrendPenaltyEngine(config)

        constructor = PortfolioConstructor(
            config=config,
            regime_detector=regime,
            trend_penalty=trend_penalty,
            meme_pool=meme,
            endgame=endgame,
            paxg=paxg,
        )

        # Synthetic momentum scores for 10 assets (plausible)
        scores = {
            "BTC": 1.00, "ETH": 0.95, "BNB": 0.90, "LTC": 0.85, "ADA": 0.80,
            "LINK": 0.75, "DOT": 0.70, "NEAR": 0.65, "AAVE": 0.60, "UNI": 0.55,
        }

        result = constructor.construct(
            momentum_scores=scores,
            current_positions={},
            nav=1_000_000,
        )

        # CRITICAL: weight invariants must always hold
        total_weight = sum(result.target_weights.values())
        assert total_weight <= 1.0 + 1e-9, (
            f"Weights sum to {total_weight:.6f} — exceeds 1.0 (weight overflow)"
        )
        for sym, w in result.target_weights.items():
            assert w >= 0.0, (
                f"{sym} has negative weight {w:.6f} (scaling sign-flip)"
            )

        # Sanity: metadata populated
        assert "regime" in result.metadata, "Construction metadata missing 'regime'"
        assert "total_weight" in result.metadata, "Construction metadata missing 'total_weight'"

    def test_regime_detector_cold_start(self):
        """Fresh detector starts in MEAN_REVERT, not CRISIS or BULL.
        Guards against: config-driven defaults forcing a wrong initial state.
        """
        from src.regime.detector import RegimeDetector
        from src.regime.regime_state import RegimeType

        config = _load_config()
        detector = RegimeDetector(config)

        assert detector.current_regime == RegimeType.MEAN_REVERT, (
            f"Cold-start regime should be MEAN_REVERT, got {detector.current_regime.value}"
        )
        assert not detector.state.transition_pending, (
            "Cold-start should have no pending transition"
        )
        assert detector.minutes_in_current_regime == 0, (
            "Cold-start should have 0 bars in current regime"
        )


# ===========================================================================
# Run with pytest
# ===========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
