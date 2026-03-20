#!/usr/bin/env python3
"""Lightweight web dashboard for the APEX trading bot.

Single-file HTTP server (stdlib only) serving a status page that auto-refreshes.
Reads status.json and serves it as an API endpoint plus a simple HTML/JS page.

Usage:
    python scripts/web_dashboard.py --port 8080

    # Remote access via SSH tunnel:
    ssh -L 8080:localhost:8080 ec2-instance
    # Then open http://localhost:8080
"""

from __future__ import annotations

import argparse
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

DEFAULT_STATUS_PATH = "logs/status.json"
DEFAULT_PORT = 8080

# Resolved at startup
_status_path: Path = Path(DEFAULT_STATUS_PATH)

HTML_PAGE = HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>APEX Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0d1117; color: #c9d1d9; font-family: 'Courier New', monospace; padding: 20px; }
  h1 { color: #58a6ff; margin-bottom: 10px; }
  .status-bar { color: #8b949e; margin-bottom: 20px; font-size: 14px; }
  .stale { color: #f85149; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 15px; }
  .card .label { color: #8b949e; font-size: 12px; text-transform: uppercase; }
  .card .value { font-size: 24px; font-weight: bold; margin-top: 5px; }
  .positive { color: #3fb950; }
  .negative { color: #f85149; }
  .neutral { color: #c9d1d9; }
  table { width: 100%; border-collapse: collapse; margin-top: 10px; }
  th { text-align: left; color: #8b949e; font-size: 12px; padding: 8px; border-bottom: 1px solid #30363d; }
  td { padding: 8px; border-bottom: 1px solid #21262d; font-size: 14px; }
  .section { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 15px; margin-bottom: 15px; }
  .section h2 { color: #58a6ff; font-size: 16px; margin-bottom: 10px; }
  .flags { color: #d29922; font-weight: bold; }
  .flags.clear { color: #3fb950; }
</style>
</head>
<body>
<h1>APEX Trading Bot</h1>
<div class="status-bar" id="status-bar">Loading...</div>

<div class="grid" id="metrics"></div>

<div class="section" id="positions-section" style="display:none">
  <h2>Positions</h2>
  <table><thead><tr>
    <th>Asset</th><th>Weight</th><th>Unrealised P&L</th><th>Stop Dist.</th>
  </tr></thead><tbody id="positions"></tbody></table>
</div>

<div class="section" id="trades-section" style="display:none">
  <h2>Recent Trades</h2>
  <table><thead><tr>
    <th>Time</th><th>Side</th><th>Asset</th><th>Qty</th><th>Price</th><th>Reason</th>
  </tr></thead><tbody id="trades"></tbody></table>
</div>

<div class="section" id="risk-section">
  <h2>Risk & Signals</h2>
  <div id="risk-content"></div>
</div>

<script>
function pctClass(v) { return v >= 0 ? 'positive' : 'negative'; }
// Removed * 100 because the backend already passes whole number percentages
function fmtPct(v) { return (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%'; }
function fmtUsd(v) { return '$' + Number(v).toLocaleString('en-US', {maximumFractionDigits: 2}); }

function update() {
  fetch('/api/status')
    .then(r => r.json())
    .then(d => render(d))
    .catch(() => {
      document.getElementById('status-bar').innerHTML =
        '<span class="stale">Cannot reach API — retrying...</span>';
    });
}

function render(d) {
  const now = Date.now();
  const tsStr = d.timestamp_utc || d.last_update_utc || '';
  const ts = tsStr ? new Date(tsStr + (tsStr.endsWith('Z') ? '' : 'Z')) : new Date();
  const ageSec = Math.round((now - ts.getTime()) / 1000);
  const staleClass = ageSec > 120 ? 'stale' : '';

  const uptime = d.uptime_seconds ? Math.round(d.uptime_seconds / 60) + 'm' : 'N/A';

  document.getElementById('status-bar').innerHTML =
    `Regime: <strong>${d.regime || 'UNKNOWN'}</strong> | ` +
    `Updated: ${tsStr || 'Unknown'} ` +
    `<span class="${staleClass}">(${ageSec}s ago)</span> | ` +
    `Uptime: ${uptime}`;

  const metrics = [
    { label: 'NAV', value: fmtUsd(d.nav || 0), cls: 'neutral' },
    { label: 'Daily P&L', value: fmtPct(d.pnl_daily_pct || 0), cls: pctClass(d.pnl_daily_pct || 0) },
    { label: 'Total P&L', value: fmtPct(d.pnl_total_pct || 0), cls: pctClass(d.pnl_total_pct || 0) },
    { label: 'Drawdown (USD)', value: fmtUsd(d.drawdown_pct || 0), cls: (d.drawdown_pct || 0) > 0 ? 'negative' : 'neutral' },
    { label: 'Exposure', value: (d.crypto_exposure_pct || 0).toFixed(1) + '%', cls: 'neutral' },
    { label: 'Cash', value: (d.cash_pct || 0).toFixed(1) + '%', cls: 'neutral' },
    { label: 'Positions', value: d.num_positions || 0, cls: 'neutral' },
    { label: 'Endgame', value: (d.endgame_hours_remaining || 0) + 'h', cls: (d.endgame_hours_remaining || 0) < 12 ? 'negative' : 'neutral' },
    { label: 'Loop', value: (d.loop_duration_ms || 0) + 'ms', cls: (d.loop_duration_ms || 0) > 1000 ? 'negative' : 'neutral' },
    { label: 'Errors/hr', value: d.errors_last_hour || 0, cls: (d.errors_last_hour || 0) > 0 ? 'negative' : 'positive' },
  ];

  document.getElementById('metrics').innerHTML = metrics.map(m =>
    `<div class="card"><div class="label">${m.label}</div><div class="value ${m.cls}">${m.value}</div></div>`
  ).join('');

  // Positions
  const posEl = document.getElementById('positions');
  const posSection = document.getElementById('positions-section');
  if (d.positions && d.positions.length > 0) {
    posSection.style.display = '';
    const sorted = d.positions.sort((a, b) => (b.weight_pct || 0) - (a.weight_pct || 0));
    posEl.innerHTML = sorted.map(p => {
      const sym = p.symbol || p.asset || 'N/A';
      const w = p.weight_pct || 0;
      const pnl = p.unrealised_pnl_pct || p.pnl_pct || 0;
      const stop = p.stop_distance_pct || p.trailing_stop_pct || 0;
      return `<tr>
        <td>${sym}</td>
        <td>${w.toFixed(2)}%</td>
        <td class="${pctClass(pnl)}">${fmtPct(pnl)}</td>
        <td>${stop.toFixed(2)}%</td>
      </tr>`;
    }).join('');
  } else {
    posSection.style.display = 'none';
  }

  // Trades
  const trEl = document.getElementById('trades');
  const trSection = document.getElementById('trades-section');
  if (d.recent_trades && d.recent_trades.length > 0) {
    trSection.style.display = '';
    trEl.innerHTML = d.recent_trades.map(t => {
      const time = (t.timestamp || '').slice(11, 19);
      return `<tr>
        <td>${time}</td>
        <td>${t.side || '-'}</td>
        <td>${t.asset || '-'}</td>
        <td>${(t.qty || 0).toFixed(4)}</td>
        <td>${fmtUsd(t.price || 0)}</td>
        <td>${t.reason || '-'}</td>
      </tr>`;
    }).join('');
  } else {
    trSection.style.display = 'none';
  }

  // Risk
  const flags = d.risk_flags || [];
  const h = d.signal_health || {};
  const flagsClass = flags.length > 0 ? 'flags' : 'flags clear';
  document.getElementById('risk-content').innerHTML =
    `<div class="${flagsClass}">Flags: ${flags.length > 0 ? flags.join(', ') : 'None'}</div>` +
    `<div>Hit Rate: ${((h.hit_rate || 0) * 100).toFixed(0)}% | W/L Ratio: ${(h.winner_loser_ratio || 0).toFixed(2)}</div>` +
    `<div>API Calls Remaining: ${d.api_calls_remaining || 'N/A'} | Next Rebalance: ${d.next_rebalance_utc || '?'}</div>`;
}

update();
setInterval(update, 5000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the dashboard."""

    def do_GET(self) -> None:
        if self.path == "/api/status":
            self._serve_status()
        elif self.path == "/" or self.path == "/index.html":
            self._serve_html()
        else:
            self.send_error(404)

    def _serve_status(self) -> None:
        try:
            data = _status_path.read_text(encoding="utf-8")
            json.loads(data)  # validate
        except (FileNotFoundError, json.JSONDecodeError):
            self.send_error(503, "Status file not available")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))

    def _serve_html(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        """Suppress default request logging."""
        pass


def main() -> None:
    global _status_path

    parser = argparse.ArgumentParser(description="APEX web dashboard")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP port")
    parser.add_argument(
        "--status-file", default=DEFAULT_STATUS_PATH, help="Path to status.json"
    )
    args = parser.parse_args()

    _status_path = Path(args.status_file)

    server = HTTPServer(("0.0.0.0", args.port), DashboardHandler)
    print(f"APEX Web Dashboard running on http://localhost:{args.port}")
    print(f"Reading status from: {_status_path.resolve()}")
    print("Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
