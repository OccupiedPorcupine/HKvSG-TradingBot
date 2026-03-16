# APEX Implementation TODO

> Competition: Mar 22 – Apr 1, 2026 | Starting Capital: $1,000,000 | 56 crypto pairs on Roostoo mock exchange

---

## Phase 0 — API Validation (PREREQUISITE)

- [ ] **`scripts/phase0_api_test.py`** — Write and run the API smoke-test script
  - [ ] Confirm batch pricing endpoint works and returns all 56 pairs
  - [ ] Verify limit orders fill immediately at last price
  - [ ] Determine actual rate limit (30 vs 60 calls/min)
  - [ ] Check if volume data is available (affects feature engineering)
  - [ ] Update `config.yaml` with confirmed values (base URL, rate limit, fill behavior)
- [ ] Validate API key / environment variable setup

---

## Phase 1 — Minimum Viable Bot

### Layer 1: Data Ingestion (Implementor A)

- [ ] **`apex/data/client.py`** — Async HTTP client (aiohttp)
  - [ ] Batch price fetcher (all 56 pairs every 60s)
  - [ ] Portfolio balance fetcher (every 5 min + post-trade)
  - [ ] Token-bucket rate limiter
  - [ ] Retry logic with exponential backoff
  - [ ] Error handling for API downtime / malformed responses
- [ ] **`apex/data/ring_buffer.py`** — Price storage
  - [ ] `collections.deque` ring buffers (maxlen=1440 per pair)
  - [ ] Forward-fill for missing bars
  - [ ] Data quality checks (stale prices, anomaly detection)
- [ ] **`apex/data/recovery.py`** — Crash recovery
  - [ ] Hourly Parquet snapshot writer
  - [ ] Parquet loader on startup (if snapshot < 2 hours old)

### Layer 2: Feature Engineering (Implementor A)

- [ ] **`apex/features/compute.py`** — Incremental feature computation
  - [ ] Returns at 6 windows (5m, 15m, 1h, 4h, 12h, 24h)
  - [ ] Rolling volatility (1h, 4h, 24h)
  - [ ] EMAs (fast/medium/trend-fast/trend-slow)
  - [ ] Cross-sectional momentum ranking
  - [ ] BTC vol percentile (vs 7-day rolling distribution)
  - [ ] Altcoin breadth (% with positive 4h return)
  - [ ] Volume features (conditional on Phase 0 results)

### Layer 4: Signal Generation — Momentum Only (Implementor B)

- [ ] **`apex/signals/momentum.py`** — Cross-sectional momentum signal
  - [ ] Composite score: 1h/4h/12h/24h returns (weights: 0.20/0.40/0.25/0.15)
  - [ ] Tier 1–3 universe filtering
  - [ ] Top-N selection (hardcode N=10 for Phase 1)
  - [ ] Signal output: ranked list with scores

### Layer 5: Portfolio Construction — Equal Weight (Implementor C)

- [ ] **`apex/portfolio/construction.py`** — Position sizing
  - [ ] Equal-weight allocation across selected assets
  - [ ] Tier cap enforcement (8% / 6% / 5% / 3% / 2% / 15%)
  - [ ] Excess weight redistribution
  - [ ] Max turnover constraint (25% one-way per rebalance)
  - [ ] Min trade threshold ($2,000 = 0.2% NAV)
  - [ ] Compute target vs current → generate trade list

### Layer 6: Risk Management — Hard Limits (Implementor C)

- [ ] **`apex/risk/manager.py`** — Risk checks (every 60s)
  - [ ] Drawdown > 8% → halt new entries (2h cooldown)
  - [ ] Daily loss > 5% → reduce all positions to 50% of target
  - [ ] Single-asset loss > 6% from entry → close position
  - [ ] Trailing stops (Tier 1–3: 6%, Tier 4–5: 8%, TRUMP: 10%)
  - [ ] Min stop floor: 2% (hardcoded, never violated)

### Layer 7: Execution Engine (Implementor D)

- [ ] **`apex/execution/engine.py`** — Order management
  - [ ] Limit order submission at last known price
  - [ ] 60-second fill timeout → cancel + resubmit at updated price
  - [ ] Abandon after 3 resubmissions (or escalate if CRITICAL)
  - [ ] Priority queue: risk exits > reductions > new entries > adjustments
  - [ ] Max 15 simultaneous open orders
  - [ ] Risk exit acceleration (>2% adverse → escalate after 1 failure)

### Layer 8: Monitoring — Basic (Implementor D)

- [ ] Wire `status_writer.py` into main loop (already implemented)
- [ ] Trade logging to `trades.jsonl`
- [ ] Hourly performance snapshots to `snapshots.jsonl`

### Layer 9: Orchestration (Implementor E)

- [ ] **`apex/main.py`** — Main asyncio event loop
  - [ ] Startup: validate env vars, parse config, check API connectivity
  - [ ] Startup: load crash recovery Parquet if available
  - [ ] Scheduled tasks:
    - [ ] Price fetch (every 60s)
    - [ ] Balance check (every 5 min)
    - [ ] Feature computation (incremental, after price fetch)
    - [ ] Signal generation (every 60 min)
    - [ ] Risk checks (every 60s)
    - [ ] Rebalance decision (every 60 min)
    - [ ] Execution queue processing (continuous)
    - [ ] Monitoring snapshot (every 60 min)
  - [ ] Graceful shutdown on SIGINT/SIGTERM
  - [ ] Global exception handling / logging

### Tests — Phase 1

- [ ] **`tests/test_momentum.py`** — Momentum ranking, tier filtering
- [ ] **`tests/test_sizing.py`** — Equal-weight sizing, tier caps
- [ ] **`tests/test_risk.py`** — Trailing stops, drawdown halts
- [ ] **`tests/test_execution.py`** — Order queue priority, threshold filtering

---

## Phase 2 — Full System

### Layer 3: Regime Detection (Implementor B)

- [ ] **`apex/regime/detector.py`** — Rule-based regime classifier
  - [ ] Four states: TREND_BULL, TREND_BEAR, MEAN_REVERT, HIGH_VOL_CRISIS
  - [ ] Inputs: BTC 4h/24h return signs, BTC vol percentile, altcoin breadth, contagion proxy
  - [ ] 30-minute confirmation delay for regime transitions
  - [ ] Regime → config lookup (exposure targets, top-N, stop params)

### Layer 4: Signal Generation — Extended

- [ ] Regime-conditional top-N (10 BULL / 6 MEAN_REVERT / 4 BEAR / 0 CRISIS)
- [ ] Trend filter: EMA(60) < EMA(240) → apply -0.30σ penalty
- [ ] **Meme pool**: Tier 4 ranked separately, top 1–2 get 3% NAV (BULL only)
- [ ] **Tier 5 pool**: Opportunistic allocation for top Tier 5 with 4h return > 10%
- [ ] PAXG allocation: 5–15% from cash buffer, scaled by regime

### Layer 5: Portfolio Construction — Vol-Adjusted

- [ ] Vol-adjusted sizing (replace equal-weight)
- [ ] Regime-conditional crypto deployment targets
- [ ] Adaptive exposure adjustment (raise floors if lagging benchmark)

### Layer 6: Risk Management — Dynamic

- [ ] Dynamic stop tightening based on daily P&L (+2%: tighten 40%, +1–2%: tighten 20%)
- [ ] Contagion circuit breaker (>80% positions down → reduce to 20% size)
- [ ] Signal health monitoring: hit rate warning at 45%, halt at 35%
- [ ] Winner/loser ratio: warning at 0.80, halt at 0.50
- [ ] If halted: cap exposure at 30%, keep only top 3 positions

### End-Game De-Risking (Hardcoded)

- [ ] T-48h: full regime target
- [ ] T-24h–T-48h: cap at 70%
- [ ] T-12h–T-24h: cap at 45%
- [ ] T-4h–T-12h: cap at 25%, tighten stops to 3%
- [ ] T-1h–T-4h: cap at 15%, tighten stops to 2%
- [ ] T-15min: **SELL ALL**

### Tests — Phase 2

- [ ] **`tests/test_regime.py`** — Regime classification rules, transition delays
- [ ] **`tests/test_endgame.py`** — De-risking schedule, stop tightening

---

## Phase 3 — ML Overlay (if time permits)

- [ ] **`apex/ml/model.py`** — LightGBM directional model
  - [ ] P(4h return > 0) prediction
  - [ ] Size multiplier output (0.5x–1.3x) on momentum positions
  - [ ] 24-hour retrain cadence
- [ ] **`apex/ml/monitor.py`** — IC monitoring
  - [ ] Rolling IC check every 48 hours
  - [ ] Auto-halt if IC < 0.02, resume when IC > 0.04 for 12h
  - [ ] Retrain sanity check (reject if output distribution differs > 2σ)

---

## Infrastructure & Deployment

- [ ] Set up AWS EC2 t3.medium instance
- [ ] Configure tmux session for 24/7 operation
- [ ] Set environment variables (API keys)
- [ ] Deploy `web_dashboard.py` on port 8080 for remote monitoring
- [ ] Dry-run full system for 24h before competition start (Mar 22)

---

## Implementation Priority Order

```
1. Phase 0 API test          ← FIRST — everything depends on this
2. Layer 1 (Data Ingestion)  ← foundation for all trading logic
3. Layer 2 (Features)        ← required by signals
4. Layer 4 (Momentum Signal) ← core alpha generation
5. Layer 5 (Portfolio)       ← converts signals to trades
6. Layer 6 (Risk)            ← capital preservation
7. Layer 7 (Execution)       ← sends orders to exchange
8. Layer 9 (Main Loop)       ← wires everything together
9. Phase 1 Tests             ← validate before going live
10. Phase 2 additions        ← regime, dynamic risk, endgame
11. Phase 3 ML               ← only if Phase 2 is stable
```
