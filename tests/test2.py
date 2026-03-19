import logging
from src.regime.detector import RegimeDetector
from src.regime.contagion import ContagionResult

logging.basicConfig(level=logging.INFO)

# A bulletproof mock class to fake the inputs from your FeatureEngine
class MockRegimeInputs:
    def __init__(self, btc_return, btc_vol, trend):
        self.btc_1h_return = btc_return
        
        # Extrapolate the 1h return to 4h and 24h so the math scales correctly 
        # for Mun's multi-timeframe checks
        self.btc_4h_return = btc_return * 4 
        self.btc_24h_return = btc_return * 24
        
        self.btc_vol_percentile = btc_vol
        self.market_trend = trend
        
    # THE MAGIC FIX: If Mun's code asks for anything else (e.g., eth_return), 
    # don't crash. Just return 0.0 so the math keeps running.
    def __getattr__(self, name):
        return 0.0

def run_test2():
    print("--- TEST 2: Market Regime Sensor ---")
    
    # THE FIX: Pass an empty dictionary positionally, just like we did for MomentumSignal
    detector = RegimeDetector({})
    
    print(f"Initial State: {detector.state.current_regime.name}")

    # SCENARIO 1: The Bull Market
    # BTC is up 5%, volatility is low, contagion is 0
    bull_inputs = MockRegimeInputs(btc_return=0.05, btc_vol=20.0, trend=1.0)
    bull_contagion = ContagionResult(contagion_ratio=0.0, avg_loss=0.0, negative_count=0, total_positions=10)
    
    detector.update(bull_inputs, bull_contagion)
    print(f"After Bull Data: {detector.state.current_regime.name}")
    
    # We check if it's generally in a healthy state (Bull or Mean Reverting)
    is_healthy = "BULL" in detector.state.current_regime.name or "MEAN_REVERT" in detector.state.current_regime.name
    assert is_healthy, f"FAIL: Bot is not in a healthy regime. It is in {detector.state.current_regime.name}"

    # SCENARIO 2: The Flash Crash (Crisis)
    # BTC drops 10%, high vol, 90% of assets are crashing
    crash_inputs = MockRegimeInputs(btc_return=-0.10, btc_vol=95.0, trend=-1.0)
    crash_contagion = ContagionResult(contagion_ratio=0.9, avg_loss=-0.08, negative_count=9, total_positions=10)
    
    # Run the crash data through a few times to simulate sustained panic (overcoming any hysteresis buffers)
    for _ in range(3): 
        detector.update(crash_inputs, crash_contagion)
        
    print(f"After Crash Data: {detector.state.current_regime.name}")
    
    # Verify it pulled the emergency brake
    assert "CRISIS" in detector.state.current_regime.name, f"FAIL: Bot failed to enter CRISIS mode! Stayed in {detector.state.current_regime.name}"
    
    print("\n✅ SUCCESS: Regime Detector accurately senses market shifts and pulls the emergency brake.")

if __name__ == "__main__":
    run_test2()
