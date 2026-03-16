#!/usr/bin/env python3
"""Live terminal dashboard for the APEX trading bot.

Reads status.json every 2 seconds and displays a formatted view.
Run in a separate tmux pane alongside the bot.

Usage:
    python scripts/dashboard.py
    python scripts/dashboard.py --status-file logs/status.json
    python scripts/dashboard.py --interval 5
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATUS_PATH = "logs/status.json"
DEFAULT_INTERVAL = 2


def load_status(path: Path) -> dict | None:
    """Load status.json, returning None if missing or corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def fmt_pct(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{val * 100:.2f}%"


def fmt_usd(val: float) -> str:
    return f"${val:,.0f}"


def staleness_seconds(ts_str: str) -> int:
    """Return seconds since the given UTC timestamp."""
    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        return int((datetime.now(timezone.utc) - ts).total_seconds())
    except (ValueError, TypeError):
        return -1


def render(status: dict | None, width: int = 60) -> str:
    """Render the dashboard as a string."""
    border = "═" * (width - 2)
    lines: list[str] = []

    def row(text: str) -> None:
        lines.append(f"║  {text:<{width - 5}}║")

    def sep() -> None:
        lines.append(f"╠{border}╣")

    if status is None:
        lines.append(f"╔{border}╗")
        row("APEX Trading Bot — OFFLINE")
        row("Waiting for status.json...")
        lines.append(f"╚{border}╝")
        return "\n".join(lines)

    ts = status.get("timestamp_utc", "?")
    stale = staleness_seconds(ts)
    stale_str = f"{stale}s ago" if stale >= 0 else "?"

    bot_status = "RUNNING" if stale < 120 else "STALE"
    if stale > 300:
        bot_status = "POSSIBLY DOWN"

    lines.append(f"╔{border}╗")
    row(f"APEX Trading Bot — Live Dashboard")
    row(f"Status: {bot_status} | Regime: {status.get('regime', '?')}")
    row(f"Last Update: {ts} ({stale_str})")
    sep()

    nav = fmt_usd(status.get("nav", 0))
    daily = fmt_pct(status.get("pnl_daily_pct", 0))
    total = fmt_pct(status.get("pnl_total_pct", 0))
    row(f"NAV: {nav}  |  Daily P&L: {daily}  |  Total: {total}")

    dd = fmt_pct(status.get("drawdown_pct", 0))
    exp = f"{status.get('crypto_exposure_pct', 0):.1f}%"
    cash = f"{status.get('cash_pct', 0):.1f}%"
    row(f"Drawdown: {dd}  |  Exposure: {exp}  |  Cash: {cash}")

    npos = status.get("num_positions", 0)
    nord = status.get("open_orders", 0)
    nerr = status.get("errors_last_hour", 0)
    row(f"Positions: {npos}  |  Open Orders: {nord}  |  Errors: {nerr}")

    endgame = status.get("endgame_hours_remaining", "?")
    loop_ms = status.get("loop_duration_ms", "?")
    api_left = status.get("api_calls_remaining", "?")
    row(f"Endgame: {endgame}h  |  Loop: {loop_ms}ms  |  API left: {api_left}")

    # Positions
    positions = status.get("positions", [])
    if positions:
        sep()
        row("TOP POSITIONS")
        sorted_pos = sorted(
            positions, key=lambda x: x.get("weight_pct", 0), reverse=True
        )
        for p in sorted_pos[:8]:
            asset = p.get("asset", "?")
            weight = f"{p.get('weight_pct', 0):.1f}%"
            pnl = fmt_pct(p.get("pnl_pct", 0) / 100)
            stop = f"stop:{p.get('trailing_stop_pct', 0) * 100:.0f}%"
            row(f"{asset:<7} {weight:>6}  {pnl:>8}  {stop}")

    # Recent trades
    recent = status.get("recent_trades", [])
    if recent:
        sep()
        row("RECENT TRADES")
        for t in recent[-5:]:
            ts_trade = t.get("timestamp", "?")
            if len(ts_trade) > 19:
                ts_trade = ts_trade[11:16]  # HH:MM
            side = t.get("side", "?")
            asset = t.get("asset", "?")
            qty = t.get("qty", 0)
            price = t.get("price", 0)
            reason = t.get("reason", "")
            row(f"{ts_trade}  {side:<4} {asset:<6} {qty:.4f} @ ${price:,.2f}  ({reason})")

    # Risk flags
    sep()
    flags = status.get("risk_flags", [])
    if flags:
        row(f"RISK FLAGS: {', '.join(flags)}")
    else:
        row("RISK FLAGS: none")

    health = status.get("signal_health", {})
    hr = health.get("hit_rate", 0)
    wl = health.get("winner_loser_ratio", 0)
    row(f"Signal Health: HR={hr * 100:.0f}% | W/L={wl:.2f}")

    lines.append(f"╚{border}╝")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="APEX live terminal dashboard")
    parser.add_argument(
        "--status-file", default=DEFAULT_STATUS_PATH, help="Path to status.json"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="Refresh interval in seconds",
    )
    args = parser.parse_args()

    status_path = Path(args.status_file)

    print("APEX Dashboard — press Ctrl+C to exit")
    print(f"Reading from: {status_path.resolve()}")

    try:
        while True:
            status = load_status(status_path)
            os.system("clear" if os.name != "nt" else "cls")
            print(render(status))
            print(f"\nRefreshing every {args.interval}s — Ctrl+C to exit")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


if __name__ == "__main__":
    main()
