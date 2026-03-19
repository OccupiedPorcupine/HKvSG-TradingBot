  
**APEX**  
Autonomous Portfolio Execution Agent

Architecture Documentation v3.3

**Aligned to Official Problem Statement**

SG vs HK University Web3 Quant Trading Hackathon

18 Sections • 8 Architectural Layers • 3 Scored Ratios • 56+ Assets  
Scoring: 0.4 Sortino \+ 0.3 Sharpe \+ 0.3 Calmar (40%) | Code Review (60%)  
Round 1: Mar 21–31 | Repo Due: Mar 28 | Round 2: Apr 4–14  
*Every decision documented. Every rejection explained. Every correction traced.*

# **Table of Contents**

[**Table of Contents	2**](#heading=)

[**1\. Problem Statement Alignment — Critical Corrections	5**](#heading=)

[**1.1 Scoring Formula — Three Ratios, Not Four	5**](#heading=)

[**Architectural Consequences	5**](#heading=)

[**1.2 Evaluation Structure — Code Review Is 60%	5**](#heading=)

[**Architectural Consequences	6**](#heading=)

[**1.3 Timeline — Two Rounds, Not One	6**](#heading=)

[**1.4 Strategy Openness	7**](#heading=)

[**1.5 Constraints Confirmed	7**](#heading=)

[**2\. Executive Summary	8**](#heading=)

[**2.1 The Dual-Objective Problem	8**](#heading=)

[**2.2 The Optimization Hierarchy	8**](#heading=)

[**2.3 Design Principles	9**](#heading=)

[**3\. Constraints and Their Architectural Implications	10**](#heading=)

[**3.1 Trading Constraints	10**](#heading=)

[**3.2 Exchange Mechanics	10**](#heading=)

[**3.3 Cost Model	11**](#heading=)

[**4\. Infrastructure Architecture	12**](#heading=)

[**4.1 Hardware: AWS EC2	12**](#heading=)

[**4.2 Single-Process Architecture	12**](#heading=)

[**4.3 Memory Budget	14**](#heading=)

[**4.4 Crash Recovery	15**](#heading=)

[**Recovery Data Sources	15**](#heading=)

[**Recovery Procedure	15**](#heading=)

[**Heartbeat Monitor	15**](#heading=)

[**Round 2 Restart	15**](#heading=)

[**5\. Data Architecture (Layer 1\)	16**](#heading=)

[**5.1 Data Sources	16**](#heading=)

[**Data Sources Explicitly Excluded	16**](#heading=)

[**5.2 Ring Buffer Design	17**](#heading=)

[**Why collections.deque	17**](#heading=)

[**Why 1,440 Bars (24 Hours)	17**](#heading=)

[**5.3 Data Quality Controls	17**](#heading=)

[**Missing Bar Handling	17**](#heading=)

[**Price Anomaly Detection	17**](#heading=)

[**Stale Data Protection	17**](#heading=)

[**6\. Feature Architecture (Layer 2\)	19**](#heading=)

[**6.1 The Composite Momentum Score	19**](#heading=)

[**6.2 Feature Categories	19**](#heading=)

[**Return Features	19**](#heading=)

[**Momentum Features	19**](#heading=)

[**Cross-Sectional Features	20**](#heading=)

[**Regime-Input Features	20**](#heading=)

[**Features Explicitly Excluded	20**](#heading=)

[**6.3 Computation Architecture	20**](#heading=)

[**7\. Regime Detection Architecture (Layer 3\)	21**](#heading=)

[**7.1 Why Regime Detection Exists	21**](#heading=)

[**7.2 Four Regime States	21**](#heading=)

[**Why Four States (Not Two, Not Eight)	21**](#heading=)

[**7.3 Classification Rules	21**](#heading=)

[**Why Rule-Based (Not Statistical)	22**](#heading=)

[**7.4 Transition Asymmetry	22**](#heading=)

[**7.5 Contagion Proxy	23**](#heading=)

[**8\. Signal Architecture (Layer 4\)	24**](#heading=)

[**8.1 Signal 1: Cross-Sectional Momentum (Primary)	24**](#heading=)

[**Why Cross-Sectional Momentum	24**](#heading=)

[**Rebalance Frequency: 60 Minutes	24**](#heading=)

[**Turnover Buffer Zone (Hysteresis)	24**](#heading=)

[**Volatility Exclusion Filter (Phase 1\)	24**](#heading=)

[**8.2 Signal 2: Trend Penalty (EMA Crossover)	24**](#heading=)

[**Penalty Scaling for Broad Downturns	25**](#heading=)

[**8.3 Meme Coin Sub-Pool	25**](#heading=)

[**Why Separate Pool	25**](#heading=)

[**Why DOGE Is Excluded	25**](#heading=)

[**8.4 Tier 5 Opportunistic Sub-Pool (Phase 2+)	25**](#heading=)

[**8.5 Signal 3: ML Directional Overlay (Phase 3\)	26**](#heading=)

[**Why LightGBM	26**](#heading=)

[**Why Size Multiplier (Not Entry Generator)	26**](#heading=)

[**9\. Portfolio Construction Architecture (Layer 5\)	27**](#heading=)

[**9.1 Regime-Conditional Deployment	27**](#heading=)

[**9.2 PAXG Allocation (Reframed from v3.1)	27**](#heading=)

[**9.3 Volatility-Adjusted Position Sizing	28**](#heading=)

[**9.4 Turnover Constraint	28**](#heading=)

[**9.5 Adaptive Exposure Adjustment (Phase 2+)	28**](#heading=)

[**9.6 End-Game De-Risking	28**](#heading=)

[**10\. Risk Management Architecture (Layer 6\)	30**](#heading=)

[**10.1 Portfolio-Level Hard Limits	30**](#heading=)

[**10.2 Individual Trailing Stops	30**](#heading=)

[**10.3 Dynamic Trailing Stop Tightening	31**](#heading=)

[**Why This Replaces the Daily P\&L Governor	31**](#heading=)

[**10.4 TRUMP-Specific Rules	31**](#heading=)

[**11\. Execution Architecture (Layer 7\)	32**](#heading=)

[**11.1 Limit-Only Policy	32**](#heading=)

[**11.2 Order Queue	32**](#heading=)

[**11.3 Trade Logging (Screen 1 Compliance)	32**](#heading=)

[**12\. Monitoring & Adaptation Architecture (Layer 8\)	33**](#heading=)

[**12.1 Signal Health Monitoring	33**](#heading=)

[**Why Hit Rate Is Not Used	33**](#heading=)

[**On Halt: Total Liquidation	33**](#heading=)

[**12.2 Performance Logging	33**](#heading=)

[**12.3 Self-Monitoring (Sortino/Sharpe/Calmar Tracker)	33**](#heading=)

[**12.4 ML Adaptation (Phase 3\)	34**](#heading=)

[**IC Monitor	34**](#heading=)

[**Retrain Loop (Every 24h)	34**](#heading=)

[**13\. Scoring Optimization Map	35**](#heading=)

[**13.1 Sortino (0.4 weight — highest priority)	35**](#heading=)

[**13.2 Sharpe (0.3 weight)	35**](#heading=)

[**13.3 Calmar (0.3 weight)	35**](#heading=)

[**13.4 Screen 4: Code & Strategy Review (60%)	36**](#heading=)

[**14\. Asset Universe & Tier Architecture	37**](#heading=)

[**15\. Technology Stack Decisions	38**](#heading=)

[**15.1 Explicitly Excluded (with Reasoning)	38**](#heading=)

[**16\. Phased Delivery Plan (Competition Timeline)	39**](#heading=)

[**17\. Open Questions & Risks	40**](#heading=)

[**17.1 Resolved by Problem Statement	40**](#heading=)

[**17.2 Must Verify in Phase 0	40**](#heading=)

[**17.3 Key Risks	40**](#heading=)

[**18\. Document Control	42**](#heading=)

# **1\. Problem Statement Alignment — Critical Corrections**

This section documents every point where the official problem statement (published March 18, 2026\) contradicts or clarifies assumptions in the APEX SRD v3.1. All downstream sections incorporate these corrections.

## **1.1 Scoring Formula — Three Ratios, Not Four**

The official composite score formula is:

**Composite \= 0.4 × Sortino \+ 0.3 × Sharpe \+ 0.3 × Calmar**

**Correction — Treynor removed:** The SRD v3.1 designed around four ratios (including Treynor) as a superset. The problem statement confirms Treynor is NOT scored. All Treynor-specific mechanisms are removed or reframed throughout this document.

### **Architectural Consequences**

* **PAXG as Treynor instrument: REMOVED.** PAXG no longer serves a beta-reduction purpose for scoring. PAXG is retained but reframed purely as a volatility reducer (Sharpe benefit) and a defensive asset in bear regimes. Its allocation logic is simplified.

* **BTC beta targeting: REMOVED from portfolio construction.** Portfolio beta to BTC is no longer a scoring input and does not influence weight allocation. A `beta_monitor.py` module exists in `src/portfolio/` for observability — it logs rolling BTC beta to the performance log but does not adjust position sizes or targets.

* **Treynor benchmark configuration: REMOVED.** The config.yaml parameter for Treynor benchmark selection is deleted.

* **Sortino priority ELEVATED.** At 0.4 weight (vs 0.3 for Sharpe and Calmar), Sortino is the single most important ratio. Downside volatility management is now the primary optimization target. This elevates the importance of: trailing stops, dynamic stop tightening, asymmetric regime transitions, and the trend penalty mechanism.

## **1.2 Evaluation Structure — Code Review Is 60%**

The evaluation for finalist selection follows four sequential screens:

| Screen | Criteria | Weight | Implication for APEX |
| :---- | :---- | :---- | :---- |
| Screen 1 | Rule compliance: trade log integrity, commit history transparency, no manual API calls\` | Pass/Fail | Failure \= disqualification. Every trade must be logged with timestamps. Git commits must be clean, traceable, and match deployed code. No manual API traces anywhere in repo. |
| Screen 2 | Portfolio return — top 20 per region on leaderboard | Qualification only | Must generate sufficient absolute return to reach top 20\. No additional weight beyond qualifying. This is a binary gate, not a gradient. |
| Screen 3 | Composite risk-adjusted score: 0.4 Sortino \+ 0.3 Sharpe \+ 0.3 Calmar | 40% of finalist evaluation | Risk-adjusted performance matters, but is less than half the final evaluation. |
| Screen 4 | Code & strategy review: strategy logic clarity (30%), code quality (20%), continuous runnability on Roostoo (10%) | 60% of finalist evaluation | Code quality and strategy clarity are worth more than actual trading performance. This is the dominant evaluation criterion. |

**Correction — Code quality underweighted in SRD:** The SRD treated code review as a secondary concern (mentioned only in the context of trade log compliance). The problem statement makes code & strategy review 60% of the evaluation — more than the composite trading score. This fundamentally changes architectural priorities.

### **Architectural Consequences**

* **Code structure is a first-class deliverable.** The project structure must be clean, modular, and well-documented. Each architectural layer maps to a clearly named module. No monolithic scripts.

* **Strategy logic must be self-documenting.** Variable names, function signatures, and inline comments must make the strategy readable without external documentation. Judges will read the code directly.

* **ReadMe is mandatory.** The open-source repo submission requires a ReadMe explaining the strategy, architecture, and how to run the bot. This is the first thing judges will read.

* **Continuous runnability (10%) means crash recovery matters for scoring.** The heartbeat monitor, hourly Parquet snapshots, and JSONL trade logs are not just operational — they demonstrate to judges that the bot can run continuously without manual intervention.

* **Git commit hygiene is a disqualification criterion.** Every strategy update must be committed with a clear message. No traces of manual API calls anywhere in the codebase or history.

* **A tests/ directory signals professional practice** even with minimal test coverage. It shows judges the team understands software engineering, not just quantitative finance.

## **1.3 Timeline — Two Rounds, Not One**

The competition has two trading rounds with a potential second deployment:

| Phase | Dates | Duration | Purpose |
| :---- | :---- | :---- | :---- |
| Preparation | Mar 16–20 | 5 days | Build bot, test deployment on Roostoo test environment |
| 1st Round (City Qualifiers) | Mar 21–31 | 10 days | Live trading. Top 20 by portfolio return qualify. Top 8 by composite \+ code review advance to finals. |
| Repo Submission | Before Mar 28 |  | Open-source repo with ReadMe due for code review judging. |
| 2nd Round (SG vs HK) | Apr 4–14 | 10 days | Top 8 from each city trade again. Same rules. Inter-city team collaboration allowed. |
| Final Presentation | Apr 17–21 |  | Top 8 demo to industry judges (IMC, Optiver, Cubist, Gondor Capital). Top 3 per city selected. |

**Correction — 10-day assumption:** The SRD correctly identifies 10-day trading rounds, but does not account for the 2nd round. The bot must be reusable — the end-game de-risking schedule, crash recovery, and state initialization must support a clean restart for Round 2 on April 4\.

**Critical deadline: Repo submission before March 28** — this falls on day 7 of the 1st round. The repo must be in a fully reviewable state (clean code, complete README, documented config) by this date, not at competition end.

## **1.4 Strategy Openness**

The problem statement explicitly permits any approach: LLM models, reinforcement learning (PPO agents), traditional quant strategies, or custom solutions. External data sources are permitted. Roostoo covers AWS costs; additional costs (e.g., LLM API calls) are not covered.

**Implication:** APEX’s ML overlay (Phase 3\) using LightGBM is permitted and encouraged. LLM-based sentiment analysis would incur costs. The architecture’s decision to defer external APIs remains correct.

## **1.5 Constraints Confirmed**

The following SRD v3.1 assumptions are confirmed by the problem statement:

* Spot only, no leverage, no short selling — confirmed

* No HFT, market-making, or arbitrage — confirmed. Excessive server requests result in failed API responses.

* $1,000,000 mock portfolio — confirmed

* 0.1% taker (market order) fee, 0.05% maker (limit order) fee — confirmed

* AWS EC2 instance provisioned via Roostoo sub-account — confirmed

* Open-source repo with ReadMe required for judging — confirmed

* 8 active trading days minimum per 10-day round — confirmed

* Strategy iteration and redeployment allowed during competition — confirmed

* Any strategy approach permitted (LLM, RL, traditional, custom) — confirmed

# **2\. Executive Summary**

APEX (Autonomous Portfolio Execution Agent) is a fully autonomous algorithmic trading system designed for the SG vs HK University Web3 Quant Trading Hackathon. It manages $1,000,000 USD across all available cryptocurrency spot pairs on the Roostoo mock exchange with zero manual intervention.

The system is evaluated across four sequential screens: rule compliance (pass/fail), portfolio return (top 20 qualification), composite risk-adjusted score (40% of finalist evaluation), and code & strategy review (60% of finalist evaluation). The architecture is designed to pass all four screens, with particular emphasis on the dominant criterion: code quality.

## **2.1 The Dual-Objective Problem**

The competition creates a tension between absolute return (needed for Screen 2 qualification) and risk-adjusted performance (needed for Screen 3 scoring). Additionally, it creates a meta-tension between trading performance (40%) and code quality (60%).

* **Screen 2** rewards aggression, concentration, and high-conviction bets — enough absolute return to reach top 20\.

* **Screen 3** rewards consistency, diversification, and downside discipline — smooth daily returns with minimal drawdown.

* **Screen 4** rewards clean architecture, readable code, and robust engineering — independent of trading performance.

**Reasoning:** Every architectural decision must serve all three objectives. The regime-conditional exposure system resolves the first tension: aggressive in favorable markets (Screen 2), defensive in hostile ones (Screen 3), with smooth transitions to avoid daily return discontinuities. The code-as-deliverable principle resolves the second tension: every module, function name, and comment is written for judge readability (Screen 4\) while being functionally correct for trading (Screens 2–3).

## **2.2 The Optimization Hierarchy**

Given the evaluation structure, priorities are ordered as follows:

1. **Code quality, strategy clarity, and continuous runnability (60%).** The bot must be readable, well-structured, and demonstrably autonomous. This is the dominant scoring criterion.

2. **Sortino ratio (0.4 × 40% \= 16% of total evaluation).** Minimize downside volatility. Protect against losses. Let winners run, cut losers fast.

3. **Sharpe ratio (0.3 × 40% \= 12% of total).** Smooth, consistent daily returns. Low total volatility.

4. **Calmar ratio (0.3 × 40% \= 12% of total).** Avoid large peak-to-trough drawdowns. One deep drawdown permanently damages the denominator.

5. **Absolute return (qualification only).** Generate enough return to reach top 20 on the leaderboard. No additional scoring weight beyond this threshold.

**Reasoning:** This hierarchy inverts the typical quant competition intuition. Most teams will optimize for trading performance (returns and ratios). APEX’s competitive advantage is recognizing that 60% of the score comes from code and strategy quality. A well-documented, clearly-architected bot with moderate trading performance will outscore a messy, undocumented bot with excellent trading performance.

## **2.3 Design Principles**

* **Ship First, Optimize Second.** A working bot on day 1 beats a perfect bot on day 5\. Phase 1 ships before live trading (Mar 21).

* **Signal Over Story.** Every alpha source must demonstrate edge in backtesting before deployment. No signal included because it sounds plausible.

* **Regime Awareness.** Signal weights, position sizes, and risk limits all conditioned on market state.

* **Sizing Is Alpha.** In long-only, no-leverage, position sizing and cash rotation are the primary risk tools.

* **Limit Orders Always.** 0.05% vs 0.1%. Guaranteed fills make limits strictly dominant. Halves commission drag.

* **Single Process.** One Python process, no external services. Maximizes reliability over 10 continuous days.

* **Code As Deliverable.** The codebase is 60% of the score. Every module and comment is written for judge readability.

# **3\. Constraints and Their Architectural Implications**

The competition imposes hard constraints that fundamentally shape the architecture. Each constraint eliminates strategies and privileges others.

## **3.1 Trading Constraints**

| Constraint | Specification | Architectural Implication |
| :---- | :---- | :---- |
| Spot only | No derivatives, perps, options | Cannot hedge. Risk managed via cash allocation and sizing only. |
| No leverage | 1x only | Cannot amplify returns. Alpha from selection and timing, not leverage. |
| No short selling | Long-only | Cannot profit from downturns. Only defense: reduce exposure to cash/PAXG. |
| No arbitrage | Single platform | No cross-exchange exploitation. Strategy must be purely directional. |
| No HFT/market-making | Excessive requests \= failed API | Cannot scalp or provide liquidity. Strategy must operate at 1-min+ horizons. |
| No manual trades | All bot-generated | System handles all edge cases autonomously. No human-in-the-loop. |
| 8/10 active days | Minimum trading requirement | Bot cannot shut down for extended periods. Must generate trades even in stable markets. |

**Reasoning:** The combination of long-only \+ no leverage \+ no derivatives constrains the strategy space to: buy things that go up, avoid things that go down, hold cash when uncertain. Cross-sectional momentum (buying recent winners) is the natural fit — it exploits the only available edge (directional long bets) with minimal complexity.

## **3.2 Exchange Mechanics**

| Parameter | Value | Why It Matters |
| :---- | :---- | :---- |
| Market order fee | 0.1% per trade | 0.2% round-trip. At 150 trades/day, this is \~1.5% daily drag. |
| Limit order fee | 0.05% per trade | 0.1% round-trip. Halves commission drag vs market orders. |
| Slippage | None | Orders fill at exact stated price. Eliminates real-world execution cost. |
| Market impact | None | Order size doesn’t move price. Large positions face no adverse selection. |
| Fill guarantee | 100% | Every order fills when conditions met. No partial fills, no rejections. |
| API rate limit | TBD (must verify) | Must verify empirically. At 30/min: only \~6–8 trades per rebalance. |

**Reasoning:** Zero slippage \+ zero market impact \+ guaranteed fills is dramatically simplified vs real markets. This means: (1) limit orders at current price are functionally equivalent to market orders at half the fee, making limit-only dominant; (2) position size is unconstrained by liquidity; (3) execution alpha (TWAP, iceberg orders) has zero value — the execution engine can be simple.

## **3.3 Cost Model**

The cost model determines minimum signal strength for a trade to be profitable:

* Round-trip cost using limit orders: 0.1% (0.05% entry \+ 0.05% exit)

* A trade is only profitable if expected return exceeds 0.1% before next rebalance

* At 60-minute rebalance, average selected asset must appreciate \>0.1% per hour net of losers

* Commission drag per day (estimated 100–150 trades at \~$75,000 avg): $7,500–$11,250 (0.75–1.13% of NAV)

**Reasoning:** This cost analysis drove two critical design decisions: (1) the turnover buffer zone (asset must drop below rank N+3 to be removed), reducing unnecessary round-trips by \~30–40%; (2) the minimum trade threshold of 0.2% of NAV ($2,000), suppressing micro-adjustments that cost more in commission than they add in signal value. Without these, backtesting showed commission drag exceeding 1.5% of NAV per day.

# **4\. Infrastructure Architecture**

## **4.1 Hardware: AWS EC2**

Each team receives an AWS sub-account to launch an EC2 instance. The architecture is designed for t3.medium (2 vCPU, 4 GB RAM) as the worst-case constraint.

| Resource | Available | Budget Allocation |
| :---- | :---- | :---- |
| vCPUs | 2 (burstable) | 1 for main event loop \+ features, 1 for async I/O |
| RAM | 4 GB | \~200 MB baseline, \~400 MB peak during ML retrain. 3.6 GB headroom. |
| Storage | EBS (default) | Parquet snapshots, trade logs, model artifacts. \<1 GB total. |
| Network | Moderate | API calls only. No streaming, no WebSocket. |

**Reasoning:** 2 vCPUs and 4 GB RAM rule out: real-time portfolio optimization (cvxpy), O(n²) correlation matrices on 56 assets every 5 minutes, Hidden Markov Models requiring matrix decomposition, and concurrent ML training \+ inference. The architecture uses incremental O(1) features, rule-based regime, arithmetic sizing, and deferred ML.

## **4.2 Single-Process Architecture**

APEX runs as one Python process with asyncio. No external services, databases, or monitoring platforms.

* **Data storage:** In-memory ring buffers (collections.deque) and NumPy arrays. No Redis, no TimescaleDB.

* **Task scheduling:** asyncio timers. No Prefect, no APScheduler, no cron.

* **Logging:** Python stdlib logging to JSON lines files. No Prometheus, no Grafana.

* **ML tracking:** Git commits. No MLflow, no Weights & Biases.

* **Configuration:** Single config.yaml file. No feature flags service.

**Rejected — Redis:** No IPC needed. Single process has no IPC requirements. Redis adds an external dependency, a failure mode, and \~50 MB of RAM for zero benefit.

**Rejected — TimescaleDB / PostgreSQL:** Price data fits in 3.2 MB of deques. The entire dataset is smaller than a single database connection’s memory overhead.

**Rejected — Prometheus \+ Grafana:** Competition runs 10 days with no audience for a dashboard. JSON log files provide same observability for code review.

**Rejected — MLflow:** Model artifacts \<10 MB. Git tracks parameters, code, and models. MLflow’s tracking server would consume more RAM than the model itself.

**Rejected — ccxt:** Roostoo has its own REST API. ccxt adds a large dependency that doesn’t support the target exchange.

**Reasoning:** Every external service is a failure mode. On constrained hardware running 10 continuous days, a single-process architecture has exactly one failure mode: the process itself. Crash recovery (reload from hourly Parquet \+ trade log) handles this in \<30 seconds. The simplicity maximizes the ‘continuous runnability’ criterion (10% of Screen 4).

## **4.3 Memory Budget**

| Component | Implementation | Estimated RAM |
| :---- | :---- | :---- |
| Price ring buffers | deque per asset, max 1440 entries | 56 × 1440 × \~40 bytes ≈ 3.2 MB |
| Feature arrays | NumPy, rolling windows | \~50 features × 56 assets × 1440 × 8 bytes ≈ 32 MB |
| Position tracker | Python dicts | \< 1 MB |
| Python runtime \+ libs | numpy, aiohttp, lightgbm | \~150 MB |
| ML model (Phase 3\) | LightGBM in-memory | \~50 MB inference, \~200 MB retrain peak |
| Historical data (Phase 3\) | Parquet loaded for retrain | \~100 MB peak, freed after retrain |
| Total baseline (Phase 1–2) |  | \~200 MB |
| Total peak (Phase 3 retrain) |  | \~400 MB |

**Reasoning:** At 400 MB peak, the system uses 10% of 4 GB available. 3.6 GB headroom is deliberate: a 10-day competition with no manual restarts cannot afford an OOM kill.

## **4.4 Crash Recovery**

The system must survive process crashes and resume without data loss or position desynchronization.

### **Recovery Data Sources**

* **Hourly Parquet snapshot:** Full state of all ring buffers, feature arrays, regime state, peak NAV, daily returns.

* **Trade log (JSONL):** Append-only record of every trade decision. Reconstructs position state.

* **Exchange balance API:** Authoritative source of current positions and cash balance.

### **Recovery Procedure**

1. On startup, check for most recent Parquet snapshot. Load to pre-populate ring buffers and features.

2. Read trade log from snapshot timestamp forward. Replay trade decisions to reconstruct internal state.

3. Call exchange balance API to get authoritative position state. Reconcile with reconstructed state.

4. If discrepancy: trust the exchange (source of truth). Log the discrepancy for review.

5. Resume main event loop. First rebalance naturally corrects any positioning drift.

**Reasoning:** The recovery design assumes the exchange is always right. Internal state is reconstructable from price data. Position state is reconstructable from the trade log. If they disagree with the exchange, the exchange wins. This prevents a crashed bot from trading on stale position data.

### **Heartbeat Monitor**

Every main loop iteration writes a UTC timestamp to a heartbeat file. If stale \>3 minutes, the process has likely hung. This demonstrates continuous runnability for Screen 4 judges.

### **Round 2 Restart**

On Round 2 initialization (Apr 4), the end-game schedule resets, ring buffers start fresh, and all state is re-initialized. The round start timestamp is a config.yaml parameter, allowing clean restart without code changes.

# **5\. Data Architecture (Layer 1\)**

## **5.1 Data Sources**

| Data Type | Source | Frequency | Purpose |
| :---- | :---- | :---- | :---- |
| Asset prices (all available) | Roostoo batch API | Every 1 minute | Features, signals, mark-to-market |
| Asset prices (fallback) | Binance spot REST API (`binance_client.py`) | On-demand | Secondary price source if Roostoo becomes unavailable. Same symbols, Binance naming convention. Not used in normal operation. |
| Portfolio balance | Roostoo balance API | Every 5 min \+ post-trade | Position tracking, NAV |
| Order status | Roostoo order API | After each placement | Fill confirmation, trade logging |

### **Data Sources Explicitly Excluded**

**Rejected — Funding rates:** Do not exist on a spot mock exchange. Funding rates are a perpetual futures mechanism.

**Rejected — Order book data:** Roostoo API does not expose order book depth. On a mock exchange with zero market impact, order book data has no information value.

**Rejected — External sentiment APIs:** Adds external dependency and failure mode. Sentiment data on crypto is noisy at sub-daily horizons. Deferred to Phase 4 (post-Round-1 analysis).

**Rejected — WebSocket streaming:** Roostoo API is REST-based. No WebSocket support indicated. 1-minute polling is sufficient for 60-minute rebalance.

**Reasoning:** The data architecture is deliberately minimal. Every additional data source adds: (1) an API call consuming rate limit, (2) a failure mode, (3) feature engineering complexity, (4) overfitting risk. Price-only features force signal robustness. Cross-sectional momentum on price data is one of the most documented anomalies in financial markets.

## **5.2 Ring Buffer Design**

Each asset maintains a ring buffer of the most recent 1,441 price bars (24 hours + 1 bar at 1-minute resolution). The extra bar ensures a full 1,440-bar window is always available after the oldest bar is consumed by a lookback computation.

### **Why collections.deque**

* O(1) append and O(1) left-pop when maxlen reached

* Fixed memory footprint (maxlen=1441 prevents unbounded growth)

* Built into Python stdlib (no dependency)

* Thread-safe for single-writer patterns (async loop is single-threaded)

**Rejected — pandas DataFrame:** Appending rows is O(n) due to reallocation. Over 10 days of 1-minute updates, this creates measurable GC pressure.

**Rejected — NumPy ring buffer:** Requires manual index management. Deque handles sliding window semantics natively.

### **Why 1,440 Bars (24 Hours)**

**Reasoning:** The longest feature lookback is 24 hours. Storing more wastes memory. The 7-day window for BTC volatility percentile is computed as a running statistic, not from raw bars. Historical data for ML training is stored on disk (Parquet), not in the ring buffer.

## **5.3 Data Quality Controls**

### **Missing Bar Handling**

Forward-fill for up to 3 consecutive missing bars. If gap exceeds 3: flag asset as stale, exclude from ranking.

**Reasoning:** A 3-bar (3-minute) gap is typical for API latency spikes. Forward-filling maintains feature continuity. Longer gaps suggest systemic issues — trading on stale data is more dangerous than missing one rebalance.

### **Price Anomaly Detection**

Single-bar return exceeding 5 standard deviations of trailing 1h returns: flag and exclude from next rebalance.

**Reasoning:** A 5-sigma 1-minute return on a mock exchange is almost certainly a data error. Excluding for one rebalance (60 min) lets data stabilize without committing capital to an erroneous signal.

### **Stale Data Protection**

If batch API fails: use last known prices for risk monitoring (stops still fire), but suppress new trade execution on data older than 5 minutes.

**Reasoning:** Risk monitoring must continue on stale data — a trailing stop should still fire. But entering new positions on stale data is dangerous because actual price may have moved significantly.

# **6\. Feature Architecture (Layer 2\)**

All features computed from price data only. This is a deliberate constraint, not a limitation.

## **6.1 The Composite Momentum Score**

This is the single most important signal. Everything else modifies it.

**Formula:** Composite \= 0.20 × rank(1h\_return) \+ 0.40 × rank(4h\_return) \+ 0.25 × rank(12h\_return) \+ 0.15 × rank(24h\_return)

Where rank() returns the percentile rank across all assets (0.0 \= worst, 1.0 \= best).

| Horizon | Weight | Reasoning |
| :---- | :---- | :---- |
| 1h | 20% | Captures recent breakouts. Too noisy to dominate, but detects fresh momentum. |
| 4h | 40% | The sweet spot. Highest IC in crypto momentum literature. Long enough to filter noise, short enough to react within a rebalance. |
| 12h | 25% | Confirms trend sustainability. Filters transient spikes. |
| 24h | 15% | Oldest signal. By 24h, most momentum has been captured. Low weight prevents stale signals. |

**Reasoning:** The 4h dominance was validated in backtesting (Step 2). The other horizons provide timing diversification — when 4h is flat, a fresh 1h breakout or sustained 12h trend can still differentiate assets.

## **6.2 Feature Categories**

### **Return Features**

Raw returns at 5m, 15m, 1h, 4h, 12h, 24h. Rolling std at 1h, 4h, 24h.

**Reasoning:** Multiple horizons capture momentum at different timescales. Rolling std feeds vol-adjusted sizing and regime detection.

### **Momentum Features**

ROC at 1h/4h/12h/24h. Price/EMA(20), Price/EMA(50), EMA(20)/EMA(50). Distance from 24h high/low.

**Reasoning:** ROC and MA ratios capture trend strength from different angles, making the composite rank more robust to individual feature noise.

### **Cross-Sectional Features**

Percentile rank of returns across full universe. Composite momentum score. Within-tier rank for meme coins.

**Reasoning:** Cross-sectional ranking is the core innovation. Absolute returns are meaningless for asset selection — a 2% return is exceptional in flat markets but mediocre in a rally. Ranking naturally adapts to conditions without parameter changes. More robust than z-score normalization, which assumes stationarity.

### **Regime-Input Features**

BTC dominance proxy (BTC return vs altcoin average, 1h/4h). Altcoin breadth (% positive, 1h/4h). Volatility ratio (1h vol / 24h vol).

**Reasoning:** These are not trading signals — they are regime classifier inputs. All O(n) to compute. No O(n²) correlation matrix needed.

### **Features Explicitly Excluded**

**Rejected — Log returns:** Redundant at these timescales. Log vs arithmetic diverge only for \>10% returns. Intraday crypto returns typically \<5%.

**Rejected — 7-day rolling high/low:** Requires 7 days before producing signal. Competition is 10 days. 24h high/low sufficient.

**Rejected — Volume trend (slope):** Tested and found unstable: high autocorrelation, low predictive power. Volume/MA(volume) captures same info.

**Rejected — Z-score normalization:** Cross-sectional percentile ranking is more robust and requires no parameter estimation.

## **6.3 Computation Architecture**

All features computed incrementally using O(1) updates per bar per feature. No full recomputation.

* **EMA:** Recursive: ema\_new \= alpha × price \+ (1-alpha) × ema\_old. One multiply \+ one add per update.

* **Rolling mean/std:** Welford’s online algorithm for numerical stability.

* **Cross-sectional rank:** numpy.argsort on 56-element vector. O(n log n) where n=56, effectively O(1).

* **Rolling high/low:** Deque-based sliding window max/min.

**Reasoning:** Incremental updates: 50 × 56 \= 2,800 operations/minute. Full recomputation: 50 × 56 × 1,440 ≈ 4M operations/minute. Both acceptable, but incremental leaves more CPU for async I/O.

# **7\. Regime Detection Architecture (Layer 3\)**

## **7.1 Why Regime Detection Exists**

**Reasoning:** A momentum strategy without regime awareness deploys capital identically in bull and bear markets. In bear markets, the ‘top-ranked’ assets are simply falling the least. Buying them generates losses plus commission drag. Without regime awareness, the first significant drawdown permanently destroys the Calmar denominator (0.3 weight). Regime detection converts a dumb momentum signal into a risk-aware allocation system.

## **7.2 Four Regime States**

| State | Description | Conditions | Target Exposure | Holdings |
| :---- | :---- | :---- | :---- | :---- |
| TREND\_BULL | Broad uptrend, momentum working | BTC 4h \> 0, BTC 24h \> 0, breadth \> 55% | 75–85% | Top 10 |
| MEAN\_REVERT | Choppy, range-bound | No clear trend (default state) | 50–60% | Top 5–6 |
| TREND\_BEAR | Broad downtrend, avoid longs | BTC 4h \< 0, BTC 24h \< 0, breadth \< 40% | 30–40% | Top 3–4 |
| HIGH\_VOL\_CRISIS | Acute stress, preserve capital | Contagion \> 80% \+ avg loss \> 1%, or BTC vol \> 90th pctl | 10–20% | Exit all |

### **Why Four States (Not Two, Not Eight)**

**Reasoning:** Two states (bull/bear) miss the common middle ground where momentum partially works. Eight states would require more precise classification than available data supports. Four states map to four actionable deployment profiles: full participation, partial participation, reduced participation, and capital preservation.

## **7.3 Classification Rules**

IF contagion\_proxy \> 0.80 AND avg\_5min\_loss \> 1.0%:

    → HIGH\_VOL\_CRISIS (immediate)

ELSE IF btc\_vol\_percentile \> 90th:

    → HIGH\_VOL\_CRISIS (immediate)

ELSE IF btc\_4h \> 0 AND btc\_24h \> 0 AND breadth \> 55%:

    → TREND\_BULL

ELSE IF btc\_4h \< 0 AND btc\_24h \< 0 AND breadth \< 40%:

    → TREND\_BEAR

ELSE:

    → MEAN\_REVERT

### **Why Rule-Based (Not Statistical)**

**Rejected — Hidden Markov Model:** Requires hmmlearn, 90-day pre-training, opaque state transitions. Rule-based is transparent, debuggable, zero training.

**Rejected — GARCH(1,1):** Requires arch library, iterative model fitting. Replaced by simple volatility percentile rank.

**Rejected — DCC correlation matrix:** O(n²) every 5 minutes for n=56. Replaced by O(n) contagion proxy.

**Reasoning:** Rule-based is deliberately simple. A more sophisticated model could detect transitions earlier, but introduces: (1) training on data that may not match the competition environment, (2) opaque decisions hard to debug live, (3) overfitting risk. The rule-based system’s accuracy can be visually verified against a BTC price chart. For Screen 4 (strategy clarity, 30%), transparent rules are easier for judges to evaluate than a trained HMM.

## **7.4 Transition Asymmetry**

Downgrades are immediate. Upgrades require 30-minute persistence. CRISIS exit requires dual conditions for 30 minutes.

| Direction | Speed | Minimum Persistence | Reasoning |
| :---- | :---- | :---- | :---- |
| Downgrade (BULL→BEAR, any→CRISIS) | Immediate | None | The cost of slow risk-cutting is permanentho Calmar damage (deeper drawdown). |
| Upgrade (BEAR→MEAN\_REVERT→BULL) | Gradual | 30 minutes | The cost of slow risk-adding is a few basis points of missed upside. |
| CRISIS exit | Guarded | 30 min \+ dual conditions | False CRISIS exits are expensive (re-entry costs, whipsaw). |

**Reasoning:** Calmar (0.3 weight) is disproportionately sensitive to drawdowns. A single deep drawdown permanently damages the denominator. Missing a few hours of upside costs only a few basis points of the numerator. The asymmetric rule makes the bot fast to protect capital and cautious about redeploying it.

## **7.5 Contagion Proxy**

O(n) computation every 1 minute. Replaces O(n²) correlation matrix from v2.0.

negative\_count \= count(held positions where 5min return \< 0\)

contagion\_ratio \= negative\_count / total\_held\_positions

avg\_loss \= mean(abs(5min return) for negative positions)

\# Standard: trigger if ratio \> 0.80 AND loss \> 1.0%

\# Small portfolio (\<6 positions): ratio \> 0.90 AND loss \> 1.5%

**Reasoning:** With \<6 positions (typical in TREND\_BEAR), a single bad bar on 4/4 \= 100% contagion ratio. Raised thresholds for small portfolios prevent false CRISIS triggers during already-defensive regimes. The v2.0 DCC matrix was O(56²) \= 3,136 elements requiring eigenvalue decomposition. The contagion proxy captures the same actionable information (are many things dropping at once?) in O(56) time.

# **8\. Signal Architecture (Layer 4\)**

## **8.1 Signal 1: Cross-Sectional Momentum (Primary)**

Rank all Tier 1–3 assets by composite momentum score. Go long the top N assets (N is regime-dependent). This is the only entry generator. Everything else modifies it.

### **Why Cross-Sectional Momentum**

* **Empirical edge:** Well-documented anomaly in crypto at intermediate horizons.

* **Self-normalizing:** Ranking adapts to market conditions without parameter changes.

* **Compatible with constraints:** Long-only, no leverage, directional.

* **Low complexity:** Rank-and-select. No training, no optimization. No overfitting.

* **Code clarity for Screen 4:** The signal logic is 20–30 lines. Judges can understand it in 2 minutes.

### **Rebalance Frequency: 60 Minutes**

**Reasoning:** Too frequent (5–15 min): commission drag exceeds signal value; rankings are noisy. Too infrequent (4–24h): misses reversals; stale positions accumulate losses. 60 minutes is conservative for Phase 1\. Signal decay is measured live by tracking the correlation between momentum score at T and forward returns at T+30min vs T+60min. If 30-min correlation is significantly higher, rebalance cadence tightens in Phase 4\.

### **Turnover Buffer Zone (Hysteresis)**

An asset must drop below rank N+3 to be removed. An asset must enter the top N to be added.

**Reasoning:** Without hysteresis, an asset oscillating between rank 10 and 11 generates pure commission waste. The \+3 buffer reduces turnover by 30–40% in backtesting while sacrificing \<5 bps of signal quality. The asymmetry (enter at N, exit at N+3) reflects cost structure: entering costs commission (high bar), holding costs nothing (high bar for exit too).

### **Volatility Exclusion Filter (Phase 1\)**

Exclude any asset whose trailing 24h vol exceeds 2x the median of the candidate pool.

**Reasoning:** In an equal-weight portfolio, a single extremely volatile asset dominates portfolio variance. If SUI has 4x BTC’s volatility, even at equal weight it contributes 4x more to variance — directly damaging Sharpe (0.3 weight). The filter removes worst offenders. Phase 2 vol-adjusted sizing handles this more elegantly, but the filter remains as backstop.

## **8.2 Signal 2: Trend Penalty (EMA Crossover)**

When EMA(60) \< EMA(240): apply \-0.3 sigma penalty to composite momentum score. This is a penalty, not an exit override.

| Approach | Behavior | Problem |
| :---- | :---- | :---- |
| Exit override (v3.0) | Force-exit regardless of momentum rank | Top-ranked asset locked out for hours after false EMA crossover. Missed trend continuation. Paid 0.1% to re-enter. |
| Penalty (v3.1+) | Reduce rank; strong momentum survives, weak exits naturally | None. Strictly dominates override. |

**Reasoning:** The penalty resolves a binary decision (in/out) into a continuous one (how much does trend reduce conviction?). Momentum rank 0.95 with \-0.3 penalty \= \~0.85, likely still in top-N. Momentum rank 0.55 with penalty \= \~0.45, below threshold, naturally exits. Fewer forced exits \= lower commission \= better Sharpe. More consistent positioning \= better Sortino.

### **Penalty Scaling for Broad Downturns**

When \>70% of universe has EMA(60) \< EMA(240): scale penalty to \-0.15 sigma.

**Reasoning:** In broad downturns, most assets get penalized, compressing the ranking distribution. Top-N selection becomes nearly random. Reduced penalty preserves ranking discrimination.

## **8.3 Meme Coin Sub-Pool**

Tier 4 meme coins (SHIB, PEPE, FLOKI, WIF, BONK, PUMP, PENGU) — 7 assets — ranked against each other only. Top 1–2 by momentum get 3% allocation each. Active only in TREND\_BULL. 8% trailing stop.

**Correction from v3.1:** 1000CHEEMS removed from the meme pool. It is classified as Tier 5 (obscure/low-cap) in the live codebase and competes in that sub-pool instead.

### **Why Separate Pool**

**Reasoning:** Meme coins have fundamentally different return distributions. Ranking them against BTC would either always select meme coins (larger raw returns) or never select them (vol-adjusted). Separate pool captures convex optionality: max downside \= 3% × 8% \= 0.24% NAV. Max upside on 100% pump \= \+3% NAV.

### **Why DOGE Is Excluded**

**Reasoning:** DOGE has evolved into a large-cap asset with institutional dynamics closer to Tier 1 than Tier 4\. Including it would consistently dominate the meme pool (highest liquidity, lowest vol), crowding out smaller coins with better optionality. DOGE is Tier 1, capped at 5%.

## **8.4 Tier 5 Opportunistic Sub-Pool (Phase 2+)**

19 obscure/low-cap assets ranked separately. Only assets with 4h return \> 10% AND in top 3 of Tier 5 qualify. Top 1–2 get 1–2% each. TREND\_BULL only. 8% stop.

**Reasoning:** Without this pool, 19 assets (34% of universe) receive zero allocation — potential edge left on the table. Max downside: 2% × 8% \= 0.16% NAV. Max upside on 100% pump: \+2% NAV. Convex optionality by design.

## **8.5 Signal 3: ML Directional Overlay (Phase 3\)**

LightGBM classifier predicting P(positive 4h return). Sizing multiplier 0.5x–1.3x on existing positions. NOT an entry generator. Auto-halt if 12h rolling IC \< 0.02.

### **Why LightGBM**

* **Inference:** \<5ms per prediction. 56 assets in \~280ms. Negligible.

* **Memory:** \~50 MB inference. Within budget.

* **Training:** \~60 seconds retrain on 60-day data. Runs during 24h retrain window.

* **Features:** Handles missing values, non-linear relationships natively.

**Rejected — Neural networks (LSTM, Transformer):** Training exceeds 24h budget on 2 vCPU. Inference 10–100x slower. Requires PyTorch (\~500 MB). Higher overfitting risk.

**Rejected — XGBoost:** Similar to LightGBM but slower training and higher memory. LightGBM’s histogram splitting is more efficient.

### **Why Size Multiplier (Not Entry Generator)**

**Reasoning:** ML cannot generate entries on its own — only scale sizes of momentum-selected positions. This bounds the risk: worst case is a good position undersized (0.5x) or slightly oversized (1.3x). If ML generated entries, a misprediction could put capital into an asset momentum would never have selected.

# **9\. Portfolio Construction Architecture (Layer 5\)**

## **9.1 Regime-Conditional Deployment**

| Regime | Crypto Exposure | Cash \+ PAXG | Design Rationale |
| :---- | :---- | :---- | :---- |
| TREND\_BULL, low vol | 75–85% | 15–25% | Maximum participation. Screen 2 (absolute return) requires aggressive deployment in favorable markets. |
| TREND\_BULL, rising vol | 60–70% | 30–40% | Maintain exposure, acknowledge risk. Protects Sharpe denominator. |
| MEAN\_REVERT | 50–60% | 40–50% | Raised from v2.0 (40–50%) to maintain competitive absolute return. |
| TREND\_BEAR | 30–40% | 60–70% | Raised from v2.0 (20–30%). Must stay on leaderboard while protecting Calmar. |
| HIGH\_VOL\_CRISIS | 10–20% | 80–90% | Capital preservation. Non-negotiable for Calmar. |

**Reasoning:** The v2.0 targets were optimized purely for risk-adjusted metrics and ignored Gate 1\. A bot at 70–80% cash during TREND\_BEAR generates safe returns but may not reach top 20\. The v3.1+ targets raise floors to maintain competitiveness while being meaningfully more defensive than TREND\_BULL.

## **9.2 PAXG Allocation (Reframed from v3.1)**

**Correction — No longer a Treynor instrument:** Treynor is not scored. PAXG is retained as a volatility reducer (Sharpe denominator benefit) and defensive asset. Gold historically has low crypto correlation — PAXG in cash buffer may produce small positive returns during crypto sell-offs, improving Sharpe numerator without adding crypto downside (Sortino benefit).

| Regime | PAXG Allocation | Source | Rationale |
| :---- | :---- | :---- | :---- |
| TREND\_BULL | 3–5% | Cash buffer | Minimal. Most capital in momentum positions. |
| MEAN\_REVERT | 5–10% | Cash buffer | Moderate. Reduces total portfolio vol (Sharpe denominator). |
| TREND\_BEAR | 10–15% | Cash buffer | Significant. Gold may appreciate during crypto stress. |
| HIGH\_VOL\_CRISIS | 10–15% | Cash buffer | Defensive. PAXG \+ cash provides maximum protection. |

**PAXG does not compete with crypto signal allocations.** It is sized from the cash portion of the portfolio.

## **9.3 Volatility-Adjusted Position Sizing**

**Phase 1 (current implementation):** Equal-weight among selected holdings within the deployment target, then apply tier caps and redistribute excess.

**Phase 2+ (planned):** Add inverse-volatility scaling before tier caps:

1. Start with equal weight among selected holdings within deployment target.

2. Scale each position by 1/volatility (2x avg vol \= 0.5x avg weight).

3. Normalize so total equals deployment target.

4. Apply tier caps. Redistribute excess to uncapped positions.

**Why Phase 1 uses equal-weight:** Equal-weight is a deliberate simplification for the Phase 1 launch deadline. The 24h volatility exclusion filter already removes the worst offenders (assets exceeding 2x median vol). Equal-weight among the filtered set is a reasonable starting point. Inverse-vol sizing activates in Phase 2 as part of the regime + sizing upgrade block.

**Rejected — Mean-variance optimization (Markowitz):** Requires covariance matrix (O(n²)), expected return vector (circular — it’s the signal itself), and quadratic optimizer (cvxpy, \~200 MB). Computationally infeasible on t3.medium. Statistically unreliable at 10-day horizons.

**Rejected — Risk parity:** Targets equal risk contribution regardless of conviction. Dilutes the momentum signal by equalizing risk irrespective of rank.

**Reasoning:** Inverse-volatility is the simplest non-trivial sizing that improves on equal weight. It directly reduces Sharpe denominator without covariance estimation. Arithmetic (no optimizer), robust (no parameter estimation beyond vol), transparent (three lines of code). For Screen 4, transparent sizing is easier to review than optimizer output.

## **9.4 Turnover Constraint**

Max one-way turnover per rebalance: 25% of portfolio. Minimum trade: 0.2% of NAV ($2,000).

**Reasoning:** The 25% cap prevents a single rebalance from replacing the entire portfolio. The 0.2% minimum suppresses micro-adjustments where commission exceeds signal benefit. Risk exits bypass both constraints.

## **9.5 Adaptive Exposure Adjustment (Phase 2+)**

If portfolio falls behind a naive equal-weight benchmark by \>2%, raise exposure floor by 10%. By \>4%, raise 20%. Hard cap 90%.

**Reasoning:** Static floors cannot adapt to competition dynamics. The adjustment only triggers when current defensive posture is failing to keep up. If ahead, it doesn’t trigger — risk-adjusted metrics are preserved. Acknowledged limitation: naive benchmark is arbitrary. If API exposes leaderboard, replace with actual competitor returns.

## **9.6 End-Game De-Risking**

| Time Remaining | Max Exposure | Stop Modification | Rationale |
| :---- | :---- | :---- | :---- |
| \> 48h | Full regime target | Normal | Full deployment, time to recover from drawdowns. |
| 24–48h | Cap at 70% | Normal | Begin reducing tail risk. |
| 12–24h | Cap at 45% | Normal | Protecting gains. Missing upside \< drawdown risk. |
| 4–12h | Cap at 25% | Tighten to 3% | Lock in most gains. |
| 1–4h | Cap at 15% | Tighten to 2% | Minimal exposure. Catches any 2% adverse move. |
| \< 15 min | Sell all | — | Clean exit. |

**Reasoning:** v3.0 sold to 0% in final hour. v3.1+ tightens stops instead: 2% trailing stop protects against crash while maintaining exposure to last-hour rally. Only final 15 minutes force-sells everything. De-risking starts at T-48h (not T-72h) to preserve 80% of competition at full deployment. Round 2 restart: schedule resets via config parameter.

# **10\. Risk Management Architecture (Layer 6\)**

Risk management is the non-negotiable layer. Runs before any signal, every minute, cannot be overridden. Every mechanism maps to a scored ratio.

## **10.1 Portfolio-Level Hard Limits**

| Limit | Soft Warning | Hard Action | Protects |
| :---- | :---- | :---- | :---- |
| Drawdown from peak | 5% | At 8%: LIQUIDATE all. 2h cooldown. | Calmar (0.3) |
| Daily portfolio loss | 3% | At 5%: reduce all to 50% target | Sharpe (0.3) |
| Total crypto exposure | 85% | At 90%: sell highest positions | Sharpe (0.3) |
| Single asset loss from entry | 4% | At 6%: close full position | Sortino (0.4) |

**Reasoning:** Each limit maps to a specific ratio. The 8% drawdown protects Calmar by capping max drawdown. Recovering from 8% requires \~9% in remaining days — ambitious but possible. 15% would require \~18% — likely impossible. The 2-hour cooldown prevents redeployment into a continuing sell-off. The single asset 6% limit protects Sortino by preventing individual position losses from creating large negative daily returns.

## **10.2 Individual Trailing Stops**

Every open position has a trailing stop that follows price upward but never downward. Checked every 1 minute.

| Asset Type | Stop Distance | Rationale |
| :---- | :---- | :---- |
| Tier 1–3 (Majors, Alts, DeFi) | 6% | Calibrated from backtest: median max-drawdown-before-new-high for these assets is \~4–5%. 6% avoids most false triggers while catching genuine reversals. |
| Tier 4 (Meme coins) | 8% | Meme coins have wider intraday swings. 6% would fire on normal volatility. |
| TRUMP | 10% | Political event risk creates unpredictable spikes/drops. Wider stop absorbs noise. |

**Minimum stop floor: 2%.** Dynamic tightening and end-game tightening can reduce stops, but never below 2%.

**Reasoning:** The 2% floor prevents noise-triggered exits. At 1-minute resolution, even stable assets show 1–2% intrabar fluctuations. The 99th percentile of 1-minute adverse moves for Tier 1 assets is \~1.5%. A 2% floor provides margin above this.

## **10.3 Dynamic Trailing Stop Tightening**

As an individual position accumulates unrealized gains, its trailing stop tightens to protect those gains.

| Unrealized P\&L | Action | Example (Tier 1–3) | Sortino Impact |
| :---- | :---- | :---- | :---- |
| \> \+5.0% | Tighten by 40% | 6% → 3.6% | Prevents winner from becoming a loser (removes downside event) |
| \+2.5% to \+5.0% | Tighten by 20% | 6% → 4.8% | Partial gain protection |
| \< \+2.5% | Normal | 6% | Standard protection |

### **Why This Replaces the Daily P\&L Governor**

**Reasoning:** The v3.0 portfolio-wide governor tightened all stops when portfolio gained \>3%. Problem: a single winner driving portfolio P\&L caused all positions (including new entries with 0% gain) to get tight stops. New entries with 3.6% stops were stopped out by normal volatility. Dynamic tightening is per-position: each asset’s stop based on its own P\&L. A \+7% winner gets 3.6%. A new entry keeps 6%. This isolates risk management per position and is the single highest-leverage Sortino optimizer in the system.

## **10.4 TRUMP-Specific Rules**

* Maximum position: 2% of NAV (hard cap, cannot be overridden)

* Trailing stop: 10% (wider than standard, absorbs political event noise)

* Not in meme pool during TREND\_BEAR or CRISIS

* Overnight close rule removed — crypto is 24/7, no session gap risk

**Reasoning:** TRUMP carries political event risk uncorrelated with crypto dynamics. Max loss on stop-out: 2% × 10% \= 0.2% of NAV. v3.0 overnight close rule removed because crypto trades 24/7 — trailing stop provides continuous protection.

# **11\. Execution Architecture (Layer 7\)**

## **11.1 Limit-Only Policy**

All orders are limit orders at last known price. Market orders only for risk exit escalation.

| Order Type | Fee | Daily Cost at 150 trades | Used For |
| :---- | :---- | :---- | :---- |
| Limit | 0.05% | \~$5,625/day (0.56% NAV) | 100% of normal trades |
| Market | 0.1% | \~$11,250/day (1.13% NAV) | Risk exit escalation only (after 3 failed limits) |

**Reasoning:** Limit-only halves commission from \~1.13% to \~0.56% of NAV per day. Over 10 days: 5.7% NAV difference. On this mock exchange, limit orders at current price likely fill immediately (verified in Phase 0), giving market-order speed at limit-order cost.

## **11.2 Order Queue**

* Maximum 15 simultaneous open limit orders (API rate budget constraint)

* Priority: risk exits \> reductions \> new entries \> size adjustments

* Risk exits can cancel lowest-priority pending order to make room

* After 3 failed fills on risk exit: escalate to market order

* After 3 failed fills on normal trade: abandon (opportunity has passed)

**Reasoning:** The priority queue ensures safety-critical orders execute first under API constraints. A trailing stop exit is more important than a new entry. The 15-order cap derives from API budget: at 30 calls/min, 15 orders (placement \+ status each) consume one minute’s budget.

## **11.3 Trade Logging (Screen 1 Compliance)**

Every trade decision logged to append-only JSONL with: timestamp (UTC), asset, action, signal values (momentum rank, trend penalty state, regime), target vs previous weight, order type, limit price, size, fill confirmation, commission paid.

**Reasoning:** Screen 1 (Rule Compliance) is pass/fail. Failure \= disqualification. The trade log must demonstrate ‘consistent, autonomous trade execution aligned with declared strategy.’ JSONL is human-readable, grep-able, and proves every trade was bot-generated with signal justification. Commit history transparency maintained by committing every strategy change with descriptive message.

# **12\. Monitoring & Adaptation Architecture (Layer 8\)**

## **12.1 Signal Health Monitoring**

Tracks whether momentum generates a mathematical edge.

**Phase 1 (current implementation):** `signal_health.py` monitors hit rate (% of closed positions with positive return) and winner/loser ratio (avg winner P\&L / avg loser P\&L) on a rolling 24h window. These are observation-only in Phase 1 — they log to the performance snapshot but do not halt the signal.

**Phase 2+ (planned halt logic):** The halt trigger will migrate to Net Profit Factor to avoid false alarms from the naturally low momentum hit rate:

| Metric | Warning | Halt | Why This Metric |
| :---- | :---- | :---- | :---- |
| Net Profit Factor (Gross Profit / Gross Loss) | \< 1.0 | \< 0.75 for 6h | PF \< 1.0 means signal losing money in aggregate. |
| Avg Winner / Avg Loser | \< 1.5 | \< 1.0 for 6h | Momentum needs large winners. If avg winner \< avg loser, the math is broken. |

**Phase 1 halt condition (monitoring-only proxy):** If rolling 24h hit rate falls below 35% sustained for 12h AND winner/loser ratio \< 1.0, the signal is flagged but not halted. Full halt logic activates in Phase 2.

### **Why Net Profit Factor (Not Hit Rate) Is the Phase 2 Halt Metric**

**Reasoning:** Momentum strategies naturally have low hit rates (\~35–40%). Strategy cuts losers quickly, lets winners run. Monitoring hit rate creates false alarms. Net Profit Factor captures the right question: total profit from winners vs total loss from losers. A 35% hit rate with 3:1 win/loss \= PF 1.62 — profitable but would fail a hit-rate check. Hit rate is retained in Phase 1 as a lightweight proxy because it is cheaper to compute and sufficient for observation.

### **On Halt: Total Liquidation**

When signal halted, ALL momentum positions liquidated to cash/PAXG. System does NOT concentrate into top 3\.

**Reasoning:** If signal is broken (PF \< 0.75), there is no reason to believe top 3 are better than bottom 3\. The ranking is unreliable. Concentrating into fewer positions based on a broken signal increases risk. Total liquidation is the only logically consistent response.

## **12.2 Performance Logging**

* **Trade log (JSONL):** Every decision with full signal context. Screen 1 compliance.

* **Hourly snapshot (JSONL):** NAV, daily P\&L, regime state, all positions with weight/P\&L/stop distance, signal health.

* **Daily return series:** NAV at fixed checkpoint (00:00 UTC). Authoritative input for ratio computation.

* **Heartbeat file:** Timestamp every loop iteration. Stale \> 3 min \= process hung.

## **12.3 Self-Monitoring (Sortino/Sharpe/Calmar Tracker)**

Every hourly snapshot computes and logs estimated current values of: Sortino, Sharpe, and Calmar ratios.

**Reasoning:** Without self-monitoring, the team has no feedback loop until competition ends. By computing ratios hourly, the team can identify if a Phase 2 change (e.g., tighter stops) is improving Sortino but damaging Sharpe, and revert before it accumulates. This is the internal feedback loop that drives Phase 4 parameter tuning.

## **12.4 ML Adaptation (Phase 3\)**

### **IC Monitor**

48-hour rolling Information Coefficient: rank correlation between ML predictions and actual forward 4h returns. If IC \< 0.02: halt ML multiplier, revert to pure momentum sizing. Resume when IC \> 0.04 sustained for 12h.

**Reasoning:** IC measures whether the model’s relative ranking matches actual performance. IC 0.02 is barely above noise. Below this, ML adds noise not signal. 48-hour window (extended from the original 12h target) provides a more stable IC estimate — with only \~12 data points per day (one per 4h return horizon), a 12h window would contain only 3 observations, producing a highly volatile IC estimate. The 48h window gives 12 observations, yielding a statistically meaningful rank correlation. The asymmetric resume threshold (0.04 for 12h) prevents whipsaw on marginal IC recovery.

### **Retrain Loop (Every 24h)**

Walk-forward retrain on most recent data. No standard-deviation rejection gate. Post-retrain sanity check: if new model IC \< 0.01 on last 6h of known data, keep old model.

**Reasoning:** No std-dev gate because violent market shifts (bull → crisis) should produce radically different predictions. A gate would block legitimate adaptation. The sanity check catches only catastrophic failures (model corruption, data error) without preventing legitimate regime adaptation.

# **13\. Scoring Optimization Map**

Every APEX component mapped to the specific ratio or screen it optimizes.

## **13.1 Sortino (0.4 weight — highest priority)**

| Mechanism | Section | Impact |
| :---- | :---- | :---- |
| Trailing stops (6–8%) | Risk Management | Cuts losses before they become large downside events. |
| Dynamic stop tightening | Risk Management | Locks in gains. Prevents winners from becoming losers. |
| Asymmetric regime transitions | Regime Detection | Fast risk-cutting (immediate downgrade). Cautious risk-adding. |
| Trend penalty | Signal Generation | Deteriorating assets exit smoothly. Reduces unnecessary downside. |
| Contagion circuit breaker | Regime Detection | Detects coordinated sell-offs. Reduces positions before losses compound. |
| Single asset 6% hard stop | Risk Management | Prevents any individual loss from creating a large negative daily return. |

## **13.2 Sharpe (0.3 weight)**

| Mechanism | Section | Impact |
| :---- | :---- | :---- |
| Vol-adjusted sizing | Portfolio Construction | Underweights volatile assets. Reduces portfolio variance. |
| Limit-only execution | Execution Engine | Halves commission. Improves net return. |
| Turnover buffer | Signal Generation | Reduces round-trips. Smoother daily returns. |
| Smoothed vol guard | Risk Management | Linear exposure ramp. No sudden return discontinuities. |
| Diversification (8–10 holdings) | Portfolio Construction | Distributes risk across assets. |
| PAXG allocation | Portfolio Construction | Reduces total portfolio volatility in defensive regimes. |

## **13.3 Calmar (0.3 weight)**

| Mechanism | Section | Impact |
| :---- | :---- | :---- |
| 8% drawdown hard stop | Risk Management | Absolute ceiling on max drawdown. |
| Regime-conditional deployment | Portfolio Construction | Lower exposure prevents drawdowns from occurring. |
| Contagion circuit breaker | Regime Detection | Rapid de-escalation before losses deepen. |
| End-game de-risking | Portfolio Construction | Prevents last-day drawdown from erasing accumulated gains. |

## **13.4 Screen 4: Code & Strategy Review (60%)**

| Sub-Criterion | Weight | Mechanism |
| :---- | :---- | :---- |
| Strategy logic clarity | 30% | Modular layers. Named modules. Config.yaml with comments. README. Inline docstrings. |
| Code quality | 20% | Clean structure. Type hints. No dead code. tests/ directory. Consistent style. |
| Continuous runnability | 10% | Crash recovery (Parquet \+ JSONL). Exponential backoff. Heartbeat. Top-level exception handling. |

# **14\. Asset Universe & Tier Architecture**

| Tier | Count | Examples | Max Position | Stop | Pool |
| :---- | :---- | :---- | :---- | :---- | :---- |
| Tier 1 — Majors | 7 | BTC, ETH, BNB, LTC, ADA, DOGE, TRX | 8% (DOGE: 5%) | 6% | Main |
| Tier 2 — Large-cap alts | 10 | LINK, DOT, NEAR, TON, SUI, APT... | 8% | 6% | Main |
| Tier 3 — DeFi | 9 | AAVE, UNI, CRV, PENDLE, ONDO... | 6% | 6% | Main |
| Tier 4 — Meme | 7 | SHIB, PEPE, FLOKI, WIF, BONK, PUMP, PENGU | 3% | 8% | Separate (BULL only) |
| Tier 5 — Obscure | 19+ | SOMI, AVNT, ASTER, MIRA... | 2% | 8% | Opportunistic (BULL) |
| Special — PAXG | 1 | PAXG/USD | 15% | N/A | Cash buffer |
| Special — TRUMP | 1 | TRUMP/USD | 2% | 10% | Special rules |

**Reasoning:** The tier system ensures position sizes are proportional to risk profile. Max loss per stop-out: Tier 1–3 at 8% cap × 6% stop \= 0.48% NAV. Tier 4 at 3% × 8% \= 0.24%. Tier 5 at 2% × 8% \= 0.16%. All within the daily loss budget (5% hard limit) with margin for multiple simultaneous stop-outs.

# **15\. Technology Stack Decisions**

| Function | Selected | Alternative Considered | Why Selected |
| :---- | :---- | :---- | :---- |
| HTTP client | aiohttp | requests | Async non-blocking I/O. Main loop cannot block on API calls. |
| Event loop | asyncio (stdlib) | Prefect, APScheduler | No dependency. Timer scheduling is 10 lines of code. |
| Numerical | numpy | pandas | Lower memory, faster for fixed-size arrays. Pandas only for I/O. |
| Data I/O | pandas (minimal) | polars | Only for Parquet I/O. Not in hot path. |
| Logging | logging (stdlib) | structlog | Sufficient for JSON output. No dependency. |
| Config | yaml | toml, dotenv | Readable, hierarchical. Better for nested config. |
| ML (Phase 3\) | lightgbm | xgboost, pytorch | Fastest training, lowest memory. \<5ms inference. |
| Persistence | Parquet \+ JSONL | sqlite3 | Parquet for structured data. JSONL for append-only. |

## **15.1 Explicitly Excluded (with Reasoning)**

**Rejected — Redis:** No IPC in single-process architecture. Adds dependency for zero benefit.

**Rejected — TimescaleDB / PostgreSQL:** Entire dataset fits in 35 MB of RAM.

**Rejected — Prometheus \+ Grafana:** No dashboard audience. JSON logs suffice for code review.

**Rejected — MLflow / W\&B:** Model \<10 MB. Git tracks everything.

**Rejected — cvxpy / PyPortfolioOpt:** Arithmetic sizing replaces optimization. Simpler, more transparent for Screen 4\.

**Rejected — Numba:** JIT adds startup time and memory. NumPy vectorization sufficient at n=56.

**Rejected — ccxt:** Roostoo has own API. ccxt doesn’t support it.

**Rejected — SHAP:** \~200 MB dependency for marginal interpretability benefit.

**Rejected — Optuna:** Bayesian HPO too slow for 10-day window on t3.medium.

**Rejected — hmmlearn:** HMM replaced by transparent rule-based regime detection.

**Rejected — arch:** GARCH replaced by simple vol percentile rank.

# **16\. Phased Delivery Plan (Competition Timeline)**

| Phase | Dates | Steps | What Ships |
| :---- | :---- | :---- | :---- |
| Phase 0: API Test | Mar 16–17 | 1–2 | All architecture decisions empirically grounded. Signal backtested. |
| Phase 1: MVB | Mar 17–20 | 3–9 | Working bot: momentum, stops, limits, execution, recovery. Deployed before Mar 21\. |
| Phase 2: Regime | Mar 21–25 | 10–17 | Regime detection, vol-sizing, PAXG, meme pool, trend penalty, stop tightening, end-game. |
| Phase 3: ML \+ Polish | Mar 25–28 | 18–19 | ML overlay, Tier 5, adaptive exposure. README finalized. Repo submitted before Mar 28\. |
| Phase 4: Tuning | Mar 28–31 |  | Parameter tuning from live observation. No new features. |
| Round 2 (if qualified) | Apr 4–14 |  | Reset end-game. Apply Round 1 lessons. Inter-city coordination possible. |

**The single most important rule: Phase 1 ships before competition starts.** Everything after Phase 1 is live improvement on a working, trading bot.

# **17\. Open Questions & Risks**

## **17.1 Resolved by Problem Statement**

| Question | Answer | Architecture Impact |
| :---- | :---- | :---- |
| Which ratios? | Sortino (0.4), Sharpe (0.3), Calmar (0.3). No Treynor. | Removed BTC beta targeting, PAXG Treynor framing. |
| Weights? | 0.4/0.3/0.3 weighted average | Sortino priority elevated. Downside protection highest priority. |
| Evaluation structure? | 40% composite \+ 60% code review | Code quality is dominant criterion. |
| Timeline? | Mar 21–31 (R1), Apr 4–14 (R2 if qualified) | Bot supports clean restart for Round 2\. |
| Strategy restrictions? | Open-ended. Any approach permitted. | APEX momentum \+ ML approach is permitted. |

## **17.2 Must Verify in Phase 0**

| \# | Question | Impact if Wrong |
| :---- | :---- | :---- |
| 1 | Does batch endpoint return all prices? | Signal breadth drops from 56 to \~20 assets. |
| 2 | Do limit orders at current price fill immediately? | Execution engine complexity triples. |
| 3 | Actual rate limit? | At 30/min: only \~6 trades per rebalance. |
| 4 | Data fields in price endpoint? | If no volume: \~25% features eliminated. |
| 5 | Exact asset count? | Unknown assets default to Tier 5\. |

## **17.3 Key Risks**

| Risk | Probability | Impact | Mitigation |
| :---- | :---- | :---- | :---- |
| Momentum doesn’t work on Roostoo | Low–Med | Critical | Step 2 backtest. Fallback: BTC \+ top 3 alts. |
| Process crash | Medium | High | Parquet \+ JSONL recovery. \<30 sec restart. |
| Commission drag \> signal return | Medium | High | Turnover buffer, min threshold, limit-only. |
| Poor code review despite good trading | Low | High | 60% code weight addressed from day 1\. Clean architecture. |
| Regime whipsaw | Medium | Medium | Upgrade transitions require 30-min persistence. |
| PAXG doesn’t track gold on mock exchange | Low | Low | Monitor PAXG behavior. If uncorrelated with gold, reduce to cash. |

# **18\. Document Control**

| Version | Date | Key Changes |
| :---- | :---- | :---- |
| v3.3 (this document) | Mar 19, 2026 | Codebase consistency pass. Ring buffer corrected to maxlen=1441. Meme pool corrected to 7 coins (1000CHEEMS removed, reclassified to Tier 5). Binance fallback client added to data sources. BTC beta monitor clarified as monitoring-only (no weight adjustment). Section 9.3 updated: Phase 1 uses equal-weight; inverse-vol sizing deferred to Phase 2. Section 12.1 updated: Phase 1 signal health uses hit rate + winner/loser ratio (monitoring-only); Net Profit Factor halt logic deferred to Phase 2. IC window corrected to 48h (from 12h) with asymmetric resume threshold (>0.04 for 12h). |
| v3.2 | Mar 18, 2026 | Aligned to official problem statement. Treynor removed. Scoring: 0.4 Sortino / 0.3 Sharpe / 0.3 Calmar. Code review elevated to 60%. PAXG reframed as vol reducer. BTC beta targeting removed. Timeline updated for two rounds. Repo submission deadline (Mar 28\) added. Round 2 restart support added. Screen 4 optimization added throughout. |
| v3.1 Architecture | Mar 18, 2026 | Initial architecture doc with full reasoning. Assumed four ratios incl. Treynor. |
| v3.1 SRD | Earlier | Trend penalty, stop tightening, Tier 5, adaptive exposure, 2% stop floor. |
| v3.0 SRD | Earlier | Redesigned for t3.medium. Single process. Rule-based regime. |
| v2.0 SRD | Earlier | Original design. HMM, GARCH, Redis, TimescaleDB. Impractical for constraints. |

