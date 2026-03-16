# APEX — Project File Structure

```
apex/
├── config.yaml                     # All tunable parameters (Section 17.3)
├── main.py                         # Entry point — single-process async event loop
├── requirements.txt                # Python dependencies
│
├── core/
│   ├── __init__.py
│   ├── constants.py                # Asset universe, tier definitions, hard limits
│   ├── types.py                    # Shared dataclasses/TypedDicts (Regime, Signal, Order, Position, etc.)
│   └── config.py                   # Load & validate config.yaml, expose typed config object
│
├── data/                           # Layer 1 — Data Ingestion (Section 6)
│   ├── __init__.py
│   ├── api_client.py               # Roostoo REST API wrapper (aiohttp), rate-limit tracking, backoff
│   ├── price_buffer.py             # Per-asset deque ring buffers (max 1440 entries), forward-fill, anomaly detection
│   ├── portfolio_tracker.py        # Balance fetches, NAV computation, position state
│   └── persistence.py              # Hourly Parquet snapshots, JSON trade log, crash-recovery reload
│
├── features/                       # Layer 2 — Feature Engineering (Section 7)
│   ├── __init__.py
│   ├── returns.py                  # Raw returns (5m–24h), return volatility (rolling std)
│   ├── momentum.py                 # ROC, MA ratios, distance from rolling high/low
│   ├── volume.py                   # Volume features (conditional on API providing volume data)
│   ├── cross_sectional.py          # Cross-sectional return ranks, within-tier ranks, composite momentum score
│   └── regime_inputs.py            # BTC dominance proxy, altcoin breadth, volatility ratio
│
├── regime/                         # Layer 3 — Regime Detection (Section 8)
│   ├── __init__.py
│   └── detector.py                 # Rule-based classifier (4 states), transition logic, upgrade/downgrade delays
│
├── signals/                        # Layer 4 — Signal Generation (Section 9)
│   ├── __init__.py
│   ├── momentum_signal.py          # Cross-sectional momentum ranking (Tier 1–3), meme sub-pool, Tier 5 sub-pool
│   ├── trend_filter.py             # EMA(60)/EMA(240) crossover → momentum score penalty
│   └── ml_overlay.py               # Phase 3 — LightGBM directional overlay, IC tracking, retrain sanity check
│
├── portfolio/                      # Layer 5 — Portfolio Construction (Section 10)
│   ├── __init__.py
│   ├── construction.py             # Regime-conditional deployment targets, position sizing (equal-weight → vol-adj → tier cap)
│   ├── paxg.py                     # PAXG allocation logic (Treynor instrument, regime-scaled)
│   ├── beta.py                     # Portfolio beta computation (vs BTC + equal-weight index), soft beta targeting
│   ├── adaptive_exposure.py        # Phase 2+ — Naive benchmark tracking, exposure floor adjustment
│   └── endgame.py                  # End-game de-risking schedule (T-48h → T-15min)
│
├── risk/                           # Layer 6 — Risk Management (Section 11)
│   ├── __init__.py
│   ├── hard_limits.py              # Portfolio drawdown halt, daily loss reducer, exposure cap, single-asset loss exit
│   ├── trailing_stops.py           # Per-position trailing stops (tier-specific distances), 2% floor enforcement
│   ├── stop_tightening.py          # Dynamic stop tightening based on daily P&L (Section 11.2)
│   ├── circuit_breaker.py          # Contagion proxy computation, CRISIS trigger
│   └── drawdown_recovery.py        # Post-halt cooldown, graduated re-entry logic
│
├── execution/                      # Layer 7 — Execution Engine (Section 12)
│   ├── __init__.py
│   ├── order_manager.py            # Limit order placement, cancel/resubmit logic, risk-exit escalation
│   ├── order_queue.py              # Priority queue (risk exits > reductions > entries > adjustments), max 15 open orders
│   └── trade_filter.py             # Minimum trade threshold (0.2% NAV), turnover constraint (25% per rebalance)
│
├── monitoring/                     # Layer 8 — Monitoring & Adaptation (Section 13)
│   ├── __init__.py
│   ├── signal_health.py            # Rolling hit rate, win/loss ratio, momentum halt logic
│   ├── performance_logger.py       # JSON-lines trade log, hourly snapshot
│   └── ml_monitor.py               # Phase 3 — Rolling IC check, auto-halt, retrain trigger
│
├── scripts/                        # Utilities (not part of the main loop)
│   ├── api_test.py                 # Phase 0 — API testing script (Section 6.1): batch pricing, limit fill, rate limit
│   ├── download_history.py         # Download Binance historical data for backtesting/ML pre-training
│   └── backtest.py                 # Offline backtest harness for momentum signal validation
│
├── data_store/                     # Runtime data directory (gitignored except structure)
│   ├── .gitkeep
│   ├── snapshots/                  # Hourly Parquet crash-recovery snapshots
│   ├── trades/                     # JSON-lines trade logs
│   ├── logs/                       # Application logs
│   └── models/                     # Phase 3 — Serialized LightGBM models
│
└── tests/
    ├── __init__.py
    ├── test_momentum.py            # Cross-sectional ranking, tier filtering, penalty application
    ├── test_regime.py              # Regime classification rules, transition delays
    ├── test_risk.py                # Trailing stops, circuit breaker, drawdown recovery
    ├── test_sizing.py              # Vol-adjusted sizing, tier caps, PAXG allocation
    ├── test_execution.py           # Order queue priority, threshold filtering, resubmission logic
    └── test_endgame.py             # De-risking schedule, stop tightening at end-game
```

## Module Dependency Graph

```
main.py
  ├── core/config.py          ← loaded once at startup
  │
  ├── data/api_client.py      ← 1-min price loop, balance loop
  ├── data/price_buffer.py    ← fed by api_client
  ├── data/portfolio_tracker.py
  ├── data/persistence.py     ← crash recovery on startup, hourly saves
  │
  ├── features/*              ← computed incrementally from price_buffer
  │
  ├── regime/detector.py      ← reads regime_inputs features, runs every 5 min
  │
  ├── signals/*               ← reads features + regime, runs every 60 min
  │
  ├── portfolio/*             ← reads signals + regime, computes target weights
  │
  ├── risk/*                  ← runs every 1 min (stops, contagion), modifies targets
  │
  ├── execution/*             ← converts weight deltas to orders, manages queue
  │
  └── monitoring/*            ← logs trades, checks signal health every 1 hour
```

## Phase Mapping

| Phase | Modules Implemented |
|---|---|
| **Phase 0** | `scripts/api_test.py`, `core/config.py` |
| **Phase 1 (MVB)** | `core/*`, `data/*`, `features/returns.py`, `features/momentum.py`, `features/cross_sectional.py`, `signals/momentum_signal.py` (Tier 1–3 only), `portfolio/construction.py` (equal-weight only), `risk/hard_limits.py`, `risk/trailing_stops.py`, `execution/*`, `monitoring/performance_logger.py`, `main.py` |
| **Phase 2** | `features/regime_inputs.py`, `regime/*`, `signals/trend_filter.py`, `signals/momentum_signal.py` (+ meme pool), `portfolio/construction.py` (+ vol-adjust), `portfolio/paxg.py`, `portfolio/endgame.py`, `risk/stop_tightening.py`, `risk/circuit_breaker.py`, `risk/drawdown_recovery.py`, `monitoring/signal_health.py` |
| **Phase 3** | `features/volume.py`, `signals/ml_overlay.py`, `signals/momentum_signal.py` (+ Tier 5), `portfolio/beta.py`, `portfolio/adaptive_exposure.py`, `monitoring/ml_monitor.py`, `scripts/download_history.py` |
