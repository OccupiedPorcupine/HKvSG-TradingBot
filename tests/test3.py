import sys
import os
import logging

# --- THE FIX 1: Tell Python to look in the parent directory for 'src' ---
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from pathlib import Path
from src.execution.position_tracker import PositionTracker, Position
from src.risk.factory import create_risk_manager
from apex.core.config import Config

logging.basicConfig(level=logging.INFO)

def run_test3():
    print("--- TEST 3: Take-Profit / Stop-Loss Thresholds ---")
    
    config = Config(Path("config.yaml")) 
    tracker = PositionTracker(starting_capital=50000.0)
    risk_manager = create_risk_manager(config, starting_nav=50000.0, phase=1)

    # 2. Fake a holding using MUN'S EXACT VARIABLES
    tracker.positions["ETH"] = Position(
        asset="ETH",
        quantity=10.0,
        cost_basis=3000.0,              # Correct name
        current_price=3000.0,           # Correct name
        peak_price_since_entry=3000.0   # Added the missing required field!
    )
    
    # 3. SCENARIO A: The price of ETH crashes to $2,500 (16% drop)
    crash_prices = {"ETH": 2500.0}
    
    print("\nSimulating ETH crash from $3000 to $2500...")
    risk_events = risk_manager.tick(
        current_prices=crash_prices,
        tracker=tracker,
        daily_pnl_pct=-0.10 
    )
    
    # 4. Verify the Risk Manager screamed "SELL!"
    # We use Mun's exact logic from manager.py: check for 'is_critical'
    critical_sell_assets = [event.asset for event in risk_events if event.is_critical]
    print(f"\nCritical Assets to Dump: {critical_sell_assets}")
    
    assert "ETH" in critical_sell_assets, "FAIL: Risk Manager did not trigger a CRITICAL sell for the crashing asset!"
    print("\n✅ SUCCESS: The bot successfully triggered a defensive sell threshold.")

if __name__ == "__main__":
    run_test3()
