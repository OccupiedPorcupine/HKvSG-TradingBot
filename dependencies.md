# APEX — Dependencies

## Python Version

**Python 3.11+** required. Using 3.11 for:
- `asyncio.TaskGroup` for structured concurrency
- `tomllib` in stdlib (if needed)
- Performance improvements in asyncio event loop
- Exception groups for better error handling

Confirm with: `python3 --version`

---

## Core Dependencies (Phase 1 — required from day 0)

| Package | Version | Purpose | Why This One |
|---------|---------|---------|--------------|
| `aiohttp` | >=3.9,<4.0 | Async HTTP client for Roostoo API | Only mature async HTTP client. `requests` is sync-only and would block the event loop. `httpx` is an alternative but `aiohttp` has a larger ecosystem and is battle-tested for long-running connections. |
| `numpy` | >=1.26,<2.0 | Feature computation, rolling windows, vectorized math | No alternative for numerical Python. Pin below 2.0 to avoid breaking changes in the NumPy 2.x transition. |
| `pandas` | >=2.1,<3.0 | Parquet I/O for crash recovery snapshots and historical data loading | Only used for `read_parquet()` / `to_parquet()`. Not used in hot loops. Could be replaced by `pyarrow` directly, but pandas is more ergonomic for the one-time data loads. |
| `pyarrow` | >=14.0 | Parquet engine backend for pandas | Required by `pandas.read_parquet()`. Installed as a pandas dependency but pin explicitly to avoid version mismatch. |
| `pyyaml` | >=6.0 | Parse `config.yaml` | Stdlib `json` could work but YAML is more readable for 150+ parameters with comments. |

## Phase 3 Dependencies (ML — install from day 0 but only activated in Phase 3)

| Package | Version | Purpose | Why This One |
|---------|---------|---------|--------------|
| `lightgbm` | >=4.1,<5.0 | Directional probability model (P(positive 4h return)) | Architecture decision. Gradient boosting is the right tool for tabular financial features. XGBoost is an alternative but LightGBM is faster to train and uses less memory — matters on t3.medium. |
| `scikit-learn` | >=1.3,<2.0 | Train/test split, metrics (IC computation), isotonic calibration | Standard ML toolkit. Only used for utilities, not as a model. |

## Development & Testing Dependencies (not deployed to EC2)

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | >=7.4 | Test runner |
| `pytest-asyncio` | >=0.23 | Async test support for aiohttp-based code |
| `pytest-cov` | >=4.1 | Coverage reporting |

---

## What We Do NOT Install

Explicitly excluded per architecture decisions in requirements_v3.1. If an implementor tries to add any of these, it is a bug.

| Package | Reason for Exclusion |
|---------|---------------------|
| `redis` / `aioredis` | No IPC needed — single process, in-memory `deque` |
| `psycopg2` / `asyncpg` / `sqlalchemy` | No database — `deque` + Parquet files |
| `ccxt` | Roostoo is not a standard exchange — custom REST client |
| `websockets` | Roostoo API is REST-only, no WebSocket support indicated |
| `numba` | JIT adds startup time + memory. NumPy vectorization is sufficient for 56 assets. |
| `hmmlearn` | HMM replaced by rule-based regime detection |
| `arch` | GARCH replaced by simple volatility percentile rank |
| `statsmodels` | No statistical models used. Rolling regression for beta done with numpy. |
| `cvxpy` | Portfolio optimization replaced by arithmetic sizing |
| `PyPortfolioOpt` | Same — no optimizer needed |
| `empyrical` | Ratio calculations are trivial to implement inline |
| `optuna` | Bayesian HPO removed — manual tuning via config commits |
| `shap` | Feature importance tracking removed — not worth compute cost for 10 days |
| `mlflow` | No experiment tracking server — git commits track parameters |
| `dvc` | No model versioning — models are <10MB, fit in git |
| `prefect` / `apscheduler` | No orchestration framework — `asyncio` timers |
| `prometheus-client` | No monitoring server — log files sufficient |
| `structlog` | Stdlib `logging` is sufficient |
| `celery` | No task queue — single process |
| `docker` / `docker-compose` | Direct Python on EC2, no containerization |

---

## requirements.txt

```
# Core (Phase 1)
aiohttp>=3.9,<4.0
numpy>=1.26,<2.0
pandas>=2.1,<3.0
pyarrow>=14.0
pyyaml>=6.0

# ML (Phase 3 — install now, activate later)
lightgbm>=4.1,<5.0
scikit-learn>=1.3,<2.0
```

## requirements-dev.txt

```
# Testing
pytest>=7.4
pytest-asyncio>=0.23
pytest-cov>=4.1
```

---

## Stdlib Modules Used (no install needed)

These are part of Python's standard library and are used heavily:

| Module | Purpose |
|--------|---------|
| `asyncio` | Event loop, coroutine scheduling, timers |
| `collections.deque` | Price ring buffers (maxlen=1440) |
| `logging` | All application logging to JSON lines files |
| `json` | Trade log serialization, decision logger |
| `datetime` / `time` | UTC timestamps, competition timer, end-game schedule |
| `os` / `pathlib` | File paths, environment variable access |
| `math` | Basic math operations |
| `dataclasses` | RegimeState, Position, RiskEvent, SignalOutput |
| `enum` | Regime states, order types, risk severity |
| `sqlite3` | Optional trade log persistence (if JSON lines proves insufficient) |
| `hashlib` | Config hash for startup verification |
| `typing` | Type hints |

---

## EC2 System Dependencies

On the AWS EC2 t3.medium (Amazon Linux 2 or Ubuntu):

```bash
# Python 3.11
sudo yum install python3.11 python3.11-pip   # Amazon Linux 2
# or
sudo apt install python3.11 python3.11-venv  # Ubuntu

# Virtual environment
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# tmux for persistent session
sudo yum install tmux   # or sudo apt install tmux
```

No other system packages needed. No database servers. No Redis. No Docker.

---

## RAM Budget on t3.medium (4 GB total)

| Component | Estimated RAM |
|-----------|--------------|
| Python interpreter + stdlib | ~50 MB |
| aiohttp + event loop | ~20 MB |
| numpy + pandas (loaded) | ~30 MB |
| Price ring buffers (56 × 1440 × 40B) | ~3 MB |
| Feature arrays (50 × 56 × 1440 × 8B) | ~32 MB |
| Position tracking + config | ~5 MB |
| Logging buffers | ~5 MB |
| **Phase 1-2 total** | **~145 MB** |
| Historical data (Parquet loaded for ML) | ~100 MB |
| LightGBM during retrain | ~200 MB |
| scikit-learn during calibration | ~50 MB |
| **Phase 3 peak (during retrain)** | **~495 MB** |
| **OS + headroom** | **~500 MB** |
| **Total peak** | **~1 GB / 4 GB available** |

Comfortable margin. No memory pressure expected.
