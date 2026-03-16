#!/usr/bin/env python3
"""One-shot CLI status check for the APEX trading bot.

Reads status.json, trades.jsonl, and snapshots.jsonl to display
current bot state in the terminal.

Usage:
    python scripts/status.py              # summary
    python scripts/status.py --positions  # full position table
    python scripts/status.py --trades     # last N trades
    python scripts/status.py --pnl        # P&L from snapshots (ASCII)
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STATUS_PATH = "logs/status.json"
DEFAULT_TRADES_PATH = "logs/trades.jsonl"
DEFAULT_SNAPSHOTS_PATH = "logs/snapshots.jsonl"


def load_json(path: Path) -> dict | None:
    """Load a JSON file, returning None if missing or corrupt."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Warning: could not read {path}: {e}", file=sys.stderr)
        return None


def load_jsonl(path: Path, last_n: int = 0) -> list[dict]:
    """Load a JSONL file. If last_n > 0, return only the last N lines."""
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    except FileNotFoundError:
        return []
    records = []
    for line in lines:
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if last_n > 0:
        records = records[-last_n:]
    return records


def fmt_pct(val: float) -> str:
    """Format a percentage value with sign."""
    sign = "+" if val >= 0 else ""
    return f"{sign}{val * 100:.2f}%"


def fmt_usd(val: float) -> str:
    """Format a USD value."""
    return f"${val:,.0f}"


def staleness(ts_str: str) -> str:
    """Return human-readable staleness from a UTC timestamp string."""
    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        delta = datetime.now(timezone.utc) - ts
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        return f"{secs // 3600}h {(secs % 3600) // 60}m ago"
    except (ValueError, TypeError):
        return "unknown"


def print_summary(status: dict) -> None:
    """Print a compact status summary."""
    ts = status.get("timestamp_utc", "?")
    stale = staleness(ts)

    print(f"APEX Trading Bot — Status at {ts} ({stale})")
    print(f"  Regime:    {status.get('regime', '?')}")
    print(
        f"  NAV:       {fmt_usd(status.get('nav', 0))}  |  "
        f"Daily P&L: {fmt_pct(status.get('pnl_daily_pct', 0))}  |  "
        f"Total: {fmt_pct(status.get('pnl_total_pct', 0))}"
    )
    print(
        f"  Drawdown:  {fmt_pct(status.get('drawdown_pct', 0))}  |  "
        f"Exposure: {status.get('crypto_exposure_pct', 0):.1f}%  |  "
        f"Cash: {status.get('cash_pct', 0):.1f}%"
    )
    print(
        f"  Positions: {status.get('num_positions', 0)}  |  "
        f"Orders: {status.get('open_orders', 0)}  |  "
        f"Errors/hr: {status.get('errors_last_hour', 0)}"
    )
    print(
        f"  Endgame:   {status.get('endgame_hours_remaining', '?')}h remaining  |  "
        f"Loop: {status.get('loop_duration_ms', '?')}ms  |  "
        f"API calls left: {status.get('api_calls_remaining', '?')}"
    )
    print(f"  Uptime:    {status.get('uptime_seconds', 0)}s")

    flags = status.get("risk_flags", [])
    if flags:
        print(f"  RISK FLAGS: {', '.join(flags)}")

    health = status.get("signal_health", {})
    if health:
        hr = health.get("hit_rate", 0)
        wl = health.get("winner_loser_ratio", 0)
        print(f"  Signal:    HR={hr * 100:.0f}%  W/L={wl:.2f}")


def print_positions(status: dict) -> None:
    """Print a full position table."""
    positions = status.get("positions", [])
    if not positions:
        print("No open positions.")
        return

    print(f"\n{'ASSET':<8} {'WEIGHT':>7} {'P&L':>8} {'ENTRY':>10} {'CURRENT':>10} {'STOP':>6}")
    print("-" * 55)
    for p in sorted(positions, key=lambda x: x.get("weight_pct", 0), reverse=True):
        print(
            f"{p.get('asset', '?'):<8} "
            f"{p.get('weight_pct', 0):>6.1f}% "
            f"{fmt_pct(p.get('pnl_pct', 0) / 100):>8} "
            f"{p.get('entry_price', 0):>10.2f} "
            f"{p.get('current_price', 0):>10.2f} "
            f"{p.get('trailing_stop_pct', 0) * 100:>5.1f}%"
        )


def print_trades(trades_path: Path, last_n: int = 20) -> None:
    """Print recent trades from the JSONL trade log."""
    trades = load_jsonl(trades_path, last_n=last_n)
    if not trades:
        print("No trades found.")
        return

    print(f"\n{'TIME':<20} {'SIDE':<5} {'ASSET':<8} {'QTY':>10} {'PRICE':>10} {'REASON'}")
    print("-" * 70)
    for t in trades:
        ts = t.get("timestamp", "?")
        if len(ts) > 19:
            ts = ts[11:19]  # extract HH:MM:SS
        print(
            f"{ts:<20} "
            f"{t.get('side', '?'):<5} "
            f"{t.get('asset', '?'):<8} "
            f"{t.get('qty', 0):>10.4f} "
            f"{t.get('price', 0):>10.2f} "
            f"{t.get('reason', '')}"
        )


def print_pnl(snapshots_path: Path) -> None:
    """Print an ASCII P&L chart from hourly snapshots."""
    snapshots = load_jsonl(snapshots_path)
    if not snapshots:
        print("No snapshots found.")
        return

    pnls = []
    for s in snapshots:
        pnl = s.get("pnl_total_pct", s.get("nav", 0))
        ts = s.get("timestamp_utc", s.get("timestamp", ""))
        pnls.append((ts, pnl))

    if not pnls:
        print("No P&L data.")
        return

    vals = [p[1] for p in pnls]
    min_v, max_v = min(vals), max(vals)
    rng = max_v - min_v if max_v != min_v else 1.0
    width = 40

    print(f"\nP&L over {len(pnls)} snapshots (min={fmt_pct(min_v)}, max={fmt_pct(max_v)}):")
    for ts, v in pnls[-24:]:  # last 24 snapshots
        bar_len = int((v - min_v) / rng * width)
        label = ts[-8:] if len(ts) >= 8 else ts
        print(f"  {label:>8} |{'#' * bar_len:<{width}}| {fmt_pct(v)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="APEX bot status check")
    parser.add_argument("--status-file", default=DEFAULT_STATUS_PATH, help="Path to status.json")
    parser.add_argument("--trades-file", default=DEFAULT_TRADES_PATH, help="Path to trades.jsonl")
    parser.add_argument("--snapshots-file", default=DEFAULT_SNAPSHOTS_PATH, help="Path to snapshots.jsonl")
    parser.add_argument("--positions", action="store_true", help="Show full position table")
    parser.add_argument("--trades", action="store_true", help="Show recent trades")
    parser.add_argument("--pnl", action="store_true", help="Show P&L chart")
    parser.add_argument("--last", type=int, default=20, help="Number of recent trades to show")
    args = parser.parse_args()

    status = load_json(Path(args.status_file))
    if status is None:
        print("Could not load status file. Is the bot running?")
        sys.exit(1)

    print_summary(status)

    if args.positions:
        print_positions(status)

    if args.trades:
        print_trades(Path(args.trades_file), last_n=args.last)

    if args.pnl:
        print_pnl(Path(args.snapshots_file))

    # If no specific view requested, show top 5 positions and last 5 trades
    if not (args.positions or args.trades or args.pnl):
        positions = status.get("positions", [])
        if positions:
            top = sorted(positions, key=lambda x: x.get("weight_pct", 0), reverse=True)[:5]
            print(f"\nTop positions:")
            for p in top:
                print(
                    f"  {p.get('asset', '?'):<6} "
                    f"{p.get('weight_pct', 0):.1f}%  "
                    f"{fmt_pct(p.get('pnl_pct', 0) / 100)}"
                )

        recent = status.get("recent_trades", [])
        if recent:
            print(f"\nRecent trades:")
            for t in recent[-5:]:
                ts = t.get("timestamp", "?")
                if len(ts) > 19:
                    ts = ts[11:19]
                print(
                    f"  {ts}  {t.get('side', '?')} {t.get('asset', '?')} "
                    f"{t.get('qty', 0):.4f} @ {t.get('price', 0):.2f}  "
                    f"({t.get('reason', '')})"
                )


if __name__ == "__main__":
    main()
