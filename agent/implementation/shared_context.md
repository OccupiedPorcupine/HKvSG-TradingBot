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
