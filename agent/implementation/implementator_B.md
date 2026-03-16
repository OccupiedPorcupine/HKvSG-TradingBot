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
You are implementing Layers 3 and 4 of APEX: regime detection and signal
generation. You are a specialist in quantitative signal research and
rule-based trading systems.

Your inputs come from the feature interface (Layer 2). Your outputs are:
  - A RegimeState object consumed by portfolio construction (Layer 5)
  - Ranked signal outputs consumed by portfolio construction (Layer 5)

You do not implement portfolio optimization, risk limits, or execution.

---

LAYER 3 — REGIME DETECTION (Rule-Based)

The regime classifier tells the rest of the system what market environment
we are in, so deployment levels and signal behavior can adapt accordingly.

NO HMM. NO GARCH. No statistical models. No libraries beyond NumPy.
This is a deterministic rule-based classifier with transparent, debuggable logic.

Four regime states:
  TREND_BULL      — broad uptrend, momentum signals reliable
  TREND_BEAR      — broad downtrend, minimize crypto exposure
  MEAN_REVERT     — choppy, no clear direction, momentum partially reliable
  HIGH_VOL_CRISIS — acute stress, capital preservation mode

RegimeState dataclass must contain:
  - current_regime: enum (one of four states above)
  - previous_regime: enum (state before the most recent transition)
  - bars_in_current_regime: int (how many 1-min bars since last transition)
  - transition_pending: bool (True during gradual upgrade transitions)
  - transition_target: enum or None (the regime we are upgrading toward)
  - transition_bars_remaining: int (countdown for gradual upgrades)
  - contagion_proxy: float (latest value, for consumption by risk layer)
  - btc_vol_percentile: float (latest value)

Classification inputs (all from Layer 2 feature interface):
  1. btc_4h_return: sign of BTC 4-hour return
  2. btc_24h_return: sign of BTC 24-hour return
  3. btc_vol_percentile: BTC 1h realized vol rank vs. trailing 7-day distribution
  4. altcoin_breadth: % of universe (excluding BTC, PAXG) with positive 4h return
  5. contagion_proxy: computed HERE in Layer 3 (not in Layer 2)

Contagion proxy computation (O(n), runs every 1-minute bar):
  held_positions = list of all currently held positions (from position tracker)
  For each held position, compute 5-minute return.
  negative_count = count(positions where 5-min return < 0)
  contagion_ratio = negative_count / len(held_positions)   # 0.0 to 1.0
  avg_loss = mean(abs(5-min return)) for positions where 5-min return < 0
  contagion_proxy = contagion_ratio  # the ratio alone
  Store avg_loss separately — used in classification rules below.
  NOTE: This requires access to current positions. The orchestration layer
  must pass the current position list to the regime detector on each update.

Classification rules (implement EXACTLY as written — order matters):

  IF contagion_ratio > 0.80 AND avg_loss > 1.0%:
      → HIGH_VOL_CRISIS (immediate, no transition delay)

  ELSE IF btc_vol_percentile > 90:
      → HIGH_VOL_CRISIS (immediate, no transition delay)

  ELSE IF btc_4h_return > 0 AND btc_24h_return > 0 AND altcoin_breadth > 55%:
      → TREND_BULL

  ELSE IF btc_4h_return < 0 AND btc_24h_return < 0 AND altcoin_breadth < 40%:
      → TREND_BEAR

  ELSE:
      → MEAN_REVERT

  All threshold values (0.80, 1.0%, 90, 55%, 40%) must be read from
  config.yaml, not hardcoded in the classification function.

Regime transition rules — ASYMMETRIC:
  DOWNGRADE transitions (any → CRISIS, BULL → BEAR): IMMEDIATE.
    No delay. No gradual rotation. Apply on the bar where detected.
    The cost of being slow to cut risk is permanent Calmar damage.

  UPGRADE transitions (BEAR → MEAN_REVERT → BULL): GRADUAL.
    Must persist for 30 minutes (30 bars at 1-min cadence) before confirming.
    During the 30-bar confirmation window: set transition_pending = True,
    transition_target = the upgrade target, transition_bars_remaining = 30.
    Decrement transition_bars_remaining on each bar where the upgrade
    condition still holds. If the condition fails during the window,
    reset: transition_pending = False, transition_target = None.
    After 30 consecutive bars confirming: apply the upgrade.
    The cost of being slow to add risk is a few basis points of missed upside.

  CRISIS exit: requires BOTH contagion_ratio < 0.50 AND btc_vol_percentile < 70
    for at least 30 consecutive minutes before exiting CRISIS.

  On every confirmed transition: log the old regime, new regime, timestamp,
  and the input values that triggered the transition.

Update cadence: regime classification runs every 5 minutes using the most
recent feature values. Contagion proxy runs every 1 minute (it's cheap — O(n)
where n = number of held positions, typically 8-12).

---

LAYER 4 — SIGNAL GENERATION

Three signals for the main system (Phase 1-2). ML overlay added in Phase 3.
All signals consume from the Layer 2 feature interface — no direct data access.

SIGNAL 1 — Cross-Sectional Momentum (primary signal, active from Phase 1):
  Input: get_momentum_scores() from Layer 2.
  Process:
    1. Retrieve composite momentum scores for all non-STALE Tier 1-3 assets.
    2. Apply trend penalty (Signal 2) to each asset's score.
    3. Rank assets by adjusted score (highest = best).
    4. Select top N assets based on current regime:
         TREND_BULL:      top 10
         MEAN_REVERT:     top 5-6
         TREND_BEAR:      top 3-4
         HIGH_VOL_CRISIS: top 0 (exit all to cash)
       These counts are read from config.yaml.
    5. Return: dict mapping each selected asset to its adjusted momentum score.
       Assets not in top-N get score = 0 (means "hold cash here").
  CRITICAL: Bottom-ranked assets are NOT shorted. Score = 0 means "no position,"
  not "short." Never generate a negative target weight from momentum signals.

SIGNAL 2 — Trend Penalty (active from Phase 2, skip in Phase 1):
  Input: get_ema_values(asset) from Layer 2.
  Process: for each asset in Tier 1-3:
    ema_60, ema_240 = get_ema_values(asset)
    IF ema_60 < ema_240:
        penalty = config["trend_penalty_magnitude"]  # default: -0.3
        adjusted_score = raw_momentum_score + penalty
        (penalty is in cross-sectional rank standard deviations)
    ELSE:
        adjusted_score = raw_momentum_score  # no penalty

  WHY penalty instead of exit override:
    - Strong momentum + weak trend: penalty reduces rank but doesn't force-exit.
      Saves 0.1% round-trip commission. Keeps partial exposure to potential
      trend recovery.
    - Weak momentum + weak trend: penalty pushes asset below top-N threshold.
      Natural exit through ranking system at next rebalance. Same outcome as
      an override, but smoother.
    - Trailing stops at 6% are the backstop for genuine reversals.
    - Fewer forced exits → lower commission drag → better Sharpe.

  DO NOT implement trend filter as an exit override. DO NOT force-sell assets
  solely because EMA(60) < EMA(240). The penalty approach is the design decision.

SIGNAL 3 — Meme Coin Sub-Pool (active from Phase 2):
  Input: within-tier rank for Tier 4 assets from Layer 2.
  Process:
    1. Get momentum scores for all Tier 4 meme coins (excluding DOGE, which
       is classified as Tier 1).
    2. Rank Tier 4 coins against each other only.
    3. Select top 1-2 meme coins by momentum score.
    4. Each selected meme coin gets 3% target allocation.
  Active ONLY in TREND_BULL regime. In all other regimes, all meme coin
  allocations = 0. No exceptions.
  TRUMP additional rule: if regime != TREND_BULL, TRUMP allocation = 0
  regardless of its momentum rank within the meme pool.

SIGNAL 4 — Tier 5 Opportunistic Sub-Pool (active from Phase 2):
  Input: within-tier rank for Tier 5 assets from Layer 2.
  Process:
    1. Get momentum scores and 4h returns for all Tier 5 assets.
    2. Activation threshold: only assets with 4h return > 10% AND in top 3
       of Tier 5 ranking are eligible.
    3. Top 1-2 qualifying Tier 5 assets get 1-2% allocation each.
    4. 8% trailing stop (same as meme coins).
  Active ONLY in TREND_BULL regime.
  Rationale: Without this, 19 assets (34% of universe by count) receive zero
  allocation under any regime. A 100% pump on a 2% position adds 2% to NAV
  with max downside of 2% × 8% = 0.16% of NAV. Convex optionality.

SIGNAL 5 — ML Directional Overlay (Phase 3 only — do not implement in Phase 1-2):
  When enabled:
    - Read pre-computed LightGBM predictions: P(positive 4h return) per asset.
    - For each asset with a momentum allocation:
        if P > 0.65: ml_multiplier = 1.3
        if P < 0.45: ml_multiplier = 0.5
        else: ml_multiplier = 1.0
    - This multiplier is applied to the BASE position size in Layer 5.
    - NEVER apply ml_multiplier to PAXG. Ever.
    - IC monitoring: if rolling 48h IC < 0.02, set all ml_multipliers to 1.0.
      Resume when IC > 0.04 for 12 continuous hours.
  When disabled (Phase 1-2): ml_multiplier = 1.0 for all assets.

Signal interaction — the COMPLETE flow for each asset at rebalance:
  1. Compute raw momentum composite score (cross-sectional rank from Layer 2)
  2. IF EMA(60) < EMA(240): apply trend penalty to score (Phase 2+)
  3. Rank by adjusted score. Select top-N based on regime.
  4. For meme pool: separate ranking, top 1-2 at 3% each (TREND_BULL only)
  5. For Tier 5 pool: separate ranking, top 1-2 at 1-2% each (TREND_BULL only)
  6. IF Phase 3 ML enabled: note ml_multiplier per asset (applied in Layer 5)
  7. IF regime == HIGH_VOL_CRISIS: override all → exit to cash

  The output of Layer 4 is:
    - main_pool_selections: dict[asset, adjusted_momentum_score] (top-N from Tier 1-3)
    - meme_pool_selections: dict[asset, score] (top 1-2 from Tier 4, or empty)
    - tier5_pool_selections: dict[asset, score] (top 1-2 from Tier 5, or empty)
    - ml_multipliers: dict[asset, float] (all 1.0 unless Phase 3 enabled)
    - current_regime: RegimeState

---

IMPLEMENTATION GUIDANCE:

No thread safety concerns. This is a single Python process with asyncio.
RegimeState is updated in the main loop coroutine and read by other coroutines
within the same event loop — no concurrent writes. Use a simple dataclass,
not a locked shared object.

Signal outputs are computed fresh on every rebalance trigger. They are not
cached between rebalances.

For Phase 1: implement only Signal 1 (momentum ranking, top N selection).
Set trend penalty to 0, meme pool empty, Tier 5 pool empty, ml_multiplier = 1.0.
Regime is not yet implemented in Phase 1 — use the minimal vol guard instead
(if BTC 24h vol > 2× median: reduce max crypto exposure to 40%).

Files to produce:
  src/regime/detector.py       — rule-based regime classification
  src/regime/regime_state.py   — RegimeState dataclass
  src/regime/contagion.py      — contagion proxy computation
  src/signals/momentum.py      — cross-sectional momentum ranking + trend penalty
  src/signals/meme_pool.py     — Tier 4 meme coin sub-pool signal
  src/signals/tier5_pool.py    — Tier 5 opportunistic sub-pool signal
  src/signals/ml_overlay.py    — ML multiplier reader and IC monitoring (Phase 3 stub)
  src/signals/signal_output.py — output dataclass for all signal results
  tests/test_regime.py
  tests/test_signals.py
