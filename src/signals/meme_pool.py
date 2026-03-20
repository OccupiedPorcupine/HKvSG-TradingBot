import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

class MemePoolManager:
    def __init__(self, config: dict):
        """Load pool config. Store allowed symbols as a frozenset for O(1) lookup."""
        meme_cfg = config.get('meme_pool', {})
        self.enabled = meme_cfg.get('enabled', False)
        self.symbols = frozenset(meme_cfg.get('symbols', []))
        self.max_allocation = meme_cfg.get('max_allocation_per_coin', 0.03)
        self.top_n = meme_cfg.get('top_n', 2)
        self.active_regimes = set(meme_cfg.get('active_regimes', ['TREND_BULL']))
        self.trailing_stop_pct = meme_cfg.get('trailing_stop_pct', 0.08)

    def rank_and_select(
        self,
        momentum_scores: dict[str, float],
        current_regime: str,
    ) -> list[dict]:
        """Rank meme coins by momentum and select top N.
        
        Returns list of allocation dicts:
        [{"symbol": "PEPE", "weight": 0.03, "stop_pct": 0.08}, ...]
        
        Returns empty list if:
        - regime not in active_regimes
        - meme_pool.enabled is False
        - no meme coins have valid (non-NaN, non-None) momentum scores
        
        Edge cases:
        - Fewer than top_n valid scores: return however many are valid
        - Ties in score: stable sort (arbitrary but deterministic)
        """
        if not self.enabled:
            return []

        if current_regime not in self.active_regimes:
            return []

        # Filter for valid meme scores
        valid_meme_scores = []
        for symbol, score in momentum_scores.items():
            if symbol in self.symbols:
                if score is not None and not math.isnan(score):
                    valid_meme_scores.append((symbol, score))

        if not valid_meme_scores:
            return []

        # Rank by momentum score descending
        # Python's sort is stable. We sort by symbol first for deterministic ties.
        valid_meme_scores.sort(key=lambda x: x[0]) # Deterministic secondary sort
        valid_meme_scores.sort(key=lambda x: x[1], reverse=True)

        selected = valid_meme_scores[:self.top_n]

        allocations = []
        for symbol, score in selected:
            allocations.append({
                "symbol": symbol,
                "weight": self.max_allocation,
                "stop_pct": self.trailing_stop_pct
            })
            logger.debug("MEME_POOL: selected %s (score=%.4f)", symbol, score)

        return allocations

    def is_meme_symbol(self, symbol: str) -> bool:
        """O(1) check if symbol belongs to meme pool."""
        return symbol in self.symbols
    
    def get_total_meme_exposure(self, allocations: list[dict]) -> float:
        """Sum of weights in allocation list. Max theoretical: top_n * max_allocation."""
        return sum(a['weight'] for a in allocations)


if __name__ == "__main__":
    # Setup minimal logging
    logging.basicConfig(level=logging.DEBUG)

    # Mock config
    config = {
        'meme_pool': {
            'enabled': True,
            'symbols': ["SHIB", "PEPE", "FLOKI", "WIF", "BONK", "PUMP", "PENGU"],
            'max_allocation_per_coin': 0.03,
            'top_n': 2,
            'active_regimes': ["TREND_BULL"],
            'trailing_stop_pct': 0.08
        }
    }

    manager = MemePoolManager(config)

    # 1. Returns top 2 in TREND_BULL with valid scores
    scores = {
        "SHIB": 0.5,
        "PEPE": 0.9,
        "FLOKI": 0.7,
        "BTC": 1.0,  # Not meme
        "DOGE": 0.95 # Not in config symbols
    }
    allocations = manager.rank_and_select(scores, "TREND_BULL")
    print(f"Test 1 (Top 2): {allocations}")
    assert len(allocations) == 2
    assert allocations[0]['symbol'] == "PEPE"
    assert allocations[1]['symbol'] == "FLOKI"

    # 2. Returns empty list in MEAN_REVERT, TREND_BEAR, HIGH_VOL_CRISIS
    for regime in ["MEAN_REVERT", "TREND_BEAR", "HIGH_VOL_CRISIS"]:
        allocations = manager.rank_and_select(scores, regime)
        print(f"Test 2 ({regime}): {allocations}")
        assert allocations == []

    # 3. Handles case where only 1 meme has valid score
    scores_one = {"SHIB": 0.5, "BTC": 1.0}
    allocations = manager.rank_and_select(scores_one, "TREND_BULL")
    print(f"Test 3 (Only 1): {allocations}")
    assert len(allocations) == 1
    assert allocations[0]['symbol'] == "SHIB"

    # 4. Returns empty list when enabled=False
    manager.enabled = False
    allocations = manager.rank_and_select(scores, "TREND_BULL")
    print(f"Test 4 (Enabled=False): {allocations}")
    assert allocations == []
    manager.enabled = True

    # 5. DOGE is excluded even if present in momentum_scores
    # Already tested in Test 1, but let's be explicit
    scores_doge = {"DOGE": 1.5, "SHIB": 0.1}
    allocations = manager.rank_and_select(scores_doge, "TREND_BULL")
    print(f"Test 5 (DOGE excluded): {allocations}")
    assert all(a['symbol'] != "DOGE" for a in allocations)

    # 6. NaN scores are filtered out
    scores_nan = {"SHIB": float('nan'), "PEPE": 0.8}
    allocations = manager.rank_and_select(scores_nan, "TREND_BULL")
    print(f"Test 6 (NaN filter): {allocations}")
    assert len(allocations) == 1
    assert allocations[0]['symbol'] == "PEPE"

    print("All tests passed!")
