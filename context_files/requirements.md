# APEX — Autonomous Portfolio Execution Agent
## System Requirements Document v2.0

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
18. [Open Questions & Risks](#18-open-questions--risks)

---

## 1. Project Overview

**APEX** (Autonomous Portfolio Execution Agent) is a fully autonomous algorithmic trading bot designed for a 10-day cryptocurrency trading competition. It operates on a mock exchange with a starting capital of $1,000,000 USD across a universe of 56 cryptocurrency spot pairs.

The bot must autonomously manage a long-only portfolio with zero manual intervention, generating the highest composite risk-adjusted return as scored by four performance ratios: Sharpe, Sortino, Calmar, and Treynor.

### 1.1 Design Philosophy

| Principle | Description |
|---|---|
| Signal Over Story | Every alpha source must demonstrate statistically robust out-of-sample edge before deployment |
| Regime Awareness First | All signal weights are conditioned on the currently detected market regime |
| Sizing Is Alpha | Position sizing and rotation to cash are the primary risk management tools |
| AI Where It Compounds | ML is used for pattern mining and parameter adaptation — not for free-form trade generation |
| Limit Orders Always | Given guaranteed fills and lower fees, limit orders are used for 100% of executions |

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
| API rate limit | 30–60 calls per minute |

### 2.4 Cost Implications

- Limit orders are **strictly dominant** — they must be used for all executions
- Round-trip cost using limit orders = **0.1%** (0.05% entry + 0.05% exit)
- A trade is only worth executing if the expected signal return exceeds **0.1%** before the next rebalance
- Trades below a minimum portfolio weight delta threshold (suggested: **0.2% of NAV**) should be suppressed to avoid unnecessary commission drag

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

Assets are classified into tiers for differentiated position sizing and signal handling.

| Tier | Assets | Max Position Size | Notes |
|---|---|---|---|
| Tier 1 — Majors | BTC, ETH, BNB, LTC, ADA, DOGE, TRX | 8% of NAV | Liquid, well-studied |
| Tier 2 — Large-cap alts | LINK, DOT, NEAR, TON, SUI, APT, ARB, HBAR | 8% of NAV | ETH-correlated, momentum-driven |
| Tier 3 — DeFi tokens | AAVE, UNI, CRV, PENDLE, ONDO, ENA, FET, CAKE | 6% of NAV | DeFi sentiment cycle exposure |
| Tier 4 — Meme coins | SHIB, PEPE, FLOKI, TRUMP, WIF, BONK, 1000CHEEMS, PUMP, DOGE | 3% of NAV | High volatility, separate ranking pool |
| Tier 5 — Obscure/low-cap | SOMI, AVNT, ASTER, MIRA, EDEN, FORM, LINEA, LISTA, OPEN, BMT, OMNI, PUMP, others | 2% of NAV | May have strong momentum in mock exchange |
| Special | PAXG/USD | 15% of NAV | Gold-backed, near-zero BTC beta — Treynor instrument |

### 3.3 Special Asset Rules

- **PAXG/USD**: Treated as a low-beta store of value. Held to reduce portfolio BTC beta and improve Treynor ratio. Sized separately from crypto signals.
- **TRUMP/USD**: Political event risk. Hard cap at 2% of NAV. Long-only. Never included in short-side of any ranking. Monitor for event-driven spike risk.
- **Meme coins as a pool**: Ranked against each other only — not cross-ranked with Tier 1/2 assets. Top 1–2 meme coins by momentum score get a 3% allocation each.

---

## 4. Scoring Objective

The competition scores a **composite of four risk-adjusted ratios** calculated over the full 10-day period.

### 4.1 Ratio Definitions

| Ratio | Formula | What It Rewards | What It Penalizes |
|---|---|---|---|
| Sharpe Ratio | `(Return − Risk-Free Rate) / Total Volatility` | Smooth, consistent returns | All return volatility (up and down equally) |
| Sortino Ratio | `(Return − Risk-Free Rate) / Downside Volatility` | Positive return skew — upside moves | Downside moves only |
| Calmar Ratio | `Annualized Return / Maximum Drawdown` | No large peak-to-trough loss | Single worst drawdown over the period |
| Treynor Ratio | `(Return − Risk-Free Rate) / Portfolio Beta` | Returns generated with low market exposure | High BTC beta — being a closet index |

### 4.2 Composite Optimization Implications

Satisfying all four ratios simultaneously implies the following strategy profile:

- **Low total volatility** (Sharpe) → diversify, avoid concentration in single volatile assets
- **Positive skew** (Sortino) → let winners run, cut losers hard and fast
- **No large drawdown ever** (Calmar) → conservative sizing, hard stops, aggressive rotation to cash in downturns
- **Low BTC beta** (Treynor) → beta-neutralization via cash/PAXG holdings, not through shorting

### 4.3 Consistency Requirement

All four ratios are calculated over the entire 10-day period, not just final NAV. A strategy that earns 0% for 8 days then 10% in the final 2 days scores **worse on Sharpe** than one earning 0.14% per day consistently across all 10 days. Daily return consistency is as important as total return magnitude.

---

## 5. System Architecture Overview

APEX consists of eight functional layers executing in a continuous loop.

```
┌─────────────────────────────────────────────────────┐
│  Layer 1: Data Ingestion                             │
│  Batch price feed, OHLCV, funding rates              │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 2: Feature Engineering                        │
│  OHLCV-derived features, cross-sectional transforms  │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 3: Regime Detection                           │
│  HMM + GARCH vol regime → risk-on / risk-off state   │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 4: Signal Generation                          │
│  Momentum ranking, ML directional, trend following   │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 5: Portfolio Construction                     │
│  Regime-conditional weight optimizer                 │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 6: Risk Management                            │
│  Hard limits, trailing stops, drawdown circuit break │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 7: Execution Engine                           │
│  Limit orders only, minimum trade threshold filter   │
└────────────────────┬────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────┐
│  Layer 8: Feedback & Adaptation                      │
│  IC tracking, signal degradation detection, HPO      │
└─────────────────────────────────────────────────────┘
```

---

## 6. Layer 1 — Data Ingestion

### 6.1 API Management

- API rate limit: 30–60 calls per minute
- **Batch endpoint must be used** for all price data — a single call fetching all 56 asset prices simultaneously
- Single batch price call = 1 API call (not 56)
- Remaining API budget allocated to: funding rates, historical OHLCV for features, any sentiment data endpoints

### 6.2 Data Collected

| Data Type | Frequency | Purpose |
|---|---|---|
| OHLCV (all 56 assets) | Every 1 minute via batch call | Price features, signal computation |
| Funding rates | Every 8 hours | Carry signal, market sentiment proxy |
| Order book (BTC, ETH only) | Every 5 minutes | Macro market sentiment indicator |
| External sentiment index | Every 15–30 minutes | Regime input, risk-on/off signal |

### 6.3 Data Storage

- In-memory ring buffer: last 1,440 one-minute bars per asset (24 hours)
- Persistent store: full historical OHLCV for ML model training (minimum 90 days pre-competition)
- All data timestamped in UTC

### 6.4 Data Quality Requirements

- Detect and handle missing bars (fill forward for up to 3 consecutive missing bars; flag if longer gap)
- Detect price anomalies (single-bar return exceeding 5 standard deviations)
- System must degrade gracefully if batch API call fails — use last known prices, flag stale data, do not execute new trades on stale data older than 5 minutes

---

## 7. Layer 2 — Feature Engineering

### 7.1 Feature Categories

All features are computed from OHLCV data. No tick-level or order book features (API constraint).

**Return Features**
- Raw returns: 5m, 15m, 1h, 4h, 12h, 24h
- Log returns for all above windows
- Return volatility: rolling standard deviation over 1h, 4h, 24h windows

**Momentum Features**
- Rate of change (ROC): 1h, 4h, 12h, 24h
- Moving average ratios: price / MA(20), price / MA(50), MA(10) / MA(50)
- Distance from rolling high/low: 24h, 7d

**Volume Features**
- Volume relative to rolling average: vol / MA_vol(24h)
- Volume trend: slope of volume over 12-bar window
- Price-volume correlation: 24-bar rolling

**Cross-Sectional Features** (computed across all assets at each timestamp)
- Cross-sectional return rank: percentile rank of 1h / 4h / 24h returns among all 56 assets
- Cross-sectional momentum score: composite rank across multiple horizons
- Within-tier rank: rank within asset tier (meme coins ranked against meme coins only)

**Regime-Input Features**
- BTC dominance proxy: BTC price return vs equal-weighted altcoin return
- Altcoin breadth: percentage of assets up in last 1h, 4h
- Volatility ratio: realized 1h vol / realized 24h vol (vol spike detector)

### 7.2 Feature Normalization

- All time-series features normalized using rolling z-score with 100-period window
- Cross-sectional features normalized using percentile rank (robust to outliers)
- No forward-looking normalization — all normalization windows use only past data

### 7.3 Feature Store

- Features computed incrementally on each new bar — full recomputation not required
- Feature values stored alongside timestamps for ML model training and IC tracking
- Feature computation must complete within 15 seconds of bar close

---

## 8. Layer 3 — Regime Detection

Regime detection is the most critical layer. All signal weights are conditioned on regime state. Getting the regime wrong at a transition point is the primary risk.

### 8.1 Regime States

| State | Description | Typical Conditions |
|---|---|---|
| TREND_BULL | Broad uptrend, momentum working | BTC rising, altcoin breadth > 60%, vol stable |
| TREND_BEAR | Broad downtrend, avoid longs | BTC falling, altcoin breadth < 40%, vol rising |
| MEAN_REVERT | Choppy, oscillating | No clear directional trend, compressed ranges |
| HIGH_VOL_CRISIS | Acute stress, capital preservation mode | Vol spike, correlation compression, sharp drawdown |

### 8.2 Regime Classification

Two independent classifiers run in parallel. Disagreement between them triggers a conservative posture.

**Classifier 1: Hidden Markov Model (HMM)**
- 4-state HMM trained on BTC/ETH returns and volatility
- Outputs posterior probability distribution across all 4 states
- Updated every 5 minutes
- Does not make hard state assignments — probabilities are used directly in signal blending

**Classifier 2: Rule-Based Vol Regime**
- GARCH(1,1) fitted on BTC 1-hour returns
- Volatility percentile threshold: LOW (<30th pctl), MED (30–70th), HIGH (70–90th), SPIKE (>90th)
- Simpler and faster — acts as a safety check on the HMM

**Disagreement Handling**
- If HMM says TREND_BULL but vol regime says HIGH: blend toward defensive posture
- Hard rule: if vol regime = SPIKE, override HMM and enter HIGH_VOL_CRISIS regardless of HMM output

### 8.3 Regime Transition Rules

- Do not hard-switch regimes instantly — gradual weight rotation over 3–5 rebalance periods
- Prevents whipsaw from false regime signals at transition boundaries
- Minimum regime persistence: 30 minutes before acting on a new regime detection

### 8.4 Regime Latency Acknowledgment

Regime detection has inherent latency — a transition may take 30–60 minutes to be detected with high confidence. Signals must be designed to degrade gracefully during transition periods, not rely on precise regime labeling.

---

## 9. Layer 4 — Signal Generation

### 9.1 Signal 1 — Cross-Sectional Momentum (Primary Signal)

**Description**: Rank all Tier 1–3 assets by composite recent return score. Go long the top-ranked assets. Do not hold bottom-ranked assets (rotate to cash instead of shorting).

**Specification**:
- Composite score = weighted average of return ranks across 1h (20%), 4h (40%), 12h (25%), 24h (15%)
- Rebalance frequency: every 30–60 minutes (tunable parameter)
- Target holdings: top 8–12 assets from Tier 1–3
- Regime conditioning:
  - TREND_BULL: signal weight 50%, hold top 10 assets
  - TREND_BEAR: signal weight reduced to 20%, hold only top 3–4 assets, rest in cash
  - MEAN_REVERT: signal weight 20%, momentum unreliable
  - HIGH_VOL_CRISIS: signal weight 0%, exit all positions to cash

**Meme Coin Sub-Pool**:
- Meme coins (Tier 4) ranked separately against each other only
- Top 1–2 meme coins by momentum score receive 3% allocation each
- Meme coin sub-pool is active only in TREND_BULL regime

### 9.2 Signal 2 — ML Directional Signal

**Description**: LightGBM classifier predicting the probability of positive return over the next 1–4 hours for each asset.

**Specification**:
- Model: LightGBM gradient boosted trees
- Target variable: sign(return_4h) — binary classification
- Features: all features from Layer 2 Feature Store
- Training: walk-forward validation — train on 60 days, validate on 5 days, step forward by 5 days
- Retrain cadence: every 24 hours
- Output: probability of positive 4h return, calibrated via isotonic regression
- Usage: modifies position sizes within the momentum portfolio — increases size for assets with ML score > 0.65, reduces for assets with score < 0.45
- Signal halted automatically if: rolling 48h IC falls below 0.02

**Overfitting Controls**:
- Maximum tree depth: 6
- Minimum samples per leaf: 50
- Feature subsampling: 0.7 per tree
- SHAP feature importance tracked — features with near-zero importance removed from next retrain

### 9.3 Signal 3 — Trend Following (Exit Signal)

**Description**: Moving average crossover system that acts primarily as a systematic exit mechanism.

**Specification**:
- Fast MA: 10-period (10 minutes)
- Slow MA: 50-period (50 minutes)
- Buy trigger: fast MA crosses above slow MA AND asset is in top half of cross-sectional momentum ranking
- Exit trigger: fast MA crosses below slow MA — exit position regardless of momentum ranking
- Acts as a confirmation filter — does not generate new entries independently, only exits
- Prevents "riding losers down" when momentum ranking hasn't yet reacted to a reversal

### 9.4 Signal Interaction Rules

Signals interact as follows to produce a final position decision:

```
Position Decision =
  IF regime == HIGH_VOL_CRISIS:
    → Exit all to cash
  ELSE IF asset in top momentum ranking AND ML_score > 0.45 AND trend_filter == UP:
    → Full target allocation
  ELSE IF asset in top momentum ranking AND trend_filter == DOWN:
    → Exit position (trend exit overrides momentum)
  ELSE IF asset not in top momentum ranking:
    → No position (hold cash)
```

---

## 10. Layer 5 — Portfolio Construction

### 10.1 Regime-Conditional Deployment Targets

The overall crypto exposure level is the primary lever controlled by the portfolio optimizer.

| Regime | Target Crypto Exposure | Cash + PAXG | Notes |
|---|---|---|---|
| TREND_BULL, low vol | 75–85% | 15–25% | Maximum participation |
| TREND_BULL, rising vol | 55–65% | 35–45% | Keep winners, reduce size |
| MEAN_REVERT | 40–50% | 50–60% | Choppy — momentum signal unreliable |
| TREND_BEAR | 20–30% | 70–80% | Mostly cash |
| HIGH_VOL_CRISIS | 10–20% | 80–90% | Capital preservation mode |

### 10.2 Position Sizing Rules

- **Base sizing**: equal-weight among selected holdings as starting point
- **Volatility adjustment**: scale each position inversely proportional to its realized 24h volatility. Higher volatility assets get smaller positions to equalize risk contribution
- **ML score adjustment**: multiply base size by ML confidence scalar (range: 0.5x to 1.3x)
- **Hard caps by tier**:
  - Tier 1 majors: max 8% of NAV per asset
  - Tier 2 large-cap alts: max 8% of NAV
  - Tier 3 DeFi: max 6% of NAV
  - Tier 4 meme coins: max 3% of NAV
  - Tier 5 obscure: max 2% of NAV
  - PAXG: max 15% of NAV

### 10.3 PAXG Allocation Logic

PAXG is held as a Treynor optimization instrument — it reduces portfolio BTC beta without sitting in zero-yield cash.

- Baseline PAXG allocation: 5% of NAV
- Increase to 10–15% when: regime is MEAN_REVERT or TREND_BEAR
- Increase to 15% in HIGH_VOL_CRISIS
- PAXG allocation does not compete with crypto signal allocations — it comes from the cash buffer

### 10.4 BTC Beta Targeting

- Target portfolio beta to BTC: 0.3 to 0.6 in TREND_BULL regime
- Target portfolio beta to BTC: < 0.3 in all other regimes
- Beta is computed as: weighted sum of individual asset betas estimated via 30-day rolling regression of each asset's returns on BTC returns
- If computed portfolio beta exceeds target, reduce highest-beta assets proportionally

### 10.5 Turnover Constraint

- Minimum trade size: 0.2% of current NAV (suppress smaller rebalancing trades)
- Maximum one-way turnover per rebalance: 25% of portfolio (prevents over-trading)
- Exception: risk-management exits bypass turnover constraint

### 10.6 End-Game De-risking

As competition end approaches, systematically reduce risk:

| Time Remaining | Max Crypto Exposure Cap |
|---|---|
| > 3 days | Full regime-conditional target |
| 1–3 days | Cap at 60% regardless of regime |
| 12–24 hours | Cap at 40% |
| < 12 hours | Cap at 20% |
| < 2 hours | Target 100% cash/PAXG |

---

## 11. Layer 6 — Risk Management

### 11.1 Portfolio-Level Hard Limits

These cannot be overridden by any signal, ML model, or parameter setting.

| Limit | Soft Warning | Hard Action |
|---|---|---|
| Portfolio drawdown from peak | 5% | At 8%: HALT all new entries, hold existing, no new buys |
| Daily portfolio loss | 3% | At 5%: reduce all positions to 50% target size |
| Total crypto exposure | 85% of NAV | At 90%: sell highest-beta assets to bring below cap |
| Single asset loss from entry | 4% | At 6%: close full position immediately |

### 11.2 Individual Position Trailing Stops

Every open position has a trailing stop that follows the price upward but does not follow it downward.

- Trailing stop distance: 6% below the highest price reached since entry
- If price falls 6% from its post-entry peak, position is exited immediately via limit order
- Stop is calculated and checked on every new bar (every 1 minute)
- Stop cannot be overridden — it executes regardless of what momentum signal says

### 11.3 Correlation Contagion Circuit Breaker

Crypto markets can go from 0.3 average pairwise correlation to 0.9+ in under an hour during a liquidation cascade. This is the primary systemic risk.

- Monitor rolling 30-minute average pairwise correlation across all held positions
- At average pairwise correlation > 0.75: flag warning, reduce all positions by 20%
- At average pairwise correlation > 0.85: enter HIGH_VOL_CRISIS regime immediately, exit to 80% cash
- This is a real-time check run every 5 minutes

### 11.4 Drawdown Recovery Rules

After a hard limit is triggered:

- Post-8% drawdown HALT: no new buy orders for 2 hours minimum
- After 2-hour cooldown: resume at 50% of normal position sizes
- Resume full sizing only when portfolio recovers to within 4% of peak

### 11.5 TRUMP/USD Specific Rules

- Maximum position: 2% of NAV
- Never held overnight if position is more than 1% of NAV (due to political event risk in non-trading hours)
- Trailing stop distance: 10% (wider than other assets, accounting for extreme volatility)
- Not included in meme coin sub-pool ranking when portfolio is in TREND_BEAR or CRISIS regime

---

## 12. Layer 7 — Execution Engine

### 12.1 Order Type Policy

**All orders must be limit orders. Market orders are prohibited.**

Rationale: guaranteed fills on the mock exchange make limit orders strictly better — same fill certainty at half the commission. This is hardcoded as a system invariant and not a tuneable parameter.

### 12.2 Limit Order Placement

- Entry orders: placed at current mid-price (best bid + best ask) / 2 if available, otherwise at last trade price
- Exit orders: placed at last trade price
- If a limit order is not filled within 5 minutes, cancel and resubmit at updated price
- Maximum resubmission attempts: 3. After 3 failed fills, execute at market if the position must be exited for risk reasons (trailing stop, circuit breaker) — risk exits override the limit-order policy

### 12.3 Minimum Trade Threshold

Before submitting any order, check:
- Is the target weight change > 0.2% of current NAV?
- If no: suppress the trade — commission drag exceeds expected benefit
- If yes: proceed with order submission

### 12.4 Order Queue Management

- Maximum simultaneous open limit orders: 20
- Rebalance produces a trade list sorted by: risk-management exits first, then new entries, then size adjustments
- Execute in priority order to ensure risk exits are never delayed by a queue of new entries

---

## 13. Layer 8 — Feedback & Adaptation

### 13.1 Signal IC Monitoring

For each signal, track its rolling Information Coefficient (IC) — the correlation between the signal's predictions and actual subsequent returns.

| Signal | IC Monitoring Window | Halt Threshold | Recovery Threshold |
|---|---|---|---|
| Cross-sectional momentum | 48-hour rolling | IC < 0.01 | IC > 0.03 for 12h |
| ML directional | 48-hour rolling | IC < 0.02 | IC > 0.04 for 12h |
| Trend following | 48-hour rolling | IC < 0.005 | IC > 0.02 for 12h |

When a signal is halted, its weight is redistributed proportionally to the remaining active signals.

### 13.2 CUSUM Change Detection

Apply CUSUM (Cumulative Sum) change detection to each signal's IC time series. CUSUM detects structural breaks — when a signal stops working — faster than a simple rolling average.

- CUSUM threshold: tunable, default calibrated to 2.5 standard deviations
- Detection triggers signal weight reduction before full halt threshold is reached
- Acts as an early warning system

### 13.3 Hyperparameter Adaptation

Every 6 hours, run a Bayesian optimization pass over a limited parameter set.

**Tunable parameters**:
- Momentum lookback weights (distribution across 1h/4h/12h/24h)
- Rebalance frequency (15m / 30m / 60m)
- Regime transition sensitivity
- Trailing stop distance (within bounds: 4–10%)
- ML score threshold for position scaling

**Validation gate**:
- New parameters must show improvement on the most recent 24-hour out-of-sample window
- Changes that don't show improvement on recent data are rejected
- All parameter changes logged with timestamp and rationale in the repo

### 13.4 Strategy Update Protocol

Per competition rules, strategy updates are allowed but must be committed to the repo.

- Observe live performance for the first 48 hours
- Diagnose which signals have positive IC on this specific competition market
- Tune signal weights accordingly and commit changes
- All subsequent tuning follows the Bayesian optimization process above
- No undocumented manual overrides

---

## 14. Capital Allocation

### 14.1 Initial Allocation

Starting capital: $1,000,000 USD

| Component | Initial Allocation | Notes |
|---|---|---|
| Cross-sectional momentum (Tier 1–3 assets) | $500,000 | Core signal, 50% of capital |
| Meme coin sub-pool (Tier 4) | $60,000 | Separate ranking pool, 6% |
| ML directional overlay | Adjusts sizes within above | Not a separate sleeve — a multiplier |
| PAXG allocation | $50,000 | Treynor optimization, 5% baseline |
| Cash (USD) | $390,000 | Deployed as regime and signals dictate |

Note: "Cash" here is not a reserve — it is the uninvested portion that expands and contracts based on regime state. In TREND_BULL it may fall to $100k. In CRISIS it may rise to $800k.

### 14.2 Capital Deployment by Regime

| Regime | Total Deployed (approx.) | Cash + PAXG |
|---|---|---|
| TREND_BULL | $800,000 – $850,000 | $150,000 – $200,000 |
| TREND_BEAR | $250,000 – $300,000 | $700,000 – $750,000 |
| MEAN_REVERT | $450,000 – $500,000 | $500,000 – $550,000 |
| HIGH_VOL_CRISIS | $150,000 – $200,000 | $800,000 – $850,000 |

---

## 15. Operational Cadence

### 15.1 Decision Loop Timelines

| Loop | Frequency | Actions |
|---|---|---|
| Data ingestion | Every 1 minute | Batch price call, feature update, bar formation |
| Risk check | Every 1 minute | Mark-to-market, check trailing stops, drawdown check |
| Microstructure signal | Removed | Eliminated due to API constraints |
| Regime update | Every 5 minutes | HMM + vol regime re-evaluated |
| Correlation check | Every 5 minutes | Pairwise correlation matrix update, contagion detection |
| Portfolio rebalance | Every 30–60 minutes | Full ranking recompute, target weight optimization, trade generation |
| ML model inference | Every 30 minutes | Fresh predictions on current feature snapshot |
| Hyperparameter review | Every 6 hours | Bayesian optimizer pass, validation, potential update |
| ML model retrain | Every 24 hours | Walk-forward retrain on updated historical data |
| Pairs/cointegration screen | Daily | Johansen test on universe (for potential future stat arb additions) |
| End-game de-risk | T-72h, T-24h, T-12h, T-2h | Step-down in maximum crypto exposure cap |

### 15.2 Minimum Activity Requirement

The bot must actively make trades on at least 8 out of 10 days to satisfy competition rules.

- The rebalance loop naturally generates trades when signal rankings change
- If no ranking changes produce tradeable signals on a given day (e.g., very stable market), force a minimum rebalance of 1–2 small position adjustments to maintain activity
- Log all trading activity with timestamps for competition verification

---

## 16. Technology Stack

### 16.1 Core Libraries

| Layer | Library | Purpose |
|---|---|---|
| Data | `ccxt`, `websockets`, `aiohttp` | Exchange API connectivity |
| Storage | `Redis`, `TimescaleDB` | In-memory tick buffer, OHLCV persistence |
| Features | `NumPy`, `Numba`, `pandas` | Fast numerical feature computation |
| Regime | `hmmlearn`, `arch` | HMM, GARCH model fitting |
| Signal / ML | `LightGBM`, `scikit-learn` | ML directional model |
| Calibration | `scikit-learn` isotonic | Probability calibration |
| Portfolio | `cvxpy`, `PyPortfolioOpt` | Constrained weight optimization |
| Optimization | `Optuna` | Bayesian hyperparameter search |
| Explainability | `SHAP` | Feature importance for ML model |
| Change detection | Custom CUSUM implementation | Signal IC degradation detection |
| ML tracking | `MLflow` | Model versioning and experiment tracking |
| Orchestration | `Prefect` or `APScheduler` | Loop scheduling and task management |
| Monitoring | `Prometheus` + `Grafana` | Real-time performance dashboards |
| Logging | `structlog` | Structured JSON logging for all decisions |
| Version control | `Git` + `DVC` | Code and model artifact versioning |

### 16.2 Infrastructure Requirements

- Single server deployment (local machine or cloud VM acceptable)
- Minimum 8GB RAM for in-memory feature store and model inference
- Python 3.11+
- All secrets (API keys) stored in environment variables, never in repo
- Heartbeat monitoring: alert if main loop has not executed for > 3 minutes

---

## 17. Non-Functional Requirements

### 17.1 Reliability

- System must run continuously for 10 days with zero manual restarts
- All unhandled exceptions must be caught, logged, and trigger a safe-state fallback (hold current positions, do not execute new trades until restart)
- API failures must be handled gracefully — do not trade on stale data

### 17.2 Auditability

- Every trade decision must be logged with:
  - Timestamp
  - Asset
  - Signal values at time of decision
  - Regime state
  - Target weight vs previous weight
  - Order type, price, size
  - Fill confirmation
- Decision log must be human-readable for competition code review

### 17.3 Reproducibility

- All random seeds fixed for reproducibility
- Model training pipelines deterministic given same input data
- All parameter changes logged in Git with explanatory commit message

### 17.4 Configurability

- All tunable parameters exposed as a configuration file (YAML or JSON)
- No hardcoded strategy parameters in core logic files
- Configuration file version-controlled — every parameter change is a committed change

---

## 18. Open Questions & Risks

### 18.1 Known Unknowns

| Item | Risk Level | Mitigation |
|---|---|---|
| Mock exchange pricing for obscure Tier 5 assets | Medium | Monitor for anomalous prices; set price sanity checks |
| Exact composite scoring formula weighting | Medium | Optimize each ratio independently; no known weighting to exploit |
| API batch endpoint response format and latency | Low | Test thoroughly before competition start |
| HMM convergence stability on short 10-day live window | Medium | Pre-train HMM on 90-day historical data; use pre-trained priors |
| ML model performance if competition period is an unusual regime | High | ML signal weight starts lower; IC monitoring auto-reduces it further if underperforming |

### 18.2 Pre-Competition Checklist

- [ ] Batch API endpoint tested — confirm single call returns all 56 asset prices
- [ ] API rate limit empirically verified (30 calls/min vs 60)
- [ ] Historical OHLCV data downloaded for all 56 assets (minimum 90 days)
- [ ] ML model pre-trained and walk-forward validated before competition start
- [ ] HMM pre-trained on historical data
- [ ] All hard limit circuit breakers unit tested
- [ ] Trailing stop logic unit tested against simulated price series
- [ ] Full system integration test on paper trading for minimum 48 hours
- [ ] Monitoring dashboard live and verified
- [ ] Git repo initialized, all code committed, open-source license added
- [ ] API credentials stored in environment variables, not in code
- [ ] End-game de-risk time triggers tested

### 18.3 Key Assumptions

- The mock exchange mid-price for all assets is representative enough to generate meaningful signals
- 90 days of historical data captures at least one full bull/bear cycle for HMM training
- The competition period does not start immediately after a major market event (which would make all historical volatility models miscalibrated)
- PAXG/USD on the mock exchange accurately tracks gold price

---

*Document version: 2.0*
*Last updated: reflecting spot-only, no-shorting, no-leverage constraints*
*All architecture decisions grounded in first-principles analysis of the scoring objective and competition rules*