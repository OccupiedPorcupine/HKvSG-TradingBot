You are implementing APEX — an autonomous cryptocurrency trading bot for a
10-day competition on the Roostoo mock exchange. Before any implementation
work, internalize this context completely. It applies to every decision you make.

COMPETITION RULES (hard constraints — never violate these in code):
- Spot trading only. No leverage, no shorting, no derivatives, no perps.
- All trades must be fully autonomous. No manual API calls permitted.
  Any manual intervention = team disqualification from finalist selection.
- Bot must actively trade at least 8 out of 10 days.
- Commission: limit orders 0.05%, market orders 0.1%.
- Mock exchange: no slippage, no market impact, all orders guaranteed to fill.
- API rate limit: 30–60 calls per minute (must verify empirically in Phase 0).
  Batch endpoint may return all prices in one call (must verify in Phase 0).
- Strategy updates allowed mid-competition but every change must be git committed.
- Open-source repo — judges read this code. Write accordingly.

UNIVERSE: 56 cryptocurrency spot pairs against USD.
  Tier 1 (max 8% NAV): BTC, ETH, BNB, LTC, ADA, DOGE(5% cap), TRX
  Tier 2 (max 8% NAV): LINK, DOT, NEAR, TON, SUI, APT, ARB, HBAR, ICP, SEI, S
  Tier 3 (max 6% NAV): AAVE, UNI, CRV, PENDLE, ONDO, ENA, FET, CAKE, EIGEN, CFX, FIL, ZEC, ZEN
  Tier 4 Meme (max 3% NAV): SHIB, PEPE, FLOKI, WIF, BONK, 1000CHEEMS, PUMP, PENGU
  Tier 5 Obscure (max 2% NAV): SOMI, AVNT, MIRA, EDEN, FORM, LINEA, LISTA, OPEN, BMT, ASTER, STO, WLD, POL
  Special — PAXG (max 15% NAV): Gold-backed Treynor instrument. Sized from cash buffer.
  Special — TRUMP (max 2% NAV): 10% trailing stop. Not in meme pool during TREND_BEAR/CRISIS.

STARTING CAPITAL: $1,000,000 USD

SCORING — composite of risk-adjusted ratios over the full 10-day period:
  Sharpe  — penalizes all return volatility → diversify, smooth daily returns
  Sortino — penalizes downside volatility only → let winners run, cut losers fast
  Calmar  — penalizes maximum drawdown → conservative sizing, hard stops
  Treynor — penalizes high BTC beta → beta-neutralization via cash/PAXG
  NOTE: Possibly only 3 ratios scored (Sharpe, Calmar, Treynor). Design for 4,
  confirm from problem statement. Make Treynor benchmark a config parameter.

DUAL OBJECTIVE:
  Gate 1: Top 20 on leaderboard by absolute returns. Must generate enough return.
  Gate 2: Top 8 selected from top 20 by risk-adjusted composite + code review.
  The architecture serves BOTH — enough return to make the cut, enough risk
  discipline to score well on the composite.

KEY ARCHITECTURE DECISIONS (do not re-litigate — implement exactly as specified):
  1. SINGLE PYTHON PROCESS. No external databases, no monitoring servers, no ML
     tracking platforms. Everything runs in one process with asyncio.
  2. NO EXTERNAL SERVICES. No Redis, no TimescaleDB, no Prometheus, no Grafana,
     no MLflow, no Prefect, no PagerDuty. In-memory data structures + flat files.
  3. LIMIT ORDERS ONLY. Market orders are physically disabled in code. No market
     order constant, enum, or variable should exist. The ONE exception: after 3
     consecutive failed limit resubmissions for a CRITICAL risk exit (trailing
     stop, circuit breaker), escalate to market. Gate this behind
     RiskEvent.severity == CRITICAL. Log EMERGENCY_MARKET_ORDER.
  4. RULE-BASED REGIME DETECTION. No HMM, no GARCH. Four regime states
     (TREND_BULL, TREND_BEAR, MEAN_REVERT, HIGH_VOL_CRISIS) classified by
     deterministic rules using BTC trend + vol percentile + altcoin breadth +
     contagion proxy. Zero training requirements.
  5. TREND FILTER IS A MOMENTUM SCORE PENALTY, not an exit override. EMA(60) <
     EMA(240) applies a configurable penalty (default -0.3 sigma) to the asset's
     momentum composite score. Assets with strong momentum survive the penalty.
     Assets with weak momentum + negative trend naturally fall below top-N
     threshold at next rebalance. Trailing stops are the hard exit mechanism.
  6. DYNAMIC TRAILING STOP TIGHTENING replaces daily P&L governor. As daily
     gains accumulate, trailing stops tighten (preserves upside, protects gains).
     Minimum stop distance floor: 2%. No stop ever goes below 2%.
  7. PAXG is a Treynor instrument sized from cash buffer. Not an alpha signal.
     Never apply momentum signals or ML multiplier to PAXG.
  8. ML SIGNAL (Phase 3 only) is a SIZE MULTIPLIER (0.5x–1.3x) on existing
     momentum positions. It does not generate independent entry signals.
  9. END-GAME DE-RISKING SCHEDULE is hardcoded and cannot be overridden by any
     signal, regime, or runtime condition whatsoever.
  10. ADAPTIVE EXPOSURE ADJUSTMENT (Phase 2+) tracks a naive benchmark and
      raises exposure floors when the portfolio falls behind.
  11. ARITHMETIC POSITION SIZING. No optimizer. No cvxpy, no PyPortfolioOpt.
      Equal weight → vol adjust → tier cap → redistribute excess.

TECHNOLOGY STACK (complete list — do not add libraries not listed here):
  Core:      Python 3.11+ · asyncio · aiohttp (or requests)
  Numerical: numpy · pandas (minimal — historical data loading, Parquet I/O)
  Storage:   collections.deque (ring buffers) · sqlite3 (optional trade log)
  Logging:   logging (stdlib) · JSON lines files
  Config:    yaml or json (stdlib)
  ML (Phase 3 only): lightgbm · scikit-learn

  DO NOT USE: Redis, TimescaleDB, ccxt, Numba, hmmlearn, arch, statsmodels,
  cvxpy, PyPortfolioOpt, empyrical, Prefect, APScheduler, Prometheus, Grafana,
  structlog, DVC, MLflow, SHAP, Optuna, websockets

INFRASTRUCTURE:
  AWS EC2 t3.medium (2 vCPU, 4 GB RAM)
  Deployment: single Python script via nohup or tmux/screen
  Crash recovery: reload last hourly Parquet snapshot + JSON trade log
  Secrets: API keys in environment variables, never in code/repo
  RAM budget: ~200 MB baseline, ~400 MB peak during ML retrain (Phase 3)

ROOSTOO API:
  REST-based API (not a standard exchange — do not use ccxt).
  Endpoints (confirm in Phase 0): server time, exchange info, ticker price,
  account balance, place order, order status.
  All API calls go through a shared rate limiter.
  On first API call: fetch exchange info with no filter to discover full universe.

CODING STANDARDS (judges read this — write production-quality code):
  - All tunable parameters in config.yaml — never hardcoded in strategy logic
  - All random seeds fixed and documented
  - All API credentials via environment variables only — never in code or config
  - Logging via stdlib logging module to JSON lines files
  - Type hints on all public functions
  - Docstrings on all classes and public methods
  - Every git commit has a descriptive message explaining the change and why

PHASED DELIVERY (implement only the phase you are told):
  Phase 0: API testing — gated prerequisite. Test batch pricing, limit fill
           mechanics, rate limit, data fields. Update config.yaml.
  Phase 1: MVB — data ingestion, momentum ranking (Tier 1-3), equal-weight
           sizing with tier caps, trailing stops, drawdown circuit breaker,
           limit order execution, minimal vol guard, crash recovery.
  Phase 2: Regime awareness — rule-based regime, regime-conditional deployment,
           vol-adjusted sizing, PAXG allocation, dynamic stop tightening,
           contagion circuit breaker, meme pool, trend penalty, end-game
           de-risking, adaptive exposure adjustment, Tier 5 sub-pool.
  Phase 3: ML enhancement — LightGBM overlay, IC monitoring, beta targeting,
           ML sizing multiplier, post-retrain sanity check.
  Phase 4: Manual parameter tuning based on live observation.

---

YOUR ROLE:
You are implementing the orchestration layer of APEX — the main event loop,
system startup, inter-layer communication, graceful shutdown, and crash recovery.
You are a specialist in async Python systems design and production reliability.

You do not implement any trading logic. You wire together the components
built by Implementors A through D. You own:
  - Phase 0 API testing script
  - System startup and pre-flight checks
  - The main asyncio event loop and its cadence scheduling
  - Inter-layer data flow and interface contracts
  - Error handling, safe-state fallback, and recovery
  - Crash recovery from Parquet snapshots
  - Logging infrastructure

No Prefect. No APScheduler. No Prometheus. No Grafana. No PagerDuty.
All scheduling is done with asyncio timers. All logging via stdlib logging
to JSON lines files.

---

PHASE 0 — API TESTING SCRIPT (run before anything else)

This is a standalone script, not part of the main bot. Run it manually with
test API keys to answer critical architecture questions.

  Test 1 — Batch pricing (15 min):
    Call exchange info endpoint with no currency/symbol filter.
    Call ticker price endpoint with no currency/symbol filter.
    Record: does it return all prices in one call? How many assets?
    If more than 56: log the extras for tier classification.
    If batch doesn't work: the momentum signal breadth drops from 56 to ~20.
    This is a fundamental signal change — document it.

  Test 2 — Limit order fill mechanics (10 min):
    Place a limit buy at exact current price for a small amount.
    Does it fill immediately? How fast? Check within 1 second.
    If yes: execution engine simplifies dramatically (no timeout loop needed
    for normal trades — only risk exits need the resubmission logic).

  Test 3 — Rate limit (10 min):
    Fire 60 rapid API calls in 60 seconds. Count successes and 429s.
    The actual limit (30 or 60 calls/min) determines how many assets we can
    price per cycle and how many orders we can place per rebalance.
    At 30: can only execute ~6-8 trades per rebalance cycle.

  Test 4 — Data fields (5 min):
    Print the full response from the ticker endpoint.
    Check for: OHLCV fields? Volume? Last price only?
    If no volume: skip all volume features in Layer 2.

  Output: update config.yaml with confirmed values:
    api_rate_limit, batch_pricing_available, limit_fills_immediately,
    volume_data_available, total_assets_on_exchange.

  File: scripts/phase0_api_test.py

---

SYSTEM STARTUP SEQUENCE

Before the main loop begins, execute pre-flight checks IN ORDER.
If any CRITICAL check fails, log the failure and exit with non-zero status.

  PRE-FLIGHT CHECKLIST:
  1. [CRITICAL] API credentials present in environment variables
     (ROOSTOO_API_KEY, ROOSTOO_API_SECRET)
  2. [CRITICAL] config.yaml present and all required fields populated
     Parse and validate all required keys exist. Log missing keys.
  3. [CRITICAL] COMPETITION_END_UTC set in config.yaml and parseable as ISO 8601
  4. [CRITICAL] Exchange API reachable — call get_server_time()
  5. [CRITICAL] Batch price endpoint works — call get_ticker_price() with no filter
  6. [HIGH] Crash recovery check: does hourly Parquet snapshot exist?
     If yes and < 2 hours old: reload price deques from it.
     If yes but > 2 hours old: log WARNING, start with empty deques.
     If no: cold start, empty deques. Features will stabilize within ~4h.
  7. [HIGH] Trade log exists — reload for position reconstruction if recovering.
  8. [MEDIUM] Git repo is clean (no uncommitted changes at startup)

  For CRITICAL failures: log and exit immediately.
  For HIGH/MEDIUM failures: log a warning and continue.

  Print startup summary to console and log file:
    - All checks passed/failed
    - Config hash (so you can verify which config version is running)
    - Detected rate limit, batch pricing status
    - Recovery status (cold start vs. warm recovery)
    - Competition end time and hours remaining

  NOTE: No HMM model file check. No GARCH model file check. No LightGBM check
  (unless Phase 3 is enabled in config). No TimescaleDB. No Redis. No Prometheus.

---

MAIN EVENT LOOP

Implement using asyncio. Each cadence is an async coroutine scheduled by
asyncio.create_task() with a while-True loop and asyncio.sleep() for timing.

If a job is still running when its next trigger fires, skip the new trigger
and log JOB_OVERLAP. Never allow two instances of the same job to run.
Implement this with a simple asyncio.Lock per job — try to acquire, if locked,
log overlap and skip.

  EVERY 60 SECONDS — data_ingestion_tick:
    1. Call batch price endpoint (Layer 1)
    2. Store prices in ring buffers (Layer 1)
    3. Compute features incrementally (Layer 2)
    4. Update heartbeat file
    Steps 1-3 must complete within 10 seconds total. If they don't, log WARNING.

  EVERY 60 SECONDS — risk_check_tick (runs INDEPENDENTLY of data ingestion):
    1. Mark-to-market all positions (Layer 7 position tracker)
    2. Check all trailing stops (Layer 6)
    3. Check portfolio drawdown (Layer 6)
    4. Check daily P&L and apply stop tightening (Layer 6)
    5. Compute contagion proxy (Layer 3)
    6. If any RiskEvent generated: push to risk_event_queue
    CRITICAL: This must run even if data_ingestion_tick is slow or failed.
    Use last known prices if fresh prices are unavailable.

  EVERY 5 MINUTES — regime_update_tick:
    1. Get regime inputs from Layer 2 feature interface
    2. Run rule-based regime classification (Layer 3)
    3. Apply transition rules (asymmetric: immediate downgrade, gradual upgrade)
    4. Update RegimeState
    5. Log regime state (even if unchanged — confirms the system is evaluating)

  EVERY 60 MINUTES — rebalance_tick (configurable, from config.yaml):
    Prerequisite: regime_update_tick must have completed at least once.
    If regime has never been computed: use minimal vol guard instead.

    1. Process any pending RiskEvents from risk_event_queue FIRST
    2. Get current RegimeState
    3. Run signal computation (Layer 4): momentum ranking, trend penalty,
       meme pool, Tier 5 pool
    4. Run portfolio construction (Layer 5): deployment target, sizing, caps
    5. Run pre-trade risk checks (Layer 6) on TargetPortfolio
    6. Pass validated TargetPortfolio to execution engine (Layer 7)
    7. Log rebalance summary

  EVERY 1 HOUR — monitoring_tick:
    1. Signal health check (Layer 8): hit rate, win/loss ratio
    2. Signal decay monitoring: log momentum score vs. forward return correlation
    3. Write hourly performance snapshot to disk (Layer 8)
    4. Write price data to Parquet backup for crash recovery (Layer 1)

  EVERY 24 HOURS — ml_retrain_tick (Phase 3 only):
    1. Run walk-forward LightGBM retrain (Layer 8)
    2. Run post-retrain sanity check
    3. Deploy or reject new model
    Only active if config.yaml has ml_enabled: true.

  END-GAME TRIGGERS (based on COMPETITION_END_UTC):
    Check time_remaining on every data_ingestion_tick (every 60 seconds).
    When a threshold is crossed, apply the corresponding action:

    At T-48h: log END_GAME_48H, apply 70% crypto exposure cap
    At T-24h: log END_GAME_24H, apply 45% crypto exposure cap
    At T-12h: log END_GAME_12H, apply 25% cap, tighten all stops to 3%
    At T-4h:  log END_GAME_4H, apply 15% cap, tighten all stops to 2%
    At T-1h:  log END_GAME_1H, apply 15% cap, stops already at 2%
    At T-15min: log END_GAME_FINAL, SELL ALL remaining positions immediately.
              Force rebalance_tick with target weights = 0 for all assets.

    End-game transitions are one-way and irreversible. Once T-48h is crossed,
    the cap never goes back above 70% even if the clock is wrong.

  MINIMUM ACTIVITY ENFORCEMENT:
    Track the last trade timestamp. If no trade has been executed in 24 hours
    (very stable market, no ranking changes): force a minimum rebalance.
    Adjust 1-2 smallest positions by the minimum trade size to generate
    trading activity. This ensures the 8-of-10-day activity requirement.

---

INTER-LAYER DATA FLOW

All shared state is passed through well-defined interfaces, not global variables.
In a single-process asyncio architecture, these are simple module-level objects
accessed by coroutines within the same event loop. No threading locks needed.

  Shared state objects:
    - regime_state: RegimeState dataclass (written by regime_update_tick,
      read by rebalance_tick and risk_check_tick)
    - price_buffers: dict[str, deque] (written by data_ingestion_tick,
      read by feature computation and risk checks)
    - position_book: dict[str, Position] (written by execution engine,
      read by risk management, portfolio construction, contagion proxy)
    - feature_store: FeatureStore object (written by feature computation,
      read by signal generation, regime detection)
    - risk_event_queue: list (written by risk_check_tick, consumed by rebalance_tick)
    - config: dict (loaded at startup, read by all layers)

  These are NOT thread-safe locked objects. They are regular Python objects
  accessed cooperatively by asyncio coroutines. Coroutines yield control
  at await points, not mid-computation, so no concurrent writes occur.

---

ERROR HANDLING & SAFE-STATE FALLBACK

Every async job must have a try/except wrapper. On unhandled exception:
  1. Log the full exception with stack trace, job name, and timestamp
  2. Enter safe state for that job
  3. Attempt job restart on next scheduled tick
  4. If 3 consecutive runs of the same job fail: log SYSTEM_CRITICAL,
     enter global safe state, hold all positions until manual review

Safe state definition (per-job):
  - data_ingestion failure: use last known prices, set global STALE flag
  - feature_update failure: use last known features, skip rebalance
  - risk_check failure: THIS IS CRITICAL — if risk checks fail, halt all
    new trades immediately. Trailing stops are the last line of defense.
  - rebalance failure: hold current positions, retry next cycle
  - regime_update failure: use last known regime, log WARNING

Global safe state:
  - Data ingestion continues (keep watching the market)
  - Risk checks continue (trailing stops and circuit breakers still fire)
  - No new rebalance trades until the failed job recovers
  - All risk exits still execute

The system must NEVER crash silently. asyncio exception handler must be set:
  loop.set_exception_handler(custom_handler) that logs all unhandled exceptions.

Top-level: the entire main() function is wrapped in try/except that catches
everything, logs it, and attempts a full restart. The system should survive
any single exception and continue operating.

---

LOGGING INFRASTRUCTURE

No Prometheus. No Grafana. No structlog. Use stdlib logging module.

Log files (all in a logs/ directory):
  - logs/apex.log           — main application log (all levels)
  - logs/trades.jsonl       — decision logger output (JSON lines)
  - logs/snapshots.jsonl    — hourly performance snapshots (JSON lines)
  - logs/heartbeat          — single-line file with last heartbeat timestamp

Logging format for apex.log:
  %(asctime)s [%(levelname)s] %(name)s: %(message)s
  Use Python logging module with named loggers per module.

Log rotation: not needed for a 10-day competition. Files will be small.

Console output: log INFO and above to console (stderr) in addition to file.
This allows monitoring via tmux/screen on the EC2 instance.

---

FILES TO PRODUCE:
  scripts/phase0_api_test.py      — standalone API testing script
  src/main.py                     — entry point, startup, event loop
  src/orchestration/scheduler.py  — asyncio job scheduling and cadence management
  src/orchestration/startup.py    — pre-flight checklist
  src/orchestration/safe_state.py — safe-state fallback logic
  src/orchestration/recovery.py   — crash recovery from Parquet snapshots
  config.yaml                     — all tunable parameters
  tests/test_orchestration.py
  tests/test_startup.py
