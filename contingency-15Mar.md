# Phase 0 Pre-Competition Findings — 15 March 2026

Pre-competition API testing against `https://mock-api.roostoo.com`.
These values may change when the competition starts on **22 March 2026**.

**Re-run `python scripts/api_test.py` on competition day to get final values.**

---

## Discrepancies Between Spec and Empirical Findings

| Parameter | Spec Value | Empirical (15 Mar) | Risk |
|---|---|---|---|
| Starting capital | $1,000,000 | $50,000 (`InitialWallet: {USD: 50000}`) | $50K may be a test/pre-competition default. If real: all absolute thresholds scale down 20x (e.g. min trade = $100 not $2,000). |
| Rate limit | 30–60 calls/min | 50 calls/**second** (~3,000/min) | Likely relaxed because no load. Expect tightening to 30-60/min when 300+ teams connect simultaneously. |
| Limit fill mechanics | Instant fill assumed | **Untested** (no API credentials) | Must test with credentials before competition. Place small limit buy at LastPrice, verify Status == "FILLED". |

## Action Items for Competition Day (22 March)

1. **Re-run Phase 0**: `python scripts/api_test.py` with credentials set
2. **Check `InitialWallet`** in exchangeInfo — update `competition.starting_capital_usd`
3. **Test limit order fill** — confirm `limit_fills_immediately: true`
4. **Re-test rate limit** under competition load — update `rate_limit_calls_per_min`
5. **Compare pair count** — currently 67 pairs on API, may change

## Confirmed Values (Stable)

These are structural and unlikely to change:

| Finding | Value |
|---|---|
| Base URL | `https://mock-api.roostoo.com` |
| Batch pricing | YES — `/v3/ticker` with no `pair` param returns all tickers in one call |
| Volume data | YES — `CoinTradeValue` (coin units) + `UnitTradeValue` (USD value) |
| Timestamp format | Both seconds and milliseconds accepted |
| Content-Type | `text/plain` (not `application/json`) — must parse via `text()` then `json.loads()` |
| Bid-ask spread | ~$0.01 on BTC — effectively zero on mock exchange |
| Universe coverage | All 54 expected pairs present. 13 extra pairs available (AVAX, SOL, XRP, TAO, etc.) |
| All MiniOrder | 1 (minimum 1 unit of any coin) |
| Auth signing | HMAC SHA256, params sorted alphabetically, `RST-API-KEY` + `MSG-SIGNATURE` headers |

## Ticker Response Fields

```
MaxBid          — highest bid price
MinAsk          — lowest ask price
LastPrice       — last traded price
Change          — 24h change as decimal (0.0109 = +1.09%)
CoinTradeValue  — 24h volume in coin units
UnitTradeValue  — 24h volume in USD
```

## ExchangeInfo Pair Fields

```
Coin             — asset symbol (e.g. "BTC")
CoinFullName     — full name (e.g. "Bitcoin")
Unit             — quote currency ("USD")
CanTrade         — bool
PricePrecision   — decimal places for price (BTC=2, SHIB=8)
AmountPrecision  — decimal places for quantity (BTC=5, DOGE=0)
MiniOrder        — minimum order quantity (all are 1)
```

## Rate Limit Error Response

```
HTTP 429
Body: "reach rate limit, keep slower than 50 requests pre second"
```

No `Retry-After` header returned. No rate limit info in response headers.
