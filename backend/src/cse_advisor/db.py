"""Database models (SQLite by default; Postgres-ready through SQLAlchemy)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text,
    UniqueConstraint, create_engine, event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import database_url


class Base(DeclarativeBase):
    pass


class Security(Base):
    __tablename__ = "securities"
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)      # e.g. JKH.N0000
    cse_id: Mapped[int] = mapped_column(Integer)                           # id used by chart API
    security_id: Mapped[int | None] = mapped_column(Integer)               # id used by financials API
    name: Mapped[str] = mapped_column(String(200))
    shares_issued: Mapped[float | None] = mapped_column(Float)
    beta_aspi: Mapped[float | None] = mapped_column(Float)
    beta_spsl: Mapped[float | None] = mapped_column(Float)
    logo_path: Mapped[str | None] = mapped_column(String(300))
    isin: Mapped[str | None] = mapped_column(String(20))
    info: Mapped[dict[str, Any] | None] = mapped_column(JSON)              # latest raw company summary
    sector: Mapped[str | None] = mapped_column(String(10), nullable=True)  # S&P/CSE sector code, set by the user
    updated_at: Mapped[datetime | None] = mapped_column(DateTime)


class WatchlistItem(Base):
    __tablename__ = "watchlist"
    symbol: Mapped[str] = mapped_column(ForeignKey("securities.symbol"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[str | None] = mapped_column(Text)


class PriceDaily(Base):
    __tablename__ = "prices_daily"
    __table_args__ = (UniqueConstraint("symbol", "date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0)
    turnover: Mapped[float | None] = mapped_column(Float)
    trades: Mapped[int | None] = mapped_column(Integer)
    crossing_volume: Mapped[float | None] = mapped_column(Float)
    open_estimated: Mapped[bool] = mapped_column(Boolean, default=False)   # backfilled history has no open
    # eod (trade summary) | history (cse.lk 1y chart) | import | import_adj (CSV) | tv_adj (TradingView daily,
    # already adjusted by TradingView for rights / bonus / splits; only stored for days before cse.lk's history)
    source: Mapped[str] = mapped_column(String(20), default="eod")
    # TradingView's (adjusted) close for days that also have a cse.lk bar: used to stitch the older tv_adj
    # rows onto the app's own adjusted series without a jump at the seam
    tv_close: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarketDaily(Base):
    __tablename__ = "market_daily"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    aspi: Mapped[float | None] = mapped_column(Float)
    spsl20: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    share_volume: Mapped[float | None] = mapped_column(Float)
    trades: Mapped[int | None] = mapped_column(Integer)
    advancers: Mapped[int | None] = mapped_column(Integer)
    decliners: Mapped[int | None] = mapped_column(Integer)
    # from dailyMarketSummery (shown only, not scored)
    foreign_buy: Mapped[float | None] = mapped_column(Float, nullable=True)
    foreign_sell: Mapped[float | None] = mapped_column(Float, nullable=True)
    foreign_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    per: Mapped[float | None] = mapped_column(Float, nullable=True)
    pbv: Mapped[float | None] = mapped_column(Float, nullable=True)
    dy: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_cap: Mapped[float | None] = mapped_column(Float, nullable=True)


class PriceIntraday(Base):
    """Intraday bars. cse.lk trades are stored as 1m bars (source 'cse'); TradingView bars at their own
    interval (source 'tv'). `ts` is the bar's start in Colombo wall time (naive)."""
    __tablename__ = "prices_intraday"
    __table_args__ = (UniqueConstraint("symbol", "interval", "ts", "source"),)       # cse and tv both hold 1m bars
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    interval: Mapped[str] = mapped_column(String(4))                     # 1m | 5m | 15m | 1h | 4h
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0)
    trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(4))                       # cse | tv


class SectorDaily(Base):
    """S&P/CSE sector index values recorded each evening from allSectors (history builds from first run)."""
    __tablename__ = "sector_daily"
    __table_args__ = (UniqueConstraint("code", "date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), index=True)            # e.g. EGY
    date: Mapped[date] = mapped_column(Date, index=True)
    name: Mapped[str] = mapped_column(String(120))
    index_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    change: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    trades: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Announcement(Base):
    __tablename__ = "announcements"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)             # CSE announcementId
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[datetime | None] = mapped_column(DateTime)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str | None] = mapped_column(Text)
    file_path: Mapped[str | None] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(60))               # our classification
    sentiment: Mapped[float | None] = mapped_column(Float)
    materiality: Mapped[int | None] = mapped_column(Integer)
    notified: Mapped[bool] = mapped_column(Boolean, default=False)
    # from the announcement's detail record: the attachment's file name (the real subject of a generic
    # 'Corporate Disclosure'), and typed facts (dividend per share and XD date, director trades, AGM date)
    attachment: Mapped[str | None] = mapped_column(String(300), nullable=True)
    event: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Headline(Base):
    """Business-news headlines from free RSS feeds (EconomyNext, Daily Mirror), matched to watchlist stocks."""
    __tablename__ = "headlines"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)          # hash of the link
    source: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(400))
    link: Mapped[str] = mapped_column(String(600))
    published: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbols: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # matched watchlist symbols
    tone: Mapped[float | None] = mapped_column(Float, nullable=True)        # keyword tone -1..1
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class FinancialReport(Base):
    __tablename__ = "financial_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)             # CSE report id
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(20))                          # annual | quarterly
    title: Mapped[str] = mapped_column(String(300))
    period_date: Mapped[datetime | None] = mapped_column(DateTime)
    uploaded: Mapped[datetime | None] = mapped_column(DateTime)
    path: Mapped[str] = mapped_column(String(300))


class Fundamental(Base):
    """Key figures for one reporting period (entered manually or extracted from PDFs)."""
    __tablename__ = "fundamentals"
    __table_args__ = (UniqueConstraint("symbol", "period_end", "period_type"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(4))                    # Q (quarter) | FY
    revenue: Mapped[float | None] = mapped_column(Float)
    gross_profit: Mapped[float | None] = mapped_column(Float)
    operating_profit: Mapped[float | None] = mapped_column(Float)
    net_profit: Mapped[float | None] = mapped_column(Float)                # attributable to equity holders
    eps: Mapped[float | None] = mapped_column(Float)
    total_assets: Mapped[float | None] = mapped_column(Float)
    total_liabilities: Mapped[float | None] = mapped_column(Float)
    total_equity: Mapped[float | None] = mapped_column(Float)
    current_assets: Mapped[float | None] = mapped_column(Float)
    current_liabilities: Mapped[float | None] = mapped_column(Float)
    total_debt: Mapped[float | None] = mapped_column(Float)
    finance_cost: Mapped[float | None] = mapped_column(Float)
    operating_cash_flow: Mapped[float | None] = mapped_column(Float)
    dividend_per_share: Mapped[float | None] = mapped_column(Float)
    shares_outstanding: Mapped[float | None] = mapped_column(Float)
    one_off_income: Mapped[float | None] = mapped_column(Float)            # fair-value gains, bargain-purchase gains, etc.
    source: Mapped[str] = mapped_column(String(20), default="manual")      # manual | pdf | pdf_prior (comparative column)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)     # True rows are shown but not used in scores
    report_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # FinancialReport the figures came from
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)   # evidence lines, checks, column used


class CorporateAction(Base):
    """Rights issues, bonus/scrip issues and share splits, used to back-adjust price history."""
    __tablename__ = "corporate_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    kind: Mapped[str] = mapped_column(String(20))                          # rights | bonus | split
    ex_date: Mapped[date | None] = mapped_column(Date)                     # XR / XD / first adjusted day
    ratio_new: Mapped[float | None] = mapped_column(Float)                 # new shares ...
    ratio_old: Mapped[float | None] = mapped_column(Float)                 # ... for every N held
    price: Mapped[float | None] = mapped_column(Float)                     # rights subscription price
    listing_date: Mapped[date | None] = mapped_column(Date)                # new shares listed / credited
    trading_date: Mapped[date | None] = mapped_column(Date)                # rights entitlements start trading
    announcement_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    manual: Mapped[bool] = mapped_column(Boolean, default=False)           # user entered / confirmed
    ignore: Mapped[bool] = mapped_column(Boolean, default=False)


class Analysis(Base):
    """Output of the EOD analysis for one stock on one date."""
    __tablename__ = "analysis"
    __table_args__ = (UniqueConstraint("symbol", "date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    verdict: Mapped[str] = mapped_column(String(10))
    score: Mapped[float] = mapped_column(Float)                    # Classic engine
    verdict_analyst: Mapped[str | None] = mapped_column(String(10), nullable=True)
    score_analyst: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict_smc: Mapped[str | None] = mapped_column(String(10), nullable=True)
    score_smc: Mapped[float | None] = mapped_column(Float, nullable=True)
    patterns: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    levels: Mapped[dict[str, Any]] = mapped_column(JSON)
    liquidity: Mapped[dict[str, Any]] = mapped_column(JSON)
    trend: Mapped[dict[str, Any]] = mapped_column(JSON)
    fundamentals: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    recommendation: Mapped[dict[str, Any]] = mapped_column(JSON)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    entry_date: Mapped[date] = mapped_column(Date)
    entry_price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[int] = mapped_column(Integer)
    stop: Mapped[float | None] = mapped_column(Float)
    target: Mapped[float | None] = mapped_column(Float)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    exit_date: Mapped[date | None] = mapped_column(Date)
    exit_price: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)


class ScreenerRun(Base):
    """One evening's ranking of the liquid CSE stocks (screener.py): the rows, and how they were made."""
    __tablename__ = "screener_runs"
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON)


class JournalEntry(Base):
    """Forward-test journal: one engine's call on one evening, scored later by what the price actually did.
    Recorded for every BUY and WATCH call with a plan, never edited afterwards except for `outcome`."""
    __tablename__ = "journal"
    __table_args__ = (UniqueConstraint("symbol", "date", "engine"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)                   # the session the call was made after
    engine: Mapped[str] = mapped_column(String(10))                        # original | analyst | smc
    verdict: Mapped[str] = mapped_column(String(10))
    setup: Mapped[str | None] = mapped_column(String(40), nullable=True)
    triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    close: Mapped[float] = mapped_column(Float)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON)                     # zl, zh, ideal, trigger, stop, t1, t2, rr
    rule: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)   # trend / near-high / relative-strength flags that day
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    scored_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Preference(Base):
    """Key/value user preferences (pattern display choices, layer toggles...)."""
    __tablename__ = "preferences"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(40))
    run_date: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str | None] = mapped_column(Text)


engine = create_engine(database_url(), future=True)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - sqlite only
    if engine.dialect.name == "sqlite":
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _intraday_unique_with_source()


def _add_missing_columns() -> None:
    """Tiny migration step: add columns introduced after a table was first created (SQLite)."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'))


def _intraday_unique_with_source() -> None:
    """The 1m timeframe is held twice for the same minute (cse.lk trades and TradingView bars), so the intraday table's
    unique key must include the source. SQLite cannot alter a constraint: rebuild the table once, keeping every row."""
    from sqlalchemy import text
    with engine.begin() as conn:
        row = conn.execute(text("SELECT sql FROM sqlite_master WHERE type='table' AND name='prices_intraday'")).first()
        if not row or not row[0] or "UNIQUE (symbol, interval, ts, source)" in row[0] or engine.dialect.name != "sqlite":
            return
        conn.execute(text("ALTER TABLE prices_intraday RENAME TO prices_intraday_old"))
        for (name,) in conn.execute(text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='prices_intraday_old' AND sql IS NOT NULL")).all():
            conn.execute(text(f'DROP INDEX "{name}"'))
        PriceIntraday.__table__.create(conn)
        cols = ", ".join(c.name for c in PriceIntraday.__table__.columns)
        conn.execute(text(f"INSERT INTO prices_intraday ({cols}) SELECT {cols} FROM prices_intraday_old"))
        conn.execute(text("DROP TABLE prices_intraday_old"))
