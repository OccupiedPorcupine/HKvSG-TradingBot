"""APEX Pre-flight Startup Checklist.

Verifies API connectivity, configuration integrity, and system state
before the main event loop begins.
"""

import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from src.data.api_client import RoostooClient, RoostooAPIError

logger = logging.getLogger(__name__)


class PreFlightCheckError(Exception):
    """Raised when a CRITICAL pre-flight check fails."""
    pass


async def run_preflight_checks(config_path: Path) -> dict[str, Any]:
    """Execute all pre-flight checks in order.

    Args:
        config_path: Path to config.yaml.

    Returns:
        Loaded and validated config dictionary.

    Raises:
        PreFlightCheckError: If any CRITICAL check fails.
    """
    logger.info("Starting APEX pre-flight checks...")

    # 1. [CRITICAL] config.yaml presence and basic loading
    if not config_path.exists():
        msg = f"CRITICAL: config.yaml not found at {config_path}"
        logger.error(msg)
        raise PreFlightCheckError(msg)

    try:
        with open(config_path, "r") as f:
            config = yaml.safe_load(f)
    except Exception as e:
        msg = f"CRITICAL: Failed to parse config.yaml: {e}"
        logger.error(msg)
        raise PreFlightCheckError(msg)

    # 2. [CRITICAL] API credentials present in environment variables
    api_cfg = config.get("api", {})
    key_env = api_cfg.get("key_env_var", "ROOSTOO_API_KEY")
    secret_env = api_cfg.get("secret_env_var", "ROOSTOO_API_SECRET")
    
    api_key = os.environ.get(key_env)
    api_secret = os.environ.get(secret_env)
    
    if not api_key or not api_secret:
        msg = f"CRITICAL: API credentials missing in environment ({key_env}, {secret_env})"
        logger.error(msg)
        raise PreFlightCheckError(msg)

    # 3. [CRITICAL] COMPETITION_END_UTC check
    comp_cfg = config.get("competition", {})
    end_utc_str = comp_cfg.get("competition_end_utc")
    if not end_utc_str:
        msg = "CRITICAL: competition_end_utc missing in config.yaml"
        logger.error(msg)
        raise PreFlightCheckError(msg)
    
    try:
        # ISO 8601 parsing (replaces 'Z' with '+00:00' for fromisoformat)
        end_dt = datetime.fromisoformat(end_utc_str.replace("Z", "+00:00"))
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        
        now = datetime.now(timezone.utc)
        if end_dt <= now:
            msg = f"CRITICAL: competition_end_utc ({end_utc_str}) is in the past"
            logger.error(msg)
            raise PreFlightCheckError(msg)
        
        hours_left = (end_dt - now).total_seconds() / 3600
        logger.info("Competition ends at %s (%.1f hours remaining)", end_utc_str, hours_left)
    except ValueError as e:
        msg = f"CRITICAL: Failed to parse competition_end_utc: {e}"
        logger.error(msg)
        raise PreFlightCheckError(msg)

    # Initialize API client for connectivity checks
    base_url = api_cfg.get("base_url", "https://mock-api.roostoo.com")
    client = RoostooClient(base_url=base_url)

    try:
        # 4. [CRITICAL] Exchange API reachable
        logger.info("Verifying API connectivity...")
        try:
            server_time_resp = await client.get_server_time()
            server_time = server_time_resp.get("ServerTime")
            if not server_time:
                raise RoostooAPIError("ServerTime field missing in response")
            
            # Check clock sync
            local_ms = int(time.time() * 1000)
            offset_ms = abs(server_time - local_ms)
            if offset_ms > 30000:
                logger.warning("Large clock offset detected: %.1fs", offset_ms / 1000)
            else:
                logger.info("API reachable, clock offset: %.1fms", offset_ms)
        except Exception as e:
            msg = f"CRITICAL: API connectivity test failed: {e}"
            logger.error(msg)
            raise PreFlightCheckError(msg)

        # 5. [CRITICAL] Batch price endpoint works
        logger.info("Verifying batch price endpoint...")
        try:
            ticker_resp = await client.get_all_tickers()
            data = ticker_resp.get("Data", {})
            if not data or len(data) < 10:
                raise RoostooAPIError(f"Batch ticker returned only {len(data)} assets")
            logger.info("Batch pricing verified: %d assets discovered", len(data))
        except Exception as e:
            msg = f"CRITICAL: Batch price endpoint test failed: {e}"
            logger.error(msg)
            raise PreFlightCheckError(msg)

    finally:
        await client.close()

    # 6. [HIGH] Crash recovery check: Parquet snapshot
    paths_cfg = config.get("paths", {})
    parquet_path = Path(paths_cfg.get("parquet_backup", "data/price_backup.parquet"))
    if parquet_path.exists():
        mtime = parquet_path.stat().st_mtime
        age_hr = (time.time() - mtime) / 3600
        if age_hr < 2.0:
            logger.info("HIGH: Found recent Parquet backup (%.1f hours old)", age_hr)
            config["recovery_parquet_exists"] = True
        else:
            logger.warning("HIGH: Parquet backup found but too old (%.1f hours)", age_hr)
            config["recovery_parquet_exists"] = False
    else:
        logger.info("HIGH: No Parquet backup found (cold start)")
        config["recovery_parquet_exists"] = False

    # 7. [HIGH] Trade log exists
    log_cfg = config.get("logging", {})
    trade_log_path = Path(log_cfg.get("trade_log", "logs/trades.jsonl"))
    if trade_log_path.exists():
        logger.info("HIGH: Found trade log at %s", trade_log_path)
        config["recovery_trade_log_exists"] = True
    else:
        logger.info("HIGH: No trade log found")
        config["recovery_trade_log_exists"] = False

    # 8. [MEDIUM] Git repo is clean
    try:
        import subprocess
        result = subprocess.run(
            ["git", "status", "--porcelain"], 
            capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            if result.stdout.strip():
                logger.warning("MEDIUM: Git repo has uncommitted changes")
            else:
                logger.info("MEDIUM: Git repo is clean")
        else:
            logger.info("MEDIUM: Not a git repository or git not available")
    except Exception:
        logger.info("MEDIUM: Git status check skipped")

    logger.info("All pre-flight checks completed.")
    return config
