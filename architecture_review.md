# APEX Architecture Review — Devil's Architect Assessment

**Reviewer perspective:** Contrarian systems architect (Devil's Architect)
**Document under review:** `context_files/requirements_v3.md`
**Cross-referenced against:** Meeting transcript, meeting summary, resources, trading currencies list, AWS infrastructure constraints, original v2.0 requirements
**Date:** 2026-03-15

**Review methodology:** Every recommendation is classified by problem type before action is proposed.

- **Type A — WRONG:** The design will actively produce worse outcomes. Not a tradeoff. A mistake.
- **Type B — SUBOPTIMAL:** Acceptable outcomes but leaves performance on the table.
- **Type C — UNTESTED ASSUMPTION:** Might be right or wrong. Nobody has verified it.

---

## ITEMS RESOLVED IN v3.0

The following v2.0 issues have been correctly addressed. Each is verified against the context files and endorsed without qualification.

| Original Issue | v3.0 Resolution | Verdict |
|---|---|---|
| [ARCH-CRIT-1] Infrastructure impossibility (t3.medium vs. full stack) | Single Python process, no external services. ~200MB baseline, ~400MB peak. | **Correct.** RAM estimates are conservative and feasible. |
| [ARCH-CRIT-3] Non-existent data sources (funding rates, order book) | Removed entirely. All features derived from price data only. | **Correct.** Volume features conditionally included — right approach. |
| [ARCH-CRIT-5] 48h paper-trading validation gate | Dropped. IC monitoring provides safety. | **Correct.** Add a 5-minute output distribution sanity check on retrain as a cheap guard against pathological model outputs. |
| [ARCH-GAP-1] MA(10)/MA(50) noise generator | Changed to EMA(60)/EMA(240). | **Partially correct.** See [DA-GAP-1] below — the override vs. penalty question remains. |
| [ARCH-GAP-4] Over-complex architecture | Phased delivery plan. MVA ships before competition. | **Correct direction.** See [DA-GAP-2] below — Phase 1 MVA needs a minimal regime guard. |
| [ARCH-GAP-5] Correlation contagion O(n^2) at 5-min cadence | Replaced with O(n) contagion proxy every 1 minute. | **Correct.** Simpler, faster, catches the actual dangerous scenario. |
| [ARCH-GAP-6] DOGE dual classification | DOGE is Tier 1 only, capped at 5%. | **Correct.** No further action needed. |
| [ARCH-TRD-1] Limit order timeout on risk exits | Timeout reduced to 1 minute. Risk exit escalation after 1 failed attempt if loss accelerating. | **Correct.** The aggressive escalation on accelerating loss is the right call. |
| [ARCH-TRD-2] Symmetric regime transitions | Asymmetric: downgrades immediate, upgrades gradual over 2-3 cycles. | **Correct.** The cost asymmetry is stark and correctly handled. |
| [ARCH-TRD-3] End-game de-risking too early | Starts at T-48h instead of T-72h. Compressed final wind-down. | **Partially correct.** See [DA-TRD-1] below — final hour handling needs refinement. |

These items require no further debate. Move on.

---

## REMAINING CRITICAL ISSUES

---

### [DA-CRIT-1] API Validation Is a Day-Zero Blocker — Not a Checklist Item

**Problem type:** Type A — the design cannot be finalized without these answers.

**Layers affected:** All

**Problem:** v3.0 correctly lists 7 open questions in Section 19.1 and includes a pre-competition checklist. But it treats API testing as one item among many, when in reality it is the **single prerequisite that gates all other work**. The meeting transcript from Pranesh confirms:

> "you get an option to select the currencies and after you select the currencies, you get the exchange info for that specific currency. Otherwise, if you don't select any currency, you get all the information for all the currency."

This describes the exchange info endpoint. Whether the **ticker price endpoint** behaves the same way is unconfirmed. Whether limit orders at the current price fill immediately is unconfirmed. Whether the rate limit is 30 or 60 calls/min is unconfirmed. Whether the API returns OHLCV or just last price is unconfirmed.

Every one of these answers changes a different part of the architecture:

| Question | If Answer Is Unfavorable | What Changes |
|---|---|---|
| Batch pricing doesn't work | Need 56 individual calls for prices | Rebalance cadence becomes 3+ minutes. Must reduce watchlist to ~20 assets. |
| Limit at current price doesn't fill immediately | Need timeout/resubmission logic | Execution engine complexity increases. Risk exit speed degrades. |
| Rate limit is 30, not 60 | Half the API budget | Can execute only ~6-8 trades per rebalance cycle instead of 12. |
| API returns last price only (no OHLCV) | Must construct bars from snapshots. No volume data. | ~25% of feature set eliminated. Volume features removed entirely. |

**Impact:** Writing architecture-dependent code before these are answered means potentially rewriting it after testing. On a timeline where the competition starts tomorrow (March 16), wasted hours are unrecoverable.

**Recommendation:** The testing API keys are available now. Before any other work:

1. **Hour 1:** Write a 30-line test script. Call exchange info with no filter — does it return all 56 prices? Call ticker endpoint with no filter — same? What fields come back? Time the response.
2. **Hour 1 (continued):** Place a limit buy at current price. Does it fill immediately? Place a limit buy 0.5% below current price. Does it sit unfilled? Cancel it.
3. **Hour 1 (continued):** Fire 60 rapid calls in 60 seconds. Does it throttle? At what rate?
4. **Hour 2:** Based on answers, finalize the architecture parameters (cadence, watchlist size, execution logic) and lock them into `config.yaml`.

**The strongest case against this urgency:** "We can build the MVA with conservative assumptions and adjust later." This is true for cadence and feature set — but not for the batch endpoint. If batch pricing doesn't work, the entire momentum ranking approach (rank all 56 assets) becomes infeasible. You'd need to rank only the ~20 assets you can afford to price-check, which fundamentally changes the signal's cross-sectional breadth.

**Verdict: Test the API first. Literally first. Before anything else.**

---

### [DA-CRIT-2] Scoring Formula Uncertainty — Three Ratios or Four?

**Problem type:** Type C — untested assumption.

**Layers affected:** Layer 5 (Portfolio Construction), Layer 6 (Risk Management — Daily P&L Governor)

**Problem:** The meeting transcript has Edward saying:

> "we will also be computing every team's performance based on three ratios. So TINA ratio, Sharpe ratio, and Karma ratio."

He names **three** ratios: Treynor, Sharpe, Calmar. The meeting summary lists **four**: Sharpe, Sortino, Calmar, Treynor. v3.0 designs around four.

If Sortino is not in the composite score, the daily P&L governor's justification weakens. The governor is primarily designed to improve Sharpe (reduce total volatility) and Sortino (reduce downside volatility). If only Sharpe is scored (not Sortino separately), the governor still has value — but the weight of its importance drops.

More critically: if the composite score weights the three ratios equally (33/33/33), the architecture should weight its optimization effort proportionally. Currently v3.0 has explicit mechanisms for:
- Sharpe: daily P&L governor + diversification + trailing stops
- Sortino: daily P&L governor + trailing stops + circuit breakers
- Calmar: drawdown circuit breakers + trailing stops + end-game de-risking
- Treynor: PAXG allocation + BTC beta targeting

If Sortino is removed, the daily P&L governor becomes Sharpe-only, which may not justify its complexity.

**Recommendation:** The problem statement releases Monday. Until then:
1. Keep the four-ratio design — it's the superset and doesn't cost extra implementation effort
2. When the problem statement arrives, check: which ratios are scored? Are they weighted equally? Is the composite a simple average or something else?
3. If only three ratios: simplify the daily P&L governor (see [DA-GAP-3] for modifications regardless)

**What would change this verdict:** If the problem statement confirms all four ratios with equal weighting, this issue is resolved and v3.0's design is correct.

---

### [DA-CRIT-3] Dual Objective — Static Exposure Floors Are the Wrong Lever

**Problem type:** Type B — suboptimal.

**Layers affected:** Layer 5 (Portfolio Construction — Section 10.1 deployment targets)

**Problem:** v3.0 correctly identifies the dual-objective problem (need absolute returns for Gate 1, risk-adjusted returns for Gate 2) and raises exposure floors: MEAN_REVERT from 40-50% to 50-60%, TREND_BEAR from 20-30% to 30-40%.

The diagnosis is right. The prescription is wrong.

**Exposure quantity is not the primary driver of absolute returns — asset selection quality is.** A portfolio 80% allocated to BTC during a sideways BTC week underperforms a portfolio 55% allocated to the three altcoins that happen to pump 20%. The momentum ranking system (Signal 1) is the engine that drives absolute returns. Raising exposure floors from 30% to 40% in TREND_BEAR adds maybe 1-2% absolute return in a bear market — while simultaneously increasing drawdown risk (hurting Calmar) and volatility (hurting Sharpe).

The static floor also can't adapt to competition dynamics. If the market is flat and most teams are making 0-2% by day 5, a 40% floor in TREND_BEAR is already competitive. If the market rallies 20% and teams are making 15%+, even 60% exposure isn't enough.

**Recommendation:** Keep v3.0's exposure targets as they are (the raised floors are not harmful), but add an **adaptive exposure adjustment** based on estimated relative performance:

```
# Compute a naive benchmark: equal-weight exposure at 70% to all 56 assets
# Track cumulative return of this benchmark vs. our portfolio
relative_gap = naive_benchmark_return - portfolio_return

IF relative_gap > 2.0% AND days_remaining > 5:
    # We're falling behind. Raise exposure floor by 10% across all regimes.
    exposure_adjustment = +0.10
ELIF relative_gap > 4.0% AND days_remaining > 3:
    # Significantly behind. Raise floor by 20%.
    exposure_adjustment = +0.20
ELSE:
    exposure_adjustment = 0.0
```

**Cost of this alternative:** Adds ~10 lines of code. Requires tracking the naive benchmark return, which is trivial (equal-weight average of all 56 returns). Introduces the risk of raising exposure during adverse conditions — but only triggers when we're already underperforming, which means the current defensive posture isn't working anyway.

**When the original design outperforms:** If the market is genuinely crashing and our conservative exposure is protecting capital while competitors bleed. In that scenario, the relative gap is negative (we're ahead) and the adaptive adjustment doesn't trigger. The mechanism is self-correcting.

**Scoring impact:** This directly addresses Gate 1 (absolute return for top-20 leaderboard position) without materially degrading Gate 2 metrics. The adjustment only triggers when we're already losing on absolute returns, meaning the Sharpe/Calmar optimization is failing at its primary goal of keeping us competitive.

---

### [DA-CRIT-4] Universe Count Discrepancy — 56 vs. 66 Assets

**Problem type:** Type C — untested assumption.

**Layers affected:** Layer 1 (Data Ingestion), Layer 4 (Signal Generation)

**Problem:** The meeting transcript has Edward saying "66 crypto available" when showing the platform. The trading currencies list has 56 entries (including the truncated "I/USD"). v3.0 designs around 56 assets.

If 10 additional assets exist on the platform but aren't in our trading list, we're missing potential momentum candidates. Some of those 10 might have strong momentum signals that competitors are trading.

**Recommendation:** On first API call, fetch the full exchange info with no filter and count the returned assets. If more than 56 come back, identify the extras, classify them into tiers, and add them to the universe. The momentum ranking system handles any number of assets — it just ranks whatever it receives.

**Impact if wrong:** Missing 10 assets from the universe reduces cross-sectional breadth. If any of the missing assets are high-momentum candidates, competitors who trade them gain an edge we can't match.

---

## REMAINING GAPS

---

### [DA-GAP-1] Trend Filter as Exit Override Still Causes Asymmetric Damage

**Problem type:** Type B — suboptimal.

**Layers affected:** Layer 4 (Signal 2 — Trend Following)

**Problem:** v3.0 correctly changed MA(10)/MA(50) to EMA(60)/EMA(240), which eliminates the high-frequency noise problem. But the signal is still an **exit override**: when EMA(60) crosses below EMA(240), positions are force-exited regardless of momentum rank. v3.0 adds an entry confirmation: EMA(60) must be above EMA(240) for new positions to open.

The entry confirmation prevents immediate whipsaw (exit then re-enter next cycle). But it creates a different problem: an asset that is momentum-rank #1 but has EMA(60) < EMA(240) cannot be held. If the EMA crossover is a false signal (price consolidating briefly before resuming the trend), the system:

1. Force-exits a top-ranked momentum position (paying 0.05% commission)
2. Cannot re-enter until EMA(60) recovers above EMA(240) (could take hours)
3. Misses the continuation of the trend
4. Re-enters eventually (paying another 0.05% commission)

Net cost: 0.1% commission + missed returns during the lockout period. The EMA(60)/EMA(240) crossover on 1-min bars generates roughly 1-3 signals per day per asset in choppy conditions. Across 10 held positions, that's potentially 10-30 forced exits per day.

**The strongest case for the current design:** The trend filter catches genuine reversals faster than momentum ranking alone. An asset that's still momentum-rank #3 but whose 1h MA just crossed below its 4h MA is showing early deterioration that the 4h/12h/24h momentum weights haven't captured yet. Force-exiting protects against riding a reversal down.

**Recommendation:** Downgrade the trend filter from exit override to **momentum score penalty**:

```
# Instead of: IF ema_60 < ema_240 → force exit
# Use: IF ema_60 < ema_240 → penalize momentum composite score

trend_penalty = -0.3  # standard deviations in cross-sectional rank
adjusted_momentum_score = raw_momentum_score + trend_penalty
# Asset may still be held if momentum is strong enough to survive the penalty
# Asset will be naturally rotated out if momentum + penalty drops below the top-N threshold
```

**Why this is better:**
- Strong momentum + weak trend = penalty reduces position but doesn't force-exit (saves commission, keeps partial exposure to potential recovery)
- Weak momentum + weak trend = penalty pushes asset below the top-N threshold → natural exit at next rebalance (same outcome as override, but through the ranking system)
- The penalty magnitude (-0.3 sigma) is tunable in `config.yaml`

**Cost of this alternative:** The trend filter no longer provides instant protection against genuine reversals. Trailing stops at 6% are the backstop. If a reversal is genuine and rapid (>6% in minutes), the trailing stop catches it regardless of the trend filter. The trend filter's value is in the gray zone between "slight deterioration" and "trailing stop triggers" — the penalty approach handles this by reducing exposure rather than eliminating it.

**Scoring impact:** Fewer forced exits → lower commission drag → better Sharpe. More consistent positioning → lower turnover volatility → better Sortino.

**When the original design outperforms:** If an asset reverses exactly 4-5% (enough to cause damage but not enough to trigger the 6% trailing stop), and the EMA crossover catches it early. In that specific scenario, the override exits immediately while the penalty only reduces position. The override saves ~2-3% on that position (difference between penalty-reduced exit and override exit). Over 10 days, the question is: does this scenario occur more often than the false-exit scenario? Given that EMA(60)/EMA(240) crossovers are more frequent than 4-5% reversals that don't trigger stops, the penalty is the better default.

---

### [DA-GAP-2] Phase 1 MVA Has No Regime Awareness — Vulnerable to Day-1 Crash

**Problem type:** Type B — suboptimal.

**Layers affected:** Layer 5 (Portfolio Construction — Phase 1), Layer 3 (Regime Detection)

**Problem:** v3.0's Phase 1 MVA includes: data ingestion, momentum ranking, equal-weight sizing, tier caps, trailing stops, circuit breaker, limit order execution. It explicitly skips regime detection — that's Phase 2 (Days 1-3).

But what exposure level does Phase 1 use? If the default is "assume TREND_BULL" (75-85% crypto), and the market drops 15% in the first 2 days before Phase 2 ships, the MVA is fully deployed with only trailing stops and the 8% drawdown circuit breaker as protection.

Trailing stops at 6% per position could let through a 4-5% portfolio drawdown before all stops fire (positions don't all hit -6% simultaneously — they bleed at different rates). That's more than half the 8% circuit breaker threshold, and it's permanent Calmar damage incurred before the regime system even exists.

**The strongest case for the current design:** Phase 1 is about shipping fast. Adding regime detection to Phase 1 increases implementation scope and delays deployment. The trailing stops and circuit breaker provide adequate protection.

**Recommendation:** Add a **5-line volatility guard** to Phase 1 that doesn't require the full regime system:

```python
# Minimal Phase 1 vol guard — add to the rebalance loop
btc_24h_vol = rolling_std(btc_returns, window=1440)  # 24h of 1-min returns
btc_30d_median_vol = precomputed_from_historical  # one number, set in config

if btc_24h_vol > 2.0 * btc_30d_median_vol:
    max_crypto_exposure = 0.40  # defensive mode
else:
    max_crypto_exposure = 0.75  # normal mode
```

This is not a regime system. It's a single boolean check: is BTC volatility spiking? If yes, reduce exposure. If no, proceed normally. It requires:
- One rolling standard deviation (already computed for feature engineering)
- One hardcoded threshold (precomputed from historical data, set in `config.yaml`)
- Zero new libraries, zero model fitting, zero regime state management

**Cost:** 5 lines of code, 10 minutes of implementation time. Prevents the worst-case scenario of running 75% exposure into a vol spike before Phase 2 is ready.

**Scoring impact:** Prevents potential Calmar damage in the first 1-3 days. No downside if volatility is normal (the guard doesn't trigger).

---

### [DA-GAP-3] Daily P&L Governor Thresholds Are Unvalidated and Cap Upside

**Problem type:** Type B — suboptimal.

**Layers affected:** Layer 6 (Risk Management — Section 11.2)

**Problem:** v3.0 adds a daily P&L governor:

| Daily P&L | Action |
|---|---|
| > +1.5% | Scale exposure to 50% of target |
| +0.5% to +1.5% | Scale exposure to 75% of target |
| -0.5% to +0.5% | Normal |
| < -0.5% | Existing circuit breakers |

The concept is correct — managing daily return distribution directly optimizes Sharpe and Sortino. The thresholds are presented without backtest support.

**Problem 1:** In a crypto market, +1.5% by midday might be the start of a +5% day. Cutting exposure to 50% at +1.5% caps your daily return at approximately +2.5-3%. If competitors are fully deployed and the market rallies 5%, they earn +4-5% while you earn +2.5%. Over 10 days, even one such day creates a 2% absolute return gap — directly undermining Gate 1 (leaderboard position for top-20 advancement).

**Problem 2:** The governor interacts with end-game de-risking. In the final 24 hours, if exposure is already capped at 45% by the de-risking schedule AND the governor triggers at +1.5%, effective exposure drops to 45% x 50% = 22.5%. That's essentially cash with extra steps.

**Problem 3:** The governor creates a perverse incentive structure. In a strong uptrend, the bot systematically reduces exposure as it makes money — the opposite of "let winners run." Sortino rewards positive skew (large gains, small losses). Capping gains reduces positive skew.

**The strongest case for the current design:** Sharpe rewards consistency above all. A day that goes from +2% to +0.5% (giving back gains) is worse for Sharpe than a day that ends +1.0% with low intraday variance. The governor prevents give-back by locking in gains.

**Recommendation:** Replace the exposure-cutting governor with **dynamic trailing stop tightening**:

| Daily P&L | Action |
|---|---|
| > +2.0% | Tighten all trailing stops by 40% (e.g., 6% becomes 3.6%) |
| +1.0% to +2.0% | Tighten all trailing stops by 20% (e.g., 6% becomes 4.8%) |
| -0.5% to +1.0% | Normal trailing stop distances |
| < -0.5% | Existing circuit breakers handle this |

**Why this is better:**
- You stay fully exposed → if the rally continues, you capture it (helps absolute returns)
- Tighter stops mean any reversal exits positions faster → protects the daily gain (helps Sharpe)
- You don't reduce expected return — you reduce return variance around a higher mean
- Sortino likes this: you're keeping the upside while tightening the downside protection
- No interaction with end-game de-risking — stops are per-position, not portfolio-level

**Cost of this alternative:** If the market dips briefly and recovers (a shakeout), the tightened stops cause premature exits. This costs 0.1% round-trip commission per position that gets stopped out and re-entered. But this scenario is exactly the one where the original governor also causes damage — it would have already cut exposure, missing the recovery.

**Scoring impact:**
- Sharpe: improved (lower daily return variance without capping mean)
- Sortino: improved (preserves positive skew while tightening downside)
- Calmar: neutral (tighter stops may cause slightly more frequent small exits but prevent large give-backs)
- Absolute returns: improved (stays fully exposed during rallies)

**When the original design outperforms:** If the market consistently reverses after +1.5% days and those gains are regularly given back. In that specific pattern, cutting exposure at +1.5% would preserve more gains than tighter stops. But this pattern implies mean-reverting daily returns — and the regime system should already detect MEAN_REVERT and reduce exposure accordingly.

---

### [DA-GAP-4] Treynor Benchmark Remains Unknown — PAXG Sizing May Be Wrong

**Problem type:** Type C — untested assumption.

**Layers affected:** Layer 5 (Portfolio Construction — PAXG allocation, BTC beta targeting)

**Problem:** v3.0 correctly identifies this as an open question and makes the benchmark configurable. The design computes beta against both BTC and the equal-weighted universe index.

**What's still missing:** The meeting transcript has Edward saying "TINA ratio" — likely referring to Treynor. But the exact benchmark is undisclosed. The problem statement releases Monday. If the Treynor benchmark is an external index (e.g., risk-free rate, SPY), then:

- BTC beta targeting becomes irrelevant (you'd want to measure crypto-portfolio beta against the external benchmark)
- PAXG allocation still helps (reduces total portfolio volatility, which reduces beta against any benchmark)
- But the beta-adjustment logic in Section 10.4 ("reduce allocation to highest-BTC-beta assets") would be calibrated against the wrong reference

**Observation from context:** In a crypto-only competition, the most natural benchmarks are BTC, a market-cap-weighted crypto index (dominated by BTC anyway), or an equal-weighted universe index. All three are crypto-denominated. PAXG has near-zero correlation to all crypto assets regardless of which is chosen. The PAXG allocation is robust to benchmark choice.

**Recommendation:** v3.0's approach is correct. No changes needed beyond:
1. Wait for problem statement Monday
2. If benchmark is external: the beta-targeting logic in Section 10.4 needs recalibration (5 minutes of config changes), but PAXG allocation logic is unchanged
3. Note: computing beta against 2 benchmarks (BTC + equal-weight) instead of 3 is sufficient for t3.medium RAM — the marginal value of a volume-weighted third benchmark is minimal

**Verdict: ACCEPT v3.0's design. Revisit Monday.**

---

### [DA-GAP-5] Tier 5 Universe Is 19 Assets at 2% Cap — Dead Weight or Hidden Edge?

**Problem type:** Type B — suboptimal.

**Layers affected:** Layer 4 (Signal Generation)

**Problem:** v3.0 classifies 19 assets as Tier 5 (Obscure/low-cap) with a 2% NAV cap: SOMI, AVNT, ASTER, MIRA, EDEN, FORM, LINEA, LISTA, OPEN, BMT, OMNI, STO, WLD, POL, FIL, ZEN, CFX, ZEC, S.

That's 34% of the universe by count but contributes a maximum of 38% of NAV (19 x 2%) if every position is maxed — except the momentum ranking only selects top 8-12 from Tier 1-3. Tier 5 assets are not mentioned in the momentum ranking at all.

**Are Tier 5 assets included in the cross-sectional momentum ranking?** v3.0 Section 9.1 says: "Rank all Tier 1-3 assets by composite recent return. Go long the top-ranked assets." Tier 5 is excluded from the primary signal.

This means 19 assets receive zero allocation under any regime. They're in the universe definition but not in the signal pipeline. If any Tier 5 asset pumps 50% in a day, the bot ignores it. Competitors who trade it capture the gain.

**The strongest case for excluding them:** Tier 5 assets are low-cap, likely low-liquidity on the real exchange (though this is a mock exchange — liquidity is irrelevant). Their price behavior may be erratic. At 2% cap, even a 50% pump on one asset adds only 1% to portfolio NAV. Not worth the complexity of monitoring and trading them.

**Recommendation:** Include Tier 5 in the momentum ranking but with a separate treatment:

```
# After ranking Tier 1-3 (top 8-12 selected):
# Rank Tier 5 separately (like meme coins)
# If any Tier 5 asset has 4h return > 10% AND is in top 3 of Tier 5 ranking:
#   → Allocate 1-2% NAV (below the 2% cap)
#   → Only in TREND_BULL regime
#   → 8% trailing stop (same as meme coins)
```

This is essentially the meme coin sub-pool logic applied to Tier 5 — small, bounded positions with convex optionality. Maximum downside per position: 2% x 8% stop = 0.16% of NAV. Maximum upside: unbounded (a 100% pump on a 2% position adds 2% to NAV).

**Cost:** ~20 lines of code extending the meme sub-pool pattern. No new architectural concepts.

**When excluding is better:** If the mock exchange has quality issues with obscure tokens (stale prices, erratic behavior), including them adds noise. But on a mock exchange streaming Binance prices, the data quality should be consistent across all assets.

---

## TRADEOFF FLAGS

---

### [DA-TRD-1] End-Game Final Hour — Selling to 0% vs. Tightening Stops

**The tradeoff:** v3.0's schedule ends with "< 1 hour → Target 100% cash/PAXG." This means selling all positions in the final hour, paying 0.05% commission on each exit.

The leaderboard calculation method is unknown (Section 19.1, Question 7). If it uses mark-to-market NAV (including unrealized P&L), selling doesn't change your NAV — it just moves gains from unrealized to realized while paying commission. The de-risking to 0% is only valuable if:

1. There's risk of a last-hour crash that damages final NAV, OR
2. The scoring uses realized P&L only (possible but unlikely)

Expected cost analysis for the final hour:
- **Cost of holding:** P(crash in last hour) x E(loss | crash) ≈ 5% x 3% = 0.15% expected loss
- **Cost of selling:** commission on all positions ≈ 0.05% x turnover ≈ 0.03-0.05% of NAV

These are roughly comparable. Selling to 0% is marginally protective but not a clear win.

**Recommendation:** In the final hour, **tighten trailing stops to 2%** instead of selling to 0%. This protects against a crash (any 2% drop triggers exit) while maintaining exposure to any last-hour rally. Only sell everything in the final 15 minutes if required by competition rules.

**When selling is better:** If the competition requires all positions closed at end. Check the problem statement Monday.

---

### [DA-TRD-2] 60-Minute Rebalance May Be Too Slow for Momentum Signal Decay

**The tradeoff:** v3.0 starts at 60-minute rebalancing, with an option to move to 30 minutes "if signal decay analysis justifies it." The commission math ($2,400 over 10 days = 0.24% NAV at 60-min cadence) is manageable.

But cross-sectional momentum in crypto has well-documented short-horizon signal decay. A 4h return momentum signal may have its strongest predictive power in the next 1-2 hours, decaying by hour 3-4. At 60-minute rebalancing, you capture 1 hour of the signal's peak predictive period. At 30-minute rebalancing, you capture a 30-minute window closer to the signal's peak.

The question is whether the incremental alpha from 30-min rebalancing exceeds the incremental commission:
- Going from 60-min to 30-min doubles the rebalance events (24 → 48 per day)
- But doesn't double the number of trades — many rebalances will produce zero trades if rankings haven't changed
- Incremental commission: likely +50% (not +100%) due to unchanged positions
- Incremental alpha: hard to estimate without backtesting

**Recommendation:** v3.0's approach (start at 60, tune to 30 if justified) is correct. **But add the monitoring to make the tuning decision:** track the correlation between the momentum score at rebalance T and the 30-minute forward return. If this correlation is significantly higher at T+30min than at T+60min, the signal decays fast and 30-min rebalancing is justified.

This monitoring requires zero additional infrastructure — just log the momentum scores and returns, compute the correlation hourly.

---

### [DA-TRD-3] ML Signal Deferred to Phase 3 — Correct for This Timeline

**Problem type:** This is correct. No challenge needed.

v3.0 correctly defers the ML signal to Phase 3 (Days 3-5). The reasoning holds:

1. Cross-sectional momentum requires no training and can't overfit — it's the right Phase 1/2 signal
2. LightGBM as a 0.5x-1.3x size multiplier bounds the damage from a bad model
3. On a t3.medium, ML training during rebalance could compete with the main loop for CPU
4. The model has limited training data and limited validation time in a 10-day window

**The strongest counter-argument:** ML might identify non-linear feature interactions (e.g., "high momentum + low volatility + breadth recovering" predicts 4h returns better than momentum alone) that the linear ranking misses. This is plausible but unverifiable before deployment. The multiplier design captures this upside if it exists while bounding the downside.

**Verdict: ACCEPT. Phase 3 is the right timing.**

---

### [DA-TRD-4] TRUMP Position Size Rules May Be Over-Engineered

**The tradeoff:** v3.0 gives TRUMP special treatment: 2% cap, 10% trailing stop, must close >1% NAV before end of trading day, excluded from meme pool in non-bull regimes.

TRUMP is volatile due to political event risk, but on a mock exchange, its price behavior is identical to any other volatile small-cap: it follows Binance's real TRUMP/USD price. The "close before end of day" rule implies TRUMP has overnight gap risk — but on a 24/7 crypto market, there is no overnight. The price streams continuously.

**Recommendation:** Simplify TRUMP rules:
- Keep the 2% cap (appropriate given volatility)
- Keep the 10% trailing stop (correctly wider than standard)
- **Remove the "close >1% before end of day" rule** — there is no "end of day" in a 24/7 crypto market. The trailing stop provides continuous protection.
- Keep the exclusion from meme pool in non-bull regimes (correct — TRUMP in TREND_BEAR is pure liability)

**Scoring impact:** Removing the daily close rule eliminates one forced sell per day (saving commission) and one forced re-buy (saving commission again). Over 10 days, this saves ~0.1% of NAV if TRUMP is held frequently.

---

## STRENGTHS

These design decisions are genuinely sound. The strongest counter-argument for each has been considered and does not hold.

---

### [DA-OK-1] Single Python Process Architecture

v3.0's single-process, no-external-services design is the single best architectural decision in the document. On a t3.medium with 4GB RAM, every external service is a crash vector, a memory competitor, and a configuration liability. The estimated ~200MB baseline / ~400MB peak (with ML) leaves 3.6GB of headroom — more than enough for NumPy arrays, Python overhead, and occasional peak allocations.

The RAM estimates are conservative and validated:
- Price ring buffer: 56 x 1440 x 40 bytes = 3.2MB
- Feature arrays: 50 x 56 x 1440 x 8 bytes = 32MB
- Trade log, config, misc: <5MB

**Counter-argument considered:** "A database provides crash recovery that in-memory state doesn't." This is addressed by the hourly Parquet snapshots and the JSON trade log. On restart, the bot reloads the last snapshot + replays the trade log. This is sufficient for a 10-day competition where crashes are rare and recovery within minutes is acceptable.

---

### [DA-OK-2] PAXG as Treynor Instrument

The decision to hold PAXG as a dedicated Treynor optimization tool remains correct. Sizing it from the cash buffer (not competing with crypto allocations) is the right capital allocation treatment.

**Key robustness property:** PAXG has near-zero correlation to all crypto assets regardless of which benchmark is used for Treynor. Whether the benchmark is BTC, equal-weight crypto index, or even an external benchmark, PAXG reduces portfolio beta. This makes the PAXG allocation robust to the unknown Treynor benchmark — the sizing might need adjustment, but the concept holds under all scenarios.

---

### [DA-OK-3] Meme Coin Sub-Pool Isolation

Ranking meme coins against each other (not cross-ranking with Tier 1-3) is architecturally sound. The separate pool with 3% per-asset cap creates bounded exposure with convex optionality. Maximum loss per meme position: 3% x 8% trailing stop = 0.24% of NAV. The TREND_BULL-only restriction is correct.

---

### [DA-OK-4] Execution Priority Queue

Risk exits → reductions → new entries → adjustments. This prevents the failure mode where new entries fill the order queue while trailing stop exits wait. In a Calmar-scored competition, a single unexecuted risk exit causes more damage than all the alpha from new entries.

---

### [DA-OK-5] Tier-Differentiated Trailing Stops

6% for Tier 1-3, 8% for Tier 4, 10% for TRUMP. Correctly matches stop distance to asset-specific volatility. A uniform stop would either cause premature exits on volatile assets or provide insufficient protection on majors.

---

### [DA-OK-6] Asymmetric Regime Transitions

Immediate downgrades, gradual upgrades. The cost asymmetry is correctly identified and correctly handled. This is one of the few design decisions that has no meaningful counter-argument.

---

### [DA-OK-7] Rule-Based Regime Detection (Replacing HMM + GARCH)

The v3.0 rule-based classifier is transparent, debuggable, requires no training, and uses inputs already computed in the feature pipeline. The four-input design (BTC trend, BTC vol percentile, altcoin breadth, contagion proxy) captures the essential dimensions of market state without the opacity of HMM hidden states or the fragility of GARCH parameter estimation.

**Counter-argument considered:** "HMM might capture regime transitions that simple rules miss." True in theory — but HMM requires `hmmlearn`, 90-day pre-training, and produces opaque state transitions that are difficult to debug on a live bot. On a 10-day competition timeline with a t3.medium, the rule-based system is strictly dominant.

---

### [DA-OK-8] Removal of the 48h Paper-Trading Gate

Dropping the validation gate for ML retraining is correct. The IC monitoring provides adequate safety. The ML signal's bounded multiplier range (0.5x-1.3x) means even a pathological retrain cannot cause catastrophic damage. A simple output distribution sanity check (does the new model's output look similar to the old model's?) is a sufficient guard.

---

## OPEN QUESTIONS

Ordered by priority — questions that change the most architecture are listed first.

---

### [DA-Q-1] Does the Limit Order at Current Price Fill Immediately? (PRIORITY: HIGHEST)

If yes: the execution engine simplifies dramatically. Every order becomes a limit order at current price — guaranteed immediate fill at 0.05% commission. The timeout, resubmission, and escalation logic in Layer 7 becomes near-trivial. Risk exits are instant.

If no: the full timeout/resubmission logic is needed. Risk exits may be delayed. The execution engine is 3x more complex.

**Test procedure:** Place a limit buy at exact current price. Check fill status within 1 second. If filled: confirmed. If not: test at current price + 0.01% (slightly above) to check if that fills.

---

### [DA-Q-2] Does the Batch Endpoint Return All 56 Prices in One Call? (PRIORITY: HIGHEST)

Pranesh's statement about exchange info suggests yes — but this describes the exchange info endpoint, not necessarily the ticker price endpoint. Both must be tested.

If batch works: 1 call/min for all prices, leaving 29-59 calls for execution. Architecture is viable.
If batch doesn't work: need 56 calls for prices (2 minutes at 30/min). Must reduce watchlist to ~20 assets, fundamentally changing the cross-sectional breadth of the momentum signal.

**Test procedure:** Call exchange info endpoint with no currency filter. Call ticker endpoint with no currency filter. Compare: do both return all assets? What fields?

---

### [DA-Q-3] What Data Fields Does the Price Endpoint Return? (PRIORITY: HIGH)

If OHLCV: full feature set available from day 1.
If last price only: must construct bars from 1-min snapshots. No volume data. ~25% of features eliminated. v3.0 handles this correctly (volume features are conditional).

**Test procedure:** Call ticker endpoint. Print the full response. Check for: open, high, low, close, volume fields.

---

### [DA-Q-4] What Is the Actual Rate Limit? (PRIORITY: HIGH)

At 30/min: tight. Full rebalance of 12 positions needs ~12-24 API calls (place + verify). After 1 price call, that leaves 5-17 calls of headroom.
At 60/min: comfortable. 35-47 calls of headroom.

**Test procedure:** Fire 60 rapid calls in 60 seconds. Count how many succeed. Note any throttle errors or delays.

---

### [DA-Q-5] What Is the Treynor Benchmark? (PRIORITY: MEDIUM — wait for Monday)

Determines PAXG sizing and beta targeting calibration. v3.0's dual-benchmark design (BTC + equal-weight) is robust enough until this is confirmed.

---

### [DA-Q-6] How Is the Leaderboard Return Calculated? (PRIORITY: MEDIUM)

Mark-to-market NAV vs. realized P&L. Affects end-game de-risking logic. If mark-to-market: selling in the last hour doesn't change NAV, just costs commission. If realized only: selling crystallizes gains (beneficial).

---

### [DA-Q-7] Is the First Currency "I/USD" Actually SOL/USD or Something Else? (PRIORITY: LOW)

The trading_currencies.md list starts with a truncated "I/USD". v3.0 acknowledges this. Test empirically — the exchange info endpoint will return the full list.

Note: SUI/USD already appears in the list, so "I/USD" is not SUI. Candidates: AI/USD, PI/USD, or a genuinely unknown token. If it's a major asset (unlikely given the truncation suggests it's short), it needs tier classification.

---

### [DA-Q-8] Are There More Than 56 Assets on the Platform? (PRIORITY: LOW)

Meeting says 66. List has 56. Test with the first API call. If extras exist, classify and add to universe.

---

### [DA-Q-9] What Exactly Are the Scoring Ratios? Three or Four? (PRIORITY: MEDIUM — wait for Monday)

Edward mentions three ratios (Treynor, Sharpe, Calmar). Meeting summary lists four (adding Sortino). The exact scoring formula and ratio weights determine how to balance the optimization.

---

## INTERACTIONS BETWEEN RECOMMENDATIONS

The recommendations above are not independent. Key interactions:

| Recommendation A | Recommendation B | Interaction |
|---|---|---|
| [DA-GAP-3] Replace P&L governor with stop tightening | [DA-TRD-1] End-game tighten stops instead of selling to 0% | Both modify trailing stop behavior. In the final hours with the P&L governor active, stops could be extremely tight (2% from end-game + 40% tighter from governor = 1.2%). Ensure the minimum stop distance never goes below 2% to avoid noise-triggered exits. |
| [DA-CRIT-3] Adaptive exposure adjustment | [DA-GAP-3] Stop tightening replaces exposure cutting | If the adaptive adjustment raises the exposure floor and the governor tightens stops simultaneously, you get high exposure with tight stops — which is appropriate (aggressive but protected). No conflict. |
| [DA-GAP-2] Phase 1 vol guard | [DA-CRIT-1] API must be tested first | The vol guard needs BTC historical vol data to set the threshold. This can be precomputed from Binance data before competition starts. No dependency on API testing. |
| [DA-GAP-5] Tier 5 sub-pool | [DA-CRIT-4] Universe count might be >56 | If extra assets exist, they likely fall into Tier 5. The sub-pool logic handles any number of assets. No conflict. |

---

## PRIORITY ACTION PLAN

Ordered by dependency and urgency. Time estimates assume the team can start immediately.

### Block 1 — API Testing (Hours 0-2, before anything else)

Everything else is blocked on these answers.

| # | Task | Time | Answers |
|---|---|---|---|
| 1a | Call exchange info + ticker endpoints with no filter. Document response fields and asset count. | 15 min | [DA-Q-2], [DA-Q-3], [DA-Q-8] |
| 1b | Place limit order at current price. Check if fills immediately. | 10 min | [DA-Q-1] |
| 1c | Fire 60 rapid API calls. Measure throttle behavior. | 10 min | [DA-Q-4] |
| 1d | Document all findings. Update `config.yaml` with confirmed values. | 15 min | — |

### Block 2 — Phase 1 MVA Implementation (Hours 2-8)

Build and deploy the minimum viable bot.

| # | Task | Time | Notes |
|---|---|---|---|
| 2a | Data ingestion: batch price fetch + ring buffer | 2h | If batch doesn't work, implement fallback (20-asset watchlist) |
| 2b | Momentum ranking: composite score, top 8-12 selection | 1.5h | Include Tier 1-3 only for Phase 1 |
| 2c | Equal-weight sizing with tier caps | 30min | |
| 2d | Trailing stops (tier-differentiated) + drawdown circuit breaker | 1h | |
| 2e | Limit order execution with priority queue | 1h | If limit-at-current-price fills immediately, simplify the timeout logic |
| 2f | **Vol guard (5-line addition)** | 10min | See [DA-GAP-2] |
| 2g | Config.yaml, JSON logging, crash recovery (hourly Parquet snapshot) | 1h | |

### Block 3 — Deploy and Monitor (Hour 8-10)

| # | Task | Time | Notes |
|---|---|---|---|
| 3a | Deploy to EC2 via Session Manager | 30min | |
| 3b | Verify first rebalance cycle completes without error | 30min | Watch the JSON log |
| 3c | Verify crash recovery: kill process, restart, check state reconstruction | 30min | |

### Block 4 — Phase 2 (Days 1-3, while bot is running)

| # | Task | Priority |
|---|---|---|
| 4a | Rule-based regime detection | High |
| 4b | Regime-conditional deployment targets | High |
| 4c | Vol-adjusted position sizing | Medium |
| 4d | PAXG allocation logic | Medium |
| 4e | Trend filter (as penalty, not override — see [DA-GAP-1]) | Medium |
| 4f | Stop-tightening P&L governor (see [DA-GAP-3]) | Medium |
| 4g | Contagion circuit breaker | Medium |
| 4h | Meme coin sub-pool | Low |
| 4i | End-game de-risking schedule | Low (doesn't matter until T-48h) |

### Block 5 — Phase 3 (Days 3-5, only if Phase 2 is stable)

| # | Task | Priority |
|---|---|---|
| 5a | LightGBM pre-trained model loading + inference | High |
| 5b | ML multiplier integration into sizing | High |
| 5c | BTC beta targeting | Medium |
| 5d | IC monitoring + auto-halt | Medium |
| 5e | Tier 5 sub-pool (see [DA-GAP-5]) | Low |
| 5f | Adaptive exposure adjustment (see [DA-CRIT-3]) | Low |

### Block 6 — Monday Problem Statement (March 16)

When the problem statement arrives, immediately check:

1. What ratios are scored? Three or four? Weighted how?
2. What is the Treynor benchmark?
3. Is leaderboard return mark-to-market or realized?
4. Any rules about position closure at competition end?
5. Any constraints not mentioned in the meeting?

Update `config.yaml` accordingly. All affected parameters are already configurable.

---

## META-OBSERVATIONS ON THIS REVIEW

**What this review gets right:** v3.0 is a dramatically better architecture than v2.0. The simplification to a single Python process, rule-based regime detection, phased delivery, and removal of non-existent data sources are all correct decisions. The remaining issues are refinements, not rewrites.

**What this review may get wrong:** The adaptive exposure adjustment [DA-CRIT-3] and the trend-filter-as-penalty [DA-GAP-1] are theoretically sound but untested. If backtesting on historical Binance data shows that the fixed exposure floors and the trend override outperform the alternatives, defer to the backtest evidence.

**The single most important action:** Test the API. Today. Before writing anything else. The answers to [DA-Q-1] through [DA-Q-4] determine whether the architecture as designed is feasible.

---

*Document version: 2.0 (Devil's Architect assessment of requirements_v3.md)*
*Previous version: 1.0 (review of requirements v2.0, dated 2026-03-15)*
*Changes: Updated to review v3.0. Acknowledged resolved items. Added remaining critical issues, gaps, and tradeoffs with concrete alternatives. Added interaction analysis. Added time-budgeted priority action plan.*
