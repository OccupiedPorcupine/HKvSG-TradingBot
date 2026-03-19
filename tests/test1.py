import logging
from src.signals.momentum import MomentumSignal
from unittest.mock import MagicMock

logging.basicConfig(level=logging.INFO)

def run_test1():
    print("--- TEST 1: The 'Golden Stock' Selector ---")
    
    universe = {"BTC", "ETH", "DOGE", "SOL", "ADA"}
    
    # MOCK DATA: BTC is skyrocketing, everything else is bleeding
    momentum_scores = {
        "BTC": 3.5,   # The Golden Stock
        "ETH": -0.2,  # Garbage
        "DOGE": -1.5, # Garbage
        "SOL": 0.1,   # Mediocre
        "ADA": -0.8   # Garbage
    }
    
    # THE FIX: Create a "Shape-shifter" mock regime
    # We know from your logs earlier that "BULL" or "MEAN_REVERT" are valid states.
    fake_regime = MagicMock()
    fake_regime.current_regime.value = "BULL"
    fake_regime.current_regime.name = "BULL"
    
    # Initialize engine
    signal_engine = MomentumSignal({}, universe)
    
    # Run the engine with our fake regime
    selections = signal_engine.generate(momentum_scores, fake_regime)
    
    print(f"\nEngine Selections: {selections}")
    
    assert "BTC" in selections, "FAIL: Bot missed the Golden Stock."
    assert "DOGE" not in selections, "FAIL: Bot picked a garbage stock."
    print("\n✅ SUCCESS: The bot successfully isolated the mathematically best asset!")

if __name__ == "__main__":
    run_test1()
