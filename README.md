# APEX — Autonomous Portfolio Execution Agent

An autonomous spot-crypto trading bot built for the **SG vs HK University Web3 Quant Trading
Hackathon (2026)**. It traded a $1M virtual portfolio across 60+ assets on the Roostoo exchange
with no manual intervention.

**Result:** reached the **top 5 of 100+ teams** during Round 1 live trading, then fell back in the
rankings before the round ended.

Scoring was 0.4 × Sortino + 0.3 × Sharpe + 0.3 × Calmar, so the design favours limiting downside
over maximising raw return.

## Architecture

A single asyncio process in eight layers. Every threshold is read from [`config.yaml`](config.yaml)
(~350 lines), so strategy changes don't require code changes.

```
 Roostoo REST API ──┐        Binance (fallback prices)
                    ▼
 1. Data         ring buffers (24h of 1-min bars per asset), validation, token-bucket rate limiter
 2. Features     multi-horizon returns, EMAs, volatility, cross-sectional ranks
 3. Regime       4-state rule-based classifier: TREND_BULL / MEAN_REVERT / TREND_BEAR / HIGH_VOL_CRISIS
 4. Signals      cross-sectional momentum + EMA trend penalty; top-N selection depends on regime
 5. Portfolio    tier caps, turnover cap, minimum trade size, end-of-round de-risking
 6. Risk         pre-trade checks, per-position trailing stops, drawdown circuit breakers
 7. Execution    limit-order manager with priority queue, timeout + resubmit, position tracking
 8. Orchestration scheduler, crash recovery from the JSONL trade log, safe mode, heartbeat
```

### Design choices

- **Asymmetric regime transitions.** Downgrades (e.g. to crisis) take effect immediately, while
  upgrades need a 30-bar confirmation. Being slow to add risk costs less than being slow to cut it.
- **No statistical regime model.** The classifier uses deterministic rules on BTC trend, BTC
  volatility percentile, altcoin breadth and a contagion proxy. Two weeks of live data is too little
  to fit an HMM or GARCH model reliably.
- **Commission-aware.** Turnover is capped per rebalance and trades below 0.2% of NAV are skipped.
  At the start the estimated commission drag was about 0.56% of NAV per day.
- **Crash-safe.** Every decision is logged to JSONL with its full signal context. On restart,
  positions are rebuilt from that log, and `run.sh` restarts the process within 10 seconds of any exit.
- **Rate-limit aware.** A token-bucket limiter keeps headroom below the exchange's 30 calls/min, with
  exponential backoff on HTTP 429 and a safe mode after 5 consecutive 429s.

## Repository layout

```
src/
  data/           API clients (Roostoo, Binance), ingestion, features, rate limiter
  regime/         regime detector, contagion probe, regime state
  signals/        momentum, trend penalty, meme / tier-5 pools, ML overlay (stub)
  portfolio/      constructor, PAXG allocator, end-game de-risking, beta monitor
  risk/           risk manager, pre-trade checks, trailing stops, circuit breakers
  execution/      order manager, priority queue, position tracker, decision logger
  orchestration/  scheduler, startup, recovery, safe state
  adaptation/     performance log (Sortino/Sharpe/Calmar), signal health
scripts/          API tests, market monitor, terminal and web dashboards, historical seeding
tests/            pytest suite, one file per layer, plus integration tests
context_files/    architecture document (v3.3), requirements, deployment plan
agent/            prompt specs used to build the system with parallel AI coding agents
```

## Running

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env    # set ROOSTOO_API_KEY and ROOSTOO_API_SECRET
./run.sh                # starts the bot and restarts it on exit

PYTHONPATH=. pytest tests
```

> The Roostoo competition API is no longer live. The code is kept as a record of the competition.

**Test status:** 370 tests. 341 pass; 29 fail because the code changed during live trading and the
tests weren't updated to match.

## Team

Built by a team of four:

- [@OccupiedPorcupine](https://github.com/OccupiedPorcupine): architecture and lead developer
- [@Xxdsanctuary](https://github.com/Xxdsanctuary)
- [@JaspJaspJasp](https://github.com/JaspJaspJasp)
- [@luckKCUL123](https://github.com/luckKCUL123)
