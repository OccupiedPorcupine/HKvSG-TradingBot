# APEX Implementation Plan

**Version:** 1.0 | **Date:** 15 March 2026
**Competition:** SGxHK Web3 Coin Trading Hackathon (22 March - 1 April 2026)
**Deadline to have trading bot live:** 22 March 2026, competition start

---

## Table of Contents

1. [Current State](#1-current-state)
2. [Architecture Overview](#2-architecture-overview)
3. [Phase 0 — API Validation](#3-phase-0--api-validation)
4. [Phase 1 — Minimal Viable Bot](#4-phase-1--minimal-viable-bot)
5. [Phase 2 — Regime-Aware Enhancement](#5-phase-2--regime-aware-enhancement)
6. [Phase 3 — ML Overlay](#6-phase-3--ml-overlay)
7. [Phase 4 — Live Tuning](#7-phase-4--live-tuning)
8. [Competition Day Runbook](#8-competition-day-runbook)
9. [File Map](#9-file-map)

---

## 1. Current State

### What exists and works

| File | Status | Description |
|------|--------|-------------|
| `src/data/api_client.py` | **Complete** | Async Roostoo REST client, HMAC signing, Content-Type text/plain handling |
| `src/data/rate_limiter.py` | **Complete** | Token bucket with priority tokens, 429 backoff, safe mode |
| `src/execution/roostoo_client.py` | **Complete** | Execution wrapper with precision formatting, PairInfo, OrderResult |
| `src/execution/priority_queue.py` | **Complete** | Order priority queue (CRITICAL > reductions > entries > adjustments) |
| `src/risk/risk_event.py` | **Complete** | RiskEvent dataclass with severity and priority ordering |
| `config.yaml` | **Complete** | All ~150 parameters populated, Phase 0 findings as comments |
| `contingency-15Mar.md` | **Complete** | Phase 0 findings, discrepancies, Day 1 action items |
| `dependencies.md` | **Complete** | Full dependency list with explicit exclusions |

### What exists but is partial

| File | Coverage | What's missing |
|------|----------|----------------|
| `src/portfolio/constructor.py` | ~60% | Vol-adjusted sizing, PAXG, adaptive exposure, end-game |
| `src/execution/order_manager.py` | ~70% | Fill timeout loop, resubmission escalation |
| `src/execution/position_tracker.py` | ~80% | Daily P&L tracking, NAV computation |
| `src/execution/decision_logger.py` | ~70% | Buffered flush logic |
| `src/portfolio/beta_monitor.py` | ~40% | Rolling regression, beta targeting |
| `src/portfolio/adaptive.py` | ~50% | Naive benchmark tracking |
| `src/risk/trailing_stops.py` | ~40% | Dynamic tightening, end-game tightening |
| `src/risk/circuit_breakers.py` | ~60% | Drawdown recovery cooldown |
| `src/risk/pre_trade_checks.py` | ~70% | DOGE/TRUMP special caps |
| `src/risk/contagion.py` | ~50% | Integration with regime detector |
| `src/adaptation/signal_health.py` | ~40% | Halt/resume logic |
| `src/adaptation/performance_log.py` | ~50% | Snapshot format |
| `src/adaptation/ml_retrain.py` | ~10% | Stub only |

### What does not exist yet

| File | Phase needed |
|------|-------------|
| `src/data/ingestion.py` | Phase 1 |
| `src/data/features.py` | Phase 1 |
| `src/data/validators.py` | Phase 1 |
| `src/regime/detector.py` | Phase 2 |
| `src/regime/regime_state.py` | Phase 2 |
| `src/signals/momentum.py` | Phase 1 |
| `src/signals/meme_pool.py` | Phase 2 |
| `src/signals/tier5_pool.py` | Phase 2 |
| `src/signals/ml_overlay.py` | Phase 3 |
| `src/signals/signal_output.py` | Phase 1 |
| `src/main.py` | Phase 1 |
| `src/orchestration/scheduler.py` | Phase 1 |
| `src/orchestration/startup.py` | Phase 1 |
| `src/orchestration/safe_state.py` | Phase 1 |
| `src/orchestration/recovery.py` | Phase 1 |

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        main.py                               │
│   Orchestrator: startup → event loop → shutdown              │
│   asyncio.create_task() for each cadence                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │  60s: Data    │    │  60s: Risk   │    │ 5min: Regime  │  │
│  │  Ingestion    │    │  Checks      │    │ Update        │  │
│  │  (Layer 1)    │    │  (Layer 6)   │    │ (Layer 3)     │  │
│  └──────┬───────┘    └──────┬───────┘    └──────┬────────┘  │
│         │                   │                    │           │
│         ▼                   │                    │           │
│  ┌──────────────┐           │                    │           │
│  │  Features     │◄──────────┘                    │           │
│  │  (Layer 2)    │───────────────────────────────►│           │
│  └──────┬───────┘                                            │
│         │                                                    │
│         ▼                                                    │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │  60min:       │    │  Portfolio    │    │  Execution    │  │
│  │  Signals      │───►│  Constructor  │───►│  Engine       │  │
│  │  (Layer 4)    │    │  (Layer 5)    │    │  (Layer 7)    │  │
│  └──────────────┘    └──────────────┘    └───────────────┘  │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐                       │
│  │  1h: Signal   │    │  24h: ML     │                       │
│  │  Health       │    │  Retrain     │                       │
│  │  (Layer 8a)   │    │  (Layer 8c)  │                       │
│  └──────────────┘    └──────────────┘                       │
└─────────────────────────────────────────────────────────────┘
```

**Data flow:** Prices → Ring Buffers → Features → Regime + Signals → Portfolio Weights → Risk Checks → Orders → Position Book

**Single process, asyncio only.** No threads, no external services, no databases.

---

## 3. Phase 0 — API Validation

**Status: DONE (pre-competition, 15 March)**

### Confirmed findings

| Parameter | Value |
|-----------|-------|
| Base URL | `https://mock-api.roostoo.com` |
| Batch pricing | YES — `/v3/ticker` with no `pair` param returns all tickers |
| Volume data | YES — `CoinTradeValue` + `UnitTradeValue` |
| Content-Type | `text/plain` (handled in `api_client.py` line 210) |
| Bid-ask spread | ~$0.01 on BTC — effectively zero |
| Universe | 67 pairs on API, 54 expected + 13 extra (AVAX, SOL, XRP, etc.) |
| Auth signing | HMAC SHA256, sorted params, `RST-API-KEY` + `MSG-SIGNATURE` headers |
| MiniOrder | 1 (minimum 1 unit of any coin) |
| Rate limit (pre-competition) | 50 req/sec (will tighten under competition load) |

### Unresolved — must test on Day 1 (22 March)

| Item | What to test | Impact if different |
|------|-------------|-------------------|
| Starting capital | Currently $50K in test env, spec says $1M | All absolute thresholds scale 20x |
| Limit fill mechanics | Place small limit buy at LastPrice, check Status == "FILLED" | If not instant-fill, need timeout/resubmission for normal trades too |
| Rate limit under load | Re-test with 300+ teams connected | If 30/min: max ~6-8 trades per rebalance cycle |
| Pair count | Currently 67, may change | Universe discovery handles this automatically |

### Day 1 action script

```bash
# On competition day, before starting the bot:
export ROOSTOO_API_KEY="..."
export ROOSTOO_API_SECRET="..."
python scripts/phase0_api_test.py        # Re-validate all findings
# Then manually update config.yaml with confirmed values
```

### Cleanup needed

There are 4 Phase 0 test scripts with significant overlap:
- **Keep:** `scripts/phase0_api_test.py` (API layer) + `scripts/phase0_execution_test.py` (execution layer)
- **Archive or delete:** `scripts/phase0_test.py` (duplicate of api_test), `scripts/api_test.py` (standalone client, wrong import path `apex.core.config`)

---

## 4. Phase 1 — Minimal Viable Bot

**Goal:** A bot that fetches prices, ranks assets by momentum, buys the top N, tracks positions, enforces trailing stops, and handles basic risk.

**What Phase 1 includes:**
- Data ingestion with ring buffers
- Cross-sectional momentum ranking (Tier 1-3 only)
- Equal-weight sizing with tier caps
- Fixed trailing stops (6%/8%/10% by tier)
- Drawdown circuit breaker (8% halt)
- Limit order execution with 1-min timeout
- Decision logging
- Basic crash recovery (restart clean)
- Minimal vol guard (no regime detector yet)
- Simple "sell all at T-1h" end-game

**What Phase 1 does NOT include:**
- Regime detection (hardcode TREND_BULL)
- Trend penalty
- Vol-adjusted sizing
- PAXG allocation
- Dynamic stop tightening
- Meme pool / Tier 5 pool
- Adaptive exposure
- Full end-game schedule
- ML anything
- Signal health monitoring

### Step 1.1 — Data Ingestion & Features

**Layers:** 1 and 2
**Prompt file:** `agent/implementation/implementator_A.md`
**Depends on:** Phase 0 complete, `api_client.py` and `rate_limiter.py` exist

#### What to build

| File | Description |
|------|-------------|
| `src/data/ingestion.py` | Async batch price fetcher on 60s cadence. Stores to `collections.deque` ring buffers (maxlen=1440 = 24h). Fill-forward up to 3 missing bars, STALE after 4. Parquet backup every hour. |
| `src/data/features.py` | Incremental feature computation. Returns (5m/15m/1h/4h/12h/24h), volatility (1h/4h/24h rolling stdev), EMAs (20/50/60/240), cross-sectional percentile ranks, momentum composite score (1h:20% + 4h:40% + 12h:25% + 24h:15%). Volume ratio if volume data available. |
| `src/data/validators.py` | Data quality: STALE detection (3 bars fill-forward, 4th = STALE), price anomaly detection (>5σ single-bar return), global STALE_DATA flag if batch call fails. |

#### AI session instructions

> You are implementing Phase 1 of APEX.
> Read `shared_context.md` and `implementator_A.md` completely.
> Read the existing `src/data/api_client.py` and `src/data/rate_limiter.py` — these are complete, do not rewrite them.
> Build `ingestion.py`, `features.py`, and `validators.py`.
>
> Phase 1 scope only:
> - Ring buffer storage with deque(maxlen=1440)
> - Batch price fetching using `RoostooClient.get_all_tickers()`
> - All return and momentum features
> - EMA(20), EMA(50), EMA(60), EMA(240) — incremental
> - Cross-sectional percentile ranking
> - Momentum composite score
> - Volume features (volume data is confirmed available)
> - BTC vol percentile computation (needed even for minimal vol guard)
> - Regime input features (btc_trend, altcoin_breadth, btc_vol_percentile) — compute them but they won't be consumed until Phase 2
> - Fill-forward and STALE logic
> - Feature interface: `get_momentum_scores()`, `get_regime_inputs()`, `get_ema_values()`, `get_asset_volatility()`, `get_all_features()`

#### How to verify

```bash
# Write a small test script that:
# 1. Starts ingestion against the mock API
# 2. Collects 5 minutes of price data
# 3. Prints momentum composite scores for all assets
# 4. Verifies no NaN/Inf in any feature
# 5. Verifies STALE detection works (mock a gap)
```

---

### Step 1.2 — Simplified Signals

**Layer:** 4 (Layer 3 skipped in Phase 1)
**Prompt file:** `agent/implementation/implementator_B.md`
**Depends on:** Step 1.1 (features interface)

#### What to build

| File | Description |
|------|-------------|
| `src/signals/momentum.py` | Cross-sectional momentum ranking. Consumes `get_momentum_scores()` from features. Selects top N=10 from Tier 1-3 by composite score. No trend penalty yet. |
| `src/signals/signal_output.py` | `SignalOutput` dataclass: `main_pool_selections: dict[str, float]`, `meme_pool_selections: dict[str, float]`, `tier5_pool_selections: dict[str, float]`, `ml_multipliers: dict[str, float]`, `current_regime: str`. |
| `src/signals/meme_pool.py` | **Stub** — returns empty dict. |
| `src/signals/tier5_pool.py` | **Stub** — returns empty dict. |
| `src/signals/ml_overlay.py` | **Stub** — returns `{asset: 1.0}` for all assets. |
| `src/regime/regime_state.py` | `RegimeState` dataclass with all fields. Hardcode `current_regime = TREND_BULL` for Phase 1. |

#### AI session instructions

> You are implementing Phase 1 signals.
> Read `shared_context.md` and `implementator_B.md`.
> Read the feature interface from `src/data/features.py` (Step 1.1 output).
>
> Phase 1 only:
> - Implement `momentum.py` with cross-sectional momentum ranking, top-N selection.
> - Hardcode regime to TREND_BULL (top N = 10 from config).
> - Skip trend penalty (set to 0).
> - Stub meme_pool, tier5_pool, ml_overlay (return empty/passthrough).
> - Create RegimeState dataclass but don't implement the detector.
> - Implement minimal vol guard: if `btc_vol_percentile > 90` (from features), reduce selection to top 4 and cap exposure at 40%.

#### How to verify

```bash
# Feed momentum.py the output of features.py
# Verify it selects exactly 10 assets from Tier 1-3
# Verify STALE assets are excluded from ranking
# Verify scores are ordered correctly
# Verify minimal vol guard triggers when btc_vol_percentile > 90
```

---

### Step 1.3 — Simplified Portfolio & Risk

**Layers:** 5 and 6
**Prompt file:** `agent/implementation/implementator_C.md`
**Depends on:** Step 1.2 (signal output)

#### What to build (complete the partial files)

| File | What to add |
|------|-------------|
| `src/portfolio/constructor.py` | Phase 1: fixed 80% deployment target (no regime modulation). Equal-weight sizing among N selected assets. Tier cap enforcement with redistribution loop (max 5 iterations). Turnover constraint (25%). Minimum trade threshold ($2,000 or 0.2% NAV). No vol-adjust, no PAXG, no adaptive, no end-game schedule. Simple "sell all at T-1h". |
| `src/risk/trailing_stops.py` | Phase 1: fixed stop distances (6% Tier 1-3, 8% Tier 4-5, 10% TRUMP). Peak price tracking. Stop trigger check every 60s. Emit CRITICAL RiskEvent on trigger. No dynamic tightening. |
| `src/risk/circuit_breakers.py` | Phase 1: 8% portfolio drawdown → HALT new entries. 5% daily loss → reduce all to 50%. Drawdown recovery: 2h cooldown, resume at 50% size, full size when within 4% of peak. |
| `src/risk/pre_trade_checks.py` | Phase 1: verify tier caps, total exposure <= deployment target, TRUMP <= 2%, DOGE <= 5%. Fix violations before passing to execution. |

#### AI session instructions

> You are implementing Phase 1 portfolio construction and risk management.
> Read `shared_context.md` and `implementator_C.md`.
> Read ALL existing partial files in `src/portfolio/` and `src/risk/` first.
> Read `src/risk/risk_event.py` (complete — use this dataclass).
>
> Phase 1 only:
> - Fixed 80% deployment target (no regime lookup).
> - Equal-weight sizing: `deployment_target / N` per asset.
> - Tier cap enforcement with redistribution.
> - Trailing stops at fixed widths, no tightening.
> - Circuit breakers: drawdown + daily loss.
> - Simple end-game: sell all at T-1h (read COMPETITION_END_UTC from config).
> - Skip: vol-adjusted sizing, PAXG, adaptive exposure, dynamic stop tightening, contagion circuit breaker, beta monitoring, full end-game schedule.

#### How to verify

```bash
# Unit test: 10 equal-weight assets, verify each gets 8% (80%/10)
# Unit test: tier cap clips a Tier 3 asset at 6%, redistributes excess
# Unit test: trailing stop triggers when price drops 6% from peak
# Unit test: circuit breaker halts at 8% drawdown
# Unit test: sell-all triggers at T-1h
```

---

### Step 1.4 — Execution Wiring

**Layers:** 7 and 8 (partial)
**Prompt file:** `agent/implementation/implementator_D.md`
**Depends on:** Step 1.3 (portfolio output, risk events)

#### What to build (complete the partial files)

| File | What to add |
|------|-------------|
| `src/execution/order_manager.py` | Complete: order submission flow — compute order size from weight delta, check minimum threshold, submit limit at LastPrice, 1-min fill timeout, cancel and resubmit, max 3 resubmissions. Risk exit acceleration (>2% adverse → market after 1 fail). Process orders in priority order via `priority_queue.py`. |
| `src/execution/position_tracker.py` | Complete: update cost basis on buy fills (VWAP), reduce quantity on sells, update peak_price every bar, compute NAV (positions + cash), track daily P&L (reset at 00:00 UTC). |
| `src/execution/decision_logger.py` | Complete: buffer log entries in list, flush every 10s or 100 entries. Each entry has all fields from implementator_D spec. Non-blocking writes. |

#### AI session instructions

> You are implementing Phase 1 execution engine.
> Read `shared_context.md` and `implementator_D.md`.
> Read ALL existing files in `src/execution/` — `roostoo_client.py`, `priority_queue.py`, and partial `order_manager.py`, `position_tracker.py`, `decision_logger.py`.
>
> Phase 1 only:
> - Complete the order manager: limit orders at LastPrice, 1-min timeout, resubmit up to 3x, escalate to market only for CRITICAL risk exits.
> - Complete position tracker: cost basis, peak price, NAV, daily P&L.
> - Complete decision logger: buffered JSON lines.
> - Skip: signal health monitoring, performance snapshots (just stub them).

#### How to verify

```bash
# Integration test against mock API:
# 1. Place a small limit buy for DOGE/USD
# 2. Verify OrderResult is parsed correctly
# 3. Verify position_tracker updates cost_basis and peak_price
# 4. Verify decision_logger writes a valid JSONL entry
# 5. Verify priority queue processes CRITICAL before normal
```

---

### Step 1.5 — Orchestration

**Layer:** Orchestration (main loop)
**Prompt file:** `agent/implementation/implementator_E.md`
**Depends on:** Steps 1.1-1.4 (all layers)

#### What to build

| File | Description |
|------|-------------|
| `src/main.py` | Entry point. Loads config, runs pre-flight checks, starts event loop, handles SIGTERM/SIGINT for graceful shutdown. |
| `src/orchestration/scheduler.py` | Manages cadenced jobs: `data_ingestion_tick` (60s), `risk_check_tick` (60s, independent), `rebalance_tick` (60min). Each job has an `asyncio.Lock` to prevent overlap. |
| `src/orchestration/startup.py` | Pre-flight checklist: API creds present, config valid, COMPETITION_END_UTC parseable, exchange reachable, batch pricing works. CRITICAL failures → exit. |
| `src/orchestration/safe_state.py` | Per-job safe state: data fail → use last prices; risk fail → halt new trades; rebalance fail → hold positions. Global safe state after 3 consecutive failures. |
| `src/orchestration/recovery.py` | Phase 1: simple clean restart. Load config, initialize empty deques, start fresh. (Full Parquet recovery in Phase 2.) |

#### AI session instructions

> You are implementing the Phase 1 orchestration layer.
> Read `shared_context.md` and `implementator_E.md`.
> Read `config.yaml` for all parameter keys.
> Read the interfaces of all previously built modules:
>   - `src/data/ingestion.py` (data fetching)
>   - `src/data/features.py` (feature computation)
>   - `src/signals/momentum.py` (signal generation)
>   - `src/portfolio/constructor.py` (weight computation)
>   - `src/risk/trailing_stops.py`, `circuit_breakers.py`, `pre_trade_checks.py`
>   - `src/execution/order_manager.py`, `position_tracker.py`
>
> Phase 1 only:
> - Wire together: 60s data → 60s risk → 60min rebalance.
> - Pre-flight checks (CRITICAL only: creds, config, exchange reachable).
> - Simple safe-state fallback.
> - Skip: Parquet crash recovery, regime_update_tick (hardcoded TREND_BULL), monitoring_tick, ml_retrain_tick, full end-game schedule, minimum activity enforcement.
> - Add the simple "sell all at T-1h" end-game check inside data_ingestion_tick.

#### How to verify

```bash
# Full integration test:
python src/main.py
# Bot should:
# 1. Pass pre-flight checks
# 2. Start fetching prices every 60s
# 3. After accumulating ~60 bars (1 hour), run first rebalance
# 4. Select top 10 by momentum, place limit orders
# 5. Track positions, update peak prices
# 6. Check trailing stops every 60s
# Let it run for 2-3 hours against the mock API.
# Check logs/apex.log and logs/trades.jsonl for correct entries.
```

### Phase 1 summary

After Step 1.5, you have a working trading bot that:
- Fetches all prices every 60 seconds
- Ranks assets by cross-sectional momentum
- Buys the top 10 with equal weights (80% deployed)
- Enforces tier caps (8%/8%/6%)
- Tracks trailing stops (6%/8%/10%)
- Halts on 8% drawdown or 5% daily loss
- Sells everything 1 hour before competition end
- Logs every decision to JSONL

This is sufficient to pass Gate 1 (make returns) but will not score well on Gate 2 (risk-adjusted metrics) without Phase 2.

---

## 5. Phase 2 — Regime-Aware Enhancement

**Goal:** Add regime detection, regime-conditional behavior, advanced risk management, and expand the universe coverage to Tier 4-5.

**Priority order within Phase 2** (highest value first):

### Step 2.1 — Regime Detection

**Layer:** 3
**Depends on:** Phase 1 complete (features interface)

#### What to build

| File | Description |
|------|-------------|
| `src/regime/detector.py` | Rule-based 4-state classifier. Inputs: `btc_4h_return`, `btc_24h_return`, `btc_vol_percentile`, `altcoin_breadth`, `contagion_proxy`. Classification rules exactly as specified in implementator_B. All thresholds from config.yaml. |
| `src/regime/regime_state.py` | Update the Phase 1 stub to track: `bars_in_current_regime`, `transition_pending`, `transition_target`, `transition_bars_remaining`. |
| `src/regime/contagion.py` | Complete: O(n) computation — count held positions with negative 5-min return, compute ratio and avg_loss. |

#### Key logic

```
IF contagion_ratio > 0.80 AND avg_loss > 1.0% → HIGH_VOL_CRISIS (immediate)
ELSE IF btc_vol_percentile > 90              → HIGH_VOL_CRISIS (immediate)
ELSE IF btc_4h > 0 AND btc_24h > 0 AND breadth > 55% → TREND_BULL
ELSE IF btc_4h < 0 AND btc_24h < 0 AND breadth < 40% → TREND_BEAR
ELSE → MEAN_REVERT
```

**Transitions:** Downgrades are immediate. Upgrades require 30 consecutive confirming bars. CRISIS exit requires contagion < 0.50 AND vol < 70th percentile for 30 bars.

**Cadence:** Classification runs every 5 minutes. Contagion proxy runs every 1 minute.

#### Orchestration change

Add `regime_update_tick` (5min) to the scheduler. Replace hardcoded TREND_BULL with live regime.

---

### Step 2.2 — Regime-Conditional Deployment + Vol-Adjusted Sizing

**Layer:** 5
**Depends on:** Step 2.1 (regime state)

#### Changes to `src/portfolio/constructor.py`

**Step 1 — Deployment targets:**
```
TREND_BULL, low vol (btc_vol_pct <= 70):   80%
TREND_BULL, rising vol (btc_vol_pct > 70): 65%
MEAN_REVERT:                                55%
TREND_BEAR:                                 35%
HIGH_VOL_CRISIS:                            15%
```

**Step 3b — Vol-adjusted sizing:**
```python
inv_vol_i = 1.0 / get_asset_volatility(asset, "24h")
vol_adjusted_weight_i = base_weight * (inv_vol_i / mean(all_inv_vol))
# Normalize so sum = deployment_target
```

**Step 4 — Regime-conditional top-N:**
```
TREND_BULL:      top 10
MEAN_REVERT:     top 5-6
TREND_BEAR:      top 3-4
HIGH_VOL_CRISIS: top 0 (exit to cash)
```

---

### Step 2.3 — Trend Penalty

**Layer:** 4
**Depends on:** Step 2.1 (regime), Step 1.1 (EMA features)

#### Change to `src/signals/momentum.py`

For each Tier 1-3 asset:
```python
ema_60, ema_240 = features.get_ema_values(asset)
if ema_60 < ema_240:
    adjusted_score = raw_score + config["trend_penalty_magnitude"]  # -0.3
else:
    adjusted_score = raw_score
```

This is a **penalty**, not an exit override. Strong momentum assets survive it. Weak momentum + negative trend fall below top-N naturally.

---

### Step 2.4 — Dynamic Trailing Stop Tightening

**Layer:** 6
**Depends on:** Step 1.4 (position tracker daily P&L)

#### Change to `src/risk/trailing_stops.py`

Track daily portfolio P&L (realized + unrealized, reset at 00:00 UTC).

```
> +2.0% daily P&L: tighten ALL stops by 40%  (6% → 3.6%)
> +1.0% daily P&L: tighten ALL stops by 20%  (6% → 4.8%)
> -0.5% to +1.0%:  normal distances
< -0.5%:           circuit breakers handle this

effective_stop = max(base_stop * (1 - tightening_pct), 0.02)  # 2% floor
```

---

### Step 2.5 — PAXG Allocation

**Layer:** 5
**Depends on:** Step 2.1 (regime)

#### Addition to `src/portfolio/constructor.py`

PAXG is sized from the **cash** portion, not the crypto deployment budget.

```
TREND_BULL:       5% of NAV
MEAN_REVERT:     10% of NAV
TREND_BEAR:      12% of NAV
HIGH_VOL_CRISIS: 15% of NAV
```

Never apply momentum signals or ML multiplier to PAXG. Hard cap at 15%.

---

### Step 2.6 — Meme Pool & Tier 5 Pool

**Layer:** 4
**Depends on:** Step 2.1 (regime — TREND_BULL gating)

#### `src/signals/meme_pool.py` (complete the stub)

- Rank Tier 4 meme coins (SHIB, PEPE, FLOKI, WIF, BONK, 1000CHEEMS, PUMP, PENGU) against each other by momentum
- Select top 1-2 at 3% allocation each
- Active ONLY in TREND_BULL. All other regimes → empty.
- TRUMP excluded from meme pool during TREND_BEAR/CRISIS

#### `src/signals/tier5_pool.py` (complete the stub)

- Rank Tier 5 assets (SOMI, AVNT, MIRA, etc.) against each other
- Activation: 4h return > 10% AND in top 3 of Tier 5
- Top 1-2 qualifying at 1-2% allocation each
- Active ONLY in TREND_BULL
- 8% trailing stop (same as meme)

---

### Step 2.7 — Adaptive Exposure Adjustment

**Layer:** 5
**Depends on:** Step 2.2 (regime-conditional deployment)

#### Complete `src/portfolio/adaptive.py`

Track naive benchmark: equal-weight 70% exposure to all assets.

```python
relative_gap = naive_benchmark_return - portfolio_return

if relative_gap > 4.0% and days_remaining > 3:
    exposure_adjustment = +0.20
elif relative_gap > 2.0% and days_remaining > 5:
    exposure_adjustment = +0.10
else:
    exposure_adjustment = 0.0

effective_target = min(base_regime_target + exposure_adjustment, 0.90)
```

Only triggers when our defensive posture is causing us to fall behind.

---

### Step 2.8 — Full End-Game De-Risking

**Layer:** 5 + Orchestration
**Depends on:** Step 2.4 (dynamic stop tightening)

Replace the simple "sell all at T-1h" with the full schedule:

| Time remaining | Exposure cap | Stop tightening |
|---------------|-------------|----------------|
| > 48h | None (full regime target) | None |
| 24-48h | 70% | None |
| 12-24h | 45% | None |
| 4-12h | 25% | All stops → 3% |
| 1-4h | 15% | All stops → 2% |
| < 15min | 0% (SELL ALL) | N/A |

End-game caps are **irreversible** — once crossed, they never relax.

Add `end_game_check()` inside `data_ingestion_tick` to check thresholds every 60 seconds.

---

### Step 2.9 — Contagion Circuit Breaker + BTC Beta Monitoring

**Layer:** 6
**Depends on:** Step 2.1 (contagion proxy)

#### `src/risk/contagion.py` (complete)

If `contagion_ratio > 0.80` AND `avg_loss > 1.0%`:
- Emit `RiskEvent(CONTAGION_CRISIS, CRITICAL)`
- Force `HIGH_VOL_CRISIS` regime immediately
- Reduce all positions to 20% of current size

#### `src/portfolio/beta_monitor.py` (complete)

Rolling 7-day regression of asset returns on BTC returns.
```
portfolio_beta = sum(weight_i * beta_i)
Target: 0.4-0.6 in TREND_BULL, < 0.3 otherwise
```
Soft adjustment — reduce highest-beta weights if exceeding target by > 0.1.

---

### Step 2.10 — Monitoring & Crash Recovery

**Layer:** 8 + Orchestration

#### `src/adaptation/signal_health.py` (complete)

- 24h rolling hit rate: % positions with positive return after 1 rebalance
- Warning at < 45%, halt at < 35% for 12h
- On halt: reduce to 30% exposure, hold only top 3 by unrealized P&L
- Resume when hit rate > 50% for 6h

#### `src/adaptation/performance_log.py` (complete)

Hourly snapshots to `logs/snapshots.jsonl`: NAV, daily P&L, cumulative return, regime, all positions, portfolio beta, hit rate, contagion proxy.

#### `src/orchestration/recovery.py` (complete)

Crash recovery: load hourly Parquet backup (if < 2h old) into deques, reconstruct positions from `logs/trades.jsonl`.

#### Orchestration changes

Add `monitoring_tick` (1h) and Parquet backup to scheduler. Add job overlap detection with `asyncio.Lock`. Add minimum activity enforcement (force small trade if 24h idle).

### Phase 2 summary

After Phase 2, the bot has:
- 4-state regime detection driving all behavior
- Regime-conditional deployment (15%-80% crypto exposure)
- Vol-adjusted position sizing
- PAXG as Treynor hedge (5-15% from cash)
- Dynamic trailing stop tightening (preserves gains)
- Full end-game de-risking schedule
- Meme and Tier 5 sub-pools (TREND_BULL only)
- Adaptive exposure when falling behind benchmark
- Contagion circuit breaker
- BTC beta monitoring
- Signal health monitoring with auto-halt
- Crash recovery from Parquet

This should score well on both Gate 1 (returns via adaptive exposure and broader universe) and Gate 2 (risk metrics via regime awareness, stop tightening, PAXG, beta management).

---

## 6. Phase 3 — ML Overlay

**Goal:** Add a LightGBM directional model as a position size multiplier on top of momentum signals. Does not change which assets are selected — only adjusts how much of each.

**Depends on:** Phase 2 complete. Requires `lightgbm` and `scikit-learn` installed.

### Step 3.1 — ML Retraining Pipeline

#### Complete `src/adaptation/ml_retrain.py`

**Training spec:**
- Model: LightGBM binary classifier
- Target: `sign(return_4h)` — 1 if positive, 0 if negative
- Features: all features from `get_all_features()` with rolling z-score normalization (100-period lookback, expanding window for first 100 obs)
- Training data: 60+ days pre-loaded as Parquet (need to source this separately)
- Walk-forward: retrain every 24h on most recent data
- Fixed hyperparameters from config.yaml:
  ```yaml
  max_depth: 6
  min_child_samples: 50
  feature_fraction: 0.7
  n_estimators: 500
  early_stopping_rounds: 50
  ```

**Post-retrain sanity check:**
1. Run new model on last 5 minutes of live data
2. Compute prediction distribution (mean, std, min, max)
3. Compare to old model on same data
4. If any metric differs by > 2σ → REJECT, keep old model, log `RETRAIN_REJECTED`
5. Else → deploy, log `RETRAIN_DEPLOYED` with IC metrics

**No:** Optuna HPO, SHAP, DVC, MLflow, 48h paper-trading gate.

### Step 3.2 — ML Signal Integration

#### Complete `src/signals/ml_overlay.py`

Read predictions from the deployed model: `P(positive 4h return)` per asset.

```python
if P > 0.65:   ml_multiplier = 1.3
elif P < 0.45: ml_multiplier = 0.5
else:          ml_multiplier = 1.0
```

Never apply to PAXG.

#### IC monitoring

```python
# Rolling 48h Information Coefficient
if rolling_48h_IC < 0.02:
    # Halt ML signal — set all multipliers to 1.0
    ml_halted = True
if ml_halted and rolling_12h_IC > 0.04:
    # 12 continuous hours above threshold
    ml_halted = False
```

### Step 3.3 — Portfolio Integration

#### Change to `src/portfolio/constructor.py` (Step 3d)

After vol-adjusted sizing and tier caps:
```python
for asset in selected_assets:
    if asset != "PAXG/USD":
        weights[asset] *= ml_multipliers.get(asset, 1.0)
# Re-normalize so total = deployment_target
```

### Step 3.4 — Orchestration

Add `ml_retrain_tick` (24h) to scheduler. Only active if `config.ml_enabled: true`.

### Phase 3 summary

ML overlay is a low-risk enhancement:
- Multiplier range 0.5x-1.3x means it can only adjust sizes, not create or destroy positions
- IC monitoring auto-halts if the model degrades
- Sanity check prevents deploying a broken model
- If it helps: slightly better position sizing. If it doesn't: IC monitoring disables it

---

## 7. Phase 4 — Live Tuning

**Goal:** Manual parameter adjustments based on live competition observation.

### What to tune (from `config.yaml`)

| Parameter | Watch for | Adjustment |
|-----------|----------|------------|
| `rebalance_interval_minutes` | Signal decays fast (30min correlation >> 60min) | Reduce to 30min |
| `momentum_weights` | One timeframe dominates | Increase its weight |
| `trailing_stop.*` | Stops triggering too early/late | Widen or tighten by 1-2% |
| `regime_targets.*` | Over/under-deployed vs. leaderboard | Raise/lower by 5-10% |
| `top_n.*` | Too many/few positions | Adjust by 1-2 |
| `trend_penalty_magnitude` | Trend filter too aggressive/weak | Adjust by 0.1 |
| `adaptive_exposure.*` | Falling behind/ahead of benchmark | Adjust gap thresholds |

### Tuning workflow

1. SSH into EC2
2. Check `logs/snapshots.jsonl` for latest portfolio state
3. Check leaderboard position (Gate 1 ranking)
4. Edit `config.yaml` — bot hot-reloads on next cycle (if implemented) or restart
5. `git commit -m "tune: <what and why>"` — required by competition rules

### Things NOT to tune

- Trailing stop floor (2%) — this is a safety invariant
- End-game schedule — hardcoded, not overridable
- Market order restriction — never relax this
- Risk event priority ordering — this is structural

---

## 8. Competition Day Runbook

### T-2h: Setup (22 March, before competition starts)

```bash
# 1. SSH into EC2 instance
ssh -i apex-key.pem ec2-user@<instance-ip>

# 2. Pull latest code
cd ~/apex && git pull

# 3. Set credentials
export ROOSTOO_API_KEY="<competition-key>"
export ROOSTOO_API_SECRET="<competition-secret>"

# 4. Re-run Phase 0 with real credentials
python scripts/phase0_api_test.py

# 5. Update config.yaml with confirmed values:
#    - starting_capital_usd (50K or 1M?)
#    - rate_limit_calls_per_min (actual value under load)
#    - limit_fills_immediately (true/false)
#    - total pair count

# 6. Install dependencies
pip install -r requirements.txt
```

### T-0: Competition starts

```bash
# Start the bot in a tmux session
tmux new-session -d -s apex 'python src/main.py 2>&1 | tee logs/console.log'
tmux attach -t apex
```

### Monitoring during competition

```bash
# Check if bot is alive
cat logs/heartbeat

# Tail the main log
tail -f logs/apex.log

# Check latest portfolio snapshot
tail -1 logs/snapshots.jsonl | python -m json.tool

# Check recent trades
tail -5 logs/trades.jsonl | python -m json.tool

# Check regime state
grep "regime" logs/apex.log | tail -5
```

### Emergency procedures

| Situation | Action |
|-----------|--------|
| Bot crashed | Check `logs/apex.log` for error. Fix. Restart: `python src/main.py` (Parquet recovery kicks in). |
| Rate limited | Bot enters safe mode automatically. Wait for backoff. If persistent: increase `rate_limit_calls_per_min` safety margin in config. |
| All positions liquidated | Check if circuit breaker triggered. If drawdown > 8%: 2h cooldown before resuming. |
| Bot not trading | Check `logs/trades.jsonl` timestamp. If >24h idle: minimum activity enforcement should trigger. If not: check for bugs in rebalance_tick. |
| Need to stop | `Ctrl+C` in tmux (graceful shutdown). **Never `kill -9`** — lose in-flight orders. |

---

## 9. File Map

### Complete file tree when all phases are done

```
SGxHK_Quant/
├── config.yaml                          # All ~150 tunable parameters
├── requirements.txt                     # Pinned dependencies
├── requirements-dev.txt                 # Test dependencies
├── dependencies.md                      # Dependency rationale
├── IMPLEMENTATION_PLAN.md               # This file
├── contingency-15Mar.md                 # Phase 0 findings
│
├── agent/implementation/                # AI implementation prompts
│   ├── shared_context.md                # Context for all implementors
│   ├── implementator_A.md              # Layers 1-2: Data & Features
│   ├── implementator_B.md              # Layers 3-4: Regime & Signals
│   ├── implementator_C.md              # Layers 5-6: Portfolio & Risk
│   ├── implementator_D.md              # Layers 7-8: Execution & Adaptation
│   └── implementator_E.md              # Orchestration
│
├── scripts/
│   ├── phase0_api_test.py              # Phase 0: API validation
│   └── phase0_execution_test.py        # Phase 0: Execution validation
│
├── src/
│   ├── main.py                         # Entry point (Phase 1)
│   │
│   ├── data/
│   │   ├── api_client.py               # [DONE] Roostoo REST client
│   │   ├── rate_limiter.py             # [DONE] Token bucket rate limiter
│   │   ├── ingestion.py                # [Phase 1] Batch price fetching, ring buffers
│   │   ├── features.py                 # [Phase 1] Feature computation
│   │   └── validators.py              # [Phase 1] Data quality, STALE detection
│   │
│   ├── regime/
│   │   ├── detector.py                 # [Phase 2] 4-state rule-based classifier
│   │   ├── regime_state.py            # [Phase 1 stub, Phase 2 complete]
│   │   └── contagion.py               # [Phase 2] O(n) contagion proxy
│   │
│   ├── signals/
│   │   ├── momentum.py                # [Phase 1] Cross-sectional momentum ranking
│   │   ├── meme_pool.py               # [Phase 2] Tier 4 meme sub-pool
│   │   ├── tier5_pool.py              # [Phase 2] Tier 5 opportunistic sub-pool
│   │   ├── ml_overlay.py              # [Phase 3] LightGBM size multiplier
│   │   └── signal_output.py           # [Phase 1] SignalOutput dataclass
│   │
│   ├── portfolio/
│   │   ├── constructor.py             # [Phase 1 basic, Phase 2 full]
│   │   ├── beta_monitor.py            # [Phase 2] BTC beta estimation
│   │   └── adaptive.py               # [Phase 2] Adaptive exposure adjustment
│   │
│   ├── risk/
│   │   ├── risk_event.py              # [DONE] RiskEvent dataclass
│   │   ├── trailing_stops.py          # [Phase 1 basic, Phase 2 dynamic tightening]
│   │   ├── circuit_breakers.py        # [Phase 1]
│   │   ├── contagion.py               # [Phase 2] Contagion circuit breaker
│   │   └── pre_trade_checks.py        # [Phase 1]
│   │
│   ├── execution/
│   │   ├── roostoo_client.py          # [DONE] Execution wrapper
│   │   ├── priority_queue.py          # [DONE] Order priority queue
│   │   ├── order_manager.py           # [Phase 1]
│   │   ├── position_tracker.py        # [Phase 1]
│   │   └── decision_logger.py         # [Phase 1]
│   │
│   ├── adaptation/
│   │   ├── signal_health.py           # [Phase 2] Hit rate monitoring
│   │   ├── performance_log.py         # [Phase 2] Hourly snapshots
│   │   └── ml_retrain.py             # [Phase 3] LightGBM walk-forward
│   │
│   └── orchestration/
│       ├── scheduler.py               # [Phase 1] Cadence management
│       ├── startup.py                 # [Phase 1] Pre-flight checks
│       ├── safe_state.py              # [Phase 1] Error fallback
│       └── recovery.py               # [Phase 1 stub, Phase 2 Parquet recovery]
│
├── logs/                               # Created at runtime
│   ├── apex.log                       # Main application log
│   ├── trades.jsonl                   # Decision log
│   ├── snapshots.jsonl                # Hourly performance snapshots
│   └── heartbeat                      # Last heartbeat timestamp
│
├── data/                               # Created at runtime
│   └── backup/                        # Hourly Parquet snapshots
│
└── tests/
    ├── test_ingestion.py
    ├── test_features.py
    ├── test_regime.py
    ├── test_signals.py
    ├── test_portfolio.py
    ├── test_risk.py
    ├── test_execution.py
    ├── test_adaptation.py
    ├── test_orchestration.py
    └── test_startup.py
```

---

## Build Sequence Summary

```
Phase 0:  [DONE] API validation
              │
Phase 1:  Step 1.1 Data & Features ──────► Step 1.2 Signals ──────► Step 1.3 Portfolio/Risk
              │                                                            │
              │                                                    Step 1.4 Execution
              │                                                            │
              │                                                    Step 1.5 Orchestration
              │                                                            │
              ▼                                                            ▼
          [PHASE 1 COMPLETE — bot trades]
              │
Phase 2:  Step 2.1 Regime Detection
              │
              ├──► Step 2.2 Regime Deployment + Vol Sizing
              ├──► Step 2.3 Trend Penalty
              ├──► Step 2.4 Dynamic Stop Tightening
              ├──► Step 2.5 PAXG Allocation
              ├──► Step 2.6 Meme Pool + Tier 5 Pool
              ├──► Step 2.7 Adaptive Exposure
              ├──► Step 2.8 Full End-Game Schedule
              ├──► Step 2.9 Contagion + Beta Monitor
              └──► Step 2.10 Monitoring + Recovery
              │
              ▼
          [PHASE 2 COMPLETE — bot trades intelligently]
              │
Phase 3:  Step 3.1 ML Retraining Pipeline
              ├──► Step 3.2 ML Signal Integration
              ├──► Step 3.3 Portfolio Integration
              └──► Step 3.4 Orchestration
              │
              ▼
          [PHASE 3 COMPLETE — bot trades with ML overlay]
              │
Phase 4:  Live parameter tuning (competition days 1-10)
```

**Critical path:** Phase 1 must be complete and tested by March 22.
Phase 2 can be deployed as incremental updates during competition (git commit each change).
Phase 3 is optional — only if Phase 2 is stable and there's time.
