"""APEX Crash Recovery & State Reconstruction.

Handles reloading price history from Parquet snapshots and reconstructing
portfolio positions from the JSON-lines trade log.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from src.execution.position_tracker import PositionTracker

logger = logging.getLogger(__name__)


def reconstruct_positions(
    tracker: PositionTracker, trade_log_path: Path
) -> int:
    """Reconstruct portfolio positions by replaying the trade log.

    Reads trades.jsonl line by line and applies confirmed fills to the
    position tracker. This allows the bot to resume with correct cost
    basis and quantities after a crash.

    Args:
        tracker: The PositionTracker to populate.
        trade_log_path: Path to the JSONL trade log.

    Returns:
        Number of fill entries processed.
    """
    if not trade_log_path.exists():
        logger.warning("Trade log not found at %s. Starting with empty positions.", trade_log_path)
        return 0

    logger.info("Reconstructing positions from %s...", trade_log_path)
    
    fill_count = 0
    try:
        with open(trade_log_path, "r") as f:
            for line in f:
                if not line.strip():
                    continue
                
                try:
                    entry = json.loads(line)
                    
                    # Only process entries with fill information
                    asset = entry.get("asset")
                    action = entry.get("action")
                    fill_price = entry.get("fill_price")
                    filled_qty = entry.get("filled_quantity")
                    commission_paid = entry.get("commission_paid")
                    
                    if asset and fill_price is not None and filled_qty is not None:
                        # Calculate effective commission percentage
                        # (Defaults to 0.05% if commission_paid is missing or 0)
                        comm_pct = 0.0
                        if commission_paid and fill_price and filled_qty:
                            comm_pct = commission_paid / (fill_price * filled_qty)
                        elif entry.get("order_type") == "LIMIT":
                            comm_pct = 0.0005
                        elif entry.get("order_type") == "EMERGENCY_MARKET_ORDER":
                            comm_pct = 0.001
                            
                        if action in ("NEW_ENTRY", "INCREASE"):
                            tracker.on_buy_fill(asset, filled_qty, fill_price, comm_pct)
                            fill_count += 1
                        elif action in ("DECREASE", "EXIT"):
                            tracker.on_sell_fill(asset, filled_qty, fill_price, comm_pct)
                            fill_count += 1
                            
                except (json.JSONDecodeError, KeyError, TypeError) as e:
                    logger.warning("Skipping malformed trade log line: %s", e)
                    continue
                    
        logger.info(
            "Position reconstruction complete. Processed %d fills. "
            "Current NAV: $%.2f, Positions: %d",
            fill_count, tracker.nav, len(tracker.positions)
        )
        return fill_count
        
    except Exception as e:
        logger.error("Failed to reconstruct positions: %s", e)
        # We don't raise here — better to start with potentially stale 
        # but safe empty state than to crash the recovery process.
        return fill_count
