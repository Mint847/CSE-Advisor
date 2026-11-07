"""Routes for your alerts, the inbox, and notes. Every row is scoped to the signed-in account (user_id)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from . import alerts
from .auth import User, current_user
from .config import settings
from .db import AlertEvent, Note, Position, Security, SessionLocal, UserAlert

router = APIRouter()


def _cap(name: str, default: int, u: User) -> int:
    return 10**9 if u.admin else int((settings().get("accounts") or {}).get(name, default))


# ------------------------------------------------------------------ alert rules
class AlertIn(BaseModel):
    symbol: str
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)
    once: bool = False


class AlertPatch(BaseModel):
    active: bool | None = None
    once: bool | None = None
    params: dict[str, Any] | None = None


def _rule(r: UserAlert) -> dict[str, Any]:
    return {"id": r.id, "symbol": r.symbol, "kind": r.kind, "params": r.params, "text": alerts.describe(r.kind, r.params),
            "active": r.active, "once": r.once, "created_at": r.created_at.isoformat(timespec="seconds"),
            "last_fired": r.last_fired.isoformat(timespec="seconds") + "Z" if r.last_fired else None, "fired_count": r.fired_count or 0}


@router.get("/api/alerts")
def list_alerts(u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal() as s:
        rows = s.scalars(select(UserAlert).where(UserAlert.user_id == u.uid).order_by(UserAlert.symbol, UserAlert.id)).all()
        return {"rules": [_rule(r) for r in rows],
                "kinds": {k: {"label": v["label"], "when": v["when"], "fields": v["fields"]} for k, v in alerts.KINDS.items()}}


@router.post("/api/alerts")
def add_alert(body: AlertIn, u: User = Depends(current_user)) -> dict[str, Any]:
    sym = body.symbol.strip().upper()
    try:
        params = alerts.clean_params(body.kind, body.params)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    with SessionLocal.begin() as s:
        if not s.get(Security, sym):
            raise HTTPException(400, "Unknown stock")
        n = s.scalar(select(func.count()).select_from(UserAlert).where(UserAlert.user_id == u.uid)) or 0
        if n >= _cap("max_alerts", 100, u):
            raise HTTPException(400, "You have too many alerts. Delete some first.")
        r = UserAlert(user_id=u.uid, symbol=sym, kind=body.kind, params=params, once=body.once)
        s.add(r)
        s.flush()
        out = _rule(r)
    alerts.forget_cache()
    return out


def _own_rule(s, u: User, aid: int) -> UserAlert:
    r = s.get(UserAlert, aid)
    if not r or r.user_id != u.uid:
        raise HTTPException(404, "not found")
    return r


@router.put("/api/alerts/{aid}")
def edit_alert(aid: int, body: AlertPatch, u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        r = _own_rule(s, u, aid)
        if body.params is not None:
            try:
                r.params = alerts.clean_params(r.kind, body.params)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None
        if body.once is not None:
            r.once = body.once
        if body.active is not None:
            r.active = body.active
        out = _rule(r)
    alerts.forget_cache()
    return out


@router.delete("/api/alerts/{aid}")
def delete_alert(aid: int, u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        s.delete(_own_rule(s, u, aid))
    alerts.forget_cache()
    return {"ok": True}


# ------------------------------------------------------------------ inbox
class ReadIn(BaseModel):
    ids: list[int] | None = None          # none = everything


def _event(e: AlertEvent) -> dict[str, Any]:
    return {"id": e.id, "symbol": e.symbol, "kind": e.kind, "text": e.text, "read": e.read, "alert_id": e.alert_id,
            "at": e.at.isoformat(timespec="seconds") + "Z"}          # stored in UTC


@router.get("/api/alerts/inbox")
def inbox(limit: int = Query(60, ge=1, le=300), unread: bool = False, u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal() as s:
        q = select(AlertEvent).where(AlertEvent.user_id == u.uid)
        if unread:
            q = q.where(AlertEvent.read.is_(False))
        rows = s.scalars(q.order_by(AlertEvent.id.desc()).limit(limit)).all()
        n = s.scalar(select(func.count()).select_from(AlertEvent).where(AlertEvent.user_id == u.uid, AlertEvent.read.is_(False))) or 0
        return {"unread": n, "events": [_event(e) for e in rows]}


@router.post("/api/alerts/read")
def mark_read(body: ReadIn, u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        q = select(AlertEvent).where(AlertEvent.user_id == u.uid, AlertEvent.read.is_(False))
        if body.ids is not None:
            q = q.where(AlertEvent.id.in_(body.ids))
        rows = s.scalars(q).all()
        for e in rows:
            e.read = True
        return {"ok": True, "marked": len(rows)}


@router.post("/api/alerts/clear")
def clear_inbox(u: User = Depends(current_user)) -> dict[str, Any]:
    """Remove the entries you have already read."""
    with SessionLocal.begin() as s:
        rows = s.scalars(select(AlertEvent).where(AlertEvent.user_id == u.uid, AlertEvent.read.is_(True))).all()
        for e in rows:
            s.delete(e)
        return {"ok": True, "removed": len(rows)}


# ------------------------------------------------------------------ notes
MAX_BODY = 20_000


class NoteIn(BaseModel):
    body: str
    symbol: str | None = None
    position_id: int | None = None
    tags: list[str] = Field(default_factory=list)
    pinned: bool = False


class NotePatch(BaseModel):
    body: str | None = None
    tags: list[str] | None = None
    pinned: bool | None = None


def _tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for t in tags:
        t = t.strip().lower().lstrip("#")[:30]
        if t and t not in out:
            out.append(t)
    return out[:12]


def _note(n: Note) -> dict[str, Any]:
    return {"id": n.id, "symbol": n.symbol, "position_id": n.position_id, "body": n.body, "tags": n.tags or [], "pinned": n.pinned,
            "created_at": n.created_at.isoformat(timespec="seconds") + "Z", "updated_at": n.updated_at.isoformat(timespec="seconds") + "Z"}


@router.get("/api/notes")
def list_notes(symbol: str | None = None, position_id: int | None = None, tag: str | None = None, q: str | None = None,
               limit: int = Query(300, ge=1, le=1000), u: User = Depends(current_user)) -> list[dict[str, Any]]:
    with SessionLocal() as s:
        sel = select(Note).where(Note.user_id == u.uid)
        if symbol:
            sel = sel.where(Note.symbol == symbol.upper())
        if position_id is not None:
            sel = sel.where(Note.position_id == position_id)
        if q:
            like = f"%{q.strip()}%"
            sel = sel.where(or_(Note.body.ilike(like), Note.symbol.ilike(like)))
        rows = s.scalars(sel.order_by(Note.pinned.desc(), Note.updated_at.desc()).limit(limit if not tag else 1000)).all()
        if tag:
            tg = tag.strip().lower().lstrip("#")
            rows = [n for n in rows if tg in (n.tags or [])][:limit]
        return [_note(n) for n in rows]


@router.post("/api/notes")
def add_note(body: NoteIn, u: User = Depends(current_user)) -> dict[str, Any]:
    text = body.body.strip()
    if not text:
        raise HTTPException(400, "The note is empty")
    if len(text) > MAX_BODY:
        raise HTTPException(400, f"A note can be at most {MAX_BODY:,} characters")
    sym = body.symbol.strip().upper() if body.symbol else None
    with SessionLocal.begin() as s:
        if sym and not s.get(Security, sym):
            raise HTTPException(400, "Unknown stock")
        if body.position_id is not None:
            p = s.get(Position, body.position_id)
            if not p or p.user_id != u.uid:
                raise HTTPException(404, "position not found")
            sym = sym or p.symbol
        n = s.scalar(select(func.count()).select_from(Note).where(Note.user_id == u.uid)) or 0
        if n >= _cap("max_notes", 2000, u):
            raise HTTPException(400, "You have too many notes. Delete old ones first.")
        row = Note(user_id=u.uid, symbol=sym, position_id=body.position_id, body=text, tags=_tags(body.tags), pinned=body.pinned)
        s.add(row)
        s.flush()
        return _note(row)


def _own_note(s, u: User, nid: int) -> Note:
    n = s.get(Note, nid)
    if not n or n.user_id != u.uid:
        raise HTTPException(404, "not found")
    return n


@router.put("/api/notes/{nid}")
def edit_note(nid: int, body: NotePatch, u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        n = _own_note(s, u, nid)
        if body.body is not None:
            text = body.body.strip()
            if not text:
                raise HTTPException(400, "The note is empty")
            if len(text) > MAX_BODY:
                raise HTTPException(400, f"A note can be at most {MAX_BODY:,} characters")
            n.body = text
        if body.tags is not None:
            n.tags = _tags(body.tags)
        if body.pinned is not None:
            n.pinned = body.pinned
        n.updated_at = datetime.utcnow()
        return _note(n)


@router.delete("/api/notes/{nid}")
def delete_note(nid: int, u: User = Depends(current_user)) -> dict[str, Any]:
    with SessionLocal.begin() as s:
        s.delete(_own_note(s, u, nid))
    return {"ok": True}
