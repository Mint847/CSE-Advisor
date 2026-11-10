"""What became of every module of gs-quant 2.2.5 (shown in Quant Lab > "gs-quant coverage").

status:
  used           the module's own code is called (through cse_advisor.quant)
  recreated      the method needs Goldman's Marquee data; the same formula is computed here on our own data
  not_possible   it only runs on Goldman's servers or needs Marquee data that has no free equivalent
  not_applicable it does work offline but has nothing to do with CSE stocks (e.g. epidemic models)
  plumbing       internal machinery of gs-quant (sessions, JSON, generated types, tests), nothing to show
stage: where it appears (Q0 foundations, Q1 stock QNT tab, Q2 portfolio, Q3 strategy lab, Q4 lab extras), or None.
A test checks that every module in the installed gs-quant has an entry, so nothing is left out silently.
"""
from __future__ import annotations

from typing import Any

GS_QUANT_VERSION = "2.2.5"
MARQUEE = "needs a Goldman Sachs Marquee login (institutional clients only); its data may not be given to the public"

_E = dict  # each entry: {"module", "status", "what", "where", "stage", "why"}

ENTRIES: list[dict[str, Any]] = [
    # ---------------------------------------------------------------- timeseries (the maths library)
    _E(module="timeseries.econometrics", status="used", stage="Q0",
       what="returns, volatility, correlation, beta, max_drawdown and excess_returns_pure (the module also has prices, index, change, annualize, vol/corr swaps, get_ratio_pure, sharpe_ratio...)",
       calls=["ts.returns(", "ts.volatility(", "ts.correlation(", "ts.beta(", "ts.max_drawdown(", "excess_returns_pure("],
       where="QNT tab, PORT risk, Quant Lab results (Sharpe is gs-quant's get_ratio_pure formula, tested equal to its sharpe_ratio)",
       why="Sharpe uses the CBSL T-bill rate (gs-quant would look up a US/EU rate on Marquee) and the CSE's ~239 sessions a year instead of 252"),
    _E(module="timeseries.statistics", status="used", stage="Q1",
       what="exponential_std, zscores, percentiles and LinearRegression (the module also has rolling min/max/mean/std/cov, winsorize, RollingLinearRegression, generate_series...)",
       calls=["exponential_std(", "ts.zscores(", "ts.percentiles(", "LinearRegression("],
       where="QNT tab (recent-weighted volatility, price z-score, percentile of today's move, factor regression); the Quant Lab's dip rule is tested against zscores",
       why="SIRModel / SEIRModel in the same file are epidemic models: see models.epidemiology. generate_series makes a generic random walk unrelated to a stock's own volatility, so the lab's luck test reshuffles each stock's own days instead. winsorize and the rolling functions are not needed."),
    _E(module="timeseries.technicals", status="used", stage="Q1",
       what="trend and seasonally_adjusted (the module also has moving_average, bollinger_bands, relative_strength_index, macd, exponential_moving_average...)",
       calls=["ts.trend(", "ts.seasonally_adjusted("],
       where="QNT tab: price and its long-run trend with the yearly pattern removed. The Quant Lab's moving average, EMA and RSI are computed vectorised and tested equal to gs-quant's",
       why="the engines keep their own RSI / MACD / Bollinger (analysis/indicators.py), so no verdict changes"),
    _E(module="timeseries.algebra", status="plumbing", stage=None, what="arithmetic and logic on series, weighted_sum, geometrically_aggregate",
       where=None, why="not called directly: gs-quant's other functions use it inside"),
    _E(module="timeseries.analysis", status="used", stage="Q1", what="consecutive and smooth_spikes (the module also has smooth_outliers, diff, lag, compare, count...)",
       calls=["ts.consecutive(", "ts.smooth_spikes("],
       where="up/down streaks (QNT) and the price-spike check (Data health)", why=None),
    _E(module="timeseries.datetime", status="used", stage="Q1", what="bucketize (the module also has align, interpolate, union, append, date_range, day_count...)",
       calls=["ts.bucketize("],
       where="monthly and yearly return tables (QNT)", why="the sessions-until-event countdown (CAL) is plain weekday counting: the CSE's future holidays are not known. align_calendar needs Marquee holiday data; the CSE's own sessions are used instead"),
    _E(module="timeseries.backtesting", status="used", stage="Q2", what="basket_series (backtest_basket runs inside it)",
       calls=["ts.basket_series("],
       where="PORT 'what if you had held your lists' and the Quant Lab basket builder", why="the Basket class needs Marquee prices; our own series are passed instead"),
    _E(module="timeseries.event_study", status="used", stage="Q4", what="event_impact_analysis (finds shock days in one series and maps them onto another's calendar); frame_timeseries_around_events is the reference the lab's own framing is tested against",
       calls=["event_impact_analysis("],
       where="Quant Lab event studies (world shocks)", why="get_asset_events / get_country_events read Marquee event calendars; CSE announcements are used instead. gs-quant's default horizon '1d' misses every Monday move, so the lab passes horizon=1 (one observation)"),
    _E(module="timeseries._event_study", status="used", stage="Q4", what="CalendarAlignment and EventDirection (the event-study internals)",
       calls=["CalendarAlignment", "EventDirection"], where="Quant Lab event studies", why=None),
    _E(module="timeseries.helper", status="plumbing", stage=None, what="Window, Returns and other types, decorators", where=None, why=None),
    _E(module="timeseries.measure_registry", status="plumbing", stage=None, what="dispatches Marquee measures", where=None, why=MARQUEE),
    _E(module="timeseries.measures_helper", status="plumbing", stage=None, what="helpers for Marquee measures", where=None, why=MARQUEE),
    _E(module="timeseries.etf_classification", status="not_applicable", stage=None, what="FactSet ETF category list", where=None,
       why="the CSE has no ETFs"),
    _E(module="timeseries.measures", status="not_possible", stage=None,
       what="implied/realised vols, skew, correlations, forwards, commodities, FX forecasts, fundamentals, ESG, ratings (Marquee data)",
       where=None, why=MARQUEE + "; realised volatility and correlation are computed from our own prices instead (QNT)"),
    _E(module="timeseries.measures_rates", status="not_possible", stage=None, what="swap rates, swaption vols, policy-rate expectations", where=None,
       why=MARQUEE + "; the Sri Lankan T-bill rate comes from CBSL instead"),
    _E(module="timeseries.measures_fx_vol", status="not_possible", stage=None, what="FX option volatilities", where=None, why=MARQUEE),
    _E(module="timeseries.measures_xccy", status="not_possible", stage=None, what="cross-currency swap rates", where=None, why=MARQUEE),
    _E(module="timeseries.measures_inflation", status="not_possible", stage=None, what="inflation swaps", where=None, why=MARQUEE),
    _E(module="timeseries.measures_bonds", status="not_possible", stage=None, what="government bond yields, prices, durations", where=None, why=MARQUEE),
    _E(module="timeseries.measures_tba", status="not_possible", stage=None, what="US mortgage TBA spreads", where=None, why=MARQUEE),
    _E(module="timeseries.measures_countries", status="not_possible", stage=None, what="Financial Conditions Index", where=None, why=MARQUEE),
    _E(module="timeseries.measures_etf", status="not_possible", stage=None, what="ETF flows", where=None, why=MARQUEE),
    _E(module="timeseries.measures_factset", status="not_possible", stage=None, what="FactSet estimates, fundamentals, ratings", where=None,
       why=MARQUEE + "; the app reads CSE financial statements itself (DES)"),
    _E(module="timeseries.measures_cognitive_credit", status="not_possible", stage=None, what="credit fundamentals", where=None, why=MARQUEE),
    _E(module="timeseries.measures_risk_models", status="not_possible", stage=None, what="factor z-scores, covariances, returns from GS risk models", where=None,
       why=MARQUEE + "; a simple factor regression on our own data stands in (QNT, PORT)"),
    _E(module="timeseries.measures_portfolios", status="recreated", stage="Q2",
       what="Sharpe, Sortino, Calmar, information ratio (+bull/bear), tracking error (+bull/bear), Treynor, Jensen's alpha (+bull/bear), M2, alpha, beta, correlation, R2, capture ratio, hit rate, max drawdown, drawdown length, recovery period, standard deviation, downside risk, semi-variance, skewness, kurtosis, realised VaR",
       where="QNT tab and PORT risk", why="gs-quant reads these from Marquee portfolio reports; the same formulas are computed here from our prices"),
    _E(module="timeseries.measures_reports", status="recreated", stage="Q2",
       what="the same figures for Marquee reports, plus factor exposure / P&L / proportion of risk and historical-simulation P&L",
       where="PORT risk (risk contribution per holding, historical VaR, stress tests)", why="computed from our prices; factor figures from our own regression"),
    _E(module="timeseries.tca", status="not_possible", stage=None, what="intraday covariance for trading-cost analysis", where=None,
       why=MARQUEE + "; the app's liquidity tab (LIQ) covers trading cost on the CSE"),
    # ---------------------------------------------------------------- backtesting
    _E(module="backtests.predefined_asset_engine", status="used", stage="Q3", what="the event-driven engine that runs on our own prices", calls=["PredefinedAssetEngine("],
       where="Quant Lab strategy backtester",
       why="driven by OrdersGeneratorTrigger rules deciding after the close and trading at the next open; times kept in UTC (a Colombo clock would shift them)"),
    _E(module="backtests.data_sources", status="used", stage="Q3", what="GenericDataSource, DataManager, MissingDataStrategy", calls=["GenericDataSource(", "DataManager("], where="Quant Lab", why="GsDataSource needs Marquee"),
    _E(module="backtests.data_handler", status="used", stage="Q3", what="Clock and DataHandler: blocks a strategy from reading future prices", calls=["PredefinedAssetEngine("], where="Quant Lab (inside the engine)", why=None),
    _E(module="backtests.execution_engine", status="used", stage="Q3", what="SimulatedExecutionEngine (fills orders at their time)", calls=["PredefinedAssetEngine("], where="Quant Lab (inside the engine)", why=None),
    _E(module="backtests.order", status="used", stage="Q3", calls=["OrderAtMarket(", "OrderCost("], what="OrderAtMarket (the trades) and OrderCost (fees, slippage and T-bill interest booked as cash movements)", where="Quant Lab",
       why="the other order types (TWAP, market-on-close) are not needed: every fill is at the next session's open"),
    _E(module="backtests.event", status="used", stage="Q3", what="market, order, fill and valuation events", calls=["PredefinedAssetEngine("], where="Quant Lab (inside the engine)", why=None),
    _E(module="backtests.backtest_objects", status="used", stage="Q3", calls=["bt.performance", "bt.trade_ledger("], what="PredefinedAssetBacktest (daily account value, holdings, trade ledger); fees and interest are passed in as OrderCost orders",
       where="Quant Lab", why="the engine adds no costs itself (CSE fees and slippage are ours, as OrderCost); its cash-accrual models need Marquee, so cash earns the CBSL T-bill rate through OrderCost"),
    _E(module="backtests.triggers", status="used", stage="Q3", calls=["OrdersGeneratorTrigger"],
       what="OrdersGeneratorTrigger is used (it decides after the close). The Periodic, Mkt, MeanReversion, Aggregate, Not and Date trigger ideas are re-created as our own rule tree (calendar, price and average conditions, z-score dips, ALL/ANY/NOT, announced dividend dates)",
       where="Quant Lab", why="our rules are computed for all days at once for speed; the moving average, z-score and RSI are tested against gs-quant's own functions, and a test shows no rule reads a later day. EventTrigger needs the Marquee event calendar (CSE announcement dates stand in); StrategyRiskTrigger needs GS pricing"),
    _E(module="backtests.actions", status="used", stage="Q3", what="the base Action (the trigger carries one); AddTradeAction is not needed because our order generator places the trades", calls=["Action()"], where="Quant Lab",
       why="HedgeAction, AddScaledTradeAction, RebalanceAction and the others need GS pricing (GenericEngine)"),
    _E(module="backtests.core", status="used", stage="Q3", what="ValuationMethod, TimeWindow, ValuationFixingType", calls=["ValuationMethod(", "TimeWindow("], where="Quant Lab", why=None),
    _E(module="backtests.strategy", status="used", stage="Q3", what="Strategy (initial portfolio + triggers)", calls=["Strategy(None"], where="Quant Lab", why=None),
    _E(module="backtests.backtest_utils", status="plumbing", stage=None, what="duration parsing, helpers", where=None, why=None),
    _E(module="backtests.action_handler", status="plumbing", stage=None, what="maps actions to their implementations", where=None, why=None),
    _E(module="backtests.backtest_engine", status="plumbing", stage=None, what="base engine class", where=None, why=None),
    _E(module="backtests.decorator", status="plumbing", stage=None, what="plotting decorator", where=None, why=None),
    _E(module="backtests.generic_engine", status="not_possible", stage=None, what="engine that prices instruments at every step", where=None, why=MARQUEE + " (GS pricing service)"),
    _E(module="backtests.generic_engine_action_impls", status="not_possible", stage=None, what="actions for the generic engine", where=None, why=MARQUEE),
    _E(module="backtests.equity_vol_engine", status="not_possible", stage=None, what="equity option strategies run on Goldman's servers", where=None,
       why=MARQUEE + "; the CSE has no listed options"),
    _E(module="backtests.strategy_systematic", status="not_possible", stage=None, what="systematic option strategies on Goldman's servers", where=None, why=MARQUEE),
    # ---------------------------------------------------------------- markets, models, datetime, api
    _E(module="markets.position_set", status="recreated", stage="Q4", what="position weights: equalise, redistribute (in-memory parts)",
       where="Quant Lab basket builder (equal, inverse-volatility and your own weights, computed directly)",
       why="the class is built around Marquee (resolve() and price() need it), and the weights needed here are a few lines of arithmetic"),
    _E(module="markets.position_set_utils", status="plumbing", stage=None, what="helpers for position sets", where=None, why=None),
    _E(module="markets.portfolio", status="not_applicable", stage=None, what="instrument portfolio container", where=None,
       why="pricing and saving need Marquee; the app's own positions (PORT) are used"),
    _E(module="markets.optimizer", status="not_possible", stage=None, what="Axioma portfolio optimiser on Goldman's servers", where=None,
       why=MARQUEE + "; and the app gives no allocation advice"),
    _E(module="markets.hedge", status="not_possible", stage=None, what="hedge calculator on Goldman's servers", where=None,
       why=MARQUEE + "; the CSE has no short selling or futures to hedge with"),
    _E(module="markets.factor", status="not_possible", stage=None, what="GS risk-model factors", where=None, why=MARQUEE + "; own factor regression instead (QNT, PORT)"),
    _E(module="markets.factor_analytics", status="not_possible", stage=None, what="factor analytics charts on Marquee data", where=None, why=MARQUEE),
    _E(module="markets.scenario", status="not_possible", stage=None, what="factor shock scenarios", where=None,
       why=MARQUEE + "; historical stress tests from ASPI history stand in (PORT)"),
    _E(module="markets.baskets", status="not_possible", stage=None, what="Marquee custom baskets", where=None, why=MARQUEE + "; Quant Lab basket builder instead"),
    _E(module="markets.index", status="not_possible", stage=None, what="Marquee indices", where=None, why=MARQUEE),
    _E(module="markets.indices_utils", status="plumbing", stage=None, what="index helpers", where=None, why=MARQUEE),
    _E(module="markets.securities", status="not_possible", stage=None, what="security master, prices", where=None, why=MARQUEE),
    _E(module="markets.screens", status="not_possible", stage=None, what="Marquee screens", where=None, why=MARQUEE + "; the app's CANDIDATES ranking instead"),
    _E(module="markets.report", status="not_possible", stage=None, what="Marquee reports", where=None, why=MARQUEE),
    _E(module="markets.report_utils", status="plumbing", stage=None, what="report helpers", where=None, why=MARQUEE),
    _E(module="markets.portfolio_manager", status="not_possible", stage=None, what="Marquee portfolio manager", where=None, why=MARQUEE),
    _E(module="markets.portfolio_manager_utils", status="plumbing", stage=None, what="helpers", where=None, why=MARQUEE),
    _E(module="markets.realtime", status="not_possible", stage=None, what="real-time Marquee data", where=None, why=MARQUEE),
    _E(module="markets.historical", status="not_possible", stage=None, what="historical pricing contexts", where=None, why=MARQUEE),
    _E(module="markets.core", status="not_possible", stage=None, what="PricingContext", where=None, why=MARQUEE),
    _E(module="markets.markets", status="plumbing", stage=None, what="market data containers", where=None, why=MARQUEE),
    _E(module="models.epidemiology", status="not_applicable", stage=None, what="SIR / SEIR epidemic models", where=None,
       why="models the spread of a disease, not stock prices"),
    _E(module="models.risk_model", status="not_possible", stage=None, what="downloads GS factor risk models", where=None,
       why=MARQUEE + "; own factor regression instead (QNT, PORT)"),
    _E(module="models.risk_model_utils", status="plumbing", stage=None, what="helpers", where=None, why=MARQUEE),
    _E(module="datetime.date", status="used", stage="Q3", calls=["PredefinedAssetEngine("], what="business days (the engine steps through weekdays; sessions that were CSE holidays have no price and are skipped)", where="Quant Lab",
       why="the CSE's own past sessions come from the ASPI's dates; gs-quant's holiday calendars need Marquee"),
    _E(module="datetime.relative_date", status="not_applicable", stage=None, what="relative dates ('rdates')", where=None,
       why="the lab's schedules are simply 'first session of the month / week' on the CSE's own calendar"),
    _E(module="datetime.rules", status="not_applicable", stage=None, what="date-rule grammar", where=None,
       why="not needed: see relative_date"),
    _E(module="datetime.gscalendar", status="not_possible", stage=None, what="holiday calendars", where=None, why=MARQUEE + "; CSE sessions from the app's own data instead"),
    _E(module="datetime.point", status="plumbing", stage=None, what="tenor parsing", where=None, why=None),
    _E(module="datetime.time", status="plumbing", stage=None, what="time helpers", where=None, why=None),
    _E(module="api.fred", status="used", stage="Q0", what="FredDataApi: free US Federal Reserve data", calls=["FredDataApi("],
       where="stored nightly as world series when FRED_API_KEY is set, then usable in the correlation explorer", why="off without the free key"),
    _E(module="api.gs", status="not_possible", stage=None, what="29 Marquee REST clients (assets, data, risk, portfolios, ...)", where=None, why=MARQUEE),
    _E(module="api.data", status="plumbing", stage=None, what="data API base classes", where=None, why=None),
    _E(module="api.risk", status="plumbing", stage=None, what="risk API base classes", where=None, why=MARQUEE),
    _E(module="api.api_cache", status="plumbing", stage=None, what="request cache", where=None, why=None),
    _E(module="api.api_session", status="plumbing", stage=None, what="session base", where=None, why=None),
    _E(module="api.utils", status="plumbing", stage=None, what="helpers", where=None, why=None),
    # ---------------------------------------------------------------- the rest of the package
    _E(module="analytics", status="not_possible", stage=None, what="DataGrid and workspace builders for the Marquee website", where=None,
       why=MARQUEE + "; the maths they wrap is used directly"),
    _E(module="data", status="not_possible", stage=None, what="Dataset access to Marquee data", where=None, why=MARQUEE + " (the FRED provider is used: api.fred)"),
    _E(module="entities", status="not_possible", stage=None, what="Marquee entities (countries, KPIs, risk models)", where=None, why=MARQUEE),
    _E(module="instrument", status="plumbing", stage="Q3", what="instrument classes", where="a stock label in the backtester (EqStock)",
       why="pricing them needs Marquee"),
    _E(module="risk", status="not_possible", stage=None, what="risk measures (delta, vega, ...) priced by Goldman", where=None, why=MARQUEE),
    _E(module="target", status="plumbing", stage=None, what="generated API data types", where=None, why=None),
    _E(module="content", status="not_applicable", stage=None, what="showcase notebooks (need Marquee)", where="ideas in the QNT glossary and Lab examples", why=MARQUEE),
    _E(module="mcp", status="not_applicable", stage=None, what="server exposing gs-quant to AI assistants", where=None, why=MARQUEE),
    _E(module="skills", status="not_applicable", stage=None, what="AI coding-assistant notes", where=None,
       why="notes that teach AI coding assistants to use gs-quant; nothing for the app to show"),
    _E(module="interfaces", status="plumbing", stage=None, what="abstract base classes", where=None, why=None),
    _E(module="workflow", status="plumbing", stage=None, what="JSON decoders for hedges", where=None, why=None),
    _E(module="quote_reports", status="plumbing", stage=None, what="quote-report objects", where=None, why=None),
    _E(module="tracing", status="plumbing", stage=None, what="telemetry", where=None, why=None),
    _E(module="test", status="plumbing", stage=None, what="gs-quant's own tests", where=None, why=None),
    _E(module="config", status="plumbing", stage=None, what="settings", where=None, why=None),
]
# single-file top-level modules (session, base, json...): plumbing
for _m in ("_version", "base", "common", "context_base", "errors", "json_convertors", "json_convertors_common", "json_encoder",
           "priceable", "session"):
    ENTRIES.append(_E(module=_m, status="plumbing", stage=None, what="package infrastructure", where=None,
                      why=MARQUEE if _m == "session" else None))


def table() -> list[dict[str, Any]]:
    return ENTRIES


def lookup(module: str) -> dict[str, Any] | None:
    """The entry for a module path, or for its package (e.g. markets.portfolio_manager -> markets.*)."""
    for e in ENTRIES:
        if e["module"] == module:
            return e
    top = module.split(".")[0]
    return next((e for e in ENTRIES if e["module"] == top), None)
