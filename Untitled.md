# APEX Phase 2 — Parallel Agent Implementation Plan (v2)

**Revision notes:** Incorporates adversarial review. Key changes from v1: actual project tree used for all file paths; most tasks are now "extend existing module" not "create new"; added merge protocol; added pre-flight checks; added cold-start/edge-case handling; added style guide for Gemini agents; strengthened T0 ownership; hardened T7 edge cases; added input validation layer; strengthened smoke tests.

**Phase 2 Scope (from v3.3 Architecture Doc):** Regime detection, vol-adjusted sizing, PAXG allocation, meme coin sub-pool, trend penalty, dynamic stop tightening, end-game de-risking, regime-conditional deployment.

**Estimated Wall-Clock Time:** 6–8 hours (with parallel execution). Plan for 8, be happy at 6.

---

## Pre-Flight Checklist (Before ANY Agent Runs)

Do these manually. They take 10 minutes and prevent hours of debugging.

```bash
# 1. Find FeatureEngine — every Phase 2 module depends on this interface
grep -rn "class FeatureEngine" src/ apex/
# Record the file path and read the class. Confirm these methods exist:
#   get_regime_inputs() -> object with .btc_4h_return, .btc_24h_return,
#                          .altcoin_breadth, .btc_vol_percentile
#   get_return(asset, minutes) -> float
# If any are missing, add them BEFORE starting Wave 2.

# 2. Read existing Phase 2 stubs — many modules already exist
cat src/regime/detector.py        # How much is implemented?
cat src/regime/regime_state.py    # Is RegimeState enum already defined?
cat src/regime/contagion.py       # Does contagion proxy exist?
cat src/signals/meme_pool.py      # How much is implemented?
cat src/risk/trailing_stops.py    # Current stop interface?
cat src/portfolio/constructor.py  # Current construction pipeline?
cat src/portfolio/adaptive.py     # Adaptive exposure — stub or working?

# 3. Record answers to these questions (paste into shared_context.md):
#    a. FeatureEngine file path: _______________
#    b. RegimeState enum location and values: _______________
#    c. Which Phase 2 modules are stubs vs partially implemented: _______________
#    d. RiskManager.check_stops() method signature: _______________
#    e. PortfolioConstructor.construct() current signature: _______________
#    f. Config loading pattern (e.g., config['regime']['breadth_bull_threshold']
#       or config.regime.breadth_bull_threshold): _______________
```

**STOP. Do not proceed until all 6 answers are recorded.** Paste them into `agent/implementation/shared_context.md`. Every agent prompt below includes a placeholder `{{SHARED_CONTEXT}}` — replace it with these answers before dispatch.

---

## Git Merge Protocol

All Wave 2 agents work on **feature branches**. No direct commits to main during parallel execution.

```
main ──────────────────────────────────────────────── merge ── merge ──
  │                                                     ↑       ↑
  ├── phase2/config (T0) ── merge to main FIRST ───────┘       │
  │                                                             │
  ├── phase2/regime (T1) ──────────────┐                        │
  ├── phase2/trend-penalty (T2) ──────┐│                        │
  ├── phase2/meme-pool (T3) ─────────┐││                        │
  ├── phase2/dynamic-stops (T4) ────┐│││                        │
  ├── phase2/endgame (T5) ─────────┐││││                        │
  │                                │││││                        │
  │                Wave 2 done:    ▼▼▼▼▼                        │
  │                merge all to main (resolve conflicts)        │
  │                                                             │
  ├── phase2/paxg (T6) ────────────────┐                        │
  │                                    ▼                        │
  ├── phase2/integration (T7+T8) ──────────────────────────────┘
  └── phase2/tests (T9)
```

**Rules:**

1. T0 merges to main first. All Wave 2 branches fork from this.
2. Wave 2 branches are independent (no cross-branch deps).
3. Before T7 starts: ALL Wave 2 branches merged to main. Conflicts resolved.
4. T7 and T8 work on `phase2/integration` branch forked from post-merge main.
5. Every commit message: `phase2(module): description` (e.g., `phase2(regime): add transition asymmetry`)

---

## Codebase Style Guide (Include in All Gemini Prompts)

```python
# === APEX CODEBASE CONVENTIONS ===
# Paste this into every Gemini prompt so output matches existing style.

# 1. Logging: stdlib logging, %-formatting, UPPERCASE module prefix
import logging
logger = logging.getLogger(__name__)

logger.debug(
    "TREND_PENALTY: %s score %.4f → %.4f "
    "(EMA60=%.2f < EMA240=%.2f)",
    asset, score, adjusted_score, ema_60, ema_240,
)

# 2. Type hints: built-in generics (dict[], list[], not Dict[], List[])
def get_penalties(self) -> dict[str, float]:

# 3. Config access: dict-style from loaded YAML
threshold = self.config['regime']['breadth_bull_threshold']

# 4. Docstrings: triple-quote, explain WHY not just WHAT
def _apply_transitions(self, raw_state: RegimeState) -> RegimeState:
    """Apply asymmetric transition rules.
    
    Downgrades are immediate because a single deep drawdown permanently
    damages Calmar (0.3 weight). Upgrades require persistence because
    the cost of missing upside is only a few basis points.
    """

# 5. Defensive defaults: neutral/safe value when data missing
sent = sentiment_scores.get(asset, 0.5)  # Neutral if missing

# 6. Step comments in pipeline methods
# Step 3: Apply the calculated penalty
for asset, score in scores.items():
    ...
```

---

## Dependency Graph (Revised)

```
PRE-FLIGHT (Manual, 10 min)
│  Locate FeatureEngine, read existing stubs, record in shared_context.md
▼
WAVE 0 — Config + Validation Layer (Sequential, ~15 min)
┌───────────────────────────────────────┐
│  T0: config.yaml schema update        │  ← YOU do this manually or careful review
│  T0b: Input validation helpers        │  ← Agent D (Haiku)
└───────────────────┬───────────────────┘
                    │
WAVE 2 — Parallel Module Build (~60–90 min each)
┌───────────┬───────┼───────┬───────────────┬───────────────┐
│           │       │       │               │               │
▼           ▼       ▼       ▼               ▼               ▼
T1          T2      T3      T4              T5              T6*
Regime      Trend   Meme    Dynamic         End-Game        PAXG
Detector    Penalty Pool    Stops           De-Risk         Alloc
(Opus)      (Gem)   (Gem)   (Sonnet)        (Sonnet)        (Sonnet)
│           │       │       │               │               │
└───────────┴───────┴───────┴───────────────┴───────┬───────┘
                                                    │
* T6 can run in Wave 2 IF RegimeState enum already exists
  (check in pre-flight). If not, T6 waits for T1.

MERGE CHECKPOINT: All Wave 2 branches → main. Resolve conflicts.

WAVE 3 — Integration (~60–90 min)
┌───────────────────────────────────────────────────────────────┐
│  T7: Portfolio Constructor rewrite (Opus)                     │
│  T8: Main loop wiring (Opus)                                  │
└───────────────────────────────┬───────────────────────────────┘
                                │
WAVE 4 — Tests (~30 min)
┌───────────────────────────────┴───────────────────────────────┐
│  T9: Integration + hardened smoke tests (Sonnet)              │
└───────────────────────────────────────────────────────────────┘
```

---

## Agent Assignment (Revised)

|Agent|Model|Interface|Assigned Tasks|Rationale|
|---|---|---|---|---|
|**A**|Claude Opus|Claude Code CLI|T1, T7, T8|Reads codebase, handles complex integration|
|**B**|Claude Sonnet|Claude Code CLI|T4, T5, T6|Reads + modifies existing files, solid implementation|
|**C**|Gemini 2.5 Pro|Gemini API|T2, T3|Standalone modules, given style guide + interface spec|
|**D**|Claude Haiku|Claude Code CLI|T0b, T9|Validation helpers, tests|
|**You**|Human|Manual|T0, pre-flight, merge checkpoint|Config is the contract — too important to delegate|

---

## Task Specifications & Agent Prompts

---

### T0 — Config Schema Update

**Agent:** YOU (manual, or agent-assisted with character-by-character review) **Duration:** ~10 min **Depends on:** Nothing **Output:** Updated `config.yaml`

**Why you do this manually:** Config is the contract every other task depends on. A subtle type mismatch (single float vs list, string vs enum) cascades into bugs across all 6 modules. 10 minutes of careful editing now saves hours of debugging later.

Add these blocks to the existing `config.yaml`, preserving all Phase 1 content:

```yaml
# ============================================================
# PHASE 2 PARAMETERS
# ============================================================

regime:
  btc_symbol: "BTC"
  breadth_bull_threshold: 0.55         # fraction of altcoins positive (4h)
  breadth_bear_threshold: 0.40
  contagion_ratio_threshold: 0.80
  contagion_loss_threshold: 0.01       # 1% avg 5min loss
  contagion_small_portfolio_size: 6
  contagion_small_ratio_threshold: 0.90
  contagion_small_loss_threshold: 0.015
  btc_vol_crisis_percentile: 90
  upgrade_persistence_minutes: 30
  crisis_exit_persistence_minutes: 30

trend_penalty:
  ema_short_period: 60                 # minutes
  ema_long_period: 240
  penalty_sigma: -0.3
  broad_downturn_threshold: 0.70       # fraction of universe with bearish EMA
  broad_downturn_penalty_sigma: -0.15

meme_pool:
  enabled: true
  symbols: ["SHIB", "PEPE", "FLOKI", "WIF", "BONK", "PUMP", "PENGU"]
  max_allocation_per_coin: 0.03
  top_n: 2
  active_regimes: ["TREND_BULL"]
  trailing_stop_pct: 0.08

dynamic_stops:
  tighten_high_threshold: 0.05         # unrealized PnL above this: heavy tighten
  tighten_high_factor: 0.60            # multiply base stop by this (40% tighter)
  tighten_mid_threshold: 0.025         # unrealized PnL above this: moderate tighten
  tighten_mid_factor: 0.80             # multiply base stop by this (20% tighter)
  min_stop_floor: 0.02                 # absolute minimum stop: 2%

paxg:
  symbol: "PAXG"
  allocation:
    TREND_BULL: 0.04                   # midpoint of [0.03, 0.05]
    MEAN_REVERT: 0.075                 # midpoint of [0.05, 0.10]
    TREND_BEAR: 0.125                  # midpoint of [0.10, 0.15]
    HIGH_VOL_CRISIS: 0.125             # midpoint of [0.10, 0.15]
  rebalance_tolerance: 0.01            # don't rebalance if within 1% of target

endgame:
  schedule:
    - { hours_remaining: 48, max_exposure: 1.0, stop_override: null }
    - { hours_remaining: 24, max_exposure: 0.70, stop_override: null }
    - { hours_remaining: 12, max_exposure: 0.45, stop_override: null }
    - { hours_remaining: 4, max_exposure: 0.25, stop_override: 0.03 }
    - { hours_remaining: 1, max_exposure: 0.15, stop_override: 0.02 }
    - { hours_remaining: 0.25, max_exposure: 0.0, stop_override: null }
  round_end_utc: "2026-03-31T23:59:00Z"

portfolio:
  exposure:
    TREND_BULL: 0.80                   # midpoints, not ranges
    TREND_BULL_RISING_VOL: 0.65
    MEAN_REVERT: 0.55
    TREND_BEAR: 0.35
    HIGH_VOL_CRISIS: 0.15
  holdings:
    TREND_BULL: 10
    MEAN_REVERT: 6
    TREND_BEAR: 4
    HIGH_VOL_CRISIS: 0
  turnover_max_pct: 0.25
  min_trade_nav_pct: 0.002
  tier_caps:
    tier1: 0.08
    tier1_doge: 0.05
    tier2: 0.08
    tier3: 0.06
    tier4_meme: 0.03
    tier5: 0.02
    trump: 0.02
    paxg: 0.15
```

**Design decision: single floats, not ranges.** The v1 plan used `[min, max]` lists. This was a subtle bug source — every consumer would need to compute midpoints or pick a value. Single floats are the contract. If you want adaptive range selection later, add it as a separate mechanism.

**After adding, validate:**

```bash
python -c "import yaml; c = yaml.safe_load(open('config.yaml')); print('regime keys:', list(c['regime'].keys())); print('OK')"
```

---

### T0b — Input Validation Helpers

**Agent:** D (Haiku via Claude Code CLI) **Duration:** ~15 min **Depends on:** T0 **Output:** `src/utils/validation.py` (new file)

#### Prompt for Agent D:

````
Create a new file: src/utils/validation.py

This module provides input validation for the APEX trading bot's Phase 2 modules.
Every Phase 2 module receives computed market data (returns, breadth, volatility
percentiles). A single NaN or None in these inputs can silently trigger wrong
regime classification or bad position sizing.

Create these validation functions:

```python
"""Input validation for Phase 2 market data.

Prevents silent failures from NaN, None, or out-of-range values in
derived market metrics. Each Phase 2 module calls these before acting
on inputs.
"""
import math
import logging

logger = logging.getLogger(__name__)

def validate_regime_inputs(
    btc_4h_return: float,
    btc_24h_return: float,
    altcoin_breadth: float,
    btc_vol_percentile: float,
) -> bool:
    """Validate all inputs to regime classification.
    
    Returns True if all inputs are valid. Logs specific failures.
    Called by RegimeDetector.update() before classification.
    """
    # Check for NaN/None on each input
    # btc_4h_return, btc_24h_return: any float is valid
    # altcoin_breadth: must be in [0.0, 1.0]
    # btc_vol_percentile: must be in [0, 100]
    # Return False and log which field failed if any are invalid

def validate_contagion_inputs(
    held_positions: dict,
    five_min_returns: dict[str, float],
) -> bool:
    """Validate inputs to contagion proxy calculation.
    
    Returns True if valid. Handles edge case: empty portfolio
    (no positions held) is valid but should skip contagion calc.
    """

def validate_momentum_scores(
    scores: dict[str, float],
) -> dict[str, float]:
    """Filter out assets with NaN or None momentum scores.
    
    Returns cleaned dict with invalid entries removed.
    Logs count of removed entries at WARNING level.
    """

def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Division that returns default instead of raising on zero/NaN denominator."""
````

Also create src/utils/**init**.py (empty file with just a docstring).

Follow these project conventions:

- Logging: stdlib, %-formatting, UPPERCASE module prefix (e.g., "VALIDATION: ...")
- Type hints: built-in generics (dict[], not Dict[])
- Use math.isnan() and math.isinf() for float checks

Keep it simple. This is a utility module — ~60 lines total.

```

---

### T1 — Regime Detection Engine (Extend Existing)
**Agent:** A (Claude Opus via Claude Code CLI)
**Duration:** ~60–90 min
**Depends on:** T0
**Branch:** `phase2/regime`
**Output:** Modified `src/regime/detector.py`, `src/regime/contagion.py`, `src/regime/regime_state.py`

#### Prompt for Agent A:

```

CONTEXT: You are extending the APEX trading bot's regime detection system for Phase 2. Several files already exist with partial implementations.

STEP 1 — Read these files completely before writing any code: cat src/regime/regime_state.py # Check if RegimeState enum exists cat src/regime/detector.py # Current detector implementation cat src/regime/contagion.py # Current contagion implementation cat src/regime/**init**.py # Exports cat src/utils/validation.py # Input validation (just created in T0b)

STEP 2 — Find and read the FeatureEngine to understand the data interface: grep -rn "class FeatureEngine" src/ apex/

# Then cat that file. Specifically find:

# get_regime_inputs() — what does it return? What fields?

# get_return(asset, minutes) — signature and return type

# Record these interfaces — your code must consume them exactly.

STEP 3 — Read the config to understand parameter access pattern: cat apex/core/config.py head -80 config.yaml

{{SHARED_CONTEXT}}

NOW implement the following. Work WITH existing code — extend, don't rewrite from scratch unless the existing implementation is fundamentally wrong.

## RegimeState Enum (src/regime/regime_state.py)

If not already present, define: TREND_BULL, MEAN_REVERT, TREND_BEAR, HIGH_VOL_CRISIS If already present, verify these four states exist. Add any missing.

## Contagion Proxy (src/regime/contagion.py)

Ensure it implements this O(n) computation: negative_count = count(held positions where 5min return < 0) contagion_ratio = negative_count / total_held_positions avg_loss = mean(abs(5min return) for negative positions)

Small portfolio override (<6 positions): ratio threshold 0.90 (not 0.80), loss threshold 1.5% (not 1.0%)

Edge case: zero held positions → return (0.0, 0.0), do NOT divide by zero.

Call validate_contagion_inputs() before computation.

## Regime Detector (src/regime/detector.py)

The detector must implement:

### Classification Rules (priority order, first match wins):

1. HIGH_VOL_CRISIS if:
    - contagion_ratio > threshold AND avg_5min_loss > threshold, OR
    - btc_vol_percentile > 90th
2. TREND_BULL if: btc_4h > 0 AND btc_24h > 0 AND breadth > 0.55
3. TREND_BEAR if: btc_4h < 0 AND btc_24h < 0 AND breadth < 0.40
4. MEAN_REVERT (default)

### Transition Asymmetry:

- Downgrades (BULL→BEAR, any→CRISIS): IMMEDIATE, no delay
- Upgrades (BEAR→MEAN_REVERT→BULL): candidate must persist 30 consecutive minutes
- CRISIS exit: dual conditions (contagion below threshold AND btc_vol below crisis) must hold for 30 consecutive minutes

Track upgrade candidates with: _upgrade_candidate: Optional[RegimeState] _upgrade_candidate_since: Optional[datetime]

When raw classification suggests an upgrade but persistence hasn't elapsed: stay in current (lower) regime, log that upgrade is pending.

### Cold-Start Behavior:

On first call (or after crash recovery), the detector has no history.

- Default to MEAN_REVERT (safest neutral state)
- The 30-minute persistence timer means upgrades won't happen for 30 min after startup. This is INTENTIONAL — conservative on restart is correct.
- Log clearly: "REGIME COLD_START: defaulting to MEAN_REVERT, upgrades require 30min persistence"
- Do NOT attempt to backfill regime from historical data — the data may be stale or inconsistent after a crash.

### Input Validation:

Call validate_regime_inputs() at the top of update(). If validation fails:

- Return current regime unchanged (don't reclassify on bad data)
- Log at WARNING level

### Interface:

class RegimeDetector: def **init**(self, config: dict): # Load thresholds from config['regime']

```
def update(self, regime_inputs, held_positions: dict,
           get_return_fn) -> RegimeState:
    """Called every 1 minute.
    regime_inputs: object from FeatureEngine.get_regime_inputs()
    held_positions: dict of currently held positions
    get_return_fn: callable(asset, minutes) -> float for contagion
    Returns: current RegimeState after transition logic applied.
    """

@property
def current_regime(self) -> RegimeState:
    """Read-only access to current state."""

@property
def minutes_in_current_regime(self) -> int:
    """How long we've been in this regime. Useful for logging."""
```

### Logging:

Every regime transition: log old_state, new_state, trigger_reason, key metrics. Format: "REGIME TRANSITION: %s → %s (reason: %s, btc_4h=%.4f, breadth=%.2f)" Pending upgrades: "REGIME UPGRADE_PENDING: %s → %s (%d/%d min elapsed)"

## Tests

Extend tests/test_regime.py (read it first: cat tests/test_regime.py). Add or verify these test cases:

1. CRISIS triggers immediately on high contagion (no persistence delay)
2. CRISIS triggers immediately on BTC vol > 90th percentile
3. Upgrade BEAR → MEAN_REVERT requires 30-min persistence
4. Downgrade BULL → BEAR is immediate (no persistence)
5. Small portfolio (<6 positions) uses raised contagion thresholds
6. Default/cold-start state is MEAN_REVERT
7. Invalid inputs (NaN) → regime unchanged, warning logged
8. Zero positions → contagion returns (0.0, 0.0), no crash

Run tests after implementation: cd /path/to/SGxHK_Quant && python -m pytest tests/test_regime.py -v

```

---

### T2 — Trend Penalty (EMA Crossover) — Extend Existing
**Agent:** C (Gemini 2.5 Pro via API)
**Duration:** ~45 min
**Depends on:** T0
**Branch:** `phase2/trend-penalty`
**Output:** Modified code for trend penalty in `src/signals/`

**NOTE:** Your tree shows `src/signals/momentum.py` and your code snippet already
has `_apply_trend_penalty` and `_apply_sentiment_overlay` methods. The trend penalty
may already be partially implemented inside the momentum signal module. Check this
in pre-flight.

#### Prompt for Agent C:

```

You are extending the trend penalty system for the APEX trading bot.

=== CODEBASE STYLE GUIDE (follow exactly) === import logging logger = logging.getLogger(**name**)

# Logging: stdlib, %-formatting, UPPERCASE module prefix

logger.debug( "TREND_PENALTY: %s score %.4f → %.4f " "(EMA60=%.2f < EMA240=%.2f)", asset, score, adjusted_score, ema_60, ema_240, )

# Type hints: built-in generics

def get_penalties(self) -> dict[str, float]:

# Config access: dict-style

threshold = self.config['trend_penalty']['broad_downturn_threshold']

# Defensive defaults

sent = sentiment_scores.get(asset, 0.5) # Neutral if missing === END STYLE GUIDE ===

=== EXISTING CODE CONTEXT === The file src/signals/momentum.py already contains a method _apply_trend_penalty() that partially implements this logic. The EMA states may already be tracked.

The FeatureEngine (data layer) provides per-asset prices via on_new_bar() calls every 1 minute.

Config parameters (already in config.yaml): trend_penalty.ema_short_period: 60 trend_penalty.ema_long_period: 240 trend_penalty.penalty_sigma: -0.3 trend_penalty.broad_downturn_threshold: 0.70 trend_penalty.broad_downturn_penalty_sigma: -0.15 === END CONTEXT ===

Generate a complete, standalone TrendPenaltyEngine class that can either: (a) Replace the existing _apply_trend_penalty method, or (b) Be called by it as a delegate.

The class must handle:

## Core Logic

- Maintain EMA(60) and EMA(240) per asset using incremental updates
- EMA formula: ema_new = alpha * price + (1 - alpha) * ema_old where alpha = 2 / (period + 1)
- When EMA(60) < EMA(240): asset is "bearish" → apply penalty

## Broad Downturn Scaling

- When >70% of universe has EMA(60) < EMA(240): penalty scales from -0.3 to -0.15 (half strength)
- Reasoning: in broad downturns, penalizing everything makes ranking random

## Warm-Up Handling

- Track bars_seen per asset
- Don't emit penalties until bars_seen >= ema_long_period (240 bars = 4 hours)
- Log: "TREND_PENALTY WARMUP: %s needs %d more bars"

## Interface

```python
class TrendPenaltyEngine:
    def __init__(self, config: dict):
        """Config keys: trend_penalty.*"""
        self._ema_short: dict[str, float] = {}
        self._ema_long: dict[str, float] = {}
        self._bars_seen: dict[str, int] = {}

    def update_price(self, symbol: str, price: float) -> None:
        """Call every 1 minute per asset. Updates EMAs incrementally."""

    def apply_penalties(self, scores: dict[str, float]) -> dict[str, float]:
        """Apply trend penalties to momentum scores.
        Returns new dict with adjusted scores. Does not mutate input.
        Assets still warming up: no penalty applied.
        """

    def get_bearish_fraction(self) -> float:
        """Fraction of warmed-up assets where EMA short < EMA long."""

    def is_warmed_up(self, symbol: str) -> bool:
        """True if symbol has enough bars for penalty calculation."""
```

## Edge Cases

- Asset with only 100 bars: skip penalty (not warmed up)
- Asset removed from universe: don't crash, just ignore in bearish fraction calc
- NaN price input: skip update for that bar, log warning

Output the complete class as a single Python file. Include unit tests at the bottom behind `if __name__ == "__main__":` that validate:

1. No penalty when EMA short > EMA long
2. Penalty applied when EMA short < EMA long (after warmup)
3. Broad downturn scaling at >70% bearish fraction
4. Warmup suppresses penalties for new symbols
5. NaN price is handled gracefully

```

---

### T3 — Meme Coin Sub-Pool — Extend Existing
**Agent:** C (Gemini 2.5 Pro via API, separate request from T2)
**Duration:** ~30 min
**Depends on:** T0
**Branch:** `phase2/meme-pool`
**Output:** Modified `src/signals/meme_pool.py`

#### Prompt for Agent C:

```

You are extending the meme coin sub-pool for the APEX trading bot.

=== CODEBASE STYLE GUIDE (follow exactly) === import logging logger = logging.getLogger(**name**)

logger.debug("MEME_POOL: selected %s (rank %d, score=%.4f)", symbol, rank, score)

# Type hints: built-in generics

def rank_and_select(self, ...) -> list[dict]:

# Config access

symbols = self.config['meme_pool']['symbols']

# Defensive defaults

score = momentum_scores.get(symbol, float('-inf')) === END STYLE GUIDE ===

=== CONTEXT === File src/signals/meme_pool.py already exists and may have a partial implementation. The RegimeState enum is defined in src/regime/regime_state.py with values: TREND_BULL, MEAN_REVERT, TREND_BEAR, HIGH_VOL_CRISIS

Config parameters (in config.yaml): meme_pool.enabled: true meme_pool.symbols: ["SHIB", "PEPE", "FLOKI", "WIF", "BONK", "PUMP", "PENGU"] meme_pool.max_allocation_per_coin: 0.03 meme_pool.top_n: 2 meme_pool.active_regimes: ["TREND_BULL"] meme_pool.trailing_stop_pct: 0.08 === END CONTEXT ===

Generate a complete MemePoolManager class:

```python
class MemePoolManager:
    def __init__(self, config: dict):
        """Load pool config. Store allowed symbols as a frozenset for O(1) lookup."""

    def rank_and_select(
        self,
        momentum_scores: dict[str, float],
        current_regime: str,
    ) -> list[dict]:
        """Rank meme coins by momentum and select top N.
        
        Returns list of allocation dicts:
        [{"symbol": "PEPE", "weight": 0.03, "stop_pct": 0.08}, ...]
        
        Returns empty list if:
        - regime not in active_regimes
        - meme_pool.enabled is False
        - no meme coins have valid (non-NaN, non-None) momentum scores
        
        Edge cases:
        - Fewer than top_n valid scores: return however many are valid
        - Ties in score: stable sort (arbitrary but deterministic)
        """

    def is_meme_symbol(self, symbol: str) -> bool:
        """O(1) check if symbol belongs to meme pool."""
    
    def get_total_meme_exposure(self, allocations: list[dict]) -> float:
        """Sum of weights in allocation list. Max theoretical: top_n * max_allocation."""
```

DOGE is NOT in the pool (it's Tier 1, competes in main ranking). 1000CHEEMS is NOT in the pool (reclassified to Tier 5).

Output the complete class as a single Python file. Include tests behind `if __name__ == "__main__":` that validate:

1. Returns top 2 in TREND_BULL with valid scores
2. Returns empty list in MEAN_REVERT, TREND_BEAR, HIGH_VOL_CRISIS
3. Handles case where only 1 meme has valid score
4. Returns empty list when enabled=False
5. DOGE is excluded even if present in momentum_scores
6. NaN scores are filtered out

```

---

### T4 — Dynamic Trailing Stop Tightening
**Agent:** B (Claude Sonnet via Claude Code CLI)
**Duration:** ~45 min
**Depends on:** T0
**Branch:** `phase2/dynamic-stops`
**Output:** Modified `src/risk/trailing_stops.py`

#### Prompt for Agent B:

```

CONTEXT: You are adding dynamic trailing stop tightening to the APEX trading bot. The stop tightening is PER-POSITION based on each position's own unrealized P&L.

STEP 1 — Read these files before writing any code: cat src/risk/trailing_stops.py # Current trailing stop implementation cat src/risk/manager.py # How stops are checked in the risk manager cat src/risk/**init**.py # Exports cat config.yaml | grep -A 10 "dynamic_stops" # New config parameters

{{SHARED_CONTEXT}}

STEP 2 — Understand the current stop interface:

- How is a trailing stop currently defined per position? (fixed distance?)
- Where is the stop checked? (which method, how often?)
- What happens when a stop is triggered? (method call, event, etc.)

STEP 3 — Add dynamic tightening. The changes should be MINIMAL and SURGICAL. Do not rewrite the entire module. Add the new capability to the existing structure.

## Tightening Logic

Based on each position's unrealized P&L, tighten its trailing stop distance:

|Unrealized PnL|Factor|Example (base 6%)|
|---|---|---|
|> +5.0%|0.60|6% × 0.60 = 3.6%|
|+2.5% to +5.0%|0.80|6% × 0.80 = 4.8%|
|< +2.5%|1.00|6% (unchanged)|

The adjusted stop distance can NEVER go below 2% (min_stop_floor).

## Implementation Approach

Add a method like:

```python
def get_effective_stop_distance(
    self,
    base_stop_pct: float,
    entry_price: float,
    current_price: float,
    stop_override: float | None = None,
) -> float:
    """Calculate effective stop distance after dynamic tightening.
    
    This replaces the v3.0 portfolio-wide daily P&L governor. The old
    approach tightened ALL stops when portfolio gained >3%, which caused
    new entries (0% gain) to get tight stops and immediately stop out.
    Per-position tightening isolates risk management correctly.
    
    Args:
        base_stop_pct: Tier-based default (0.06 for Tier 1-3, 0.08 for meme, etc.)
        entry_price: Position entry price
        current_price: Current market price
        stop_override: Optional override from endgame de-risking (takes precedence)
    
    Returns:
        Effective stop distance as a fraction (e.g., 0.036 for 3.6%)
        Never below config['dynamic_stops']['min_stop_floor']
    """
```

Then modify the existing stop check to call this method instead of using base_stop_pct directly.

## Config Parameters

Read from config['dynamic_stops']: tighten_high_threshold: 0.05 tighten_high_factor: 0.60 tighten_mid_threshold: 0.025 tighten_mid_factor: 0.80 min_stop_floor: 0.02

## stop_override Parameter

The endgame de-risking module (T5) can pass a stop_override value (e.g., 0.03 or 0.02) that takes precedence over the dynamic tightening calculation. The logic is: if stop_override is not None: effective = max(stop_override, min_stop_floor) else: effective = max(base_stop * tighten_factor, min_stop_floor)

## Logging

Log when a position's stop is dynamically tightened: "DYNAMIC_STOP: %s tightened %.2f%% → %.2f%% (unrealized_pnl=+%.2f%%)"

## Tests

Extend tests/test_risk.py (read it first). Add these test cases:

1. No tightening below +2.5% unrealized
2. 20% tightening (factor 0.80) between +2.5% and +5%
3. 40% tightening (factor 0.60) above +5%
4. Floor at 2% even with extreme gains (e.g., +50%)
5. stop_override takes precedence over dynamic tightening
6. stop_override still respects min_stop_floor

Run tests: python -m pytest tests/test_risk.py -v

```

---

### T5 — End-Game De-Risking
**Agent:** B (Claude Sonnet via Claude Code CLI, 2nd instance)
**Duration:** ~30 min
**Depends on:** T0
**Branch:** `phase2/endgame`
**Output:** New file `src/portfolio/endgame.py` or extend existing if present

#### Prompt for Agent B:

```

CONTEXT: You are implementing end-game de-risking for the APEX trading bot. As the competition round nears its end, the bot progressively reduces exposure.

STEP 1 — Read existing portfolio module: cat src/portfolio/constructor.py cat src/portfolio/adaptive.py # May have related logic cat src/portfolio/**init**.py ls src/portfolio/

{{SHARED_CONTEXT}}

STEP 2 — Check if endgame logic already exists anywhere: grep -rn "endgame|end_game|de_risk|hours_remaining" src/

STEP 3 — Create src/portfolio/endgame.py (or extend if file exists):

```python
"""End-game de-risking for competition rounds.

As time remaining decreases, exposure caps tighten and trailing stops
narrow to lock in accumulated gains. The final 15 minutes force a
complete exit to avoid last-minute drawdowns that would damage Calmar.

The schedule is loaded from config and supports clean reset for Round 2.
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class EndgameManager:
    def __init__(self, config: dict):
        """Parse schedule from config['endgame']['schedule'] and round end time.
        
        Schedule is a list of dicts sorted by hours_remaining descending:
          [{"hours_remaining": 48, "max_exposure": 1.0, "stop_override": null}, ...]
        """

    def get_constraints(self, now: datetime | None = None) -> dict:
        """Return current endgame constraints.
        
        Returns:
            {
                "max_exposure": float,        # 0.0 to 1.0, caps portfolio crypto exposure
                "stop_override": float | None, # If set, overrides dynamic stop distance
                "sell_all": bool,              # True if < 15 min remaining (non-negotiable)
                "hours_remaining": float,      # For logging/monitoring
                "tier": int,                   # Which schedule tier we're in (for logging)
            }
        
        Logic:
        - Walk schedule from tightest (lowest hours_remaining) to loosest
        - First entry where hours_remaining >= time left applies
        - If hours_remaining <= 0.25 (15 min): sell_all = True
        
        Edge cases:
        - now > round_end: return sell_all=True (competition is over)
        - now is None: use datetime.now(timezone.utc)
        """

    def reset_for_round(self, new_end_utc: str) -> None:
        """Reset round end time for Round 2. Parse ISO format string.
        Log: "ENDGAME RESET: new round end %s"
        """
    
    @property
    def round_end(self) -> datetime:
        """Read-only access to configured round end time."""
```

## Logging

Log tier transitions: "ENDGAME TIER_CHANGE: tier %d → %d (%.1fh remaining, max_exposure=%.0f%%)"

## Edge Case: Competition Already Over

If current time is past round_end, return the most conservative constraint (sell_all=True, max_exposure=0.0). This handles delayed restarts gracefully.

## Tests

Create tests or extend tests/test_portfolio.py:

1. > 48h remaining: max_exposure=1.0, no stop override, sell_all=False
    
2. 24-48h: max_exposure=0.70
3. 4-12h: max_exposure=0.25, stop_override=0.03
4. < 15 min: sell_all=True, max_exposure=0.0
5. Past round_end: sell_all=True
6. reset_for_round updates the end time correctly
7. Timezone handling: all comparisons in UTC

Run tests: python -m pytest tests/test_portfolio.py -v

```

---

### T6 — PAXG Allocator
**Agent:** B (Claude Sonnet via Claude Code CLI)
**Duration:** ~20 min
**Depends on:** T0 (and T1 only if RegimeState enum doesn't exist yet — check in pre-flight)
**Branch:** `phase2/paxg` (can merge to `phase2/regime` branch if needed for import)
**Output:** New file `src/portfolio/paxg_allocator.py`

#### Prompt for Agent B:

```

CONTEXT: PAXG (gold-backed token) is a volatility reducer allocated from the CASH portion of the portfolio. It does not compete with crypto signal allocations.

STEP 1 — Read: cat src/regime/regime_state.py # RegimeState enum cat src/portfolio/**init**.py cat config.yaml | grep -A 10 paxg

{{SHARED_CONTEXT}}

STEP 2 — Create src/portfolio/paxg_allocator.py:

```python
"""PAXG allocation as portfolio volatility reducer.

PAXG is allocated from the cash buffer, not from crypto signal allocations.
Its weight scales with regime defensiveness — more PAXG in bearish conditions
provides a Sharpe denominator benefit (lower total portfolio volatility).

This is NOT a Treynor instrument (Treynor is not scored). It is purely a
volatility reducer for Sharpe (0.3 weight) and a potential positive-return
source during crypto selloffs for Sortino (0.4 weight).
"""
import logging
from src.regime.regime_state import RegimeState

logger = logging.getLogger(__name__)


class PAXGAllocator:
    def __init__(self, config: dict):
        """Load allocation targets from config['paxg']['allocation'].
        Map string regime names to RegimeState enum values.
        """

    def get_target_weight(self, regime: RegimeState) -> float:
        """Target PAXG weight as fraction of NAV for given regime.
        Returns config value directly (already midpoint floats).
        Falls back to 0.05 if regime not in config (defensive default).
        """

    def compute_order(
        self,
        current_paxg_weight: float,
        regime: RegimeState,
        nav: float,
    ) -> dict | None:
        """Compute rebalance order if PAXG weight differs from target.
        
        Returns None if |current - target| < rebalance_tolerance (1% NAV).
        Otherwise returns:
            {"symbol": "PAXG", "side": "buy" | "sell", "amount_usd": float}
        
        Logs: "PAXG REBALANCE: %s $%.0f (current=%.1f%%, target=%.1f%%, regime=%s)"
        """
```

Keep it simple. ~50 lines of logic. Full type hints. Defensive defaults.

Test cases (add to tests/test_portfolio.py or new file):

1. TREND_BULL target = 0.04
2. HIGH_VOL_CRISIS target = 0.125
3. No order when within tolerance
4. Buy order when current < target - tolerance
5. Sell order when current > target + tolerance
6. Unknown regime falls back to 0.05

Run: python -m pytest tests/test_portfolio.py -v

````

---

### MERGE CHECKPOINT

**Before proceeding to T7, ALL Wave 2 branches must be merged to main.**

```bash
# Merge order (least likely to conflict first):
git checkout main
git merge phase2/endgame
git merge phase2/paxg
git merge phase2/meme-pool
git merge phase2/trend-penalty
git merge phase2/dynamic-stops
git merge phase2/regime          # Most likely to touch shared files

# Run full test suite to verify no merge breakage:
python -m pytest tests/ -v

# If conflicts: resolve, test, commit.
# If tests fail: fix on main, commit with "phase2(merge): fix [description]"
````

---

### T7 — Portfolio Construction Rewrite (Integration)

**Agent:** A (Claude Opus via Claude Code CLI) **Duration:** ~60–90 min **Depends on:** ALL of T0–T6 merged to main **Branch:** `phase2/integration` **Output:** Rewritten `src/portfolio/constructor.py`

#### Prompt for Agent A:

````
CONTEXT: You are rewriting the portfolio construction module to integrate all
Phase 2 components. This is the strategy heart — the module judges will read
first for Screen 4 (60% of evaluation).

STEP 1 — Read ALL Phase 2 modules (post-merge, these are all on main now):
  cat src/regime/regime_state.py
  cat src/regime/detector.py
  cat src/regime/contagion.py
  cat src/signals/meme_pool.py
  cat src/risk/trailing_stops.py    # Note the get_effective_stop_distance method
  cat src/portfolio/endgame.py
  cat src/portfolio/paxg_allocator.py
  cat src/utils/validation.py

STEP 2 — Read the existing portfolio constructor:
  cat src/portfolio/constructor.py
  cat src/portfolio/factory.py       # How constructor is instantiated
  cat src/portfolio/adaptive.py      # Adaptive exposure logic

STEP 3 — Read the main loop to understand calling convention:
  cat src/main.py

STEP 4 — Read the FeatureEngine for data interface:
  grep -rn "class FeatureEngine" src/ apex/
  # Then cat that file

{{SHARED_CONTEXT}}

NOW rewrite src/portfolio/constructor.py. This is a REWRITE, not a patch —
the existing code was Phase 1 (equal weight, no regime awareness). But preserve
any utility functions or type definitions that are still useful.

## The Portfolio Construction Pipeline

The construct() method runs every rebalance (~60 minutes) and executes these
steps IN ORDER. Each step is a private method with a clear name.

```python
class PortfolioConstructor:
    """Regime-conditional portfolio construction.
    
    Integrates: regime detection → exposure targeting → momentum ranking →
    trend penalty → position sizing → meme pool → PAXG → turnover control.
    
    Each step is a private method. The construct() method reads as a
    narrative of the strategy for Screen 4 judges.
    """
    
    def __init__(
        self,
        config: dict,
        regime_detector: RegimeDetector,
        trend_penalty: TrendPenaltyEngine,  # or however it's named
        meme_pool: MemePoolManager,
        endgame: EndgameManager,
        paxg: PAXGAllocator,
        risk_manager: RiskManager,
    ):
        """Dependency injection. All Phase 2 components passed by reference."""
    
    def construct(
        self,
        momentum_scores: dict[str, float],
        current_positions: dict[str, float],  # {symbol: current_weight}
        nav: float,
        market_data,  # FeatureEngine or equivalent
    ) -> ConstructionResult:
        """Execute the full construction pipeline.
        
        Returns ConstructionResult with:
          target_weights: dict[str, float]  — {symbol: target_weight}
          orders: list[dict]                — orders needed to reach targets
          metadata: dict                    — regime, exposure, decisions for logging
        """
        # Step 1: Get current regime
        regime = self.regime_detector.current_regime
        
        # Step 2: Determine exposure target for this regime
        target_exposure = self._get_target_exposure(regime)
        
        # Step 3: Apply endgame constraints (caps exposure, may force sell-all)
        endgame = self.endgame.get_constraints()
        target_exposure = min(target_exposure, endgame['max_exposure'])
        if endgame['sell_all']:
            return self._force_liquidation(current_positions, nav)
        
        # Step 4: Apply trend penalties to momentum scores
        adjusted_scores = self.trend_penalty.apply_penalties(momentum_scores)
        
        # Step 5: Rank and select top N assets (N = regime holdings count)
        max_holdings = self._get_max_holdings(regime)
        candidates = self._rank_and_select(adjusted_scores, max_holdings)
        
        # Step 6: Apply volatility exclusion filter
        candidates = self._apply_vol_filter(candidates, market_data)
        
        # Step 7: Handle edge case — vol filter removed all candidates
        if not candidates and target_exposure > 0:
            # Fall back to top 3 by raw momentum (skip vol filter)
            candidates = self._rank_and_select(adjusted_scores, min(3, max_holdings))
            logger.warning("VOL_FILTER removed all candidates, falling back to top %d",
                          len(candidates))
        
        # Step 8: Size positions (Phase 1: equal weight, Phase 2: inverse-vol)
        crypto_weights = self._size_positions(candidates, target_exposure)
        
        # Step 9: Apply tier caps and redistribute excess
        crypto_weights = self._apply_tier_caps(crypto_weights)
        
        # Step 10: Get meme pool allocations
        meme_weights = self._get_meme_allocations(momentum_scores, regime)
        
        # Step 11: Get PAXG target
        paxg_weight = self.paxg.get_target_weight(regime)
        
        # Step 12: Combine and validate total doesn't exceed 1.0
        target_weights = self._combine_weights(
            crypto_weights, meme_weights, paxg_weight, target_exposure
        )
        
        # Step 13: Apply turnover constraint
        target_weights = self._apply_turnover_cap(
            target_weights, current_positions
        )
        
        # Step 14: Suppress small trades
        target_weights = self._suppress_small_trades(
            target_weights, current_positions, nav
        )
        
        # Step 15: Compute orders
        orders = self._compute_orders(target_weights, current_positions, nav)
        
        return ConstructionResult(
            target_weights=target_weights,
            orders=orders,
            metadata={...}
        )
````

## CRITICAL EDGE CASES (address each explicitly):

1. **Vol filter removes all candidates:** Fall back to top 3 by raw momentum. Never deploy 80% exposure into zero assets.
    
2. **Combined weights exceed 100%:** _combine_weights must enforce: total = sum(crypto) + sum(meme) + paxg_weight <= 1.0 Priority if over: reduce crypto weights proportionally first. If still over: reduce meme weights. PAXG is last to be cut.
    
3. **Meme + PAXG + crypto deployment exceeds target_exposure:** Meme allocations come FROM the crypto exposure budget, not on top of it. PAXG comes from the cash buffer (does not count toward crypto exposure).
    
4. **Endgame sell_all:** Return a target portfolio with all weights = 0. The execution engine handles the actual selling.
    
5. **Zero NAV or negative NAV:** Log error, return empty portfolio. Don't divide by zero.
    
6. **No momentum scores available (data outage):** Hold current positions unchanged. Log warning. Return current_positions as target_weights.
    

## ConstructionResult

Define as a dataclass or TypedDict:

```python
@dataclass
class ConstructionResult:
    target_weights: dict[str, float]
    orders: list[dict]
    metadata: dict  # regime, exposure_target, holdings_count, endgame_tier, etc.
```

## Tier Caps (from config['portfolio']['tier_caps'])

Apply AFTER sizing, BEFORE turnover constraint:

- Tier 1: 8% (DOGE: 5%)
- Tier 2: 8%
- Tier 3: 6%
- Tier 4 (meme): 3%
- Tier 5: 2%
- TRUMP: 2%
- PAXG: 15% Excess from capped positions redistributed to uncapped, pro-rata.

## Code Quality (THIS IS THE MOST REVIEWED FILE)

- construct() must read like a narrative — a judge should understand the strategy in 5 minutes by reading just this method
- Each _private_method has a 2-line docstring explaining its role
- Log at each step: what was decided and why
- Type hints on everything
- No magic numbers — all from config
- Add a method: def explain(self) -> str that returns a human-readable summary of the current portfolio construction state (for README/debugging)

After writing, re-read construct() from top to bottom. Ask: can a non-quant judge follow this? If not, refactor.

## Tests

Extend tests/test_portfolio.py:

1. TREND_BULL regime → 75-85% exposure, 10 holdings
2. HIGH_VOL_CRISIS → 10-20% exposure, 0 crypto holdings (PAXG only)
3. Endgame sell_all → all weights zero
4. Vol filter removes all → falls back to top 3
5. Tier cap overflow → excess redistributed correctly
6. Combined weights never exceed 1.0
7. Meme allocations only in TREND_BULL
8. Turnover constraint limits changes to 25%

Run: python -m pytest tests/test_portfolio.py -v

```

---

### T8 — Main Loop Integration
**Agent:** A (Claude Opus via Claude Code CLI)
**Duration:** ~30 min
**Depends on:** T7
**Branch:** `phase2/integration` (same branch as T7)
**Output:** Modified `src/main.py`

#### Prompt for Agent A:

```

CONTEXT: You are wiring Phase 2 components into the main event loop.

STEP 1 — Read the current main loop: cat src/main.py cat src/orchestration/scheduler.py # How timing works cat src/orchestration/startup.py # How components are initialized

STEP 2 — Read the factory pattern (if used): cat src/portfolio/factory.py cat src/risk/factory.py

{{SHARED_CONTEXT}}

STEP 3 — Integrate Phase 2. The changes are:

## Initialization (startup sequence):

Add Phase 2 component instantiation. Use dependency injection.

```python
# --- Phase 2 Components ---
from src.regime.detector import RegimeDetector
from src.regime.regime_state import RegimeState
from src.signals.trend_penalty import TrendPenaltyEngine  # or wherever it lives
from src.signals.meme_pool import MemePoolManager
from src.portfolio.endgame import EndgameManager
from src.portfolio.paxg_allocator import PAXGAllocator

regime_detector = RegimeDetector(config)
trend_penalty = TrendPenaltyEngine(config)
meme_pool = MemePoolManager(config)
endgame = EndgameManager(config)
paxg_allocator = PAXGAllocator(config)

# Wire into portfolio constructor
portfolio_constructor = PortfolioConstructor(
    config=config,
    regime_detector=regime_detector,
    trend_penalty=trend_penalty,
    meme_pool=meme_pool,
    endgame=endgame,
    paxg=paxg_allocator,
    risk_manager=risk_manager,
)
```

## Every 1-Minute Tick (add to existing loop):

```python
# --- Phase 2: per-minute updates ---
# Update trend penalty EMAs
for symbol, price in latest_prices.items():
    trend_penalty.update_price(symbol, price)

# Update regime detection
regime_inputs = feature_engine.get_regime_inputs()
current_regime = regime_detector.update(
    regime_inputs, 
    position_tracker.held_positions,
    feature_engine.get_return,
)

# Check endgame constraints (may override stop distances)
endgame_constraints = endgame.get_constraints()
if endgame_constraints['sell_all']:
    # Immediately liquidate everything — non-negotiable
    logger.critical("ENDGAME: sell_all triggered, liquidating")
    # ... trigger liquidation through execution engine ...
```

## Error Handling:

Each Phase 2 call wrapped individually:

```python
try:
    current_regime = regime_detector.update(regime_inputs, positions, get_return_fn)
except Exception as e:
    logger.error("REGIME_DETECTOR failed: %s. Defaulting to MEAN_REVERT", e)
    current_regime = RegimeState.MEAN_REVERT
```

Do this for EACH Phase 2 component. A failure in trend penalty should not prevent regime detection from running. A failure in meme pool should not prevent the main portfolio construction.

## DO NOT:

- Restructure the existing Phase 1 loop unless necessary
- Move existing code into new files
- Change the scheduler/timing mechanism
- Add new external dependencies

Mark all Phase 2 additions with clear comments:

# --- Phase 2: Regime Detection ---

# --- Phase 2: Trend Penalty Updates ---

# --- Phase 2: Endgame Check ---

```

---

### T9 — Integration Tests + Hardened Smoke Suite
**Agent:** B (Claude Sonnet via Claude Code CLI)
**Duration:** ~30 min
**Depends on:** T7, T8
**Branch:** `phase2/tests`
**Output:** Updated `tests/test_integration.py`, new `tests/test_phase2_smoke.py`

#### Prompt for Agent B:

```

CONTEXT: You are writing integration and smoke tests for Phase 2 of the APEX trading bot. These tests verify that all Phase 2 components work together.

STEP 1 — Read existing test infrastructure: cat tests/conftest.py # Fixtures, shared setup cat tests/test_integration.py # Existing integration tests cat tests/test_regime.py # How regime tests are structured cat tests/test_portfolio.py cat tests/test_risk.py

STEP 2 — Read all Phase 2 modules to understand interfaces: cat src/regime/detector.py cat src/signals/meme_pool.py cat src/risk/trailing_stops.py cat src/portfolio/constructor.py cat src/portfolio/endgame.py cat src/portfolio/paxg_allocator.py cat src/utils/validation.py

STEP 3 — Extend tests/test_integration.py with Phase 2 integration tests:

```python
# All tests use pytest. Mock market data and exchange API.
# Each test is self-contained with clear arrange/act/assert.

class TestPhase2Integration:
    """Integration tests verifying Phase 2 component interactions."""

    def test_regime_drives_exposure(self):
        """In TREND_BULL, portfolio targets 75-85% crypto.
        In CRISIS, targets 10-20%. Verify constructor respects regime."""

    def test_trend_penalty_affects_ranking(self):
        """Asset with bearish EMA crossover gets lower effective score,
        potentially dropping out of top-N selection."""

    def test_meme_pool_regime_gating(self):
        """Meme allocations appear in TREND_BULL, disappear in other regimes."""

    def test_endgame_overrides_regime(self):
        """With < 12h remaining, exposure capped at 45% even if regime
        says TREND_BULL (which would normally target 80%)."""

    def test_paxg_scales_with_defensiveness(self):
        """PAXG weight: BULL < MEAN_REVERT < BEAR."""

    def test_dynamic_stop_tightening_per_position(self):
        """Position with +6% gain has tighter stop than +1% gain position.
        Verify they are independent (tightening one doesn't affect other)."""

    def test_crisis_is_immediate(self):
        """Regime transitions to CRISIS without waiting for persistence timer."""

    def test_turnover_constraint(self):
        """Rebalance that would replace >25% of portfolio is throttled."""

    def test_total_weights_never_exceed_one(self):
        """CRITICAL: After construction, sum(weights) <= 1.0.
        Test with high-exposure regime + meme pool + PAXG to stress this."""

    def test_no_negative_weights(self):
        """No position weight is negative after construction."""
```

STEP 4 — Create tests/test_phase2_smoke.py:

```python
"""Smoke tests — verify Phase 2 bot starts and runs one cycle without crash.

Run on EC2 instance before live trading to catch import errors, config
mismatches, and initialization failures. No network calls.
"""

class TestPhase2Smoke:
    def test_config_has_all_phase2_keys(self):
        """All Phase 2 config sections present and parseable."""
        config = yaml.safe_load(open('config.yaml'))
        assert 'regime' in config
        assert 'trend_penalty' in config
        assert 'meme_pool' in config
        assert 'dynamic_stops' in config
        assert 'paxg' in config
        assert 'endgame' in config
        assert 'portfolio' in config

    def test_all_phase2_imports(self):
        """All Phase 2 modules import without error."""
        from src.regime.detector import RegimeDetector
        from src.regime.regime_state import RegimeState
        from src.signals.meme_pool import MemePoolManager
        from src.portfolio.endgame import EndgameManager
        from src.portfolio.paxg_allocator import PAXGAllocator
        from src.utils.validation import validate_regime_inputs

    def test_full_component_initialization(self):
        """All Phase 2 components instantiate from config without crash."""
        config = yaml.safe_load(open('config.yaml'))
        regime = RegimeDetector(config)
        meme = MemePoolManager(config)
        endgame = EndgameManager(config)
        paxg = PAXGAllocator(config)

    def test_one_rebalance_cycle(self):
        """Synthetic data through one full rebalance. No crash.
        ALSO ASSERT: output weights sum <= 1.0 and all >= 0.0.
        These two assertions catch real classes of bugs."""
        # Create synthetic momentum scores for ~10 assets
        # Create synthetic positions (empty portfolio)
        # Run portfolio_constructor.construct()
        result = constructor.construct(scores, {}, 1_000_000, mock_data)
        
        total_weight = sum(result.target_weights.values())
        assert total_weight <= 1.0 + 1e-9, f"Weights sum to {total_weight}"
        for sym, w in result.target_weights.items():
            assert w >= 0.0, f"{sym} has negative weight {w}"

    def test_regime_detector_cold_start(self):
        """Fresh detector starts in MEAN_REVERT, not CRISIS or BULL."""
        config = yaml.safe_load(open('config.yaml'))
        detector = RegimeDetector(config)
        assert detector.current_regime == RegimeState.MEAN_REVERT
```

Run full suite: python -m pytest tests/ -v Fix any failures before marking T9 complete.

````

---

## Execution Checklist

### Pre-Flight (~10 min) — DO THIS FIRST
- [ ] Run all pre-flight commands from top of document
- [ ] Record answers in `agent/implementation/shared_context.md`
- [ ] Confirm FeatureEngine location and interface

### Wave 0: Config (T0 + T0b) — ~15 min
- [ ] **YOU:** Add Phase 2 config to config.yaml (use the YAML block above)
- [ ] Validate: `python -c "import yaml; yaml.safe_load(open('config.yaml'))"`
- [ ] Commit to main: `git add config.yaml && git commit -m "phase2(config): add all Phase 2 parameters"`
- [ ] Agent D: T0b — Create src/utils/validation.py
- [ ] Commit to main: `git commit -m "phase2(validation): add input validation helpers"`

### Wave 2: Parallel Module Build — ~60–90 min
Create all feature branches from main (post-T0):
```bash
for branch in regime trend-penalty meme-pool dynamic-stops endgame paxg; do
  git checkout main && git checkout -b "phase2/$branch"
done
````

Start ALL simultaneously:

- [ ] Agent A (Opus CLI): T1 — Regime Detection → branch `phase2/regime`
- [ ] Agent C (Gemini): T2 — Trend Penalty → branch `phase2/trend-penalty`
- [ ] Agent C (Gemini, 2nd): T3 — Meme Pool → branch `phase2/meme-pool`
- [ ] Agent B (Sonnet CLI): T4 — Dynamic Stops → branch `phase2/dynamic-stops`
- [ ] Agent B (Sonnet CLI, 2nd): T5 — End-Game → branch `phase2/endgame`
- [ ] Agent B (Sonnet CLI, 3rd or after T4/T5): T6 — PAXG → branch `phase2/paxg`

As each completes:

- [ ] Review output, fix import paths (especially Gemini output for T2, T3)
- [ ] Run its unit tests on the feature branch
- [ ] Commit on feature branch

### Merge Checkpoint — ~15 min

- [ ] Merge all Wave 2 branches to main (see merge order above)
- [ ] Resolve any conflicts
- [ ] Run: `python -m pytest tests/ -v` — ALL tests must pass
- [ ] Commit merge: `git commit -m "phase2(merge): integrate all Wave 2 modules"`

### Wave 3: Integration (T7 + T8) — ~60–90 min

- [ ] `git checkout -b phase2/integration`
- [ ] Agent A (Opus CLI): T7 — Portfolio Constructor rewrite
- [ ] **PAUSE.** Read T7 output yourself. This is the strategy heart. Does construct() read as a clear narrative? Can a judge follow it in 5 min?
- [ ] Agent A (Opus CLI): T8 — Main Loop wiring
- [ ] Run: `python -m pytest tests/ -v`
- [ ] Merge to main

### Wave 4: Tests (T9) — ~30 min

- [ ] `git checkout -b phase2/tests`
- [ ] Agent B (Sonnet CLI): T9 — Integration + smoke tests
- [ ] Run: `python -m pytest tests/ -v`
- [ ] Fix failures
- [ ] Merge to main

### Final Validation

- [ ] Full suite: `python -m pytest tests/ -v --tb=short`
- [ ] Smoke test: `python -m pytest tests/test_phase2_smoke.py -v`
- [ ] Bot starts without crash (if possible, dry-run the main loop for 5 minutes)
- [ ] `git log --oneline` shows clean Phase 2 commit trail
- [ ] No dead code or commented-out Phase 1 logic left behind

---

## Time Budget (Realistic)

| Phase      | Tasks             | Wall Clock      | Notes                              |
| ---------- | ----------------- | --------------- | ---------------------------------- |
| Pre-flight | Manual checks     | 10 min          | Cannot be skipped                  |
| Wave 0     | T0 (manual) + T0b | 15 min          | Config is the foundation           |
| Wave 2     | T1–T6 in parallel | 90 min          | Bounded by T1 (Opus, most complex) |
| Merge      | Resolve + test    | 15 min          | Budget for 1-2 conflicts           |
| Wave 3     | T7 + T8           | 90–120 min      | Include your review pass           |
| Wave 4     | T9                | 30 min          |                                    |
| Buffer     | Debug, fix        | 60 min          | Something WILL break at the seams  |
| **Total**  |                   | **5–6.5 hours** | Plan for 8, be happy at 6          |
