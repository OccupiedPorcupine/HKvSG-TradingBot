# SGxHK Web3 Coin Trading Hackathon - Meeting Summary

## What to Build

- A **fully autonomous trading bot** (or AI agent) that trades on the Roost2 simulated crypto exchange platform
- The bot interfaces with Roost2 Exchange REST APIs to:
  - Fetch real-time pricing data (sourced from Binance)
  - Fetch portfolio/balance data
  - Execute buy/sell orders
- **66 cryptocurrencies** available to trade
- Starting portfolio: **$1,000,000 USD (virtual)**
- Strategies are **open-ended** - any approach is allowed as long as it's autonomous

## Rules and Constraints

- **Spot trading only** - no leverage, no shorting, no derivatives/options/perps
- **No arbitrage** possible (single platform, not cross-exchange)
- **Directional/discretionary strategies only** (not market-making or arbitrage)
- **All trades must be autonomous** - zero manual API calls allowed. If caught, team is disqualified from finalist selection
- Bot must run for **at least 8-10 out of 10 trading days** actively making trades
- **Commission fees**: 0.1% for market orders, 0.05% (5 bps) for limit orders
- **No slippage, no market impact** - orders are always filled at the stated price (mock exchange)
- Market orders execute immediately; limit orders execute when price is hit
- **API rate limit**: ~30-60 calls per minute
- Can update/iterate bot strategy during competition, but **every change must be committed to the repo**
- **Open-source repo required** - submit for code/strategy review and validation
- AI models, news sentiment scraping, external data APIs are all allowed

## Two Sets of API Keys (Do NOT Confuse)

1. **Testing keys** - use these before and during development to test/debug API interactions
2. **Competition keys** - use ONLY when the competition starts; every call results in actual trades that affect your leaderboard standing

## Infrastructure

- Each team gets an **AWS account** (expenses covered by organizers)
- **EC2 instance**: `t3.medium`, one instance only (extras will be terminated)
- **Region**: Sydney (ap-southeast-2)
- Use the pre-built **launch template** - don't configure manually
- Connect via **Session Manager** (browser-based, no SSH)
- Deploy bot on AWS so it runs **24/7 on the cloud** (don't run locally)
- AWS setup: reset password from email > set up MFA > access portal > hackathon permission set > EC2

## Competition Format

### Round 1 - City Qualifier (10 days)
- Two parallel competitions: Hong Kong track and Singapore track
- All teams compete within their city
- **Top 8 teams from each city** advance to finals (16 total)
- Selection process: Top 20 from each city undergo deeper evaluation on metrics + code/strategy review

### Round 2 - Head-to-Head Finals (10 days)
- 16 teams (8 HK + 8 SG) compete in a single competition
- Individual team performance matters
- **City aggregate**: average portfolio return of each city's 8 teams determines the winning city
- Winning city's teams all receive cash prizes + bragging rights

### Final Presentation (Physical, Apr 17-21)
- 8-minute presentation + 4-minute Q&A per finalist team
- **Presentation deck**: max 12 slides
- Judges: traders and portfolio managers from sponsor firms (LIM Trading, Optiva, Qubis)
- 3 winning teams selected from each city

## How Submissions Are Judged

### Performance Metrics (Data-Driven Awards)
- **Portfolio returns** (absolute return on leaderboard)
- **Risk-adjusted metrics**:
  - Sharpe Ratio
  - Sortino Ratio
  - Calmar (Karma) Ratio
  - Treynor (TINA) Ratio
- **Composite score** combining these ratios

### Code & Strategy Review
- Commit history must be clean and legitimate
- Strategy changes tracked via git commits
- Code reviewed for legitimacy

### Best Finance Presentation (Separate Award)
- Quality of the physical presentation and strategy explanation

## Award Categories (22 prize slots total)

| Category | Slots | Based On |
|---|---|---|
| Best Finance Presentation | 6 | Presentation quality at finals |
| Pure Data-Driven Performance | 8 | Portfolio returns + risk-adjusted metrics |
| Winning City Award | 8 | Aggregate city performance |

- **Prize pool**: HKD $62,000 / SGD $10,000
- A single team can win **multiple awards**

## How to Win

1. **Maximize absolute returns** - this is the primary leaderboard ranking
2. **Optimize risk-adjusted returns** - Sharpe, Sortino, Calmar ratios matter for composite score awards
3. **Run the bot consistently** - must be active 8-10 out of 10 days minimum
4. **Clean code and clear commit history** - required for finalist evaluation
5. **Prepare a strong presentation** - 12-slide deck covering strategy, results, and rationale
6. **Let strategies play out** - don't change too frequently, but iterate with clear commits
7. **Use all tools available** - AI models, sentiment analysis, external data are all fair game

## Key Dates

| Date | Milestone |
|---|---|
| March 16 | Monday (competition start) | Receive all resources: API guide, keys, AWS setup, problem statement, FAQ |
| March 21-31 | 10-day window (Round 1) | Deploy and run bot, iterate strategy |
| March 28 | Submit repo link (required for finalist eligibility) |
| April 2 | Finalist announcement |
| April 4 | Round 2 (finals) begins |
| April 17 | Submit presentation deck (finalists) |
| April 17-21 | Physical final presentations (SG + HK) |

## Resources Provided

- Slide deck + meeting recording
- Problem statement document
- API guide with sample Python code
- Data resource pack
- FAQ document
- AWS setup guide
- Two sets of API keys + secrets
- WhatsApp group for support and announcements

## API Endpoints

| Endpoint | Purpose |
|---|---|
| Server Time | Verify API connectivity |
| Exchange Info | Get coin info and prices |
| Market Ticker | Real-time price of any coin |
| Balance | Check portfolio balance and positions |
| Place Order | Buy/sell currencies (market or limit orders) |

## Sponsors (Career Opportunities)

- **LIM Trading** (Platinum) - global HFT/quant research firm
- **Optiva** - co-sponsor
- **Gondal Capital** - co-sponsor
- **Qubis Systemic Strategies** - co-sponsor
- Winning teams get: assessment fast-tracks, company visits, networking dinners, swag
- All submitted resumes shared with sponsors
