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


LOCAL_UID = "local"          # owner of the data when sign-in is off (and of everything made before sign-in existed)


class WatchlistItem(Base):
    """One stock on one account's watchlist."""
    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("user_id", "symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=LOCAL_UID)
    symbol: Mapped[str] = mapped_column(ForeignKey("securities.symbol"), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    notes: Mapped[str | None] = mapped_column(Text)


class WatchlistGroup(Base):
    """A named list ("Watchlist 1", "Swing ideas"...) on an account's home page. The `watchlist` table above is the set
    of stocks the account tracks (what the evening job fetches and analyses); every tracked stock is on at least one
    list, and a stock can be on several."""
    __tablename__ = "watchlist_groups"
    __table_args__ = (UniqueConstraint("user_id", "name"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=LOCAL_UID)
    name: Mapped[str] = mapped_column(String(40))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class WatchlistGroupItem(Base):
    __tablename__ = "watchlist_group_items"
    group_id: Mapped[int] = mapped_column(ForeignKey("watchlist_groups.id", ondelete="CASCADE"), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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
    """News headlines from free RSS feeds (Sri Lankan papers, CBSL, Google News searches), matched to tracked stocks.
    Only the title, link, date and the feed's short summary are kept; articles are linked, never copied."""
    __tablename__ = "headlines"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)          # hash of the link
    source: Mapped[str] = mapped_column(String(40))                        # the feed (Daily FT, Google News...)
    title: Mapped[str] = mapped_column(String(400))
    link: Mapped[str] = mapped_column(String(600))
    published: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    symbols: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # stocks the headline is about (primary)
    tone: Mapped[float | None] = mapped_column(Float, nullable=True)        # keyword tone -1..1
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    kind: Mapped[str | None] = mapped_column(String(10), nullable=True)     # company | macro | global
    outlet: Mapped[str | None] = mapped_column(String(80), nullable=True)   # the publisher (for Google News items)
    cluster: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)   # same story, several outlets
    llm: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # Claude's reading (news_llm.py)


class MacroDaily(Base):
    """Daily closes of world series (Brent, Nasdaq-100, S&P 500, USD/LKR, gold, Nifty) from TradingView. Shown and
    studied (macro_study.py); no engine score uses them."""
    __tablename__ = "macro_daily"
    __table_args__ = (UniqueConstraint("code", "date"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(12), index=True)                # brent | ndx | spx | usdlkr | gold | nifty
    date: Mapped[date] = mapped_column(Date, index=True)                     # the series' own session date
    close: Mapped[float] = mapped_column(Float)


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
    user_id: Mapped[str] = mapped_column(String(128), index=True, default=LOCAL_UID)
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
    # what was known that evening but not used by the engines (headline tone, world markets), so it can be tested later
    context: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class Preference(Base):
    """Key/value app state shared by everyone (screener universe, corporate-action notices seen, Telegram owner...).
    Each account's own display choices are in UserPref."""
    __tablename__ = "preferences"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class UserPref(Base):
    """One account's display preferences (pattern layers, catalog toggles...), key/value."""
    __tablename__ = "user_prefs"
    user_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class UserAccount(Base):
    """An account's profile, created on its first sign-in and never overwritten with defaults (only empty fields are
    filled). Sign-in itself (passwords, Google) lives in Firebase; nothing secret is stored here."""
    __tablename__ = "users"
    uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), default="")
    first_name: Mapped[str] = mapped_column(String(100), default="")
    last_name: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PositionReview(Base):
    """The engines' review of one account's open position in a stock (HOLD / TRIM / SELL, suggested stop), made from the
    same evening's analysis. The shared Analysis row is position-free; this is the per-account layer on top of it."""
    __tablename__ = "position_reviews"
    __table_args__ = (UniqueConstraint("user_id", "symbol"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    symbol: Mapped[str] = mapped_column(String(20), index=True)
    position_id: Mapped[int] = mapped_column(Integer)
    date: Mapped[date] = mapped_column(Date)
    verdict: Mapped[str] = mapped_column(String(10))
    score: Mapped[float] = mapped_column(Float)
    verdict_analyst: Mapped[str | None] = mapped_column(String(10), nullable=True)
    score_analyst: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict_smc: Mapped[str | None] = mapped_column(String(10), nullable=True)
    score_smc: Mapped[float | None] = mapped_column(Float, nullable=True)
    recommendation: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(40))
    run_date: Mapped[date] = mapped_column(Date)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str | None] = mapped_column(Text)


engine = create_engine(database_url(), future=True, connect_args={"timeout": 20} if database_url().startswith("sqlite") else {})   # wait up to 20 s for a lock


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - sqlite only
    if engine.dialect.name == "sqlite":
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db() -> None:
    _watchlist_per_user()
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _intraday_unique_with_source()
    _owner_for_old_rows()
    _lists_for_old_watchlists()
    _date_undated_headlines()


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


def _watchlist_per_user() -> None:
    """Accounts (sign-in) made the watchlist per user: its key changed from the symbol to (user, symbol). SQLite cannot
    change a primary key, so the old table is rebuilt once; its rows become the local user's."""
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if not insp.has_table("watchlist") or "user_id" in {c["name"] for c in insp.get_columns("watchlist")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE watchlist RENAME TO watchlist_old"))
        WatchlistItem.__table__.create(conn)
        conn.execute(text("INSERT INTO watchlist (user_id, symbol, added_at, notes) "
                          "SELECT :uid, symbol, added_at, notes FROM watchlist_old ORDER BY added_at"), {"uid": LOCAL_UID})
        conn.execute(text("DROP TABLE watchlist_old"))


# display preferences the web app stores (prefs.ts): before accounts they were global, now each account has its own
_USER_PREF_KEYS = ("catalog",)
_USER_PREF_PREFIXES = ("stock:",)


def _owner_for_old_rows() -> None:
    """Rows made before accounts existed belong to the local user: positions get user_id 'local', and the web app's
    display preferences are copied into the local user's preferences (once)."""
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("UPDATE positions SET user_id = :uid WHERE user_id IS NULL"), {"uid": LOCAL_UID})
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_positions_user_id ON positions (user_id)"))
        if engine.dialect.name != "sqlite" or conn.execute(text("SELECT 1 FROM preferences WHERE key = 'user_prefs_migrated'")).first():
            return
        for key, value in conn.execute(text("SELECT key, value FROM preferences")).all():
            if key in _USER_PREF_KEYS or key.startswith(_USER_PREF_PREFIXES):
                conn.execute(text("INSERT OR IGNORE INTO user_prefs (user_id, key, value) VALUES (:u, :k, :v)"),
                             {"u": LOCAL_UID, "k": key, "v": value})
        conn.execute(text("INSERT INTO preferences (key, value) VALUES ('user_prefs_migrated', 'true')"))


def _lists_for_old_watchlists() -> None:
    """Named lists were added after the watchlist: every account that tracks stocks but has no list yet gets
    "Watchlist 1" holding those stocks (once; later accounts get theirs when they first open the home page)."""
    from sqlalchemy import text
    with engine.begin() as conn:
        users = [r[0] for r in conn.execute(text(
            "SELECT DISTINCT user_id FROM watchlist WHERE user_id NOT IN (SELECT user_id FROM watchlist_groups)")).all()]
        for uid in users:
            gid = conn.execute(text("INSERT INTO watchlist_groups (user_id, name, sort_order, created_at) "
                                    "VALUES (:u, 'Watchlist 1', 0, CURRENT_TIMESTAMP)"), {"u": uid}).lastrowid
            conn.execute(text("INSERT INTO watchlist_group_items (group_id, symbol, added_at) "
                              "SELECT :g, symbol, added_at FROM watchlist WHERE user_id = :u"), {"g": gid, "u": uid})


def _date_undated_headlines() -> None:
    """Daily Mirror's feed dates items as '2026-11-05 12:00:00', which the first feed reader could not parse, so those
    headlines were stored undated and never counted. Give them the time they were fetched (the feed shows only recent items)."""
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("UPDATE headlines SET published = fetched_at WHERE published IS NULL"))
