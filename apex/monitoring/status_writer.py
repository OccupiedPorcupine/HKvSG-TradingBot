"""Atomic JSON status writer for the APEX trading bot.

Writes a single status.json file every main loop tick using atomic
write (write to .tmp, then os.replace) so readers never see partial files.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Module-level start time, set on first call to write_status
_start_time: float | None = None


def write_status(
    status_path: str | Path,
    *,
    regime: str,
    nav: float,
    pnl_daily_pct: float,
    pnl_total_pct: float,
    drawdown_pct: float,
    crypto_exposure_pct: float,
    cash_pct: float,
    num_positions: int,
    open_orders: int,
    positions: list[dict[str, Any]],
    recent_trades: list[dict[str, Any]],
    signal_health: dict[str, float],
    risk_flags: list[str],
    next_rebalance_utc: str,
    endgame_hours_remaining: float,
    loop_duration_ms: float,
    api_calls_remaining: int,
    errors_last_hour: int,
) -> None:
    """Write current bot state to a JSON file atomically.

    Args:
        status_path: Path to the output status.json file.
        All other args represent current bot state fields.
    """
    global _start_time
    if _start_time is None:
        _start_time = time.monotonic()

    now = datetime.now(timezone.utc)
    status = {
        "timestamp_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "uptime_seconds": round(time.monotonic() - _start_time),
        "regime": regime,
        "nav": round(nav, 2),
        "pnl_daily_pct": round(pnl_daily_pct, 4),
        "pnl_total_pct": round(pnl_total_pct, 4),
        "drawdown_pct": round(drawdown_pct, 4),
        "crypto_exposure_pct": round(crypto_exposure_pct, 2),
        "cash_pct": round(cash_pct, 2),
        "num_positions": num_positions,
        "open_orders": open_orders,
        "positions": positions,
        "recent_trades": recent_trades[-10:],  # keep last 10
        "signal_health": signal_health,
        "risk_flags": risk_flags,
        "next_rebalance_utc": next_rebalance_utc,
        "endgame_hours_remaining": round(endgame_hours_remaining, 1),
        "loop_duration_ms": round(loop_duration_ms, 1),
        "api_calls_remaining": api_calls_remaining,
        "errors_last_hour": errors_last_hour,
    }

    status_path = Path(status_path)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = status_path.with_suffix(".tmp")

    try:
        tmp_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
        os.replace(tmp_path, status_path)
    except OSError:
        logger.exception("Failed to write status file to %s", status_path)
