# APEX — Project Status

> **Archived snapshot.** This page was last updated on March 19, 2026, two days before Round 1
> started. The competition has since ended: APEX traded live through Round 1 and reached the top 5
> of 100+ teams before falling back. The Phase 2/3 items below are as planned at that time.
> See the [README](README.md) for the final summary.

**As of: March 19, 2026**
**Current Phase: Phase 1 (MVB) — ~90% Complete**
**Competition Round 1 starts: March 21, 2026 (T-2 days)**

---

## Phase Summary

| Phase                             | Dates     | Status          | Completeness   |
| :-------------------------------- | :-------- | :-------------- | :------------- |
| Phase 0: API Test                 | Mar 16–17 | **COMPLETE**    | 100%           |
| Phase 1: MVB (Minimum Viable Bot) | Mar 17–20 | **IN PROGRESS** | ~90%           |
| Phase 2: Regime + Sizing Upgrade  | Mar 21–25 | NOT STARTED     | Stubs in place |
| Phase 3: ML + Repo Polish         | Mar 25–28 | NOT STARTED     | Stubs in place |
| Phase 4: Live Tuning              | Mar 28–31 | NOT STARTED     | —              |
| Round 2 (if qualified)            | Apr 4–14  | NOT STARTED     | —              |

---

## Phase 1 — Detailed Status

**Goal:** A working, continuously running bot trading Tier 1–3 assets with momentum + full risk controls. Must be deployed and generating trades before Mar 21.

### ✅ Complete

| Component                       | Module                              | Notes                                                                           |
| :------------------------------ | :---------------------------------- | :------------------------------------------------------------------------------ |
| Data ingestion (ring buffers)   | `src/data/ingestion.py`             | deque(maxlen=1441) per asset, 1-min bars, Parquet backup hourly                 |
| Feature engineering             | `src/data/features.py`              | Returns (5m→24h), EMAs (20/50/60/240), vol, cross-sectional rank                |
| Regime detection (4-state)      | `src/regime/detector.py`            | TREND_BULL / MEAN_REVERT / TREND_BEAR / HIGH_VOL_CRISIS, asymmetric transitions |
| Cross-sectional momentum signal | `src/signals/momentum.py`           | Composite rank (1h×0.20, 4h×0.40, 12h×0.25, 24h×0.15), top-N by regime          |
| Portfolio construction          | `src/portfolio/constructor.py`      | Equal-weight Phase 1, tier caps, turnover constraint (25% cap), min trade $2k   |
| Trailing stops                  | `src/risk/trailing_stops.py`        | Per-position, Tier 1–3: 6%, Tier 4–5: 8%, TRUMP: 10%, floor: 2%                 |
| Circuit breakers                | `src/risk/circuit_breakers.py`      | 8% drawdown halt, 5% daily loss reduce, 6% single-asset close                   |
| Order management                | `src/execution/order_manager.py`    | Limit-only, 60s timeout + resubmit, priority queue, 15 max open orders          |
| Position tracking               | `src/execution/position_tracker.py` | Cost basis, peak prices, unrealised P&L, daily P&L                              |
| Trade logging (JSONL)           | `src/execution/decision_logger.py`  | Full signal context per trade — Screen 1 compliance                             |
| Crash recovery                  | `src/orchestration/recovery.py`     | Reconstructs positions from JSONL trade log on restart                          |
| Orchestration & scheduler       | `src/orchestration/scheduler.py`    | asyncio event loop, overlap prevention, heartbeat file                          |
| API client                      | `src/data/api_client.py`            | Async aiohttp, HMAC auth, exponential backoff on 429                            |
| Binance fallback                | `src/data/binance_client.py`        | Secondary price source if Roostoo unavailable                                   |
| Rate limiter                    | `src/data/rate_limiter.py`          | Token bucket, safe mode after 5 consecutive 429s                                |
| Test suite                      | `tests/`                            | 296 test cases across 9 files, all layers covered                               |
| Run wrapper                     | `run.sh`                            | Immortal loop, restarts bot within 10s of any exit                              |
| Config                          | `config.yaml`                       | ~480 parameters, all tunable without code changes                               |

### ⚠️ Partial / Phase 1 Limitation

| Component                | Module                              | Gap                                                                            | Fix In                   |
| :----------------------- | :---------------------------------- | :----------------------------------------------------------------------------- | :----------------------- |
| Signal health monitoring | `src/adaptation/signal_health.py`   | Monitoring-only (hit rate + W/L ratio logged). No halt/resume logic active.    | Phase 2                  |
| Performance logging      | `src/adaptation/performance_log.py` | Sortino/Sharpe/Calmar computed and logged but not self-monitored.              | Phase 2                  |
| Vol-adjusted sizing      | `src/portfolio/constructor.py`      | Equal-weight used in Phase 1. Inverse-vol scaling code written, not activated. | Phase 2                  |
| BTC beta monitor         | `src/portfolio/beta_monitor.py`     | Logs rolling BTC beta, does not influence weights (monitoring-only).           | N/A (observability only) |

### ❌ Not Started (Stubs Only)

| Component | Module | Status |
| :---- | :---- | :---- |
| Meme coin pool allocation | `src/signals/meme_pool.py` | Selection logic written; returns empty dict in Phase 1 |
| Tier 5 opportunistic pool | `src/signals/tier5_pool.py` | Selection + activation threshold written; returns empty dict in Phase 1 |
| PAXG allocation | `src/portfolio/constructor.py` | Logic written; 0% allocation in Phase 1 |
| Dynamic stop tightening | `src/risk/trailing_stops.py` | Logic written; not activated in Phase 1 |
| End-game de-risking | `src/portfolio/constructor.py` | Logic written; schedule not activated until Phase 2 |
| Adaptive exposure | `src/portfolio/adaptive.py` | ~50% implemented; not connected to constructor |
| ML overlay | `src/signals/ml_overlay.py` | Returns 1.0x for all assets (no-op stub) |
| ML retraining | `src/adaptation/ml_retrain.py` | Placeholder only (~5% implemented) |
| IC halt/resume | `src/signals/ml_overlay.py` | Not implemented |

---

## Phase 2 — Activation Plan (Mar 21–25, live during Round 1)

Phase 2 features are implemented as stubs in the codebase. Activation requires config flag changes and minor wiring — no structural rewrites.

| Feature | Activation Method | Risk |
| :---- | :---- | :---- |
| Inverse-vol position sizing | Enable in `config.yaml → portfolio.sizing_mode` | Low — reduces variance, tested |
| Trend penalty on momentum | Enable via `config.yaml → signals.trend_penalty.enabled` | Low — already in signal code |
| Meme pool (Tier 4) | Enable via `config.yaml → signals.meme_pool.enabled` | Medium — small allocation (3% each) |
| Tier 5 opportunistic pool | Enable via `config.yaml → signals.tier5_pool.enabled` | Medium — small allocation (1–2% each) |
| PAXG cash buffer allocation | Enable via `config.yaml → portfolio.paxg.enabled` | Low — defensive instrument |
| Dynamic stop tightening | Enable via `config.yaml → risk.dynamic_tightening.enabled` | Low — protects gains |
| End-game de-risking | Auto-activates based on `config.yaml → competition.round_end` | Low — time-based trigger |
| Signal health halt (NP Factor) | Migrate from hit rate in `signal_health.py` | Medium — code change required |
| Adaptive exposure adjustment | Wire `adaptive.py` into `constructor.py` | Medium — code change required |

---

## Phase 3 — ML + Repo Polish (Mar 25–28)

**Hard deadline: Repo submitted before March 28 (Day 7 of Round 1).**

| Task | Status | Notes |
| :---- | :---- | :---- |
| LightGBM overlay (Phase 3 signal) | Not started | `ml_overlay.py` is a stub |
| Walk-forward retrain loop | Not started | `ml_retrain.py` is a placeholder |
| IC monitoring + halt/resume | Not started | Needs 48h of live IC data to validate |
| README finalized | Not started | Required for code review judging (Screen 4) |
| Repo cleanup (dead code, type hints, docstrings) | Not started | Screen 4 code quality (20%) |
| tests/ — final coverage pass | Not started | Judges will see the directory |

---

## Critical Path to Round 1 Launch (Mar 21)

1. **Mar 19 (today):** Complete any remaining Phase 1 wiring. Final integration test with Roostoo test environment.
2. **Mar 20:** Dry run on Roostoo test environment for 24h. Verify trade logging, crash recovery, heartbeat.
3. **Mar 21 (competition start):** Deploy Phase 1 bot. Monitor first rebalance. Confirm orders executing and trade log populating.

**Phase 1 must be live and trading on March 21. Phase 2 features are enhancements, not prerequisites for launch.**

---

## Key Risk Flags

| Risk | Status | Mitigation |
| :---- | :---- | :---- |
| Roostoo API rate limit not confirmed | Open from Phase 0 | Monitor 429 rate in first live session. Rate limiter in place. |
| Limit order fill speed not confirmed | Open from Phase 0 | Phase 0 verified batch endpoint. Fill speed needs live validation. |
| Momentum not working on Roostoo mock data | Unknown | First 24h of live data will show. Fallback: BTC + top 3 alts equal-weight. |
| Commission drag | Estimated 0.56% NAV/day | Turnover buffer + min $2k trade threshold already active. |
| Repo submission deadline (Mar 28) | T-9 days | Phase 3 / README work must begin by Mar 25 at latest. |
