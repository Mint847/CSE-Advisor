"""Rule-based classification of CSE announcements (free; optional LLM upgrade later)."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# (regex on title/body, category, sentiment -1..1, materiality 1-5). First match wins, so specific rules come
# first and the generic "Corporate Disclosure" title last: once a notice's attachment name / text is read
# ("Appointment of Deputy Chairman", "Disposal of a subsidiary"), the real subject decides its category.
RULES: list[tuple[str, str, float, int]] = [
    (r"RIGHTS ISSUE|RIGHTS SHARES", "rights_issue", -0.2, 5),
    (r"TRADING HALT LIFTED", "halt_lifted", 0.0, 1),
    (r"TRADING HALT", "halt", 0.0, 2),
    # 'modified audit opinion' is the umbrella term for qualified / adverse / disclaimer opinions
    (r"QUALIFIED OPINION|MODIFIED AUDIT OPINION|ADVERSE OPINION|DISCLAIMER OF OPINION|GOING CONCERN|DEFAULT", "red_flag", -0.8, 5),
    (r"SUSPEN", "suspension", -0.6, 5),
    (r"DEALINGS BY DIRECTORS|DIRECTOR.*DEALING", "director_dealing", 0.0, 3),
    (r"DIVIDEND", "dividend", 0.4, 3),
    (r"SUB-?DIVISION|SPLIT|CONSOLIDATION OF SHARES", "share_split", 0.0, 4),
    (r"BONUS|CAPITALI[SZ]ATION OF RESERVES|SCRIP", "bonus_issue", 0.1, 4),
    (r"ERRATA|CORRIGENDUM", "errata", -0.05, 1),
    (r"INTERIM FINANCIAL|FINANCIAL STATEMENTS|QUARTER(LY)? RESULTS|ANNUAL REPORT", "results", 0.0, 3),
    (r"MANDATORY OFFER|TAKEOVER|MERGER|AMALGAMATION|ACQUISITION OF (A |THE )?(SUBSIDIARY|COMPANY|STAKE|CONTROLLING)", "mna", 0.3, 5),
    (r"DISPOSAL OF|DIVESTMENT|DIVEST", "disposal", 0.1, 3),
    (r"INVESTMENT IN|JOINT VENTURE|SUBSCRIPTION OF SHARES|INCORPORATION OF", "investment", 0.2, 3),
    (r"SHARE REPURCHASE|BUY-?BACK", "buyback", 0.5, 4),
    (r"RATING", "rating", 0.0, 3),
    (r"DEBENTURE", "debt_issue", 0.0, 2),
    (r"PRIVATE PLACEMENT|NEW ISSUE", "dilution", -0.3, 4),
    (r"ESOS|EMPLOYEE SHARE OPTION", "esos", -0.1, 2),
    (r"RELATED PARTY", "related_party", -0.1, 2),
    (r"LITIGATION|COURT|LEGAL", "litigation", -0.3, 3),
    (r"NON-?COMPLIANCE", "non_compliance", -0.3, 3),
    (r"RESIGNATION|CESSATION|RETIREMENT OF", "management_exit", -0.1, 2),
    (r"APPOINTMENT|CHANGE (IN|OF) (THE )?DIRECTORATE", "management_change", 0.0, 2),
    (r"LISTING OF (NEW )?SHARES|NOTIFICATION ON THE LISTING", "listing", 0.0, 2),
    (r"CIRCULAR TO SHAREHOLDERS", "circular", 0.0, 2),
    (r"\bAGM\b|ANNUAL GENERAL MEETING|\bEGM\b|EXTRAORDINARY GENERAL MEETING", "meeting", 0.0, 1),
    (r"CORPORATE DISCLOSURE", "disclosure", 0.0, 2),
]


def classify_announcement(title: str, body: str | None) -> tuple[str, float, int]:
    text = f"{title} {body or ''}".upper()
    for pat, cat, sent, mat in RULES:
        if re.search(pat, text):
            if cat == "director_dealing":
                if re.search(r"\bPURCHASE|BOUGHT|ACQUI", text):
                    return cat, 0.4, 3
                if re.search(r"\bSALE\b|\bSOLD\b|DISPOS", text):
                    return cat, -0.4, 3
            if cat == "rating":
                if re.search(r"UPGRADE|POSITIVE", text):
                    return cat, 0.4, 3
                if re.search(r"DOWNGRADE|NEGATIVE|WATCH NEGATIVE", text):
                    return cat, -0.5, 4
                return cat, 0.0, 2
            return cat, sent, mat
    return "other", 0.0, 1


def _date(v: Any) -> str | None:
    """'20 Feb 2026' or epoch ms -> 'YYYY-MM-DD'."""
    if v in (None, "", 0):
        return None
    if isinstance(v, (int, float)):
        from ..cse_client import ms_to_date
        return str(ms_to_date(v))
    for fmt in ("%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def attachment_subject(file_url: str | None) -> str | None:
    """'.../Letter to CSE - Appointment of Duputy Chairman - Dilip Wijeyeratne_11906678805790032.pdf'
    -> 'Letter to CSE - Appointment of Duputy Chairman - Dilip Wijeyeratne'."""
    if not file_url:
        return None
    from urllib.parse import unquote
    name = unquote(file_url.rsplit("/", 1)[-1])
    name = re.sub(r"\.(pdf|docx?|xlsx?)$", "", name, flags=re.I)
    name = re.sub(r"_\d{8,}$", "", name)
    name = re.sub(r"_+", " ", name).strip()
    return name[:290] or None


def parse_typed(base: dict[str, Any]) -> dict[str, Any]:
    """Facts from a getAnnouncementById record (see CSEClient.announcement_typed)."""
    t = base.get("dType") or ""
    ev: dict[str, Any] = {"dtype": t}
    if t.startswith("CashDividend"):
        ev.update(kind="cash_dividend", dps=base.get("votingDivPerShare"), dps_non_voting=base.get("nonVotingDivPerShare"),
                  xd=_date(base.get("xd")), payment=_date(base.get("payment")), agm=_date(base.get("agm")),
                  record=_date(base.get("recordDate")), fy=base.get("financialYear"), final=bool(base.get("firstAndFinal")))
    elif t.startswith("ScripDividend"):
        ev.update(kind="scrip_dividend", proportion=base.get("votingPropotion"), xd=_date(base.get("xd")),
                  payment=_date(base.get("payment")), agm=_date(base.get("agm")), fy=base.get("financialYear"))
    elif t == "DealingsByDirectors":
        trades, net = [], 0.0
        for x in base.get("directorTransactions") or []:
            kind = (x.get("transType") or "").lower()
            side = "buy" if kind.startswith("acqui") or "purchase" in kind else "sell" if kind.startswith("dispos") or "sale" in kind else kind
            qty, px = float(x.get("quantity") or 0), float(x.get("price") or 0)
            trades.append({"side": side, "date": _date(x.get("transactionDate")), "qty": qty, "price": px, "value": qty * px})
            net += qty * px * (1 if side == "buy" else -1 if side == "sell" else 0)
        ev.update(kind="dealing", director=(base.get("dircetorsName") or base.get("directorsName") or "").strip(),
                  role=base.get("natureOfDir"), own_account=bool(base.get("accTypeOwnAcc")), trades=trades, net_value=net)
    elif t == "AppointmentOfDirectors":
        ev.update(kind="appointment", directors=[{"name": (d.get("dirName") or "").strip(), "role": d.get("natureOfDir"),
                                                  "date": _date(d.get("effectiveDate"))} for d in base.get("dirList") or []])
    elif t.startswith("Agm"):
        ev.update(kind="agm", date=_date(base.get("dateOfAgm")), venue=base.get("venue"))
    elif t:
        ev.update(kind=t)
    else:
        ev.update(kind="general")
    return ev


def event_text(ev: dict[str, Any]) -> str:
    """Words for the classifier and the UI."""
    k = ev.get("kind")
    if k == "cash_dividend" and ev.get("dps"):
        return f"Cash dividend Rs {ev['dps']:g} per share, XD {ev.get('xd') or '?'}, payment {ev.get('payment') or '?'}"
    if k == "dealing":
        parts = [f"{t['side']} {t['qty']:,.0f} @ {t['price']:g}" for t in ev.get("trades", [])]
        sides = {t["side"] for t in ev.get("trades", [])}
        word = "Acquisition" if sides == {"buy"} else "Disposal" if sides == {"sell"} else "Dealing"
        return f"{word} by {ev.get('director') or 'a director'}: " + "; ".join(parts)
    if k == "appointment":
        return "Appointment of " + ", ".join(f"{d['name']} ({d.get('role') or 'director'})" for d in ev.get("directors", []))
    if k == "agm" and ev.get("date"):
        return f"AGM on {ev['date']}"
    return ""


# Rights issues are no longer a blanket red flag: corporate_actions.phases() handles the
# XR gap (TERP) and the post-listing supply overhang explicitly.
HARD_FLAGS = {"red_flag": 120, "suspension": 120, "dilution": 30}
# a director's net sale must be at least this large (LKR) to count as the Classic engine's "director selling" flag
DIRECTOR_SELL_MIN_LKR = 1_000_000
# The Classic engine keeps its original rule: a rights issue is a dilution red flag for 60 days.
LEGACY_HARD_FLAGS = {**HARD_FLAGS, "rights_issue": 60}


def news_score(announcements: list[dict[str, Any]], asof: datetime, half_life_days: float = 10.0,
               legacy: bool = False) -> dict[str, Any]:
    """Time-decayed sentiment -> 0-100 score plus hard red flags."""
    if not announcements:
        return {"score": None, "items": [], "red_flags": [], "detail": "no announcements on record"}
    num, den = 0.0, 0.0
    flags = []
    items = []
    for a in announcements:
        if not a.get("date"):
            continue
        age = (asof - a["date"]).days
        if age < 0 or age > 180:
            continue
        w = (0.5 ** (age / half_life_days)) * (a.get("materiality") or 1)
        num += w * (a.get("sentiment") or 0)
        den += w
        items.append({**a, "age_days": age, "weight": round(w, 3)})
        lim = (LEGACY_HARD_FLAGS if legacy else HARD_FLAGS).get(a.get("category") or "")
        if lim and age <= lim:
            flags.append({"category": a["category"], "title": a["title"], "date": a["date"].strftime("%Y-%m-%d"), "age_days": age})
        if a.get("category") == "director_dealing" and (a.get("sentiment") or 0) < 0 and age <= 30:
            net = (a.get("event") or {}).get("net_value")
            if net is None or net <= -DIRECTOR_SELL_MIN_LKR:
                flags.append({"category": "director_selling", "title": a["title"] + (f" (net sale LKR {-net:,.0f})" if net else ""),
                              "date": a["date"].strftime("%Y-%m-%d"), "age_days": age})
    if den == 0:
        return {"score": None, "items": [], "red_flags": flags, "detail": "no recent announcements"}
    sent = num / den
    return {"score": round(50 + 50 * sent, 1), "sentiment": round(sent, 3), "items": sorted(items, key=lambda x: x["age_days"])[:15],
            "red_flags": flags, "detail": f"{len(items)} announcements in the last 180 days"}
