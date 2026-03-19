import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

class TrendPenaltyEngine:
    def __init__(self, config: dict):
        """Config keys: trend_penalty.*
        
        Maintain trend state (EMA60/240) to penalize assets in a downtrend.
        Includes broad downturn scaling to maintain ranking discrimination
        when the entire market is bearish.
        """
        self.config = config
        self._ema_short_period = config['trend_penalty']['ema_short_period']
        self._ema_long_period = config['trend_penalty']['ema_long_period']
        self._penalty_sigma = config['trend_penalty']['penalty_sigma']
        self._broad_threshold = config['trend_penalty']['broad_downturn_threshold']
        self._broad_penalty_sigma = config['trend_penalty']['broad_downturn_penalty_sigma']

        self._ema_short: dict[str, float] = {}
        self._ema_long: dict[str, float] = {}
        self._bars_seen: dict[str, int] = {}

        # Alpha = 2 / (N + 1)
        self._alpha_short = 2.0 / (self._ema_short_period + 1)
        self._alpha_long = 2.0 / (self._ema_long_period + 1)

    def update_price(self, symbol: str, price: float) -> None:
        """Call every 1 minute per asset. Updates EMAs incrementally."""
        if price is None or math.isnan(price):
            logger.warning("TREND_PENALTY: NaN price for %s, skipping update", symbol)
            return

        # Initialize or update short EMA
        if symbol not in self._ema_short:
            self._ema_short[symbol] = price
            self._ema_long[symbol] = price
            self._bars_seen[symbol] = 1
        else:
            self._ema_short[symbol] = (self._alpha_short * price) + \
                                     (1 - self._alpha_short) * self._ema_short[symbol]
            self._ema_long[symbol] = (self._alpha_long * price) + \
                                    (1 - self._alpha_long) * self._ema_long[symbol]
            self._bars_seen[symbol] += 1

    def apply_penalties(self, scores: dict[str, float]) -> dict[str, float]:
        """Apply trend penalties to momentum scores.
        Returns new dict with adjusted scores. Does not mutate input.
        Assets still warming up: no penalty applied.
        """
        adjusted_scores = scores.copy()
        bearish_fraction = self.get_bearish_fraction()
        
        # Determine penalty strength based on market breadth
        # If >70% of universe is bearish, reduce penalty to preserve ranking
        penalty_to_apply = self._penalty_sigma
        if bearish_fraction >= self._broad_threshold:
            penalty_to_apply = self._broad_penalty_sigma
            
        for asset, score in scores.items():
            if not self.is_warmed_up(asset):
                needed = self._ema_long_period - self._bars_seen.get(asset, 0)
                logger.debug("TREND_PENALTY WARMUP: %s needs %d more bars", asset, needed)
                continue

            ema_60 = self._ema_short[asset]
            ema_240 = self._ema_long[asset]

            if ema_60 < ema_240:
                adjusted_score = score + penalty_to_apply
                adjusted_scores[asset] = adjusted_score
                
                logger.debug(
                    "TREND_PENALTY: %s score %.4f → %.4f "
                    "(EMA60=%.2f < EMA240=%.2f)",
                    asset, score, adjusted_score, ema_60, ema_240,
                )

        return adjusted_scores

    def get_bearish_fraction(self) -> float:
        """Fraction of warmed-up assets where EMA short < EMA long."""
        warmed_up_assets = [
            s for s in self._ema_short.keys() 
            if self.is_warmed_up(s)
        ]
        
        if not warmed_up_assets:
            return 0.0
            
        bearish_count = sum(
            1 for s in warmed_up_assets 
            if self._ema_short[s] < self._ema_long[s]
        )
        
        return bearish_count / len(warmed_up_assets)

    def is_warmed_up(self, symbol: str) -> bool:
        """True if symbol has enough bars for penalty calculation."""
        return self._bars_seen.get(symbol, 0) >= self._ema_long_period


if __name__ == "__main__":
    # Unit Tests
    logging.basicConfig(level=logging.DEBUG)
    
    test_config = {
        'trend_penalty': {
            'ema_short_period': 60,
            'ema_long_period': 240,
            'penalty_sigma': -0.3,
            'broad_downturn_threshold': 0.70,
            'broad_downturn_penalty_sigma': -0.15
        }
    }
    
    engine = TrendPenaltyEngine(test_config)
    
    # 1. Warmup suppresses penalties
    engine.update_price("BTC", 100.0)
    engine.update_price("BTC", 90.0) # EMA60 < EMA240 immediately but not warmed up
    scores = {"BTC": 1.0}
    adjusted = engine.apply_penalties(scores)
    assert adjusted["BTC"] == 1.0, "Should not apply penalty during warmup"
    
    # 2. Penalty applied after warmup when EMA60 < EMA240
    # Seed BTC with 240 bars of declining price
    for i in range(240):
        engine.update_price("BTC", 100.0 - i)
    
    assert engine.is_warmed_up("BTC")
    assert engine._ema_short["BTC"] < engine._ema_long["BTC"]
    
    scores = {"BTC": 1.0}
    adjusted = engine.apply_penalties(scores)
    assert adjusted["BTC"] == 0.7, f"Expected 0.7, got {adjusted['BTC']}"
    
    # 3. No penalty when EMA60 > EMA240
    engine.update_price("ETH", 100.0)
    for i in range(240):
        engine.update_price("ETH", 100.0 + i)
        
    assert engine.is_warmed_up("ETH")
    assert engine._ema_short["ETH"] > engine._ema_long["ETH"]
    scores = {"ETH": 1.0}
    adjusted = engine.apply_penalties(scores)
    assert adjusted["ETH"] == 1.0, "Should not penalize uptrend"
    
    # 4. Broad downturn scaling
    # We need >70% bearish. Let's add 10 assets, 8 bearish.
    for i in range(10):
        sym = f"ALT_{i}"
        price = 100.0
        for _ in range(240):
            # First 8 are bearish, last 2 are bullish
            price = price - 1 if i < 8 else price + 1
            engine.update_price(sym, price)
            
    bearish_frac = engine.get_bearish_fraction()
    # BTC is bearish, ETH is bullish. Total assets: 1 (BTC) + 1 (ETH) + 10 (ALTs) = 12
    # Bearish: BTC (1) + 8 (ALTs) = 9
    # Bullish: ETH (1) + 2 (ALTs) = 3
    # Fraction: 9/12 = 0.75 (> 0.70 threshold)
    assert bearish_frac == 0.75
    
    scores = {"BTC": 1.0}
    adjusted = engine.apply_penalties(scores)
    assert adjusted["BTC"] == 0.85, f"Expected broad penalty 0.85, got {adjusted['BTC']}"
    
    # 5. NaN price handled gracefully
    engine.update_price("BTC", float('nan'))
    assert engine._bars_seen["BTC"] == 241, "Bars seen should not increment on NaN"
    
    print("All tests passed!")
