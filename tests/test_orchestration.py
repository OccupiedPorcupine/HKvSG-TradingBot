"""Tests for the orchestration layer."""

import asyncio
import anyio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.orchestration.safe_state import SystemState
from src.orchestration.scheduler import Scheduler
from src.orchestration.recovery import reconstruct_positions
from src.execution.position_tracker import PositionTracker

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
async def test_scheduler_overlap_prevention():
    """Test that the scheduler prevents job overlap."""
    system_state = SystemState()
    heartbeat_path = Path("/tmp/heartbeat")
    scheduler = Scheduler(system_state, heartbeat_path)
    
    mock_func = AsyncMock()
    # Mock function that takes some time
    async def slow_func():
        await asyncio.sleep(0.5)
        await mock_func()
        
    # Schedule every 0.1s, but job takes 0.5s
    await scheduler.schedule_job("test_job", 0.1, slow_func, immediate=True)
    
    # Wait for a bit
    await anyio.sleep(0.7)
    
    # Should only have run once because of overlap prevention
    assert mock_func.call_count == 1
    
    await scheduler.stop_all()

@pytest.mark.anyio
async def test_system_state_safe_mode():
    """Test safe-state transitions."""
    state = SystemState(max_consecutive_failures=2)
    
    # Report failure
    state.report_failure("test_job", "error")
    assert state.is_global_safe_mode is False
    assert state.can_rebalance is True
    
    # Second failure triggers safe mode
    state.report_failure("test_job", "error")
    assert state.is_global_safe_mode is True
    assert state.can_rebalance is False
    
    # Recovery
    state.report_success("test_job")
    # Per our implementation, it stays in safe mode for safety
    assert state.is_global_safe_mode is True

def test_position_reconstruction(tmp_path):
    """Test reconstructing positions from trade log."""
    trade_log = tmp_path / "trades.jsonl"
    tracker = PositionTracker(1000)
    
    # Create a mock trade log
    import json
    with open(trade_log, "w") as f:
        # Buy 1 BTC
        f.write(json.dumps({
            "asset": "BTC",
            "action": "NEW_ENTRY",
            "fill_price": 50000.0,
            "filled_quantity": 1.0,
            "commission_paid": 25.0, # 0.05%
            "order_type": "LIMIT"
        }) + "\n")
        # Buy 10 ETH
        f.write(json.dumps({
            "asset": "ETH",
            "action": "NEW_ENTRY",
            "fill_price": 3000.0,
            "filled_quantity": 10.0,
            "commission_paid": 15.0, # 0.05%
            "order_type": "LIMIT"
        }) + "\n")

    count = reconstruct_positions(tracker, trade_log)
    
    assert count == 2
    assert "BTC" in tracker.positions
    assert tracker.positions["BTC"].quantity == 1.0
    assert tracker.positions["ETH"].quantity == 10.0
    assert tracker.cash_balance == 1000 - (50000 + 25) - (30000 + 15)
