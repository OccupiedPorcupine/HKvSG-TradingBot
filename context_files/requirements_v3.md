# APEX — Autonomous Portfolio Execution Agent
## System Requirements Document v3.1

**Changes from v3.0:** Trend filter changed from exit override to momentum score penalty [DA-GAP-1]. Phase 1 MVA gets minimal vol guard [DA-GAP-2]. Daily P&L governor replaced with dynamic trailing stop tightening [DA-GAP-3]. Tier 5 sub-pool added [DA-GAP-5]. Adaptive exposure adjustment added [DA-CRIT-3]. End-game final hour changed to stop tightening [DA-TRD-1]. TRUMP overnight close rule removed (24/7 market) [DA-TRD-4]. Universe discovery on first API call [DA-CRIT-4]. Scoring uncertainty flagged (3 vs 4 ratios) [DA-CRIT-2]. API testing elevated to gated prerequisite [DA-CRIT-1]. Minimum trailing stop floor of 2% added. ML retrain sanity check added.

**Changes from v2.0 (in v3.0):** Architecture redesigned for t3.medium (2 vCPU, 4GB RAM). Removed all external services. Removed non-existent data sources. Replaced HMM + GARCH with rule-based regime. Deferred ML to Phase 3. Simplified portfolio construction. Fixed trend following MA periods. Added phased delivery plan.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Competition Constraints](#2-competition-constraints)
3. [Asset Universe](#3-asset-universe)
4. [Scoring Objective](#4-scoring-objective)
5. [System Architecture Overview](#5-system-architecture-overview)
6. [Layer 1 — Data Ingestion](#6-layer-1--data-ingestion)
7. [Layer 2 — Feature Engineering](#7-layer-2--feature-engineering)
8. [Layer 3 — Regime Detection](#8-layer-3--regime-detection)
9. [Layer 4 — Signal Generation](#9-layer-4--signal-generation)
10. [Layer 5 — Portfolio Construction](#10-layer-5--portfolio-construction)
11. [Layer 6 — Risk Management](#11-layer-6--risk-management)
12. [Layer 7 — Execution Engine](#12-layer-7--execution-engine)
13. [Layer 8 — Feedback & Adaptation](#13-layer-8--feedback--adaptation)
14. [Capital Allocation](#14-capital-allocation)
15. [Operational Cadence](#15-operational-cadence)
16. [Technology Stack](#16-technology-stack)
17. [Non-Functional Requirements](#17-non-functional-requirements)
18. [Phased Delivery Plan](#18-phased-delivery-plan)
19. [Open Questions & Risks](#19-open-questions--risks)

---

## 1. Project Overview

**APEX** (Autonomous Portfolio Execution Agent) is a fully autonomous algorithmic trading bot designed for a 10-day cryptocurrency trading competition. It operates on the Roostoo mock exchange with a starting capital of $1,000,000 USD across a universe of 56 cryptocurrency spot pairs.

The bot must autonomously manage a long-only portfolio with zero manual intervention. Advancement to finals requires sufficient **absolute return** to reach top 20 on the leaderboard, followed by deeper evaluation on **risk-adjusted metrics** (Sharpe, Sortino, Calmar, Treynor) and code review to select the top 8. The architecture must serve both objectives.

### 1.1 Design Philosophy

| Principle | Description |
|---|---|
| Ship First, Optimize Second | A working bot on day 1 beats a perfect bot on day 5 |
| Signal Over Story | Every alpha source must demonstrate edge before deployment |
| Regime Awareness | Signal weights conditioned on market regime state |
| Sizing Is Alpha | Position sizing and cash rotation are the primary risk management tools |
| Limit Orders Always | Guaranteed fills + lower fees make limit orders strictly dominant |
| Single Process | Everything runs in one Python process — no external services |

---

## 2. Competition Constraints

These are hard rules. Any violation results in disqualification.

### 2.1 Trading Constraints

| Constraint | Specification |
|---|---|
| Trading type | Spot only |
| Leverage | Not permitted |
| Short selling | Not permitted |
| Derivatives / options / perps | Not permitted |
| Arbitrage | Not permitted (single platform) |
| Strategy type | Directional only — no market-making |
| Manual API calls | Strictly prohibited — all trades must be bot-generated |

### 2.2 Operational Constraints

| Constraint | Specification |
|---|---|
| Minimum active trading days | 8 out of 10 days minimum |
| Strategy updates | Permitted during competition but every change must be committed to the repo |
| Repo | Open-source, submitted for code and strategy review |
| External data | Allowed — AI models, news sentiment APIs, external data sources permitted |

### 2.3 Exchange Rules

| Parameter | Value |
|---|---|
| Market order commission | 0.1% per trade |
| Limit order commission | 0.05% per trade |
| Slippage | None — orders fill at exact stated price |
| Market impact | None — order size does not move price |
| Fill guarantee | 100% — all orders always fill when price condition is met |
| API rate limit | 30–60 calls per minute (must verify empirically) |

### 2.4 Cost Implications

- Limit orders are **strictly dominant** — used for 100% of executions
- Round-trip cost using limit orders = **0.1%** (0.05% entry + 0.05% exit)
- A trade is only worth executing if the expected signal return exceeds **0.1%** before the next rebalance
- Trades below a minimum portfolio weight delta threshold of **0.2% of NAV** are suppressed

---

## 3. Asset Universe

56 cryptocurrency spot pairs traded against USD.

### 3.1 Full Universe

```
AAVE/USD, UNI/USD, WLD/USD, POL/USD, BMT/USD, CAKE/USD, LINK/USD,
DOT/USD, FIL/USD, ZEN/USD, EIGEN/USD, PENDLE/USD, SUI/USD, TRUMP/USD,
DOGE/USD, CFX/USD, FET/USD, ZEC/USD, SHIB/USD, HBAR/USD, TRX/USD,
SEI/USD, TON/USD, S/USD, ARB/USD, SOMI/USD, APT/USD, AVNT/USD, ADA/USD,
FLOKI/USD, PEPE/USD, ENA/USD, OMNI/USD, PAXG/USD, PUMP/USD, ASTER/USD,
STO/USD, NEAR/USD, LISTA/USD, EDEN/USD, FORM/USD, PENGU/USD,
1000CHEEMS/USD, LTC/USD, CRV/USD, OPEN/USD, ETH/USD, BTC/USD, BNB/USD,
WIF/USD, LINEA/USD, ICP/USD, BONK/USD, ONDO/USD, MIRA/USD
```

### 3.2 Asset Tiers

| Tier | Assets | Max Position Size | Notes |
|---|---|---|---|
| Tier 1 — Majors | BTC, ETH, BNB, LTC, ADA, DOGE, TRX | 8% of NAV | Liquid, well-studied. DOGE capped at 5% due to meme-tier volatility |
| Tier 2 — Large-cap alts | LINK, DOT, NEAR, TON, SUI, APT, ARB, HBAR, ICP, SEI | 8% of NAV | Momentum-driven |
| Tier 3 — DeFi tokens | AAVE, UNI, CRV, PENDLE, ONDO, ENA, FET, CAKE, EIGEN | 6% of NAV | DeFi sentiment cycle exposure |
| Tier 4 — Meme coins | SHIB, PEPE, FLOKI, WIF, BONK, 1000CHEEMS, PUMP, PENGU | 3% of NAV | Separate ranking pool |
| Tier 5 — Obscure/low-cap | SOMI, AVNT, ASTER, MIRA, EDEN, FORM, LINEA, LISTA, OPEN, BMT, OMNI, STO, WLD, POL, FIL, ZEN, CFX, ZEC, S | 2% of NAV | May have momentum on mock exchange |
| Special — PAXG | PAXG/USD | 15% of NAV | Gold-backed, near-zero BTC beta — Treynor instrument |
| Special — TRUMP | TRUMP/USD | 2% of NAV | Political event risk. 10% trailing stop. |

### 3.3 Special Asset Rules

- **PAXG/USD**: Treynor instrument. Sized from the cash buffer, does not compete with crypto signal allocations. Baseline 5%, scales to 15% in defensive regimes.
- **TRUMP/USD**: Hard cap 2% NAV. 10% trailing stop (wider than standard). Not included in meme pool ranking during TREND_BEAR or CRISIS. ~~Overnight close rule removed — crypto markets are 24/7, there is no "overnight." The trailing stop provides continuous protection.~~
- **DOGE/USD**: Classified as Tier 1 only (not in meme pool). Capped at 5% NAV despite Tier 1 standard being 8%.
- **Meme coins as a pool**: Ranked against each other only. Top 1–2 by momentum score get 3% allocation each. Active only in TREND_BULL regime.

---

## 4. Scoring Objective

### 4.1 Dual Objective

The competition has two selection gates:

1. **Gate 1 — Leaderboard position (absolute returns)**: Top 20 from each city advance to deeper evaluation. The bot must generate sufficient absolute returns to clear this screen.
2. **Gate 2 — Risk-adjusted composite + code review**: Top 8 selected from the top 20 based on Sharpe, Sortino, Calmar, Treynor ratios and code/strategy quality.

The architecture must serve **both** objectives: enough return to make the cut, with enough risk discipline to score well on the composite.

### 4.2 Scoring Uncertainty (Resolve Monday)

Edward mentions **three** ratios in the meeting ("TINA ratio, Sharpe ratio, Karma ratio" = Treynor, Sharpe, Calmar). The meeting summary lists **four** (adding Sortino). The architecture designs around four — this is the superset and costs nothing extra. When the problem statement arrives, confirm:
1. Which ratios are scored? Three or four?
2. Are they weighted equally or differently?
3. Is the composite a simple average, geometric mean, or something else?

If Sortino is not scored, the dynamic stop tightening mechanism (Section 11.2) still has value via Sharpe alone, but its relative priority drops.

### 4.3 Ratio Definitions

| Ratio | Formula | What It Rewards | What It Penalizes |
|---|---|---|---|
| Sharpe | `(Return − Rf) / Total Volatility` | Smooth, consistent returns | All return volatility |
| Sortino | `(Return − Rf) / Downside Volatility` | Positive return skew | Downside moves only |
| Calmar | `Annualized Return / Maximum Drawdown` | No large peak-to-trough loss | Single worst drawdown |
| Treynor | `(Return − Rf) / Portfolio Beta` | Market-independent returns | High benchmark beta |

**Note:** The Treynor benchmark (BTC? market index?) must be confirmed from the problem statement. Until confirmed, compute beta against both BTC and equal-weighted universe index (two benchmarks sufficient — a volume-weighted third adds minimal value). Make benchmark a config parameter.

### 4.4 Composite Optimization Implications

- **Low total volatility** (Sharpe) → diversify, avoid concentration, smooth daily returns
- **Positive skew** (Sortino) → let winners run, cut losers hard and fast
- **No large drawdown** (Calmar) → conservative sizing, hard stops, cash rotation in downturns
- **Low benchmark beta** (Treynor) → beta-neutralization via cash/PAXG, not shorting

### 4.5 Consistency Requirement

All four ratios are computed over the entire 10-day period. A strategy that earns 0% for 8 days then 10% in 2 days scores **worse on Sharpe** than one earning 0.14% per day consistently. Daily return consistency is as important as total return magnitude. The architecture must explicitly manage daily return distribution, not just total return.

---

## 5. System Architecture Overview

APEX runs as a **single Python process** with an async event loop. No external databases, no monitoring servers, no ML tracking platforms.

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: Data Ingestion                             │
│  Batch price call → in-memory ring buffers           │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 2: Feature Engineering                        │
│  Price-derived features, cross-sectional ranks       │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 3: Regime Detection                           │
│  Rule-based: BTC trend + vol percentile + breadth    │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 4: Signal Generation                          │
│  Cross-sectional momentum + trend exit filter        │
│  (Phase 3: ML directional overlay)                   │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 5: Portfolio Construction                     │
│  Regime-conditional sizing, vol-adjusted weights     │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 6: Risk Management                            │
│  Trailing stops, circuit breakers, daily P&L gov     │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 7: Execution Engine                           │
│  Limit orders at current price, priority queue       │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 8: Monitoring & Adaptation                    │
│  Rolling IC tracking, simple signal health checks    │
│  (Phase 3: ML retrain loop)                          │
└─────────────────────────────────────────────────────┘
```

---

## 6. Layer 1 — Data Ingestion

### 6.1 API Testing — Gated Prerequisite

**API testing is the single prerequisite that gates all other work.** No architecture-dependent code should be written until these answers are confirmed. With testing API keys, run the following before anything else:

| # | Test | Time | What It Determines |
|---|---|---|---|
| 1 | Call exchange info + ticker endpoints with no currency filter | 15 min | Batch pricing feasibility. Data fields available (OHLCV vs. last price). Actual asset count (56 or 66?). |
| 2 | Place a limit buy at exact current price | 10 min | Whether limit-at-current-price fills immediately (simplifies execution engine dramatically) |
| 3 | Fire 60 rapid API calls in 60 seconds | 10 min | Actual rate limit (30 or 60 calls/min) |
| 4 | Document findings, update `config.yaml` | 15 min | Lock architecture parameters |

If batch pricing doesn't work, the cross-sectional breadth of the momentum signal drops from 56 assets to ~20 (whatever can be priced within rate limits). This is a fundamental signal change, not a minor adjustment.

### 6.2 API Management

- API rate limit: 30–60 calls per minute (must verify empirically)
- **Batch endpoint**: attempt to fetch all prices in a single API call (exchange info endpoint with no currency filter). If this doesn't work, fetch only actively held + candidate assets (~20-25 per call)
- **Universe discovery**: on first API call, fetch exchange info with no filter and count returned assets. If more than 56, identify extras, classify into tiers, and add to the signal universe
- Remaining API budget allocated to: order placement, balance checks, order status

### 6.3 Data Collected

| Data Type | Frequency | Purpose |
|---|---|---|
| Prices (all assets) | Every 1 minute via batch call | Price features, signal computation |
| Portfolio balance | Every 5 minutes + after each trade | Position tracking, NAV computation |

**Removed from v2.0:**
- ~~Funding rates~~ — do not exist on a spot mock exchange
- ~~Order book data~~ — Roostoo API does not expose order book
- ~~External sentiment index~~ — adds complexity and an external dependency; defer to Phase 4

### 6.4 Data Storage

All storage is in-process. No external databases.

| Store | Implementation | Size Estimate |
|---|---|---|
| Price ring buffer | `collections.deque` per asset, max 1440 entries (24h of 1-min bars) | 56 assets × 1440 bars × ~40 bytes ≈ 3.2 MB |
| Feature arrays | NumPy arrays, rolling windows | ~50 features × 56 assets × 1440 bars × 8 bytes ≈ 32 MB |
| Trade log | Append-only JSON lines file on disk | Negligible |
| Historical prices (pre-loaded) | Parquet file on disk, loaded into memory as needed for ML training | ~100 MB (if ML enabled) |

**Total estimated RAM for data: ~40 MB** (without ML historical data)

### 6.5 Data Quality

- Detect and handle missing bars: fill forward for up to 3 consecutive missing bars; flag if longer gap
- Detect price anomalies: single-bar return exceeding 5 standard deviations → flag, do not trade that asset until next bar
- If batch API call fails: use last known prices, flag stale data, **do not execute new trades on stale data older than 5 minutes**
- Persist price data to disk (Parquet/CSV) every hour as crash-recovery backup

---

## 7. Layer 2 — Feature Engineering

### 7.1 Feature Categories

All features computed from price data only. Volume features included only if the API returns volume data (must verify).

**Return Features**
- Raw returns: 5m, 15m, 1h, 4h, 12h, 24h
- Return volatility: rolling standard deviation over 1h, 4h, 24h windows

**Momentum Features**
- Rate of change (ROC): 1h, 4h, 12h, 24h
- Moving average ratios: price / EMA(20), price / EMA(50), EMA(20) / EMA(50)
- Distance from rolling high/low: 24h

**Volume Features** (conditional on API providing volume data)
- Volume relative to rolling average: vol / MA_vol(24h)
- Price-volume correlation: 24-bar rolling

**Cross-Sectional Features** (computed across all assets at each timestamp)
- Cross-sectional return rank: percentile rank of 1h / 4h / 12h / 24h returns among all 56 assets
- Cross-sectional momentum score: composite rank across multiple horizons
- Within-tier rank: rank within asset tier (meme coins ranked against meme coins only)

**Regime-Input Features**
- BTC dominance proxy: BTC return vs equal-weighted altcoin return (1h, 4h)
- Altcoin breadth: percentage of assets with positive return in last 1h, 4h
- Volatility ratio: realized 1h vol / realized 24h vol (vol spike detector)

**Removed from v2.0:**
- ~~Log returns~~ — redundant with raw returns at these timescales
- ~~7-day rolling high/low~~ — requires 7 days of data before producing signal; 24h sufficient
- ~~Volume trend (slope)~~ — unstable, low signal value
- ~~Rolling z-score normalization~~ — cross-sectional percentile rank is more robust and sufficient

### 7.2 Feature Normalization

- Cross-sectional features: percentile rank (robust to outliers, no parameterization needed)
- Time-series features used directly in momentum composite (raw returns → rank). No z-score normalization needed for the ranking signal.
- If ML signal is enabled (Phase 3): rolling z-score normalization applied only to ML input features

### 7.3 Computation

- Features computed incrementally on each new bar using rolling window updates
- No full recomputation — O(1) update per bar per feature
- Feature computation must complete within **5 seconds** of bar close
- No separate "feature store" — features are computed inline and held in NumPy arrays

---

## 8. Layer 3 — Regime Detection

### 8.1 Regime States

Same four states as v2.0. The concept is correct — the implementation changes.

| State | Description | Typical Conditions |
|---|---|---|
| TREND_BULL | Broad uptrend, momentum working | BTC rising, breadth > 60%, vol stable/low |
| TREND_BEAR | Broad downtrend, avoid longs | BTC falling, breadth < 40%, vol rising |
| MEAN_REVERT | Choppy, range-bound | No clear trend, compressed ranges |
| HIGH_VOL_CRISIS | Acute stress, capital preservation | Vol spike, everything dropping together |

### 8.2 Regime Classification — Rule-Based

**Replaced HMM + GARCH with a deterministic rule-based classifier.** This eliminates two heavy libraries (`hmmlearn`, `arch`), removes the need for 90-day pre-training, and produces transparent, debuggable regime decisions.

**Inputs (all computed from price data in Layer 2):**

| Input | Computation | Updated |
|---|---|---|
| BTC trend | Sign of BTC 4h return + sign of BTC 24h return | Every bar |
| BTC volatility percentile | Current 1h realized vol rank vs. trailing 7-day distribution of 1h vol | Every 5 min |
| Altcoin breadth | % of universe with positive 4h return | Every bar |
| Contagion proxy | % of held positions with negative 5-min return × average loss magnitude | Every bar |

**Classification Rules:**

```
IF contagion_proxy > 0.80 AND avg_5min_loss > 1.0%:
    → HIGH_VOL_CRISIS (immediate, no transition delay)

ELSE IF btc_vol_percentile > 90th:
    → HIGH_VOL_CRISIS (immediate, no transition delay)

ELSE IF btc_4h_return > 0 AND btc_24h_return > 0 AND breadth > 55%:
    → TREND_BULL

ELSE IF btc_4h_return < 0 AND btc_24h_return < 0 AND breadth < 40%:
    → TREND_BEAR

ELSE:
    → MEAN_REVERT
```

### 8.3 Regime Transition Rules

- **Downgrade transitions** (any → CRISIS, BULL → BEAR): **immediate**. No delay. The cost of being slow to cut risk is permanent Calmar damage.
- **Upgrade transitions** (BEAR → MEAN_REVERT → BULL): gradual, over 2-3 rebalance cycles. Must persist for 30 minutes minimum before acting. The cost of being slow to add risk is a few basis points of missed upside.
- **CRISIS exit**: requires contagion proxy < 0.50 AND btc_vol_percentile < 70th for at least 30 minutes before exiting CRISIS state.

### 8.4 Removed from v2.0

- ~~Hidden Markov Model~~ — requires `hmmlearn`, 90-day pre-training, opaque state transitions. Rule-based system is transparent, debuggable, and has zero training requirements.
- ~~GARCH(1,1)~~ — requires `arch` library, model fitting on each update. Replaced with simple volatility percentile rank (same information, no model fitting).
- ~~DCC correlation monitoring~~ — O(n²) computation every 5 minutes. Replaced with O(n) contagion proxy.

---

## 9. Layer 4 — Signal Generation

### 9.1 Signal 1 — Cross-Sectional Momentum (Primary Signal)

**Description**: Rank all Tier 1–3 assets by composite recent return. Go long the top-ranked assets.

**Specification:**
- Composite score = weighted average of return ranks across: 1h (20%), 4h (40%), 12h (25%), 24h (15%)
- Rebalance frequency: every 60 minutes (start conservative; can tune to 30 min if signal decay justifies it)
- Target holdings: top 8–12 assets from Tier 1–3
- Regime conditioning:
  - TREND_BULL: hold top 10 assets
  - TREND_BEAR: hold only top 3–4 assets, rest in cash
  - MEAN_REVERT: hold top 5–6 assets (momentum partially reliable)
  - HIGH_VOL_CRISIS: exit all to cash

**Meme Coin Sub-Pool:**
- Tier 4 meme coins (excluding DOGE) ranked against each other only
- Top 1–2 meme coins by momentum score receive 3% allocation each
- Active only in TREND_BULL regime

**Tier 5 Opportunistic Sub-Pool (Phase 2+):**
- Tier 5 obscure/low-cap assets ranked separately (same pattern as meme pool)
- Activation threshold: only assets with 4h return > 10% AND in top 3 of Tier 5 ranking are eligible
- Top 1–2 qualifying Tier 5 assets receive 1–2% allocation each (below the 2% tier cap)
- Active only in TREND_BULL regime
- 8% trailing stop (same as meme coins)
- Max downside per position: 2% × 8% = 0.16% of NAV. Convex optionality: a 100% pump on a 2% position adds 2% to NAV
- Without this, 19 assets (34% of universe by count) receive zero allocation under any regime — potential edge left on the table

### 9.2 Signal 2 — Trend Following (Momentum Score Penalty)

**Description**: EMA crossover that penalizes the momentum score of deteriorating assets. Does not force exits independently — works through the ranking system.

**Specification:**
- Fast EMA: 60-period (60 minutes on 1-min bars)
- Slow EMA: 240-period (4 hours on 1-min bars)
- When EMA(60) < EMA(240) for an asset: apply a **penalty to its momentum composite score** (configurable, default: -0.3 in cross-sectional rank standard deviations)
- Effect: assets with strong momentum survive the penalty and remain in the portfolio (at potentially reduced rank). Assets with weak momentum + negative trend get pushed below the top-N threshold and are naturally rotated out at the next rebalance.

**Why penalty instead of exit override:**
- Strong momentum + weak trend = penalty reduces rank but doesn't force-exit. Saves commission, keeps partial exposure to potential trend recovery.
- Weak momentum + weak trend = penalty pushes asset below top-N threshold → natural exit through the ranking system (same outcome as an override, but smoother).
- Trailing stops at 6% are the backstop for genuine reversals. The trend penalty handles the gray zone between "slight deterioration" and "stop triggered."
- Fewer forced exits → lower commission drag → better Sharpe. More consistent positioning → lower turnover volatility → better Sortino.

**Changed from v3.0:** Was an exit override (force-exit regardless of momentum rank). Changed to penalty approach because the override caused asymmetric damage: a top-ranked momentum asset locked out for hours after a false EMA crossover, missing trend continuation, then re-entered with 0.1% round-trip cost.

**Changed from v2.0:** MA(10)/MA(50) changed to EMA(60)/EMA(240). SMA changed to EMA.

### 9.3 Signal 3 — ML Directional Overlay (Phase 3 — Deferred)

**Deferred to Phase 3 of delivery plan.** Not in the initial deployment.

When enabled:
- LightGBM classifier predicting P(positive 4h return) per asset
- Acts as a **size multiplier** (0.5x to 1.3x) on existing momentum positions — not an entry generator
- Pre-trained before competition on 60+ days of historical data from Binance
- Retrained every 24h with no validation gate (IC monitoring in Layer 8 provides safety)
- Halted automatically if rolling 48h IC falls below 0.02

### 9.4 Signal Interaction Rules

```
For each asset:
  1. Compute raw momentum composite score (cross-sectional rank)
  2. IF EMA(60) < EMA(240): apply trend penalty to score (default: -0.3 sigma)
  3. IF Phase 3 ML enabled: score unchanged, but sizing gets ML multiplier later

Position Decision =
  IF regime == HIGH_VOL_CRISIS:
    → Exit all to cash

  ELSE IF asset in top-N by adjusted momentum score:
    → Target allocation (× ML multiplier if Phase 3 enabled)

  ELSE IF asset not in top-N:
    → No position (hold cash)
```

The trend filter no longer creates a separate decision branch — it modifies the ranking input. Assets with deteriorating trends naturally fall out of the top-N selection. Trailing stops remain the hard exit mechanism for genuine reversals.

---

## 10. Layer 5 — Portfolio Construction

### 10.1 Regime-Conditional Deployment Targets

| Regime | Target Crypto Exposure | Cash + PAXG | Notes |
|---|---|---|---|
| TREND_BULL, low vol | 75–85% | 15–25% | Maximum participation |
| TREND_BULL, rising vol | 60–70% | 30–40% | Keep winners, reduce size |
| MEAN_REVERT | 50–60% | 40–50% | Raised from v2.0 (was 40-50%) to ensure adequate absolute return |
| TREND_BEAR | 30–40% | 60–70% | Raised from v2.0 (was 20-30%) — must stay competitive on leaderboard |
| HIGH_VOL_CRISIS | 10–20% | 80–90% | Capital preservation mode |

**Changed from v2.0:** Exposure floors raised across MEAN_REVERT and TREND_BEAR to address the dual-objective problem.

### 10.1.1 Adaptive Exposure Adjustment (Phase 2+)

Static exposure floors alone cannot adapt to competition dynamics. Add an adaptive adjustment based on estimated relative performance:

```
# Track a naive benchmark: equal-weight 70% exposure to all assets in the universe
# Compute cumulative return of this benchmark vs. our portfolio
relative_gap = naive_benchmark_return - portfolio_return

IF relative_gap > 2.0% AND days_remaining > 5:
    # Falling behind. Raise exposure floor by 10% across all regimes.
    exposure_adjustment = +0.10
ELIF relative_gap > 4.0% AND days_remaining > 3:
    # Significantly behind. Raise floor by 20%.
    exposure_adjustment = +0.20
ELSE:
    exposure_adjustment = 0.0

# Apply: effective_target = base_regime_target + exposure_adjustment
# Cap at 90% total crypto exposure (hard limit)
```

**Why this works:** The adjustment only triggers when the current defensive posture is already failing to keep us competitive. If we're ahead (relative_gap negative), it doesn't trigger — the self-correcting mechanism preserves risk-adjusted metrics when they're already working. Cost: ~10 lines of code. Requires tracking a naive benchmark return (trivial: equal-weight average of all asset returns).

### 10.2 Position Sizing Rules

All sizing is **arithmetic** — no optimizer needed. No `cvxpy` or `PyPortfolioOpt`.

1. **Start with equal weight** among selected holdings within the regime deployment target
2. **Volatility adjustment**: scale each position inversely proportional to its realized 24h volatility. Normalize so total equals deployment target.
3. **Apply tier caps**: hard-cap each position at its tier maximum. Redistribute excess equally to uncapped positions.
4. **Apply ML multiplier** (Phase 3 only): multiply by ML confidence scalar (0.5x to 1.3x). Re-normalize to deployment target.

Example with 10 selected assets at 75% deployment target:
- Base weight: 7.5% each
- After vol adjustment: range from ~4% (high vol) to ~11% (low vol)
- After tier cap: any position exceeding tier max is capped, excess redistributed

### 10.3 PAXG Allocation Logic

PAXG reduces portfolio benchmark beta without sitting in zero-yield cash.

| Regime | PAXG Allocation | Source |
|---|---|---|
| TREND_BULL | 5% of NAV | From cash buffer |
| MEAN_REVERT | 10% of NAV | From cash buffer |
| TREND_BEAR | 12% of NAV | From cash buffer |
| HIGH_VOL_CRISIS | 15% of NAV | From cash buffer |

PAXG does not compete with crypto signal allocations. The Treynor benchmark must be confirmed — if it's not BTC-based, PAXG still helps by reducing total portfolio volatility (Sharpe benefit).

### 10.4 BTC Beta Awareness

- Compute portfolio beta to BTC: weighted sum of individual asset betas estimated via trailing 7-day rolling regression of each asset's returns on BTC returns
- Target portfolio beta: 0.4–0.6 in TREND_BULL, < 0.3 in other regimes
- If portfolio beta exceeds target by >0.1: reduce allocation to highest-beta assets proportionally at next rebalance
- This is a **soft adjustment**, not a hard constraint — it influences sizing, doesn't override signals
- Also compute portfolio beta against equal-weighted universe index as a hedge in case Treynor uses a different benchmark

### 10.5 Turnover Constraint

- Minimum trade size: 0.2% of current NAV ($2,000 at start). Smaller trades are suppressed.
- Maximum one-way turnover per rebalance: 25% of portfolio
- Exception: risk-management exits bypass both constraints

### 10.6 End-Game De-risking

| Time Remaining | Max Crypto Exposure Cap | Trailing Stop Modification |
|---|---|---|
| > 48 hours | Full regime-conditional target | Normal stop distances |
| 24–48 hours | Cap at 70% regardless of regime | Normal stop distances |
| 12–24 hours | Cap at 45% | Normal stop distances |
| 4–12 hours | Cap at 25% | Tighten all stops to 3% |
| 1–4 hours | Cap at 15% | Tighten all stops to 2% |
| < 15 minutes | Sell all remaining positions | — |

**Changed from v3.0:** Final hour no longer sells to 0% immediately. Instead, tighten trailing stops to 2% — this protects against a crash (any 2% drop triggers exit) while maintaining exposure to any last-hour rally. Only force-sell everything in the final 15 minutes. If the problem statement requires all positions closed at competition end, sell in final 15 min regardless.

**Changed from v2.0:** De-risking starts at T-48h instead of T-72h (gives 80% of competition at full deployment).

---

## 11. Layer 6 — Risk Management

### 11.1 Portfolio-Level Hard Limits

These cannot be overridden by any signal or parameter setting.

| Limit | Soft Warning | Hard Action |
|---|---|---|
| Portfolio drawdown from peak | 5% | At 8%: HALT all new entries, hold existing, no new buys |
| Daily portfolio loss | 3% | At 5%: reduce all positions to 50% target size |
| Total crypto exposure | 85% of NAV | At 90%: sell highest-beta assets to bring below cap |
| Single asset loss from entry | 4% | At 6%: close full position immediately |

### 11.2 Dynamic Trailing Stop Tightening (Replaces Daily P&L Governor)

Manages daily return distribution by tightening downside protection as gains accumulate, rather than cutting exposure. This preserves upside participation while protecting captured gains.

| Daily P&L (unrealized + realized) | Action |
|---|---|
| > +2.0% | Tighten all trailing stops by 40% (e.g., 6% → 3.6%) |
| +1.0% to +2.0% | Tighten all trailing stops by 20% (e.g., 6% → 4.8%) |
| -0.5% to +1.0% | Normal trailing stop distances |
| < -0.5% | Existing drawdown circuit breakers handle this |

**Why this replaces the exposure-cutting governor from v3.0:**
- **Stays fully exposed** → if a rally continues, you capture it (helps absolute returns for Gate 1)
- **Tighter stops** → any reversal exits positions faster, protecting the daily gain (helps Sharpe)
- **Preserves positive skew** → Sortino rewards large gains with small losses. Cutting exposure at +1.5% caps your upside, reducing skew. Tighter stops keep the upside while narrowing the downside.
- **No interaction with end-game de-risking** — stops are per-position, not portfolio-level

**Minimum stop distance floor: 2%.** Even with maximum tightening (40%), no trailing stop goes below 2%. This prevents noise-triggered exits from intrabar price fluctuations. Enforced as: `effective_stop = max(base_stop * tightening_factor, 0.02)`.

"Day" resets at 00:00 UTC.

### 11.3 Individual Position Trailing Stops

Every open position has a trailing stop that follows the price upward but does not follow it downward.

| Asset Type | Trailing Stop Distance |
|---|---|
| Tier 1–3 (Majors, Alts, DeFi) | 6% below post-entry peak |
| Tier 4 (Meme coins) | 8% below post-entry peak |
| TRUMP | 10% below post-entry peak |

- Checked on every new bar (every 1 minute)
- Cannot be overridden — executes regardless of signal state
- Implemented as: track `highest_price_since_entry` per position; if `current_price < highest_price * (1 - stop_distance)`, trigger exit
- **Minimum stop distance floor: 2%.** Dynamic tightening (Section 11.2) and end-game tightening (Section 10.6) can reduce stops, but never below 2%. This prevents noise-triggered exits.

### 11.4 Contagion Circuit Breaker

Simple O(n) check every 1 minute (replaces O(n²) correlation matrix from v2.0).

```
held_positions = all current positions
negative_count = count(positions where 5-min return < 0)
contagion_ratio = negative_count / len(held_positions)
avg_loss = mean(abs(5-min return) for positions where 5-min return < 0)

IF contagion_ratio > 0.80 AND avg_loss > 1.0%:
    → Enter HIGH_VOL_CRISIS immediately
    → Reduce all positions to 20% of current size
```

### 11.5 Drawdown Recovery Rules

After a hard limit is triggered:

- Post-8% drawdown HALT: no new buy orders for 2 hours minimum
- After 2-hour cooldown: resume at 50% of normal position sizes
- Resume full sizing only when portfolio recovers to within 4% of peak

### 11.6 TRUMP/USD Specific Rules

- Maximum position: 2% of NAV
- Trailing stop: 10% (wider than standard)
- Not included in meme pool ranking during TREND_BEAR or CRISIS
- ~~Overnight close rule removed~~ — crypto markets are 24/7. No "overnight" gap risk. Trailing stop provides continuous protection.

---

## 12. Layer 7 — Execution Engine

### 12.1 Order Type Policy

**All orders are limit orders.** Market orders are not used under any circumstance.

On this mock exchange, limit orders at the current price may fill immediately (must verify with test keys). If confirmed, this gives market-order execution speed at limit-order commission.

### 12.2 Limit Order Placement

- **All orders**: placed at last known price from the most recent batch price call
- If order is not filled within **1 minute** (one price update cycle): cancel and resubmit at updated price
- Maximum resubmission attempts: **3**
- After 3 failed fills on a **risk exit** (trailing stop, circuit breaker): escalate to market order. Risk exits override the limit-order policy.
- After 3 failed fills on a **normal trade**: abandon the trade. It wasn't meant to be.

**Changed from v2.0:** Timeout reduced from 5 minutes to 1 minute. Risk exit escalation after 1 failed attempt if loss is accelerating (>2% adverse move since stop trigger).

### 12.3 Minimum Trade Threshold

Before submitting any order:
- Is the target weight change > 0.2% of current NAV?
- If no: suppress the trade
- If yes: proceed

### 12.4 Order Queue Management

- Maximum simultaneous open limit orders: 15 (reduced from 20 for API rate budget)
- Priority order: **risk exits → position reductions → new entries → size adjustments**
- Risk exits are never delayed by a queue of new entries

---

## 13. Layer 8 — Monitoring & Adaptation

### 13.1 Signal Health Monitoring

Track whether the momentum signal is working by monitoring hit rate: what percentage of positions entered at rebalance T have positive returns by rebalance T+1.

| Metric | Window | Warning | Halt |
|---|---|---|---|
| Momentum hit rate | Rolling 24h | < 45% | < 35% for 12h |
| Average winner / average loser ratio | Rolling 24h | < 0.8 | < 0.5 for 12h |

When momentum signal is halted:
- Reduce total crypto exposure to 30% regardless of regime
- Hold only top 3 positions by current unrealized P&L
- Resume when hit rate recovers above 50% for 6 hours

### 13.2 Performance Logging

Every trade decision logged to a JSON lines file with:
- Timestamp (UTC)
- Asset
- Action (BUY/SELL/HOLD)
- Signal values: momentum rank, trend filter state, regime state
- Target weight vs. previous weight
- Order type, price, size
- Fill confirmation

This log serves double duty: debugging and competition code review compliance.

### 13.3 Hourly Snapshot

Every hour, log to a separate file:
- Current NAV and daily P&L
- Current regime state
- All positions with current weight, unrealized P&L, distance to trailing stop
- Portfolio beta estimate
- Signal health metrics

### 13.4 ML Adaptation (Phase 3 Only)

When ML signal is enabled:
- Retrain LightGBM every 24h on most recent data
- No paper-trading validation gate — IC monitoring provides safety
- **Post-retrain sanity check (5 minutes):** run the new model on the most recent 5 minutes of data. If the output distribution (mean, std, min, max of predicted probabilities) differs from the old model's distribution by more than 2 standard deviations, reject the retrain and keep the old model. This catches pathological retrains without a full validation gate.
- If 48h rolling IC < 0.02: halt ML signal, revert to pure momentum
- All model updates logged with timestamp and IC metrics

**Removed from v2.0:**
- ~~Bayesian HPO (Optuna)~~ — heavy library, compute-intensive, only 2-3 runs possible in 10 days. Manual parameter tuning via config commits is sufficient.
- ~~CUSUM change detection~~ — nice theoretically but adds implementation complexity for marginal benefit over simple rolling IC check.
- ~~48h paper-trading validation gate~~ — makes first adapted model deploy at T+72h (30% through competition). IC monitoring provides adequate safety.
- ~~Johansen cointegration screen~~ — stat arb isn't used in this architecture.
- ~~SHAP feature importance tracking~~ — adds `shap` dependency, compute cost, for a feature pruning benefit that matters over months, not 10 days.

---

## 14. Capital Allocation

### 14.1 Initial Allocation

Starting capital: $1,000,000 USD

| Component | Initial Allocation | Notes |
|---|---|---|
| Cross-sectional momentum (Tier 1–3) | $500,000 | Core signal, 50% |
| Meme coin sub-pool (Tier 4) | $60,000 | Separate ranking pool, 6% |
| PAXG allocation | $50,000 | Treynor instrument, 5% baseline |
| Cash (USD) | $390,000 | Expands/contracts with regime |

### 14.2 Capital Deployment by Regime

| Regime | Total Deployed | Cash + PAXG |
|---|---|---|
| TREND_BULL | $750,000 – $850,000 | $150,000 – $250,000 |
| MEAN_REVERT | $500,000 – $600,000 | $400,000 – $500,000 |
| TREND_BEAR | $300,000 – $400,000 | $600,000 – $700,000 |
| HIGH_VOL_CRISIS | $100,000 – $200,000 | $800,000 – $900,000 |

---

## 15. Operational Cadence

### 15.1 Decision Loop Timelines

| Loop | Frequency | Actions | Compute Cost |
|---|---|---|---|
| Data ingestion | Every 1 minute | Batch price call, ring buffer update | Negligible |
| Feature update | Every 1 minute | Incremental feature computation | <1 sec |
| Risk check | Every 1 minute | Mark-to-market, trailing stops, daily P&L governor, contagion proxy | <1 sec |
| Regime update | Every 5 minutes | Rule-based regime classification | <0.1 sec |
| Portfolio rebalance | Every 60 minutes | Momentum ranking, target weights, trade generation | <3 sec |
| Signal health check | Every 1 hour | Rolling hit rate, win/loss ratio | <1 sec |
| Hourly snapshot | Every 1 hour | Log portfolio state to file | <1 sec |
| ML inference | Every 60 minutes (Phase 3) | LightGBM predictions for held + candidate assets | <5 sec |
| ML retrain | Every 24 hours (Phase 3) | Walk-forward retrain | ~60 sec |
| End-game de-risk | T-48h, T-24h, T-12h, T-4h, T-1h | Step-down exposure caps | Negligible |

**Signal decay monitoring (for rebalance tuning):**
Track the correlation between momentum score at rebalance T and the 30-minute forward return. If this correlation is significantly higher at T+30min than at T+60min, the signal decays fast and 30-minute rebalancing is justified. Log this hourly — requires zero additional infrastructure, just logging momentum scores and returns. Use this data to decide whether to tighten the rebalance cadence from 60 to 30 minutes.

**Removed from v2.0:**
- ~~Correlation matrix update every 5 min~~ — replaced by O(n) contagion proxy in risk check
- ~~ML inference every 30 min~~ — aligned to 60-min rebalance cadence
- ~~Hyperparameter review every 6h~~ — removed (Optuna eliminated)
- ~~Pairs/cointegration screen daily~~ — removed (stat arb not used)

### 15.2 Minimum Activity Requirement

The bot must actively make trades on at least 8 of 10 days.

- The rebalance loop naturally generates trades when rankings change
- If no rankings change for 24h (very stable market): force a minimum rebalance of 1–2 small position adjustments
- All trading activity logged with timestamps for verification

---

## 16. Technology Stack

### 16.1 Core Libraries

Single Python process. No external services.

| Function | Library | Purpose | RAM Impact |
|---|---|---|---|
| HTTP client | `aiohttp` or `requests` | Roostoo API calls | Negligible |
| Async loop | `asyncio` | Event loop, timer scheduling | Negligible |
| Numerical | `numpy` | Feature computation, rolling windows | ~50 MB (feature arrays) |
| Data frames | `pandas` (minimal) | Historical data loading, Parquet I/O | ~100 MB peak during load |
| Logging | `logging` (stdlib) | Structured logging to file | Negligible |
| Config | `yaml` or `json` (stdlib) | Parameter configuration | Negligible |
| Persistence | `sqlite3` (stdlib) | Trade log, hourly snapshots (optional) | Negligible |
| ML (Phase 3) | `lightgbm`, `scikit-learn` | Directional model | ~200 MB during retrain |

**Total estimated RAM: ~200 MB baseline, ~400 MB peak during ML retrain (Phase 3)**

### 16.2 Removed from v2.0

| Removed | Reason |
|---|---|
| `Redis` | No IPC needed — single process, in-memory `deque` |
| `TimescaleDB` | No TSDB needed — `deque` + Parquet files |
| `Prometheus` + `Grafana` | No monitoring dashboard — log files sufficient |
| `MLflow` | No experiment tracking server — git commits track parameters |
| `Prefect` / `APScheduler` | No orchestration framework — `asyncio` timers |
| `DVC` | No model versioning — models are <10MB, fit in git |
| `SHAP` | Adds dependency + compute for marginal pruning benefit |
| `Optuna` | Bayesian HPO too heavy for t3.medium and too slow for 10-day window |
| `hmmlearn` | HMM replaced by rule-based regime detection |
| `arch` | GARCH replaced by simple volatility percentile |
| `cvxpy` / `PyPortfolioOpt` | Portfolio optimization replaced by arithmetic sizing |
| `Numba` | JIT compilation adds startup time and memory; NumPy vectorization sufficient |
| `ccxt` | Not needed — Roostoo has its own REST API, not a standard exchange |
| `websockets` | Roostoo API is REST-based, no WebSocket support indicated |
| `structlog` | stdlib `logging` is sufficient |

### 16.3 Infrastructure

- **Instance**: AWS EC2 t3.medium (2 vCPU, 4 GB RAM)
- **Region**: ap-southeast-2 (Sydney)
- **Runtime**: Python 3.11+
- **Deployment**: single Python script, launched via `nohup` or `tmux`/`screen` in Session Manager
- **Crash recovery**: on startup, reload last hourly snapshot + trade log to reconstruct state
- **Secrets**: API keys in environment variables, never in code/repo
- **Heartbeat**: write timestamp to a file every loop iteration. If timestamp is >3 minutes old, something has crashed.

---

## 17. Non-Functional Requirements

### 17.1 Reliability

- System must run continuously for 10 days with zero manual restarts
- Top-level try/except wrapping the main loop — catch all exceptions, log them, continue
- If an exception occurs during trade execution: abort that rebalance cycle, hold current positions, retry on next cycle
- API failures: exponential backoff (1s, 2s, 4s, max 30s). After 5 consecutive failures: enter safe mode (hold positions, no new trades) until API recovers.

### 17.2 Auditability

Every trade decision logged with:
- Timestamp, asset, action
- Signal values, regime state
- Target weight vs. previous weight
- Order details and fill confirmation

Log format: JSON lines (one JSON object per line). Human-readable for competition code review.

### 17.3 Configurability

All tunable parameters in a single `config.yaml` file:
- Regime thresholds (BTC trend, vol percentile, breadth, contagion)
- Momentum weights (1h/4h/12h/24h blend)
- Trend penalty magnitude (default: -0.3 sigma)
- Rebalance frequency
- Trailing stop distances per tier
- Minimum stop distance floor (default: 2%)
- Stop tightening thresholds and percentages
- Tier caps
- Deployment targets by regime
- Adaptive exposure adjustment thresholds
- End-game de-risk schedule
- Phase 1 vol guard threshold (BTC vol multiple)
- Treynor benchmark selection (BTC / equal-weight / configurable)

No hardcoded strategy parameters in core logic. Every parameter change is a committed config change.

---

## 18. Phased Delivery Plan

### Phase 0 — API Testing (Hours 0-2, before anything else)

**Everything is blocked on this.** See Section 6.1. Test batch pricing, limit order fill mechanics, rate limit, data fields. Document findings, update `config.yaml`. If batch pricing doesn't work, redesign the watchlist strategy before proceeding.

### Phase 1 — Minimum Viable Bot (Hours 2-10, must ship before competition starts)

**Components:**
- Layer 1: Data ingestion (batch price fetch, ring buffers)
- Layer 4: Cross-sectional momentum ranking (Tier 1-3)
- Layer 5: Equal-weight sizing with tier caps (no vol adjustment yet)
- Layer 6: Trailing stops + portfolio drawdown circuit breaker
- Layer 7: Limit order execution with priority queue
- **Minimal vol guard** (5 lines — prevents full deployment into a vol spike before Phase 2 regime detection is ready):
  ```python
  btc_24h_vol = rolling_std(btc_returns, window=1440)
  btc_30d_median_vol = precomputed_from_historical  # set in config.yaml
  if btc_24h_vol > 2.0 * btc_30d_median_vol:
      max_crypto_exposure = 0.40  # defensive mode
  else:
      max_crypto_exposure = 0.75  # normal mode
  ```
- Config: `config.yaml` with all parameters
- Crash recovery: hourly Parquet snapshot + JSON trade log

**What it does:** Fetches prices, ranks assets by momentum, buys the top 8-10, enforces hard risk limits, executes via limit orders. The vol guard prevents catastrophic deployment into a vol spike on day 1. This alone is a competitive strategy.

**What it skips:** Full regime detection, vol-adjusted sizing, PAXG logic, meme pool, stop tightening, ML signal, trend penalty.

### Phase 2 — Regime Awareness (Days 1-3 of competition)

**Adds:**
- Layer 3: Rule-based regime detection (replaces Phase 1 vol guard)
- Layer 5: Regime-conditional deployment targets
- Layer 5: Volatility-adjusted position sizing
- Layer 5: PAXG allocation logic
- Layer 6: Dynamic trailing stop tightening (daily P&L → stop distances)
- Layer 6: Contagion circuit breaker
- Layer 4: Meme coin sub-pool
- Layer 4: Trend penalty on momentum scores (EMA crossover)
- Layer 5: End-game de-risking schedule

**What it does:** The bot now adapts to market conditions, sizes positions by risk, holds PAXG for Treynor, and dynamically tightens stops to protect daily gains.

### Phase 3 — ML Enhancement + Extended Universe (Days 3-5, only if Phase 2 is stable)

**Adds:**
- Layer 2: Extended feature set for ML input
- Layer 4: LightGBM directional overlay (pre-trained, with post-retrain sanity check)
- Layer 8: IC monitoring + auto-halt
- Layer 5: ML score position multiplier
- Layer 5: BTC beta targeting
- Layer 4: Tier 5 opportunistic sub-pool (small positions in high-momentum obscure assets)
- Layer 5: Adaptive exposure adjustment (relative performance tracking)

**What it does:** ML signal enhances momentum sizing. Beta targeting improves Treynor. Tier 5 sub-pool captures convex optionality from obscure assets. Adaptive exposure prevents falling behind on absolute returns.

### Phase 4 — Optimization (Days 5+, only if Phase 3 is stable)

**Adds:**
- Manual parameter tuning based on live IC observation
- Possible rebalance frequency adjustment (60 min → 30 min if signal decay justifies)
- Strategy commits with rationale

**What it does:** Human-in-the-loop optimization of parameters, committed to repo.

---

## 19. Open Questions & Risks

### 19.1 Must-Answer Before Competition (Ordered by Priority)

**HIGHEST — gates all other work (test with API keys in Phase 0):**

| # | Question | Impact if Wrong | Test Procedure |
|---|---|---|---|
| 1 | Does the batch endpoint return all prices in one call? | If no: momentum signal breadth drops from 56 to ~20 assets. Fundamental signal change. | Call exchange info + ticker with no currency filter |
| 2 | Can a limit order at current price fill immediately? | If yes: execution engine simplifies 3x. If no: need full timeout/resubmission logic. | Place limit buy at exact current price, check fill within 1 sec |
| 3 | What data fields does the price endpoint return? | If no volume: ~25% of features eliminated | Print full response, check for OHLCV fields |
| 4 | Actual API rate limit: 30 or 60 calls/min? | At 30: can only execute ~6-8 trades per rebalance cycle | Fire 60 rapid calls in 60 seconds, count successes |

**MEDIUM — answer from problem statement (Monday):**

| # | Question | Impact if Wrong |
|---|---|---|
| 5 | What is the Treynor benchmark? | If not BTC: beta targeting needs recalibration (config change) |
| 6 | Which ratios are scored — three or four? Weighted how? | If only 3 (no Sortino): stop tightening mechanism priority drops |
| 7 | Does the leaderboard use mark-to-market NAV or realized P&L? | If mark-to-market: selling in final hour costs commission for no NAV benefit |
| 8 | Any rules about position closure at competition end? | Affects end-game de-risk logic |

**LOW — verify empirically:**

| # | Question | Impact if Wrong |
|---|---|---|
| 9 | What is the first currency in the list? (truncated as "I/USD") | Missing asset needs tier classification |
| 10 | Are there more than 56 assets on the platform? (meeting said 66) | Extra assets need classification and inclusion in universe |

### 19.2 Pre-Competition Checklist

- [ ] API connectivity verified with test keys
- [ ] Batch price endpoint behavior confirmed
- [ ] Limit order fill mechanics confirmed
- [ ] Rate limit empirically verified
- [ ] Historical price data downloaded (Binance) for backtesting momentum signal
- [ ] Phase 1 MVB fully tested on test API
- [ ] Config.yaml reviewed and committed
- [ ] AWS EC2 instance launched and bot deployed
- [ ] Crash recovery tested (kill process, restart, verify state reconstruction)
- [ ] API credentials in environment variables, not in code

### 19.3 Key Assumptions

- The mock exchange prices are streamed from Binance in real-time → historical Binance data is representative
- Cross-sectional momentum has positive IC at 60-minute horizons in crypto markets (well-supported by academic literature)
- PAXG/USD on the mock exchange accurately tracks gold price
- The API batch endpoint exists and is efficient (one call = all prices)

---

*Document version: 3.1*
*v3.1 changes: Incorporated Devil's Architect review. Trend filter → penalty. P&L governor → stop tightening. Tier 5 sub-pool. Adaptive exposure. Phase 0 API testing. Vol guard in Phase 1. TRUMP overnight rule removed. End-game stop tightening. ML retrain sanity check. 2% minimum stop floor.*
*v3.0 changes: Redesigned for t3.medium. Single-process Python. Rule-based regime. ML deferred. Phased delivery.*
