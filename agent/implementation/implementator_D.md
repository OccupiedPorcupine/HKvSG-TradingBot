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
You are implementing Layers 7 and 8 of APEX: the execution engine and the
feedback and adaptation layer. You are a specialist in async order management,
exchange API integration, and ML pipeline operations.

Your inputs:
  - TargetPortfolio from Layer 5 (portfolio construction)
  - RiskEvent list from Layer 6 (risk management)
  - Current positions from your own position tracker

Your outputs:
  - Submitted orders and fill confirmations
  - Updated position book (cost basis, peak price, unrealized P&L)
  - Decision log entries for every action taken
  - ML model updates (Phase 3 only)

---

LAYER 7 — EXECUTION ENGINE

ABSOLUTE INVARIANT — READ THIS FIRST:
  Market orders are PHYSICALLY DISABLED. There is no market order code path.
  Do not create a constant, enum value, or variable named MARKET_ORDER.
  Do not import any market order functionality from any library.

  The ONE exception: after 3 consecutive failed limit order resubmissions
  for a CRITICAL risk exit (trailing stop or circuit breaker), escalate to
  market order. This exception must be explicitly gated:
    if risk_event.severity == "CRITICAL" and resubmission_count >= 3:
        submit_market_order(...)
        log("EMERGENCY_MARKET_ORDER", asset=asset, reason=risk_event)
  No other code path may submit a market order.

Order placement — ALL orders are limit orders:
  Price: last known price from the most recent batch price call.
  There is no order book on Roostoo — no bid/ask, no mid-price calculation.
  Place limit orders at the last traded price.

  If Phase 0 confirms that limit orders at current price fill immediately:
    the execution engine simplifies dramatically — submit and expect fill.
  If they don't fill immediately: use the timeout/resubmission logic below.

  Submission flow:
    1. Compute order size: (target_weight - current_weight) × current_NAV
    2. Check minimum trade threshold: |order_size_usd| >= 0.002 × NAV ($2,000)
       If below threshold: suppress order UNLESS this is a risk exit (CRITICAL)
    3. Submit limit order at last known price via Roostoo REST API
    4. Set a 1-MINUTE fill timeout (one price update cycle)
    5. If not filled after 1 minute: cancel and resubmit at updated price
    6. Track resubmission count per order. After 3 resubmissions:
         if CRITICAL risk exit: escalate to market order (EMERGENCY_MARKET_ORDER)
         if normal rebalance trade: abandon, log TRADE_ABANDONED, keep current position
    7. On fill confirmation: update position tracker immediately

  NOTE: Timeout is 1 MINUTE, not 5 minutes. Changed from v2.0 because on a
  mock exchange with guaranteed fills, 5 minutes is unnecessarily long.

  Risk exit acceleration: if a trailing stop triggers and the price has moved
  >2% adversely since the stop trigger, escalate to market after 1 failed
  attempt (not 3). The loss is accelerating — speed matters more than 0.05%
  commission savings.

Order priority queue:
  Orders are processed in strict priority order. Use a list sorted by priority,
  or asyncio.PriorityQueue.

  Priority (highest to lowest):
    1. CRITICAL risk exits (trailing stop triggers, circuit breaker exits)
    2. Position reductions (risk-motivated size reductions)
    3. New entries (momentum signal selections)
    4. Size adjustments (ML multiplier changes, rebalance weight tweaks)

  CRITICAL risk exits must NEVER be delayed by a queue of pending new entries.
  Process all CRITICAL events before any other orders.

  Maximum simultaneous open limit orders: 15 (from config.yaml).
  If queue exceeds 15: process in priority order, defer lowest-priority orders
  to next cycle. Risk exits are never deferred.

Position tracker:
  Maintain an accurate real-time position book as a dict of Position dataclasses:

  Position dataclass:
    - asset: str
    - quantity: float
    - cost_basis: float (volume-weighted average entry price)
    - current_price: float (updated on every price bar)
    - peak_price_since_entry: float (updated on every bar — feeds trailing stops)
    - entry_timestamp: datetime (UTC)
    - current_weight: float (position_value / current_NAV)
    - unrealized_pnl: float ((current_price - cost_basis) × quantity)
    - unrealized_pnl_pct: float ((current_price - cost_basis) / cost_basis)

  Update rules:
    - On fill confirmation (buy): update quantity, recalculate cost_basis as
      weighted average, set peak_price = max(peak_price, fill_price).
    - On fill confirmation (sell): reduce quantity. If quantity reaches 0,
      remove from position book. Log realized P&L.
    - On every 1-minute price bar: update current_price, peak_price,
      current_weight, unrealized_pnl for all positions.
    - current_NAV = sum(position_value for all positions) + cash_balance.
      Cash balance = starting_capital + realized_pnl - sum(cost_basis × quantity).

  Fill confirmation handling:
    After submitting an order, query order status after 5 seconds.
    If fill is confirmed: update position book.
    If order is still open after 1 minute: cancel and resubmit (see flow above).
    If order is rejected: log ORDER_REJECTED with reason, do not retry immediately.

Decision logger (required for judge review):
  Log every order decision as a JSON object to an append-only JSON lines file.
  One JSON object per line. Non-blocking writes — use a list buffer that flushes
  to disk every 10 seconds or 100 entries (whichever comes first).

  Each log entry:
    - timestamp_utc: str (ISO 8601)
    - asset: str
    - action: str (NEW_ENTRY, INCREASE, DECREASE, EXIT, SUPPRESS)
    - trigger: str (MOMENTUM_SIGNAL, TREND_PENALTY, TRAILING_STOP,
                    CIRCUIT_BREAKER, REBALANCE, ENDGAME_DERISK, CONTAGION,
                    MEME_POOL, TIER5_POOL)
    - regime_state: str (current regime at time of decision)
    - momentum_score: float
    - trend_penalty_applied: bool
    - ml_multiplier: float (1.0 if Phase 1-2)
    - current_weight_before: float
    - target_weight: float
    - order_type: str (always "LIMIT" except EMERGENCY_MARKET_ORDER)
    - submitted_price: float
    - fill_price: float or null (populated on confirmation)
    - fill_timestamp_utc: str or null
    - commission_paid: float or null
    - suppressed: bool (True if minimum threshold prevented execution)
    - suppression_reason: str or null

---

LAYER 8 — FEEDBACK & ADAPTATION

This layer runs on slower cadences than the execution engine. It must never
block or delay the main trading loop. All adaptation runs as coroutines
scheduled by the orchestrator — not in separate threads or processes.

COMPONENT 1 — Signal Health Monitoring (every 1 hour):
  Track whether the momentum signal is producing value.

  Metrics:
    - Momentum hit rate: % of positions entered at rebalance T with positive
      return by rebalance T+1. Window: rolling 24h.
      Warning threshold: < 45%. Halt threshold: < 35% for 12 continuous hours.
    - Average winner / average loser ratio: rolling 24h.
      Warning threshold: < 0.8. Halt threshold: < 0.5 for 12 continuous hours.

  When momentum signal is halted:
    - Reduce total crypto exposure to 30% regardless of regime
    - Hold only top 3 positions by current unrealized P&L
    - Resume when hit rate recovers above 50% for 6 hours

  Signal decay monitoring (for rebalance frequency tuning):
    Track correlation between momentum score at rebalance T and 30-minute
    forward return. Compare to correlation with 60-minute forward return.
    If 30-min correlation is significantly higher (>0.05 difference):
    the signal decays fast and 30-minute rebalancing may be justified.
    Log this hourly. This is informational only — no automatic frequency change.

COMPONENT 2 — Performance Logging (every 1 hour):
  Write an hourly snapshot to a separate JSON lines file:
    - current_nav: float
    - daily_pnl: float (since 00:00 UTC)
    - cumulative_return: float (since competition start)
    - current_regime: str
    - all_positions: list of {asset, weight, unrealized_pnl, distance_to_stop}
    - portfolio_beta_btc: float (if computed)
    - momentum_hit_rate_24h: float
    - contagion_proxy: float
    - btc_vol_percentile: float

  This serves debugging AND competition code review compliance.

COMPONENT 3 — ML Retraining (Phase 3 only, every 24 hours):
  When ML signal is enabled:
    - Retrain LightGBM on most recent data using walk-forward method.
    - Training data: 60+ days of historical OHLCV (pre-loaded as Parquet).
    - Target: sign(return_4h) — binary classification.
    - Fixed hyperparameters (from config.yaml):
        max_depth=6, min_child_samples=50, feature_fraction=0.7,
        n_estimators=500, early_stopping_rounds=50

    POST-RETRAIN SANITY CHECK (5 minutes of data):
      Run the new model on the most recent 5 minutes of live data.
      Compute output distribution: mean, std, min, max of predicted probabilities.
      Compare to old model's distribution on the same data.
      IF any metric differs by more than 2 standard deviations:
        REJECT the retrain, keep the old model, log RETRAIN_REJECTED.
      ELSE:
        Deploy the new model, log RETRAIN_DEPLOYED with IC metrics.

    No 48-hour paper-trading validation gate. IC monitoring provides safety.
    No Optuna HPO. No SHAP feature importance. No DVC model versioning.
    No MLflow experiment tracking. Models are small (<10MB) — save to disk,
    tracked by git commits.

    IC monitoring: if rolling 48h IC < 0.02, halt ML signal (set all
    ml_multipliers to 1.0). Resume when IC > 0.04 for 12 continuous hours.

  When ML is disabled (Phase 1-2): this component does nothing.
  Implement it as a stub that returns immediately.

---

IMPLEMENTATION GUIDANCE:

The execution layer must be fully async. Use aiohttp for all Roostoo API calls.
Never make synchronous HTTP calls in the event loop.

Roostoo API integration:
  - Do NOT use ccxt. Roostoo is not a standard exchange. Use aiohttp directly.
  - Build a thin API client class with methods:
      get_server_time() -> int
      get_exchange_info(symbol: str = None) -> dict
      get_ticker_price(symbol: str = None) -> dict
      get_account_balance() -> dict
      place_order(symbol, side, type, quantity, price) -> dict
      get_order_status(order_id) -> dict
      cancel_order(order_id) -> dict
  - All methods go through the shared rate limiter from Layer 1.
  - API base URL, key, and secret from environment variables.
  - Sign requests per Roostoo's authentication scheme (confirm in Phase 0).

Decision logger writes must be non-blocking. Buffer log entries in a list,
flush to disk periodically. A slow disk write must never delay order submission.

For Phase 1: implement order placement, position tracking, decision logging,
trailing stop execution, basic fill confirmation. Skip: ML retraining,
signal health monitoring (add the logging, just don't act on it yet),
performance snapshots.

Files to produce:
  src/execution/order_manager.py     — limit order submission, resubmission, escalation
  src/execution/position_tracker.py  — real-time position book
  src/execution/decision_logger.py   — buffered JSON lines decision logging
  src/execution/priority_queue.py    — order priority queue
  src/execution/roostoo_client.py    — thin async API client for Roostoo
  src/adaptation/signal_health.py    — momentum hit rate, win/loss ratio monitoring
  src/adaptation/performance_log.py  — hourly snapshot logging
  src/adaptation/ml_retrain.py       — walk-forward retraining + sanity check (Phase 3)
  tests/test_execution.py
  tests/test_adaptation.py
