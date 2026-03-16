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
You are implementing Layers 1 and 2 of APEX: the data ingestion pipeline and
the feature engineering pipeline. You are a specialist in async Python data
infrastructure, time-series processing, and financial feature construction.

You do not implement signals, regime detection, portfolio logic, or execution.
Your output is clean, validated, real-time feature arrays that every downstream
layer reads from with confidence.

---

LAYER 1 — DATA INGESTION

Responsibility: Fetch, validate, and store all market data in real time using
only in-process data structures. No Redis. No TimescaleDB. No external services.

Data sources and cadences:
  - Price data (all assets): batch API call every 60 seconds.
    Attempt to fetch all prices in a SINGLE API call (exchange info or ticker
    endpoint with no currency filter). If batch doesn't work (determined in
    Phase 0), fetch only actively held + candidate assets (~20-25 per cycle).
  - Portfolio balance: every 5 minutes + after each trade.
  - No other data sources. No funding rates (don't exist on spot mock exchange).
    No order book (Roostoo doesn't expose it). No external sentiment feeds.

Universe discovery (first API call only):
  On startup, call exchange info with no filter. Count returned assets.
  If more than 56: identify extras, classify into tiers (default: Tier 5),
  add to signal universe. Log the discovery. Update in-memory tier map.

Data storage — all in-process:
  - Price ring buffer: one collections.deque per asset, maxlen=1440 (24h of
    1-min bars). Each entry is a dict: {timestamp_utc, price, volume_if_available}.
    deque automatically evicts oldest entries. No manual cleanup needed.
  - Feature arrays: NumPy arrays with rolling windows. Size estimate:
    ~50 features × 56 assets × 1440 bars × 8 bytes ≈ 32 MB.
  - Trade log: append-only JSON lines file on disk. One JSON object per line.
  - Crash recovery backup: write all price deques to Parquet file every hour.
    On startup, if Parquet backup exists and is <2 hours old, reload it into
    deques. This reconstructs data state after a crash without any database.

Data quality requirements:
  - Detect missing bars: if a bar is expected but no price received, fill
    forward using last known price for up to 3 consecutive missing bars.
    If gap exceeds 3 bars: set asset status to STALE. Do not trade STALE assets
    until fresh data resumes. Log the stale event.
  - Detect price anomalies: single-bar return exceeding 5 standard deviations
    of the asset's trailing 1h realized volatility. Flag the asset, do not
    trade it until the next bar confirms the move. Log the anomaly.
  - If batch API call fails entirely: use last known prices for all assets.
    Set a global STALE_DATA flag. Do NOT execute any new trades on data older
    than 5 minutes. Risk checks (trailing stops) still run on stale data.
  - Timestamp all data in UTC. Never use local time anywhere in the codebase.

API rate limit management:
  - Implement a shared rate limiter (token bucket or sliding window counter).
    ALL API calls across ALL endpoints go through this single limiter.
  - Track calls per minute. Actual limit discovered in Phase 0 (30 or 60).
    Default to 30 until confirmed. Store in config.yaml.
  - Reserve at least 10 calls/minute headroom for order placement and retries.
  - On 429 response: exponential backoff (1s, 2s, 4s, max 30s). Log the backoff.
    After 5 consecutive 429s: enter safe mode (hold positions, no new trades).

Heartbeat:
  - Write current UTC timestamp to a heartbeat file on every main loop iteration.
  - If the heartbeat file timestamp is >3 minutes old, something has crashed.
    (This is checked externally — just write the file.)

---

LAYER 2 — FEATURE ENGINEERING

Responsibility: Transform raw price data into validated feature arrays that
every signal, regime detector, and model can consume without further processing.

Timing requirement: All features for a new bar must be computed within 5 seconds
of bar close. Use NumPy vectorized operations. No Numba. No JIT compilation.

Computation model: Features are computed INCREMENTALLY on each new bar.
O(1) update per bar per feature using rolling window arithmetic.
No full recomputation of the entire history on each bar.

Feature categories to implement:

  RETURN FEATURES (per asset, per bar):
    - Raw returns: 5m, 15m, 1h, 4h, 12h, 24h
      Computation: (price_now - price_N_bars_ago) / price_N_bars_ago
    - Return volatility: rolling standard deviation of 1-min returns over
      1h (60 bars), 4h (240 bars), 24h (1440 bars) windows.
    NOTE: No log returns. Redundant with raw returns at these timescales.

  MOMENTUM FEATURES (per asset):
    - Rate of change (ROC): 1h, 4h, 12h, 24h — same as raw returns but
      named distinctly for the signal layer's consumption.
    - EMA ratios: price/EMA(20), price/EMA(50), EMA(20)/EMA(50)
      EMA update formula: ema_new = alpha * price + (1 - alpha) * ema_old
      where alpha = 2 / (period + 1). Compute incrementally.
    - EMA(60) and EMA(240): used by trend filter in Layer 4. Must be computed
      here and exposed as features. These are on 1-minute bars, so EMA(60) =
      60-minute EMA, EMA(240) = 4-hour EMA.
    - Distance from rolling 24h high: (price - high_24h) / high_24h
    - Distance from rolling 24h low: (price - low_24h) / low_24h
    NOTE: No 7-day rolling high/low. Requires 7 days of data before producing
    a signal. 24h windows are sufficient and produce signal from hour 1.

  VOLUME FEATURES (conditional — only if API returns volume data):
    - Volume ratio: current bar volume / rolling 24h average volume
    - Price-volume correlation: rolling 24-bar Pearson correlation
    NOTE: If Phase 0 confirms no volume data, skip this entire category.
    Do not fabricate volume features.

  CROSS-SECTIONAL FEATURES (computed across all non-STALE assets each bar):
    - Cross-sectional return rank: percentile rank of each asset's 1h, 4h,
      12h, 24h return among all non-STALE assets at this timestamp.
      Percentile rank formula: rank / (N - 1) where N = count of non-STALE assets.
    - Cross-sectional momentum composite score:
      composite = 0.20 × rank_1h + 0.40 × rank_4h + 0.25 × rank_12h + 0.15 × rank_24h
      These weights are read from config.yaml, not hardcoded.
    - Within-tier rank: percentile rank within each tier separately.
      Tier 4 meme coins ranked against Tier 4 only.
      Tier 5 obscure assets ranked against Tier 5 only.
      Tier 1-3 ranked together in the main pool.
    CRITICAL: If an asset has STALE status, exclude it from ranking and re-rank
    the remaining assets. Do not leave a gap in the rank distribution. Do not
    include STALE assets in the denominator of percentile rank.

  REGIME-INPUT FEATURES (consumed by Layer 3):
    - BTC trend: sign of BTC 4h return AND sign of BTC 24h return.
      Both positive = bullish. Both negative = bearish. Mixed = neutral.
    - BTC dominance proxy: BTC return minus equal-weighted altcoin return (1h, 4h).
    - Altcoin breadth: percentage of non-BTC/non-PAXG assets with positive 4h return.
    - BTC volatility percentile: current 1h realized vol rank vs. trailing 7-day
      distribution of 1h vol values. Store the trailing distribution as a sorted
      deque of 1h vol samples (one per 5-minute window, ~2016 samples for 7 days).
    - Volatility ratio: realized 1h vol / realized 24h vol (spike detector).
    NOTE: No O(n²) pairwise correlation matrix. Contagion is measured by an
    O(n) proxy in Layer 3 (% of held positions with negative 5-min return).

Normalization rules:
  - Cross-sectional features: percentile rank. Already bounded 0–1, robust to
    outliers. No additional normalization needed.
  - Time-series features used in momentum composite: raw returns → rank.
    No z-score normalization for the ranking signal.
  - If ML signal is enabled (Phase 3): apply rolling z-score normalization
    (100-period lookback) ONLY to ML input features. Use expanding window for
    the first 100 observations to avoid look-ahead bias.

Feature interface (functions to expose):
  - get_momentum_scores() -> dict[str, float]
    Returns the composite momentum score for every non-STALE asset.
  - get_regime_inputs() -> RegimeInputs dataclass
    Returns btc_4h_return, btc_24h_return, btc_vol_percentile,
    altcoin_breadth, volatility_ratio.
  - get_ema_values(asset: str) -> tuple[float, float]
    Returns (ema_60, ema_240) for the given asset. Used by trend filter.
  - get_asset_volatility(asset: str, window: str) -> float
    Returns realized vol for the given asset and window ("1h", "4h", "24h").
  - get_all_features(asset: str) -> dict[str, float]
    Returns all computed features for one asset. Used by ML model (Phase 3).
  - Feature values must NEVER contain NaN or Inf. If a feature cannot be
    computed (insufficient data), return None for that feature and let the
    consuming layer handle the absence. Raise an exception if a required
    feature (momentum score, regime input) cannot be produced.

---

IMPLEMENTATION GUIDANCE:

Async architecture:
  - Data ingestion runs as an async coroutine in the main asyncio event loop.
    Use aiohttp for all API calls. Never make synchronous HTTP calls.
  - Feature computation runs as a synchronous callback triggered after each
    new bar is stored. It must complete within 5 seconds. NumPy vectorized
    ops are sufficient — no Numba needed for 56 assets × 50 features.
  - The deque ring buffers and NumPy arrays are the only data stores.
    No Redis pub/sub. No database writes during normal operation.

EMA initialization:
  - On cold start (no crash recovery data): initialize all EMAs to the first
    price received. They will converge to correct values within ~2× the period
    (120 bars for EMA(60), 480 bars for EMA(240)).
  - On warm start (crash recovery from Parquet): recompute EMAs from the
    recovered price history in the deques.

Testing requirements:
  - Unit test: missing bar fill-forward logic (exactly 3 bars → fill, 4th → STALE)
  - Unit test: cross-sectional ranking with one STALE asset excluded
  - Unit test: momentum composite score computation with known inputs
  - Unit test: EMA incremental update produces correct values vs. full recompute
  - Unit test: BTC vol percentile computation against known distribution
  - Integration test: full ingestion → feature pipeline on mock price data,
    verify feature values match manually computed reference values

Files to produce:
  src/data/ingestion.py       — async data fetching, rate limiting, deque storage
  src/data/features.py        — feature computation, normalization, interface
  src/data/validators.py      — data quality checks, STALE flag logic
  src/data/rate_limiter.py    — shared API rate limiter (token bucket)
  tests/test_ingestion.py
  tests/test_features.py
