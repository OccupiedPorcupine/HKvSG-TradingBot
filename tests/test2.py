import sys
import os
import logging

# --- THE FIX 1: Tell Python to look in the parent directory for 'src' ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from src.regime.detector import RegimeDetector

logging.basicConfig(level=logging.INFO)

# A bulletproof mock class to fake the inputs from your FeatureEngine
class MockRegimeInputs:
    def __init__(self, btc_return, btc_vol, trend, breadth=0.5):
        # Extrapolate the 1h return to 4h and 24h
        self.btc_4h_return = btc_return * 4 
        self.btc_24h_return = btc_return * 24
        
        self.btc_vol_percentile = btc_vol
        # THE FIX 3: Use the correct property names expected by the real code
        self.btc_trend = "bullish" if trend > 0 else "bearish"
        self.altcoin_breadth = breadth
        
    def __getattr__(self, name):
        return 0.0

# Dummy position class to satisfy the contagion calculator
class MockPosition:
    pass

def run_test2():
    print("--- TEST 2: Market Regime Sensor ---")
    
    detector = RegimeDetector({})
    print(f"Initial State: {detector.state.current_regime.name}")

    # THE FIX 2: Create dummy positions for the internal contagion calculator
    dummy_positions = {f"COIN{i}": MockPosition() for i in range(10)}

    # SCENARIO 1: The Bull Market
    bull_inputs = MockRegimeInputs(btc_return=0.05, btc_vol=20.0, trend=1.0, breadth=0.8)
    
    # Mock return function: Tell the detector everything is up 5%
    def bull_returns(asset, window): 
        return 0.05
    
    # Pass the inputs, the dummy positions, and the return function
    detector.update(bull_inputs, dummy_positions, bull_returns)
    print(f"After Bull Data: {detector.state.current_regime.name}")
    
    is_healthy = "BULL" in detector.state.current_regime.name or "MEAN_REVERT" in detector.state.current_regime.name
    assert is_healthy, f"FAIL: Bot is not in a healthy regime. It is in {detector.state.current_regime.name}"

    # SCENARIO 2: The Flash Crash (Crisis)
    crash_inputs = MockRegimeInputs(btc_return=-0.10, btc_vol=95.0, trend=-1.0, breadth=0.1)
    
    # Mock return function: Tell the detector everything is crashing 10%
    def crash_returns(asset, window): 
        return -0.10
    
    # Run the crash data through to simulate a flash crash
    for _ in range(5): 
        detector.update(crash_inputs, dummy_positions, crash_returns)
        
    print(f"After Crash Data: {detector.state.current_regime.name}")
    
    assert "CRISIS" in detector.state.current_regime.name, f"FAIL: Bot failed to enter CRISIS mode! Stayed in {detector.state.current_regime.name}"
    
    print("\n✅ SUCCESS: Regime Detector accurately senses market shifts and pulls the emergency brake.")

if __name__ == "__main__":
    run_test2()