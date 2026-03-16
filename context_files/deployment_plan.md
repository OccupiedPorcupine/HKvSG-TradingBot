# APEX — Deployment Plan

**Target:** AWS EC2 t3.medium (2 vCPU, 4 GB RAM), ap-southeast-2 (Sydney)
**Access:** Session Manager (browser-based, no SSH)
**Runtime:** 24/7 for 10 days (March 22 – April 1)
**Constraint:** Single instance only — extras will be terminated

---

## Table of Contents

1. [EC2 Instance Setup](#1-ec2-instance-setup)
2. [Environment Configuration](#2-environment-configuration)
3. [Dependency Installation](#3-dependency-installation)
4. [Secrets Management](#4-secrets-management)
5. [Deployment Procedure](#5-deployment-procedure)
6. [Process Supervision](#6-process-supervision)
7. [Health Monitoring & Alerting](#7-health-monitoring--alerting)
8. [Log Management](#8-log-management)
9. [Hot Config Reload](#9-hot-config-reload)
10. [Update & Rollback Procedure](#10-update--rollback-procedure)
11. [Crash Recovery Verification](#11-crash-recovery-verification)
12. [Competition-Day Runbook](#12-competition-day-runbook)
13. [Pre-Launch Checklist](#13-pre-launch-checklist)
14. [Post-Competition Teardown](#14-post-competition-teardown)

---

## 1. EC2 Instance Setup

### 1.1 Launch

Use the **pre-built launch template** provided by the organizers. Do not configure manually.

1. Log into the AWS hackathon portal (reset password from email, set up MFA)
2. Navigate to EC2 > Launch Templates > select the hackathon template
3. Launch **exactly one** `t3.medium` instance in `ap-southeast-2`
4. Verify the instance is running via EC2 console

### 1.2 Connect

All access is via **Session Manager** (no SSH keys, no security groups to configure):

```bash
# From AWS Console: EC2 > Instances > Select instance > Connect > Session Manager
# Or via AWS CLI (if installed locally):
aws ssm start-session --target <instance-id> --region ap-southeast-2
```

### 1.3 Verify Instance Resources

Run immediately after first connect:

```bash
# Confirm instance type
cat /proc/cpuinfo | grep processor | wc -l   # expect: 2
free -h                                        # expect: ~4 GB
df -h                                          # check available disk
timedatectl                                    # confirm timezone is UTC
```

### 1.4 System Clock Sync

End-game de-risking depends on accurate time. Verify NTP is active:

```bash
timedatectl status
# If NTP is not synchronized:
sudo timedatectl set-ntp true
# Verify:
chronyc tracking   # or ntpstat
```

---

## 2. Environment Configuration

### 2.1 Python Setup

```bash
# Check pre-installed Python version
python3 --version   # need 3.11+

# If Python 3.11+ is not available:
sudo yum install -y python3.11   # Amazon Linux 2
# or
sudo apt install -y python3.11   # Ubuntu

# Create virtual environment
python3.11 -m venv /home/ssm-user/apex-env
source /home/ssm-user/apex-env/bin/activate

# Confirm
python --version
pip --version
```

### 2.2 Project Directory Structure

```bash
mkdir -p /home/ssm-user/apex
mkdir -p /home/ssm-user/apex/data_store/{snapshots,trades,logs,models}
mkdir -p /home/ssm-user/apex/backups
```

### 2.3 System Limits

Ensure open file limits are adequate for async connections:

```bash
ulimit -n   # check current limit
# If < 4096:
echo "ssm-user soft nofile 4096" | sudo tee -a /etc/security/limits.conf
echo "ssm-user hard nofile 8192" | sudo tee -a /etc/security/limits.conf
```

---

## 3. Dependency Installation

### 3.1 requirements.txt

This file must be created in the repo root before deployment:

```
# Core
aiohttp>=3.9,<4.0
pyyaml>=6.0
numpy>=1.26,<2.0

# Data persistence
pyarrow>=14.0            # Parquet read/write
fastparquet>=2023.10     # fallback Parquet engine

# ML (Phase 3 only — install from the start to avoid mid-competition installs)
lightgbm>=4.1
scikit-learn>=1.3

# Monitoring (optional — lightweight alerting)
requests>=2.31           # for webhook alerts
```

### 3.2 Install

```bash
cd /home/ssm-user/apex
source /home/ssm-user/apex-env/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Verify critical imports
python -c "import aiohttp, yaml, numpy, pyarrow, lightgbm; print('OK')"
```

### 3.3 Pin Versions

After install, freeze exact versions for reproducibility:

```bash
pip freeze > requirements-lock.txt
```

---

## 4. Secrets Management

### 4.1 Environment Variables

Secrets are stored in a `.env` file that is **never committed to git**.

```bash
cat > /home/ssm-user/apex/.env << 'EOF'
ROOSTOO_API_KEY=your_competition_key_here
ROOSTOO_API_SECRET=your_competition_secret_here
ALERT_WEBHOOK_URL=your_discord_or_slack_webhook_here
EOF

chmod 600 /home/ssm-user/apex/.env
```

### 4.2 Loading Secrets

The supervisor script (Section 6) sources `.env` before launching the bot. The bot reads secrets via `os.environ`.

### 4.3 Key Swap (Testing → Competition)

On competition start day (March 22):

1. Stop the bot
2. Edit `.env` — replace testing keys with competition keys
3. **Delete all state files** (snapshots, trade logs) — start clean
4. Restart the bot

```bash
# Key swap procedure
cd /home/ssm-user/apex
./scripts/stop.sh
# Edit .env with competition keys
rm -rf data_store/snapshots/* data_store/trades/* data_store/logs/*
./scripts/start.sh
```

---

## 5. Deployment Procedure

### 5.1 Initial Deployment

```bash
cd /home/ssm-user

# Clone the repo
git clone <your-repo-url> apex-repo

# Symlink or copy to working directory
cp -r apex-repo/* /home/ssm-user/apex/

# Install deps
source /home/ssm-user/apex-env/bin/activate
cd /home/ssm-user/apex
pip install -r requirements.txt
```

### 5.2 Deployment Scripts

Create three scripts in `scripts/`:

**scripts/start.sh**
```bash
#!/bin/bash
set -euo pipefail

APEX_DIR="/home/ssm-user/apex"
VENV="/home/ssm-user/apex-env/bin/activate"
PIDFILE="$APEX_DIR/apex.pid"
LOGFILE="$APEX_DIR/data_store/logs/apex.log"

# Check if already running
if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    echo "APEX is already running (PID $(cat $PIDFILE)). Use stop.sh first."
    exit 1
fi

# Load environment
source "$VENV"
set -a
source "$APEX_DIR/.env"
set +a

cd "$APEX_DIR"

# Start in background with nohup
nohup python -u main.py >> "$LOGFILE" 2>&1 &
echo $! > "$PIDFILE"

echo "APEX started (PID $(cat $PIDFILE)). Logging to $LOGFILE"
```

**scripts/stop.sh**
```bash
#!/bin/bash
set -euo pipefail

APEX_DIR="/home/ssm-user/apex"
PIDFILE="$APEX_DIR/apex.pid"

if [ ! -f "$PIDFILE" ]; then
    echo "No PID file found. APEX may not be running."
    exit 0
fi

PID=$(cat "$PIDFILE")

if kill -0 "$PID" 2>/dev/null; then
    echo "Sending SIGTERM to APEX (PID $PID)..."
    kill "$PID"

    # Wait up to 30 seconds for graceful shutdown
    for i in $(seq 1 30); do
        if ! kill -0 "$PID" 2>/dev/null; then
            echo "APEX stopped gracefully after ${i}s."
            rm -f "$PIDFILE"
            exit 0
        fi
        sleep 1
    done

    echo "APEX did not stop gracefully. Sending SIGKILL..."
    kill -9 "$PID"
    rm -f "$PIDFILE"
    echo "APEX killed."
else
    echo "APEX process $PID is not running. Cleaning up PID file."
    rm -f "$PIDFILE"
fi
```

**scripts/status.sh**
```bash
#!/bin/bash

APEX_DIR="/home/ssm-user/apex"
PIDFILE="$APEX_DIR/apex.pid"
HEARTBEAT="$APEX_DIR/data_store/logs/heartbeat"
LOGFILE="$APEX_DIR/data_store/logs/apex.log"

echo "=== APEX Status ==="

# Process status
if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    PID=$(cat "$PIDFILE")
    echo "Process:   RUNNING (PID $PID)"
    echo "Uptime:    $(ps -o etime= -p $PID | xargs)"
    echo "Memory:    $(ps -o rss= -p $PID | awk '{printf "%.1f MB", $1/1024}')"
    echo "CPU:       $(ps -o %cpu= -p $PID | xargs)%"
else
    echo "Process:   NOT RUNNING"
fi

# Heartbeat
if [ -f "$HEARTBEAT" ]; then
    LAST_BEAT=$(stat -c %Y "$HEARTBEAT" 2>/dev/null || stat -f %m "$HEARTBEAT")
    NOW=$(date +%s)
    AGE=$(( NOW - LAST_BEAT ))
    if [ $AGE -lt 180 ]; then
        echo "Heartbeat: OK (${AGE}s ago)"
    else
        echo "Heartbeat: STALE (${AGE}s ago) *** WARNING ***"
    fi
else
    echo "Heartbeat: NO FILE"
fi

# Latest log lines
echo ""
echo "=== Last 10 Log Lines ==="
tail -10 "$LOGFILE" 2>/dev/null || echo "(no log file)"

# Latest snapshot
echo ""
echo "=== Latest Performance Snapshot ==="
tail -1 "$APEX_DIR/data_store/logs/snapshots.jsonl" 2>/dev/null || echo "(no snapshots)"

# Disk usage
echo ""
echo "=== Disk Usage ==="
du -sh "$APEX_DIR/data_store/"* 2>/dev/null || echo "(no data)"
echo "Disk free: $(df -h /home | tail -1 | awk '{print $4}')"
```

Make all scripts executable:

```bash
chmod +x scripts/{start,stop,status}.sh
```

---

## 6. Process Supervision

### 6.1 Watchdog via Cron

Since the EC2 instance is managed (no systemd service installation guaranteed), use a cron-based watchdog that restarts the bot if it dies:

**scripts/watchdog.sh**
```bash
#!/bin/bash

APEX_DIR="/home/ssm-user/apex"
PIDFILE="$APEX_DIR/apex.pid"
HEARTBEAT="$APEX_DIR/data_store/logs/heartbeat"
WATCHDOG_LOG="$APEX_DIR/data_store/logs/watchdog.log"
WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"

log() {
    echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') $1" >> "$WATCHDOG_LOG"
}

send_alert() {
    if [ -n "$WEBHOOK_URL" ]; then
        curl -s -X POST "$WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "{\"content\": \"🚨 APEX: $1\"}" > /dev/null 2>&1
    fi
}

# Check 1: Is the process running?
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if ! kill -0 "$PID" 2>/dev/null; then
        log "ALERT: Process $PID is dead. Restarting..."
        send_alert "Process died (PID $PID). Auto-restarting..."
        rm -f "$PIDFILE"
        source "$APEX_DIR/scripts/start.sh"
        log "Restarted. New PID: $(cat $PIDFILE)"
        send_alert "Restarted successfully (PID $(cat $PIDFILE))"
        exit 0
    fi
else
    log "ALERT: No PID file. Starting APEX..."
    send_alert "No PID file found. Starting APEX..."
    source "$APEX_DIR/scripts/start.sh"
    log "Started. PID: $(cat $PIDFILE)"
    exit 0
fi

# Check 2: Is the heartbeat stale? (>5 min = likely hung)
if [ -f "$HEARTBEAT" ]; then
    LAST_BEAT=$(stat -c %Y "$HEARTBEAT" 2>/dev/null || stat -f %m "$HEARTBEAT")
    NOW=$(date +%s)
    AGE=$(( NOW - LAST_BEAT ))

    if [ $AGE -gt 300 ]; then
        log "ALERT: Heartbeat stale (${AGE}s). Process may be hung. Restarting..."
        send_alert "Heartbeat stale (${AGE}s). Killing and restarting..."
        kill "$PID" 2>/dev/null
        sleep 5
        kill -9 "$PID" 2>/dev/null
        rm -f "$PIDFILE"
        source "$APEX_DIR/scripts/start.sh"
        log "Restarted after stale heartbeat. New PID: $(cat $PIDFILE)"
        send_alert "Restarted after stale heartbeat (PID $(cat $PIDFILE))"
    fi
fi

# Check 3: Memory usage (kill if >3.5GB to prevent OOM killer)
if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    RSS_KB=$(ps -o rss= -p "$PID" 2>/dev/null || echo 0)
    RSS_MB=$(( RSS_KB / 1024 ))

    if [ "$RSS_MB" -gt 3500 ]; then
        log "ALERT: Memory usage ${RSS_MB}MB exceeds 3500MB. Restarting..."
        send_alert "Memory usage ${RSS_MB}MB. Restarting to prevent OOM..."
        kill "$PID"
        sleep 5
        kill -9 "$PID" 2>/dev/null
        rm -f "$PIDFILE"
        source "$APEX_DIR/scripts/start.sh"
        log "Restarted after memory limit. New PID: $(cat $PIDFILE)"
    fi
fi
```

### 6.2 Cron Schedule

```bash
# Install watchdog cron (runs every 2 minutes)
(crontab -l 2>/dev/null; echo "*/2 * * * * /home/ssm-user/apex/scripts/watchdog.sh") | crontab -

# Verify
crontab -l
```

### 6.3 Graceful Shutdown Handling

The bot's `main.py` must handle SIGTERM for clean shutdown:

```python
# Required in main.py
import signal
import asyncio

shutdown_event = asyncio.Event()

def handle_sigterm(signum, frame):
    logger.info("SIGTERM received. Initiating graceful shutdown...")
    shutdown_event.set()

signal.signal(signal.SIGTERM, handle_sigterm)
signal.signal(signal.SIGINT, handle_sigterm)

# In the main loop:
# while not shutdown_event.is_set():
#     ...run cycle...
# On exit: save final Parquet snapshot, cancel open orders, flush logs
```

---

## 7. Health Monitoring & Alerting

### 7.1 Heartbeat File

The bot writes a heartbeat file every cycle (every 60 seconds):

```python
# In the main loop, after each price fetch cycle:
Path("data_store/logs/heartbeat").write_text(
    f"{datetime.utcnow().isoformat()} NAV={nav:.2f} positions={n_positions}"
)
```

The watchdog (Section 6) checks this file. If stale >5 min, it restarts the bot.

### 7.2 Webhook Alerts

Alerts are sent to a Discord or Slack webhook for critical events. The bot should call this function for key events:

```python
import requests
import os

def send_alert(message: str) -> None:
    url = os.environ.get("ALERT_WEBHOOK_URL")
    if not url:
        return
    try:
        requests.post(url, json={"content": f"APEX: {message}"}, timeout=5)
    except Exception:
        pass  # alerting must never crash the bot
```

### 7.3 Events That Trigger Alerts

| Event | Severity | Alert Message |
|---|---|---|
| Bot started | Info | `Bot started. NAV=$X` |
| Bot stopped (graceful) | Info | `Bot stopped gracefully. Final NAV=$X` |
| Drawdown halt triggered | Critical | `DRAWDOWN HALT: -X% from peak. No new entries for 2h.` |
| Circuit breaker fired | Critical | `CONTAGION BREAKER: X% positions down. Reducing all to 20%.` |
| Daily loss >5% | Critical | `DAILY LOSS -X%. Reducing positions to 50%.` |
| 3+ consecutive API failures | Warning | `API: X consecutive failures. Entering safe mode.` |
| Crash recovery activated | Warning | `Restarted from crash. Restored state from snapshot at HH:MM.` |
| End-game phase entered | Info | `END-GAME: T-Xh. Max exposure capped at Y%.` |
| Regime change | Info | `Regime: OLD → NEW` |
| Rebalance complete | Info (hourly) | `Rebalance: NAV=$X, positions=N, exposure=Y%` |

### 7.4 Discord Webhook Setup

1. Create a private Discord server (or channel in an existing one)
2. Channel Settings > Integrations > Webhooks > New Webhook
3. Copy the webhook URL
4. Set it in `.env` as `ALERT_WEBHOOK_URL`

### 7.5 Periodic Health Report

A cron job posts a summary every 4 hours:

**scripts/health_report.sh**
```bash
#!/bin/bash

APEX_DIR="/home/ssm-user/apex"
WEBHOOK_URL="${ALERT_WEBHOOK_URL:-}"

if [ -z "$WEBHOOK_URL" ]; then exit 0; fi

# Load env for webhook URL
set -a
source "$APEX_DIR/.env"
set +a

# Get latest snapshot
SNAPSHOT=$(tail -1 "$APEX_DIR/data_store/logs/snapshots.jsonl" 2>/dev/null || echo "{}")

# Get process stats
PIDFILE="$APEX_DIR/apex.pid"
if [ -f "$PIDFILE" ] && kill -0 "$(cat $PIDFILE)" 2>/dev/null; then
    PID=$(cat "$PIDFILE")
    UPTIME=$(ps -o etime= -p $PID | xargs)
    MEM=$(ps -o rss= -p $PID | awk '{printf "%.0f", $1/1024}')
    STATUS="RUNNING"
else
    UPTIME="N/A"
    MEM="N/A"
    STATUS="DOWN"
fi

DISK_FREE=$(df -h /home | tail -1 | awk '{print $4}')

MSG="📊 **APEX Health Report**
Status: $STATUS | Uptime: $UPTIME | Mem: ${MEM}MB | Disk free: $DISK_FREE
Latest snapshot: \`$SNAPSHOT\`"

curl -s -X POST "$WEBHOOK_URL" \
    -H "Content-Type: application/json" \
    -d "{\"content\": \"$MSG\"}" > /dev/null 2>&1
```

```bash
# Cron: every 4 hours
(crontab -l 2>/dev/null; echo "0 */4 * * * /home/ssm-user/apex/scripts/health_report.sh") | crontab -
```

---

## 8. Log Management

### 8.1 Log Files

| File | Content | Growth Rate |
|---|---|---|
| `data_store/logs/apex.log` | Main application log | ~1-5 MB/day |
| `data_store/logs/trades.jsonl` | Every trade executed | ~50 KB/day |
| `data_store/logs/snapshots.jsonl` | Hourly NAV/performance snapshots | ~10 KB/day |
| `data_store/logs/heartbeat` | Single-line, overwritten each cycle | Static |
| `data_store/logs/watchdog.log` | Watchdog events | ~1 KB/day |

### 8.2 Log Rotation

Over 10 days, logs are manageable (~50-100 MB total). Simple daily rotation via cron:

**scripts/rotate_logs.sh**
```bash
#!/bin/bash

LOGDIR="/home/ssm-user/apex/data_store/logs"
DATE=$(date -u +%Y%m%d)

# Rotate main log (keep appending to new file)
if [ -f "$LOGDIR/apex.log" ]; then
    cp "$LOGDIR/apex.log" "$LOGDIR/apex.log.$DATE"
    truncate -s 0 "$LOGDIR/apex.log"
fi

# Compress old logs
find "$LOGDIR" -name "*.log.2*" -mtime +2 -exec gzip {} \;

# Delete logs older than 12 days (beyond competition window)
find "$LOGDIR" -name "*.gz" -mtime +12 -delete
```

```bash
# Cron: daily at 00:05 UTC
(crontab -l 2>/dev/null; echo "5 0 * * * /home/ssm-user/apex/scripts/rotate_logs.sh") | crontab -
```

### 8.3 Disk Space Monitoring

Parquet snapshots are the largest files. At ~500 KB per snapshot, 24 snapshots/day = ~12 MB/day = ~120 MB over 10 days. Well within t3.medium's disk.

The watchdog does not monitor disk, but `status.sh` reports it. If disk usage becomes a concern, older snapshots can be deleted (keep last 48 hours):

```bash
find /home/ssm-user/apex/data_store/snapshots -name "*.parquet" -mtime +2 -delete
```

---

## 9. Hot Config Reload

### 9.1 Mechanism

The bot watches for a signal to reload `config.yaml` without restarting:

```python
# In main.py — check for reload signal every cycle
RELOAD_FLAG = Path("data_store/logs/reload_config")

async def maybe_reload_config():
    if RELOAD_FLAG.exists():
        logger.info("Config reload requested. Reloading config.yaml...")
        try:
            new_config = load_config("config.yaml")
            validate_config(new_config)
            global config
            config = new_config
            logger.info("Config reloaded successfully.")
            send_alert("Config reloaded successfully.")
        except Exception as e:
            logger.error(f"Config reload FAILED: {e}. Keeping old config.")
            send_alert(f"Config reload FAILED: {e}")
        finally:
            RELOAD_FLAG.unlink(missing_ok=True)
```

### 9.2 Triggering a Reload

```bash
# Edit config
vim /home/ssm-user/apex/config.yaml

# Trigger reload (bot picks it up on next cycle, within 60s)
touch /home/ssm-user/apex/data_store/logs/reload_config
```

### 9.3 What Can Be Hot-Reloaded

| Parameter | Hot-reloadable | Notes |
|---|---|---|
| Trailing stop distances | Yes | Takes effect next risk check |
| Regime thresholds | Yes | Takes effect next regime evaluation |
| Top-N selections | Yes | Takes effect next rebalance |
| Tier caps | Yes | Takes effect next rebalance |
| PAXG allocation | Yes | Takes effect next rebalance |
| End-game schedule | Yes | Takes effect next end-game check |
| API rate limit | Yes | Takes effect next cycle |
| Ring buffer size | **No** | Requires restart (data structure resize) |
| ML enabled flag | **No** | Requires restart (model loading) |

---

## 10. Update & Rollback Procedure

### 10.1 Code Update (Mid-Competition)

Every code change during competition must be committed to the repo. Deployment procedure:

```bash
# 1. Pull latest code on EC2
cd /home/ssm-user/apex-repo
git pull origin main

# 2. Copy updated files (preserve data_store and .env)
rsync -av --exclude='data_store' --exclude='.env' --exclude='apex.pid' \
    /home/ssm-user/apex-repo/ /home/ssm-user/apex/

# 3. If dependencies changed:
source /home/ssm-user/apex-env/bin/activate
pip install -r requirements.txt

# 4. Restart bot
cd /home/ssm-user/apex
./scripts/stop.sh
./scripts/start.sh

# 5. Verify
./scripts/status.sh
```

### 10.2 Rollback

If a new deployment causes issues:

```bash
# 1. Stop the bot
./scripts/stop.sh

# 2. Revert to previous commit
cd /home/ssm-user/apex-repo
git log --oneline -5        # find the good commit
git checkout <good-commit>

# 3. Redeploy
rsync -av --exclude='data_store' --exclude='.env' --exclude='apex.pid' \
    /home/ssm-user/apex-repo/ /home/ssm-user/apex/

# 4. Restart
cd /home/ssm-user/apex
./scripts/start.sh

# 5. Alert the team
# send_alert "ROLLBACK: Reverted to commit <hash>. Reason: ..."
```

### 10.3 Config-Only Changes

For parameter tuning that doesn't require code changes:

```bash
# Edit config directly on EC2
vim /home/ssm-user/apex/config.yaml

# Hot-reload (no restart needed)
touch /home/ssm-user/apex/data_store/logs/reload_config

# Commit the change back to repo
cd /home/ssm-user/apex-repo
cp /home/ssm-user/apex/config.yaml .
git add config.yaml
git commit -m "tune: <describe parameter change>"
git push origin main
```

---

## 11. Crash Recovery Verification

### 11.1 What Gets Saved (Hourly)

| Artifact | Format | Contains |
|---|---|---|
| `data_store/snapshots/state_YYYYMMDD_HHMM.parquet` | Parquet | Price ring buffers, feature arrays, regime state, position state, NAV |
| `data_store/trades/trades.jsonl` | JSON Lines | Every trade since last snapshot (append-only) |
| `config.yaml` | YAML | All parameters (never changes unless explicitly modified) |

### 11.2 Recovery Sequence (Automatic on Startup)

1. Find the most recent Parquet snapshot in `data_store/snapshots/`
2. Load ring buffers, feature arrays, regime state, position state
3. Replay trade log entries timestamped after the snapshot
4. Fetch current balances from API to reconcile positions
5. Resume normal operation from the next cycle
6. Send alert: "Crash recovery activated"

### 11.3 Pre-Competition Recovery Test

Run this test **before competition starts** to verify recovery works:

```bash
# 1. Start bot, let it run 2-3 cycles
./scripts/start.sh
sleep 180

# 2. Verify a snapshot exists
ls -la data_store/snapshots/

# 3. Kill the bot ungracefully (simulate crash)
kill -9 $(cat apex.pid)

# 4. Restart
./scripts/start.sh

# 5. Check logs for recovery message
grep -i "recovery" data_store/logs/apex.log

# 6. Verify positions match pre-crash state
./scripts/status.sh
```

---

## 12. Competition-Day Runbook

### 12.1 Day Before Competition (March 21)

| # | Task | Verify |
|---|---|---|
| 1 | EC2 instance running, accessible via Session Manager | Can connect |
| 2 | Bot deployed and running with **testing keys** | `status.sh` shows RUNNING |
| 3 | Watchdog cron active | `crontab -l` shows entry |
| 4 | Log rotation cron active | `crontab -l` shows entry |
| 5 | Health report cron active | `crontab -l` shows entry |
| 6 | Discord webhook working | Received test alert |
| 7 | Crash recovery tested | Recovery message in logs |
| 8 | `config.yaml` has all Phase 0 confirmed values | All `MUST SET` values populated |
| 9 | `btc_30d_median_vol` computed and set | Non-zero value in config |
| 10 | Competition keys ready (not yet deployed) | Keys in a secure location |

### 12.2 Competition Start (March 22)

```bash
# 1. Stop bot
./scripts/stop.sh

# 2. Swap to competition keys
vim /home/ssm-user/apex/.env
# Replace ROOSTOO_API_KEY and ROOSTOO_API_SECRET with competition values

# 3. Clean state (fresh start for competition)
rm -rf data_store/snapshots/* data_store/trades/* data_store/logs/apex.log*

# 4. Start bot
./scripts/start.sh

# 5. Watch first few cycles
tail -f data_store/logs/apex.log

# 6. Verify first trade executes
grep "ORDER" data_store/logs/trades.jsonl

# 7. Confirm alert received
# Check Discord for "Bot started" message
```

### 12.3 Daily Check (Every Morning)

```bash
# Quick check via Session Manager
./scripts/status.sh

# Or just check Discord for the 4-hourly health reports
```

### 12.4 Emergency: Bot Not Trading

If the bot is running but hasn't traded in >24 hours (violates the 8/10 active days rule):

```bash
# 1. Check logs for errors
grep -i "error\|exception\|fail" data_store/logs/apex.log | tail -20

# 2. Check if API is responsive
curl -s "https://<api-url>/api/v1/time" | python -m json.tool

# 3. Check if the signal pipeline is producing targets
grep -i "rebalance\|signal\|target" data_store/logs/apex.log | tail -10

# 4. If signal pipeline is working but no trades:
#    - Check if all deltas are below min_trade_threshold (0.2% NAV)
#    - Consider lowering the threshold temporarily via config reload

# 5. Nuclear option: force a small trade
#    The bot should have a minimum_activity_check that forces a trade
#    if none have occurred in 24h (see config: orchestration.minimum_activity_check_hr)
```

### 12.5 Emergency: Market Crash in Progress

```bash
# 1. Check current state
./scripts/status.sh

# 2. If circuit breakers haven't fired and you want to reduce exposure manually:
#    Edit config.yaml — lower regime_targets across all regimes
vim /home/ssm-user/apex/config.yaml
touch data_store/logs/reload_config

# 3. Do NOT stop the bot — it will miss the recovery
# 4. Trust the trailing stops and drawdown halt to limit damage
```

### 12.6 March 28: Repo Submission Deadline

```bash
# Ensure all config changes are committed
cd /home/ssm-user/apex-repo
cp /home/ssm-user/apex/config.yaml .
git add -A
git commit -m "competition state as of March 28"
git push origin main

# Verify repo is public and accessible
```

### 12.7 End-Game (T-48h to T-0)

The bot handles end-game automatically via the hardcoded schedule. No manual intervention needed unless:

- The bot is down during end-game → restart immediately, it will pick up the schedule
- You want to override end-game timing → edit `config.yaml` endgame section + hot-reload

**T-15 minutes (automatic):** Bot sells all positions.

**After competition ends:** Let the bot run for 5 more minutes to ensure final sells complete, then stop.

```bash
# After competition end time:
./scripts/stop.sh

# Save final state for presentation preparation
cp -r data_store/ /home/ssm-user/apex-final-data/
```

---

## 13. Pre-Launch Checklist

Run through this checklist the day before competition starts. Every item must be checked.

### Infrastructure

- [ ] EC2 instance is `t3.medium` in `ap-southeast-2`
- [ ] Only one instance running (check EC2 console)
- [ ] Session Manager access works
- [ ] System clock synchronized (NTP active)
- [ ] Python 3.11+ installed in venv
- [ ] All dependencies installed (`python -c "import aiohttp, yaml, numpy, pyarrow"`)
- [ ] Disk space >5 GB free

### Application

- [ ] `main.py` runs without import errors
- [ ] `config.yaml` — all `MUST SET` values populated from Phase 0 testing
- [ ] `config.yaml` — `competition_start_utc` and `competition_end_utc` correct
- [ ] `.env` has testing keys (swap to competition keys on March 22)
- [ ] Heartbeat file being written every 60s
- [ ] Crash recovery tested (kill -9 → restart → state restored)
- [ ] Graceful shutdown tested (SIGTERM → clean exit)
- [ ] Trades execute correctly against test API

### Operations

- [ ] `scripts/start.sh` works
- [ ] `scripts/stop.sh` works
- [ ] `scripts/status.sh` works
- [ ] Watchdog cron installed and tested
- [ ] Log rotation cron installed
- [ ] Health report cron installed
- [ ] Discord/Slack webhook delivers alerts
- [ ] Team knows how to connect via Session Manager
- [ ] Team knows the key-swap procedure
- [ ] Team knows the rollback procedure

### Git

- [ ] Repo is public (or ready to be made public by March 28)
- [ ] `.gitignore` excludes: `.env`, `data_store/`, `*.pyc`, `__pycache__/`, `apex.pid`
- [ ] Commit history is clean and meaningful

---

## 14. Post-Competition Teardown

After Round 1 ends (April 1):

1. **Export all data** for presentation preparation:
   ```bash
   tar czf apex-round1-data.tar.gz data_store/
   # Download via S3 or scp if available
   ```

2. **Stop the bot:**
   ```bash
   ./scripts/stop.sh
   ```

3. **Remove cron jobs:**
   ```bash
   crontab -r
   ```

4. If advancing to **Round 2 (April 4):**
   - Keep the instance running
   - Clean state files
   - Deploy any strategy improvements
   - Re-run the pre-launch checklist

5. If **not advancing:**
   - Stop the instance (do not terminate — organizers handle cleanup)
   - Download any data you need for the presentation deck

---

## Appendix A: Directory Layout on EC2

```
/home/ssm-user/
├── apex-env/                    # Python virtual environment
├── apex-repo/                   # Git clone (source of truth)
└── apex/                        # Working directory (deployed code)
    ├── main.py
    ├── config.yaml
    ├── .env                     # NOT in git
    ├── apex.pid                 # NOT in git
    ├── requirements.txt
    ├── core/
    ├── data/
    ├── features/
    ├── regime/
    ├── signals/
    ├── portfolio/
    ├── risk/
    ├── execution/
    ├── monitoring/
    ├── scripts/
    │   ├── start.sh
    │   ├── stop.sh
    │   ├── status.sh
    │   ├── watchdog.sh
    │   ├── health_report.sh
    │   ├── rotate_logs.sh
    │   └── api_test.py
    ├── tests/
    ├── data_store/               # Runtime data (NOT in git)
    │   ├── snapshots/
    │   ├── trades/
    │   ├── logs/
    │   │   ├── apex.log
    │   │   ├── trades.jsonl
    │   │   ├── snapshots.jsonl
    │   │   ├── heartbeat
    │   │   └── watchdog.log
    │   └── models/
    └── backups/
```

## Appendix B: Cron Summary

```
*/2 * * * *  /home/ssm-user/apex/scripts/watchdog.sh      # Process supervision
5 0 * * *    /home/ssm-user/apex/scripts/rotate_logs.sh    # Daily log rotation
0 */4 * * *  /home/ssm-user/apex/scripts/health_report.sh  # 4-hourly health report
```

## Appendix C: Key Commands Quick Reference

```bash
# Start/stop/status
./scripts/start.sh
./scripts/stop.sh
./scripts/status.sh

# Watch live logs
tail -f data_store/logs/apex.log

# Watch trades
tail -f data_store/logs/trades.jsonl

# Hot-reload config
vim config.yaml
touch data_store/logs/reload_config

# Check NAV
tail -1 data_store/logs/snapshots.jsonl | python -m json.tool

# Deploy code update
cd ~/apex-repo && git pull && rsync -av --exclude='data_store' --exclude='.env' --exclude='apex.pid' . ~/apex/ && cd ~/apex && ./scripts/stop.sh && ./scripts/start.sh
```
