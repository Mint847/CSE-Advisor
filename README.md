# CSE Advisor

A personal **analysis tool** for the Colombo Stock Exchange (CSE). You choose 10–20 stocks. After every close it scans each one for chart patterns, liquidity, levels and announcements, and shows what three rule-based engines found, **with the reasoning behind every number**. Patterns are drawn on a live chart, and an optional Telegram bot sends a daily digest.

It runs on your own computer with free data. There is no login, no cloud service and no trading: it never places an order.

> ## ⚠️ Not financial advice
> **CSE Advisor is a software tool that organises public market data and applies fixed technical-analysis rules to it. It is not financial, investment, legal or tax advice, and it is not a recommendation to buy, sell or hold any security.** The author is not a licensed financial adviser, and giving personalised investment advice may require a licence in your country. Words such as *BUY*, *WATCH*, *AVOID*, *entry*, *stop* and *target* are labels the rules produce from price history, not instructions.
>
> - **No edge is claimed.** The project's own 14-year tests found that most patterns do **not** predict price beyond the stock's existing trend, and that after CSE trading costs (about 2.6% per round trip) the average trade loses money.
> - You can lose all the money you invest. Past patterns do not predict future prices.
> - Data comes from unofficial endpoints (cse.lk, TradingView) that can be wrong, late or disappear. Check prices with your broker.
> - Make your own decisions or ask a licensed adviser. The software is provided "as is", without warranty, and the author accepts no liability for losses.
>
> Not affiliated with or endorsed by the Colombo Stock Exchange, TradingView or any broker.

## Architecture

```
 cse.lk JSON endpoints ─┐                                   ┌─ Classic (CLS)
 TradingView bars ──────┼─► SQLite ─► analysis/ ─► engines ─┼─ Levels  (LVL) ─► recommendations ─► FastAPI ─► React UI
 CSE announcements/PDFs ┘  (prices,   indicators,           └─ SMC            (verdict, plan,       │         (charts,
                            news,     patterns, levels,                        reasons)             │          watchlist)
                            figures)  liquidity, SMC                                                └─► Telegram digest / alerts
```

- **Data** (`cse_client.py`, `tv_client.py`, `ingest.py`, `intraday.py`): daily history back to 2012, intraday bars from 1 minute to 4 hours, announcements, and financial statements read from the PDFs by rules (no LLM). Prices are adjusted for rights issues and splits.
- **Analysis** (`analysis/`): indicators, chart and candlestick patterns, Fibonacci and confluence levels, liquidity, smart-money structure, corporate actions, catalysts.
- **Engines** (`strategy/`): three independent rule sets, each giving a verdict, score, trade plan (entry, stop, targets after fees) and a plain-language explanation. The UI shows whether they agree.
- **Jobs** (`jobs.py`): the end-of-day run at 15:30 Sri Lanka time on weekdays (analysis, forward-test journal, alerts, a nightly database backup), plus intraday checks while the app is open.
- **Stock selection** (`screener.py`, `analysis/selection.py`): after the close, ranks every liquid CSE stock on the one selection rule that held up in the tests (below). Shown only.
- **Forward-test journal** (`journal.py`): each evening's calls are written down once and scored later, as an honest check on the back-tests.
- **UI** (`frontend/`): React, TypeScript and lightweight-charts, with a terminal-style look and a command line (`JKH GP`, `WL`, `HELP`).

### The three engines

| | **CLS**: Classic | **LVL**: Levels | **SMC**: Smart money |
|---|---|---|---|
| Idea | chart and candlestick patterns plus a 5-factor score | Fibonacci, confluence buy / sell zones, trendlines, level entries | order blocks, fair value gaps, premium / discount, liquidity sweeps, BOS / CHoCH |
| Score | trend & patterns, liquidity, fundamentals, news, market | 50 ± points for location, momentum, volume, extension, reward:risk | 50 ± points for weekly bias, daily structure, discount, order block / gap, sweeps |
| BUY needs | a 7-item checklist | a 9-item checklist | a 9-item checklist |

A BUY in any engine requires, among other checks, a triggered setup, reward:risk of at least 2 **after** about 1.3% costs per side, liquidity grade C or better, and no hard red flags (going concern, suspension, qualified audit). All thresholds are in `backend/config/settings.yaml`. The engines are long-only, since the CSE has no short selling.

The engines were previously called *Original* and *Analyst*; their internal ids (`original`, `analyst`) are unchanged.

## Screenshots

Illustrations of the tool's output on 4 Nov 2026 with seven example stocks, not suggestions.

**Watchlist:** every stock with the three engines' verdicts side by side, trend, liquidity and the next key level.

![Watchlist](docs/screenshots/01-watchlist.png)

**Stock page:** engine panels, the list of everything drawn on the chart, and the chart with patterns, zones and plan lines. Timeframes 1M to 1W; candles, line or zig-zag; a `Show CLS | LVL | SMC` toggle to see one engine's drawings at a time.

![Stock page](docs/screenshots/02-stock-overview.png)

**Comparing the engines:** each engine's setup, prices and reasons for and against, in plain language. When they disagree, the page says so.

![Compare](docs/screenshots/14-recommendation-compare.png)

## What the tests found

The repository includes the tests that check whether the signals work: a mirror-image test for every pattern detector, a 14-year event study of 53,292 pattern events across 43 stocks, and a study of the entry and exit plans.

- Of 46 pattern types, **1** showed a stable edge over the stock's own trend (a rising wedge breaking up), 3 were weak and **42 had none**.
- Entering at a plan's buy zone and exiting at its stop or first target did no better than the same stop and target on random days. With about 2.6% round-trip cost the average trade lost about 2.3%.
- Each pattern's explanation in the app shows this measured track record next to the pattern.

A later study (`factor_study.py`, 42 stocks, 14 years, after costs) looked for what does help:

- The only combination that held up in both halves of the history was a stock in an **uptrend** (close above its 50-day average, which is above its 200-day), **within 10% of its 52-week high**, and in the **top 40% by 120-day return**. It beat the average uptrending stock by roughly 1.5 to 3.5 points over 40 to 90 sessions. It was chosen after looking at the data, so it is *tested, not proven*; the journal keeps checking it on new data.
- Entry style (resting limit, next open, waiting for a bounce) made no difference, and the engines' BUY calls were no better than WATCH (Classic BUYs sat a median 21% below the 52-week high, still falling).
- Staying out of weak markets did not improve results, and candlestick patterns pooled showed no edge (they are hidden by default).

Use the tool to study charts and keep a consistent checklist, not as a source of trades. The studies are rerunnable (reports go to `backend/data/backtests/`):

```
python -m cse_advisor.pattern_study       # does a pattern predict anything?
python -m cse_advisor.backtest --calls    # record every call with its plan
python -m cse_advisor.plan_study          # is the entry / exit plan sound?
python -m cse_advisor.factor_study        # which stocks, entries and exits add return after costs?
```

## Run it

Requirements: Python 3.11+ and Node 20+.

```
start.bat
```

`start.bat` builds the web app the first time, starts the backend and opens <http://localhost:8000>. Keep the window open. Manual way:

```
cd backend
.venv\Scripts\python -m cse_advisor.main      # API + web app on :8000
cd ..\frontend && npm run dev                 # optional: hot-reload UI on :5173
```

The app listens on this PC only (no login). Do not expose it to a network you don't trust.

### Telegram (optional)

1. Message **@BotFather** in Telegram, run `/newbot` and copy the token.
2. Copy `backend/.env.example` to `backend/.env` and set `TELEGRAM_TOKEN=...`.
3. Send your bot a message, run `python -m cse_advisor.telegram_bot --whoami` and put the printed id in `TELEGRAM_CHAT_ID=`.
4. Restart the app and test with `python -m cse_advisor.telegram_bot --test`.

You get an evening digest, alerts for signal changes and announcements, and commands such as `/watchlist` and `/report JKH`. The bot only obeys your chat id.

## More screens

- **SCR (F5), candidates:** the ranking above for all liquid stocks (median daily turnover of at least LKR 2M), with a `+ADD` button for stocks not on your watchlist. First run reads every ordinary share once (about 15 minutes), then only the liquid ones each evening.
- **JRN (F6), journal:** every BUY and WATCH call with its plan, scored after 5, 20, 40 and 60 sessions with fees, next to the back-test numbers. Needs a few months and roughly 100 scored calls per group before it means anything.
- **Positions, "Consider trimming":** information only. It lists bearish patterns that the tests found to fall more often than comparable days, and a close below the 50-day average. The HOLD / TRIM / SELL review is unchanged.
- **Market banner** on the watchlist: ASPI against its 100- and 200-day averages and market breadth. A description, not a signal; no engine uses it.
- **Backups:** each evening the database is copied to `backend/data/backups/` (newest 14 kept). `install_autostart.ps1` starts the app when you log in to Windows.

## Data and limits

- Prices come from the JSON endpoints cse.lk itself uses, and intraday history from TradingView's chart feed. Both are **unofficial** and could change; all access lives in `cse_client.py` and `tv_client.py`. Requests are few and rate-limited.
- Daily history goes back to 2012 (TradingView bars are stitched onto the rights-adjusted series). Intraday depth varies by timeframe and by how often a stock trades.
- Financial statements are text PDFs read by rules and cross-checked; scanned ones need typing in.
- CSE costs (about 1.1–1.3% a side) exceed most 5M / 15M targets, so the engines mostly say WATCH or AVOID on small timeframes.
- The intraday engines have not been walk-forward tested.

## Tests

```
cd backend && .venv\Scripts\python -m pytest -q
```

Covers the pattern detectors (planted and mirrored patterns), trade-plan rules, the three engines and their independence, intraday bar building, statement parsing, catalysts, corporate-action maths, portfolio totals, and replay tests against known example setups.

## Disclaimer

CSE Advisor is educational software for personal analysis. It is **not** financial advice and the author is **not** a licensed financial adviser. Nothing it displays, sends or logs is an offer or solicitation to buy or sell any security. You are solely responsible for your decisions and for checking prices and company information with official sources and your broker. The software comes without warranty, and the author is not liable for any loss arising from its use.
