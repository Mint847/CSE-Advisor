"""Per-account data on top of the shared market data.

Shared by everyone: prices, announcements, statements, corporate actions, the evening analysis of each stock, the
screener and the journal. Each account's own: watchlist, positions (and the engines' review of them), display
preferences and profile. Every API call works on the caller's own rows only (main.py passes the verified uid).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .auth import User
from .db import (
    LOCAL_UID, Analysis, Position, PositionReview, Preference, UserAccount, UserPref, WatchlistGroup, WatchlistGroupItem,
    WatchlistItem,
)

log = logging.getLogger(__name__)

TELEGRAM_OWNER_KEY = "telegram_owner_uid"


# ---------------------------------------------------------------- profile
def _split_name(display: str) -> tuple[str, str]:
    parts = (display or "").strip().split(" ", 1)
    return parts[0], (parts[1].strip() if len(parts) > 1 else "")


def ensure_profile(s: Session, u: User, first_name: str = "", last_name: str = "") -> UserAccount:
    """The account's profile; created on its first sign-in. An existing profile is never overwritten: only fields that
    are still empty get filled (the name typed at sign-up, else the Google name, else the part of the email before @)."""
    first_name, last_name = (first_name or "").strip()[:100], (last_name or "").strip()[:100]
    row = s.get(UserAccount, u.uid)
    if row is None:
        row = UserAccount(uid=u.uid, email=u.email, first_name="", last_name="")
        s.add(row)
    if not first_name and u.name:
        first_name, last_name = _split_name(u.name)
    if not first_name and u.email:
        first_name, last_name = u.email.split("@")[0], ""
    changed = False
    if not row.first_name and first_name:
        row.first_name, changed = first_name[:100], True
    if not row.last_name and last_name:
        row.last_name, changed = last_name[:100], True
    if u.email and row.email != u.email:              # the address can change in Firebase; keep the copy current
        row.email, changed = u.email, True
    if changed:
        row.last_updated = datetime.utcnow()
    # "last seen" is refreshed at most every 10 minutes: a write on every call would make /api/me wait for (and fail
    # behind) any long job holding SQLite's write lock
    if row.last_seen is None or (datetime.utcnow() - row.last_seen).total_seconds() > 600:
        row.last_seen = datetime.utcnow()
    return row


def set_name(s: Session, u: User, first_name: str, last_name: str) -> UserAccount:
    row = ensure_profile(s, u)
    row.first_name, row.last_name = first_name.strip()[:100] or row.first_name, last_name.strip()[:100]
    row.last_updated = datetime.utcnow()
    return row


def profile_dict(row: UserAccount, u: User) -> dict[str, Any]:
    return {"uid": u.uid, "email": row.email or u.email, "first_name": row.first_name, "last_name": row.last_name,
            "created_at": row.created_at.isoformat() if row.created_at else None, "admin": u.admin, "provider": u.provider}


# ---------------------------------------------------------------- watchlist / symbols
def watchlist_symbols(s: Session, uid: str | None = None) -> list[str]:
    """One account's watchlist in the order added; with uid=None every account's stocks (each once), which is what
    the evening job analyses."""
    if uid is not None:
        return list(s.scalars(select(WatchlistItem.symbol).where(WatchlistItem.user_id == uid).order_by(WatchlistItem.added_at)))
    rows = s.execute(select(WatchlistItem.symbol, func.min(WatchlistItem.added_at)).group_by(WatchlistItem.symbol)
                     .order_by(func.min(WatchlistItem.added_at))).all()
    return [r[0] for r in rows]


def tracked_symbols(s: Session) -> list[str]:
    """Every stock someone watches or holds: the evening job analyses all of them."""
    syms = watchlist_symbols(s)
    held = s.scalars(select(Position.symbol).where(Position.closed.is_(False)).distinct()).all()
    return syms + [x for x in held if x not in syms]


def user_symbols(s: Session, uid: str) -> set[str]:
    """Stocks this account watches or holds (open positions)."""
    held = s.scalars(select(Position.symbol).where(Position.user_id == uid, Position.closed.is_(False))).all()
    return set(watchlist_symbols(s, uid)) | set(held)


def on_watchlist(s: Session, uid: str, symbol: str) -> bool:
    return s.scalar(select(WatchlistItem.id).where(WatchlistItem.user_id == uid, WatchlistItem.symbol == symbol)) is not None


# ---------------------------------------------------------------- named lists (home page tabs)
DEFAULT_LIST = "Watchlist 1"
MAX_NAME = 40


class ListError(ValueError):
    """A list action the user can fix (shown as the message)."""


def groups(s: Session, uid: str) -> list[WatchlistGroup]:
    """The account's lists in tab order. Makes "Watchlist 1" if there is none, and puts any tracked stock that is on no
    list (added by Telegram, or before lists existed) on the first list, so every tracked stock is on some list."""
    rows = s.scalars(select(WatchlistGroup).where(WatchlistGroup.user_id == uid).order_by(WatchlistGroup.sort_order, WatchlistGroup.id)).all()
    if not rows:
        g = WatchlistGroup(user_id=uid, name=DEFAULT_LIST, sort_order=0)
        s.add(g)
        s.flush()
        rows = [g]
    listed = set(s.scalars(select(WatchlistGroupItem.symbol).where(WatchlistGroupItem.group_id.in_([g.id for g in rows]))))
    added = False
    for sym in watchlist_symbols(s, uid):
        if sym not in listed:
            s.add(WatchlistGroupItem(group_id=rows[0].id, symbol=sym))
            added = True
    if added:
        s.flush()
    return list(rows)


def group_symbols(s: Session, uid: str, gid: int) -> list[str]:
    """Symbols on one list, oldest first."""
    g = s.get(WatchlistGroup, gid)
    if g is None or g.user_id != uid:
        raise ListError("List not found.")
    groups(s, uid)
    return list(s.scalars(select(WatchlistGroupItem.symbol).where(WatchlistGroupItem.group_id == gid)
                          .order_by(WatchlistGroupItem.added_at, WatchlistGroupItem.symbol)))


def lists_overview(s: Session, uid: str) -> list[dict[str, Any]]:
    gs = groups(s, uid)
    counts = dict(s.execute(select(WatchlistGroupItem.group_id, func.count()).where(WatchlistGroupItem.group_id.in_([g.id for g in gs]))
                            .group_by(WatchlistGroupItem.group_id)).all())
    return [{"id": g.id, "name": g.name, "count": counts.get(g.id, 0)} for g in gs]


def symbol_lists(s: Session, uid: str) -> dict[str, list[int]]:
    """symbol -> ids of this account's lists holding it."""
    gs = groups(s, uid)
    out: dict[str, list[int]] = {}
    for gid, sym in s.execute(select(WatchlistGroupItem.group_id, WatchlistGroupItem.symbol).where(WatchlistGroupItem.group_id.in_([g.id for g in gs]))):
        out.setdefault(sym, []).append(gid)
    return out


def _clean_name(name: str) -> str:
    n = " ".join((name or "").split())
    if not n:
        raise ListError("Give the list a name.")
    if len(n) > MAX_NAME:
        raise ListError(f"A list name can have at most {MAX_NAME} characters.")
    return n


def _name_taken(s: Session, uid: str, name: str, except_id: int | None = None) -> bool:
    q = select(WatchlistGroup.id).where(WatchlistGroup.user_id == uid, func.lower(WatchlistGroup.name) == name.lower())
    if except_id is not None:
        q = q.where(WatchlistGroup.id != except_id)
    return s.scalar(q) is not None


def create_group(s: Session, uid: str, name: str, max_lists: int = 10) -> WatchlistGroup:
    name = _clean_name(name)
    gs = groups(s, uid)
    if len(gs) >= max_lists:
        raise ListError(f"You can have at most {max_lists} lists.")
    if _name_taken(s, uid, name):
        raise ListError(f'You already have a list called "{name}".')
    g = WatchlistGroup(user_id=uid, name=name, sort_order=max(x.sort_order for x in gs) + 1)
    s.add(g)
    s.flush()
    return g


def rename_group(s: Session, uid: str, gid: int, name: str) -> WatchlistGroup:
    name = _clean_name(name)
    g = s.get(WatchlistGroup, gid)
    if g is None or g.user_id != uid:
        raise ListError("List not found.")
    if _name_taken(s, uid, name, gid):
        raise ListError(f'You already have a list called "{name}".')
    g.name = name
    return g


def untrack_if_unlisted(s: Session, uid: str, symbols: list[str]) -> list[str]:
    """Stop tracking stocks that are no longer on any of the account's lists. Returns the symbols untracked."""
    gids = [g.id for g in s.scalars(select(WatchlistGroup).where(WatchlistGroup.user_id == uid))]
    still = set(s.scalars(select(WatchlistGroupItem.symbol).where(WatchlistGroupItem.group_id.in_(gids), WatchlistGroupItem.symbol.in_(symbols)))) if gids else set()
    gone = [x for x in symbols if x not in still]
    if gone:
        s.execute(delete(WatchlistItem).where(WatchlistItem.user_id == uid, WatchlistItem.symbol.in_(gone)))
    return gone


def delete_group(s: Session, uid: str, gid: int) -> list[str]:
    """Delete a list (never the last one). Stocks that were only on it stop being tracked (an open position keeps its
    stock analysed anyway). Returns the symbols no longer tracked."""
    gs = groups(s, uid)
    g = next((x for x in gs if x.id == gid), None)
    if g is None:
        raise ListError("List not found.")
    if len(gs) == 1:
        raise ListError("You need at least one list.")
    syms = group_symbols(s, uid, gid)
    s.execute(delete(WatchlistGroupItem).where(WatchlistGroupItem.group_id == gid))
    s.delete(g)
    s.flush()
    return untrack_if_unlisted(s, uid, syms)


def add_to_group(s: Session, uid: str, gid: int | None, symbol: str) -> int:
    """Put a (tracked) stock on a list; the first list when `gid` is None. Returns the list id."""
    gs = groups(s, uid)
    g = next((x for x in gs if x.id == gid), None) if gid is not None else gs[0]
    if g is None:
        raise ListError("List not found.")
    if s.get(WatchlistGroupItem, (g.id, symbol)) is None:
        s.add(WatchlistGroupItem(group_id=g.id, symbol=symbol))
    return g.id


def remove_from_group(s: Session, uid: str, gid: int | None, symbol: str) -> bool:
    """Take a stock off one list (or off all of them when `gid` is None). Off its last list it stops being tracked.
    Returns True when it is no longer tracked."""
    gs = groups(s, uid)
    if gid is not None and not any(x.id == gid for x in gs):
        raise ListError("List not found.")
    ids = [gid] if gid is not None else [x.id for x in gs]
    s.execute(delete(WatchlistGroupItem).where(WatchlistGroupItem.group_id.in_(ids), WatchlistGroupItem.symbol == symbol))
    s.flush()
    return bool(untrack_if_unlisted(s, uid, [symbol]))


def held_symbols(s: Session, uid: str) -> list[str]:
    """Stocks with an open position, in the order the positions were opened."""
    rows = s.execute(select(Position.symbol, func.min(Position.entry_date)).where(Position.user_id == uid, Position.closed.is_(False))
                     .group_by(Position.symbol).order_by(func.min(Position.entry_date), Position.symbol)).all()
    return [r[0] for r in rows]


# ---------------------------------------------------------------- preferences
def get_pref(s: Session, uid: str, key: str) -> Any:
    p = s.get(UserPref, (uid, key))
    return p.value if p else None


def put_pref(s: Session, uid: str, key: str, value: Any) -> None:
    p = s.get(UserPref, (uid, key)) or UserPref(user_id=uid, key=key)
    p.value = value
    s.add(p)


# ---------------------------------------------------------------- position reviews (the per-account layer)
def open_position(s: Session, uid: str, symbol: str) -> Position | None:
    return s.scalar(select(Position).where(Position.user_id == uid, Position.symbol == symbol, Position.closed.is_(False))
                    .order_by(Position.entry_date.desc(), Position.id.desc()))


def refresh_review(s: Session, uid: str, symbol: str) -> PositionReview | None:
    """Re-run the engines for this account's open position in `symbol` (HOLD / TRIM / SELL and the suggested stop),
    or drop the stored review when nothing is held."""
    from .engine import _row, analyze_symbol
    pos = open_position(s, uid, symbol)
    old = s.scalar(select(PositionReview).where(PositionReview.user_id == uid, PositionReview.symbol == symbol))
    if pos is None:
        if old is not None:
            s.delete(old)
        return None
    r = analyze_symbol(s, symbol, save=False, position=_row(pos))
    if not r:
        return old
    row = old or PositionReview(user_id=uid, symbol=symbol)
    d = r["date"]                                    # analyze_symbol returns JSON-ready values: the date is text
    row.position_id, row.date = pos.id, d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
    row.verdict, row.score = r["verdict"], r["score"]
    row.verdict_analyst, row.score_analyst = r["verdict_analyst"], r["score_analyst"]
    row.verdict_smc, row.score_smc = r["verdict_smc"], r["score_smc"]
    row.recommendation, row.created_at = r["recommendation"], datetime.utcnow()
    s.add(row)
    return row


def refresh_reviews_for_symbol(s: Session, symbol: str) -> int:
    """After the shared analysis of `symbol` changed: refresh every account's review of its open position there."""
    uids = s.scalars(select(Position.user_id).where(Position.symbol == symbol, Position.closed.is_(False)).distinct()).all()
    stale = s.scalars(select(PositionReview).where(PositionReview.symbol == symbol, PositionReview.user_id.not_in(uids))).all()
    for row in stale:
        s.delete(row)
    for uid in uids:
        try:
            refresh_review(s, uid, symbol)
        except Exception as exc:  # noqa: BLE001 - one bad position must not stop the evening run
            log.warning("position review %s/%s failed: %s", uid, symbol, exc)
    return len(uids)


VERDICT_FIELDS = ("verdict", "score", "verdict_analyst", "score_analyst", "verdict_smc", "score_smc")


def effective(s: Session, uid: str, a: Analysis | None) -> dict[str, Any] | None:
    """The analysis as this account sees it: the shared one, or, when the account holds the stock, the same analysis
    run with its position (verdicts HOLD / TRIM / SELL, position reviews and 'your position' text)."""
    if a is None:
        return None
    base = {k: getattr(a, k) for k in VERDICT_FIELDS} | {"recommendation": a.recommendation, "held": False}
    rv = s.scalar(select(PositionReview).where(PositionReview.user_id == uid, PositionReview.symbol == a.symbol))
    if rv is None or rv.date < a.date or not open_position(s, uid, a.symbol):
        return base
    return {k: getattr(rv, k) for k in VERDICT_FIELDS} | {"recommendation": rv.recommendation, "held": True}


def view(s: Session, uid: str, a: Analysis | None) -> Any:
    """An Analysis-like object (same attributes) as this account sees it; see effective()."""
    if a is None:
        return None
    from types import SimpleNamespace
    row = {c.name: getattr(a, c.name) for c in a.__table__.columns}
    return SimpleNamespace(**(row | effective(s, uid, a)))


def latest_view(s: Session, uid: str, symbol: str) -> Any:
    return view(s, uid, s.scalar(select(Analysis).where(Analysis.symbol == symbol).order_by(Analysis.date.desc())))


# ---------------------------------------------------------------- Telegram owner
def telegram_owner(s: Session) -> str:
    """The account whose watchlist and positions the Telegram bot reports on (one bot, one chat: the owner's)."""
    p = s.get(Preference, TELEGRAM_OWNER_KEY)
    return str(p.value) if p and p.value else LOCAL_UID


def set_telegram_owner(s: Session, uid: str) -> None:
    s.merge(Preference(key=TELEGRAM_OWNER_KEY, value=uid))


# ---------------------------------------------------------------- local data, delete
_OWNED = (WatchlistItem, Position, PositionReview)


def local_data_counts(s: Session) -> dict[str, int]:
    """Rows made before sign-in was switched on (owned by 'local')."""
    out = {m.__tablename__: s.scalar(select(func.count()).select_from(m).where(m.user_id == LOCAL_UID)) or 0 for m in _OWNED}
    out["user_prefs"] = s.scalar(select(func.count()).select_from(UserPref).where(UserPref.user_id == LOCAL_UID)) or 0
    return out


def claim_local(s: Session, uid: str) -> dict[str, int]:
    """Give the pre-sign-in data (watchlist, positions, preferences) to this account; stocks it already watches are kept
    once. It also becomes the Telegram owner, since the bot reported on that data."""
    if uid == LOCAL_UID:
        return {}
    mine = set(watchlist_symbols(s, uid))
    moved_w = 0
    for w in s.scalars(select(WatchlistItem).where(WatchlistItem.user_id == LOCAL_UID)).all():
        if w.symbol in mine:
            s.delete(w)
        else:
            w.user_id = uid
            moved_w += 1
    _claim_lists(s, uid)
    moved_p = s.execute(update(Position).where(Position.user_id == LOCAL_UID).values(user_id=uid)).rowcount or 0
    s.execute(delete(PositionReview).where(PositionReview.user_id == LOCAL_UID))
    have = set(s.scalars(select(UserPref.key).where(UserPref.user_id == uid)))
    moved_k = 0
    for p in s.scalars(select(UserPref).where(UserPref.user_id == LOCAL_UID)).all():
        if p.key not in have:
            s.add(UserPref(user_id=uid, key=p.key, value=p.value))
            moved_k += 1
        s.delete(p)
    if telegram_owner(s) == LOCAL_UID:
        set_telegram_owner(s, uid)
    s.flush()
    for sym in {p.symbol for p in s.scalars(select(Position).where(Position.user_id == uid, Position.closed.is_(False)))}:
        try:
            refresh_review(s, uid, sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("position review after claim %s: %s", sym, exc)
    return {"watchlist": moved_w, "positions": moved_p, "preferences": moved_k}


def _claim_lists(s: Session, uid: str) -> None:
    """The pre-sign-in lists join this account's: a list with the same name is merged into it, others are moved over."""
    mine = {g.name.lower(): g for g in s.scalars(select(WatchlistGroup).where(WatchlistGroup.user_id == uid))}
    top = max((g.sort_order for g in mine.values()), default=-1)
    for g in s.scalars(select(WatchlistGroup).where(WatchlistGroup.user_id == LOCAL_UID).order_by(WatchlistGroup.sort_order, WatchlistGroup.id)).all():
        same = mine.get(g.name.lower())
        if same is None:
            top += 1
            g.user_id, g.sort_order = uid, top
            continue
        have = set(s.scalars(select(WatchlistGroupItem.symbol).where(WatchlistGroupItem.group_id == same.id)))
        for it in s.scalars(select(WatchlistGroupItem).where(WatchlistGroupItem.group_id == g.id)).all():
            if it.symbol not in have:
                s.add(WatchlistGroupItem(group_id=same.id, symbol=it.symbol, added_at=it.added_at))
        s.execute(delete(WatchlistGroupItem).where(WatchlistGroupItem.group_id == g.id))
        s.delete(g)
    s.flush()


def delete_account_data(s: Session, uid: str) -> dict[str, int]:
    """Everything this account owns. Shared market data stays (it isn't anyone's)."""
    gids = list(s.scalars(select(WatchlistGroup.id).where(WatchlistGroup.user_id == uid)))
    if gids:
        s.execute(delete(WatchlistGroupItem).where(WatchlistGroupItem.group_id.in_(gids)))
        s.execute(delete(WatchlistGroup).where(WatchlistGroup.id.in_(gids)))
    out = {m.__tablename__: s.execute(delete(m).where(m.user_id == uid)).rowcount or 0 for m in _OWNED}
    out["user_prefs"] = s.execute(delete(UserPref).where(UserPref.user_id == uid)).rowcount or 0
    out["users"] = s.execute(delete(UserAccount).where(UserAccount.uid == uid)).rowcount or 0
    if telegram_owner(s) == uid:
        s.execute(delete(Preference).where(Preference.key == TELEGRAM_OWNER_KEY))
    return out
