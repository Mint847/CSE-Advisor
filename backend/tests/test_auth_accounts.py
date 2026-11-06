"""Sign-in and accounts: token checks, profiles, each account seeing only its own data, the one-off migration."""
import time
from datetime import date, datetime, timedelta

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from cse_advisor import accounts, auth
from cse_advisor.auth import User
from cse_advisor.db import LOCAL_UID, Base, Position, Preference, Security, UserAccount, UserPref, WatchlistItem

PID = "demo-project"
NOW = time.time()


# ---------------------------------------------------------------- token verification
@pytest.fixture(scope="module")
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "securetoken")])
    start = datetime.utcnow() - timedelta(days=1)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(start).not_valid_after(start + timedelta(days=3)).sign(key, hashes.SHA256()))
    auth.certs.set({"k1": cert.public_bytes(serialization.Encoding.PEM).decode()}, max_age=3600)
    return key


def _token(key, kid="k1", **over):
    claims = {"iss": f"https://securetoken.google.com/{PID}", "aud": PID, "sub": "uid-a", "iat": int(NOW) - 10,
              "exp": int(NOW) + 3600, "auth_time": int(NOW) - 10, "email": "a@example.com", "email_verified": True,
              "firebase": {"sign_in_provider": "password"}}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


def test_valid_token_gives_the_uid(signer):
    c = auth.verify_token(_token(signer), PID, now=NOW)
    assert c["sub"] == "uid-a"
    u = auth.user_from_claims(c)
    assert (u.uid, u.email, u.provider, u.admin) == ("uid-a", "a@example.com", "password", False)


@pytest.mark.parametrize("over", [
    {"aud": "other-project"},
    {"iss": "https://securetoken.google.com/other-project"},
    {"exp": int(NOW) - 3600},                       # expired an hour ago
    {"iat": int(NOW) + 3600},                       # issued in the future
    {"auth_time": int(NOW) + 3600},
    {"sub": ""},
    {"sub": "x" * 129},
])
def test_bad_claims_are_rejected(signer, over):
    with pytest.raises(HTTPException) as e:
        auth.verify_token(_token(signer, **over), PID, now=NOW)
    assert e.value.status_code == 401


def test_unknown_key_wrong_algorithm_and_garbage_are_rejected(signer, monkeypatch):
    monkeypatch.setattr(auth.certs, "_refresh", lambda: None)          # no network: the key id simply isn't known
    for tok in (_token(signer, kid="nope"), jwt.encode({"sub": "x", "aud": PID}, "secret", algorithm="HS256"), "not-a-token"):
        with pytest.raises(HTTPException) as e:
            auth.verify_token(tok, PID, now=NOW)
        assert e.value.status_code == 401
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)   # right kid, signed by someone else
    with pytest.raises(HTTPException):
        auth.verify_token(_token(other), PID, now=NOW)


def test_unsigned_emulator_tokens_only_for_demo_projects(monkeypatch):
    unsigned = jwt.encode({"iss": "https://securetoken.google.com/demo-cse", "aud": "demo-cse", "sub": "e1", "iat": int(NOW),
                           "exp": int(NOW) + 600}, None, algorithm="none")
    monkeypatch.setenv("FIREBASE_AUTH_EMULATOR_HOST", "127.0.0.1:9099")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "demo-cse")
    assert auth.verify_token(unsigned, now=NOW)["sub"] == "e1"
    assert auth.public_config()["emulator"] == "http://127.0.0.1:9099"
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "real-project")             # a real project never accepts them
    assert auth.emulator_host() is None
    real = jwt.encode({"iss": "https://securetoken.google.com/real-project", "aud": "real-project", "sub": "e1", "iat": int(NOW),
                       "exp": int(NOW) + 600}, None, algorithm="none")
    with pytest.raises(HTTPException):
        auth.verify_token(real, now=NOW)


def test_admin_needs_a_verified_listed_email_or_the_claim(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "Owner@Example.com, other@x.org")
    base = {"sub": "u1", "email": "owner@example.com"}
    assert auth.user_from_claims({**base, "email_verified": True}).admin
    assert not auth.user_from_claims({**base, "email_verified": False}).admin      # anyone can sign up with an unverified address
    assert auth.user_from_claims({"sub": "u2", "email": "z@z.z", "admin": True}).admin
    assert not auth.user_from_claims({"sub": "u3", "email": "z@z.z", "admin": "yes"}).admin


def test_auth_off_means_the_local_admin(monkeypatch):
    monkeypatch.delenv("FIREBASE_PROJECT_ID", raising=False)
    assert not auth.enabled() and auth.public_config() == {"enabled": False}
    u = auth.local_user()
    assert u.uid == LOCAL_UID and u.admin


# ---------------------------------------------------------------- accounts on an in-memory database
@pytest.fixture()
def Session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    with S.begin() as s:
        for sym in ("JKH.N0000", "COMB.N0000", "HNB.N0000"):
            s.add(Security(symbol=sym, cse_id=1, name=sym))
    return S


A = User(uid="uid-a", email="a@example.com", name="", provider="password")
B = User(uid="uid-b", email="b@gmail.com", name="Bee Gee Smith", provider="google.com")


def test_profile_is_created_once_and_never_overwritten(Session):
    with Session.begin() as s:
        row = accounts.ensure_profile(s, A, "Kavi", "Perera")              # the name typed at sign-up
        assert (row.first_name, row.last_name) == ("Kavi", "Perera")
    with Session.begin() as s:
        row = accounts.ensure_profile(s, A, "Other", "Name")               # later sign-ins never replace it
        assert (row.first_name, row.last_name) == ("Kavi", "Perera")
        g = accounts.ensure_profile(s, B)                                  # Google name split at the first space
        assert (g.first_name, g.last_name) == ("Bee", "Gee Smith")
        e = accounts.ensure_profile(s, User(uid="uid-c", email="carol@x.org"))   # else the email before @
        assert (e.first_name, e.last_name) == ("carol", "")
        row = accounts.set_name(s, A, "Kavindu", "")
        assert (row.first_name, row.last_name) == ("Kavindu", "")


def test_each_account_has_its_own_watchlist_prefs_and_positions(Session):
    with Session.begin() as s:
        s.add_all([WatchlistItem(user_id="uid-a", symbol="JKH.N0000"), WatchlistItem(user_id="uid-a", symbol="COMB.N0000"),
                   WatchlistItem(user_id="uid-b", symbol="JKH.N0000")])
        s.add(Position(user_id="uid-b", symbol="HNB.N0000", entry_date=date(2026, 1, 5), entry_price=100, quantity=10))
        accounts.put_pref(s, "uid-a", "catalog", {"x": True})
    with Session() as s:
        assert accounts.watchlist_symbols(s, "uid-a") == ["JKH.N0000", "COMB.N0000"]
        assert accounts.watchlist_symbols(s, "uid-b") == ["JKH.N0000"]
        assert accounts.watchlist_symbols(s) == ["JKH.N0000", "COMB.N0000"]                 # everyone's, each once
        assert set(accounts.tracked_symbols(s)) == {"JKH.N0000", "COMB.N0000", "HNB.N0000"}  # + held stocks
        assert accounts.user_symbols(s, "uid-a") == {"JKH.N0000", "COMB.N0000"}
        assert accounts.get_pref(s, "uid-a", "catalog") == {"x": True}
        assert accounts.get_pref(s, "uid-b", "catalog") is None
        assert accounts.open_position(s, "uid-a", "HNB.N0000") is None
        assert accounts.open_position(s, "uid-b", "HNB.N0000") is not None


def test_claim_local_moves_old_data_and_sets_the_telegram_owner(Session):
    with Session.begin() as s:
        s.add_all([WatchlistItem(user_id=LOCAL_UID, symbol="JKH.N0000"), WatchlistItem(user_id=LOCAL_UID, symbol="COMB.N0000"),
                   WatchlistItem(user_id="uid-a", symbol="JKH.N0000"),
                   Position(user_id=LOCAL_UID, symbol="HNB.N0000", entry_date=date(2026, 1, 5), entry_price=100, quantity=10, closed=True),
                   UserPref(user_id=LOCAL_UID, key="catalog", value={"a": 1})])
    with Session.begin() as s:
        assert accounts.local_data_counts(s)["watchlist"] == 2
        moved = accounts.claim_local(s, "uid-a")
        assert moved == {"watchlist": 1, "positions": 1, "preferences": 1}
        assert accounts.telegram_owner(s) == "uid-a"
    with Session() as s:
        assert sorted(accounts.watchlist_symbols(s, "uid-a")) == ["COMB.N0000", "JKH.N0000"]   # JKH kept once
        assert sum(accounts.local_data_counts(s).values()) == 0


def test_delete_account_data_removes_only_that_account(Session):
    with Session.begin() as s:
        accounts.ensure_profile(s, A)
        accounts.ensure_profile(s, B)
        s.add_all([WatchlistItem(user_id="uid-a", symbol="JKH.N0000"), WatchlistItem(user_id="uid-b", symbol="JKH.N0000")])
        accounts.put_pref(s, "uid-a", "k", 1)
        accounts.set_telegram_owner(s, "uid-a")
    with Session.begin() as s:
        got = accounts.delete_account_data(s, "uid-a")
        assert got["watchlist"] == 1 and got["users"] == 1 and got["user_prefs"] == 1
    with Session() as s:
        assert s.get(UserAccount, "uid-a") is None and s.get(UserAccount, "uid-b") is not None
        assert accounts.watchlist_symbols(s) == ["JKH.N0000"]
        assert accounts.telegram_owner(s) == LOCAL_UID


def test_a_holder_sees_the_position_review_others_see_the_shared_analysis(Session):
    from cse_advisor.db import Analysis, PositionReview
    d = date(2026, 11, 5)
    with Session.begin() as s:
        s.add(Analysis(symbol="JKH.N0000", date=d, verdict="BUY", score=72, patterns=[], levels={}, liquidity={}, trend={},
                       recommendation={"original": {"verdict": "BUY"}}))
        s.add(Position(user_id="uid-a", symbol="JKH.N0000", entry_date=date(2026, 10, 1), entry_price=20, quantity=100))
        s.flush()
        s.add(PositionReview(user_id="uid-a", symbol="JKH.N0000", position_id=1, date=d, verdict="HOLD", score=70,
                             recommendation={"original": {"verdict": "HOLD", "position_review": {"action": "HOLD"}}}))
    with Session() as s:
        a = s.scalar(select(Analysis))
        assert accounts.view(s, "uid-a", a).verdict == "HOLD"
        assert accounts.view(s, "uid-a", a).recommendation["original"]["position_review"]["action"] == "HOLD"
        assert accounts.view(s, "uid-b", a).verdict == "BUY"
        a.date = d + timedelta(days=1)                                     # a newer analysis than the review: shared view
        assert accounts.effective(s, "uid-a", a)["held"] is False


# ---------------------------------------------------------------- migration of a database made before accounts
def test_old_database_is_migrated_to_the_local_user(tmp_path, monkeypatch):
    from cse_advisor import db
    path = tmp_path / "old.db"
    eng = create_engine(f"sqlite:///{path.as_posix()}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE securities (symbol VARCHAR(20) PRIMARY KEY, cse_id INTEGER, name VARCHAR(200))"))
        c.execute(text("CREATE TABLE watchlist (symbol VARCHAR(20) PRIMARY KEY REFERENCES securities(symbol), added_at DATETIME, notes TEXT)"))
        c.execute(text("CREATE TABLE positions (id INTEGER PRIMARY KEY, symbol VARCHAR(20), entry_date DATE, entry_price FLOAT, quantity INTEGER, "
                       "stop FLOAT, target FLOAT, closed BOOLEAN, exit_date DATE, exit_price FLOAT, notes TEXT)"))
        c.execute(text("CREATE TABLE preferences (key VARCHAR(100) PRIMARY KEY, value JSON)"))
        c.execute(text("INSERT INTO securities VALUES ('JKH.N0000', 1, 'JKH'), ('COMB.N0000', 2, 'COMB')"))
        c.execute(text("INSERT INTO watchlist VALUES ('COMB.N0000', '2026-01-02 00:00:00', NULL), ('JKH.N0000', '2026-01-01 00:00:00', 'n')"))
        c.execute(text("INSERT INTO positions (symbol, entry_date, entry_price, quantity, closed) VALUES ('JKH.N0000', '2026-01-05', 100, 10, 0)"))
        c.execute(text("""INSERT INTO preferences VALUES ('catalog', '{"x": true}'), ('stock:JKH.N0000', '{"mode": "top5"}'), ('screener_universe', '{}')"""))
    monkeypatch.setattr(db, "engine", eng)
    db.init_db()
    db.init_db()                                                     # running twice changes nothing
    S = sessionmaker(bind=eng)
    with S() as s:
        assert [(w.user_id, w.symbol, w.notes) for w in s.scalars(select(WatchlistItem).order_by(WatchlistItem.added_at))] == \
            [(LOCAL_UID, "JKH.N0000", "n"), (LOCAL_UID, "COMB.N0000", None)]
        assert s.scalar(select(Position.user_id)) == LOCAL_UID
        assert {p.key for p in s.scalars(select(UserPref))} == {"catalog", "stock:JKH.N0000"}
        assert s.get(UserPref, (LOCAL_UID, "catalog")).value == {"x": True}
        from cse_advisor.db import WatchlistGroup, WatchlistGroupItem
        g = s.scalar(select(WatchlistGroup))                                 # old watchlist -> "Watchlist 1" holding both stocks
        assert (g.user_id, g.name) == (LOCAL_UID, "Watchlist 1")
        assert sorted(s.scalars(select(WatchlistGroupItem.symbol).where(WatchlistGroupItem.group_id == g.id))) == ["COMB.N0000", "JKH.N0000"]
        assert s.get(Preference, "screener_universe") is not None    # app state stays shared


# ---------------------------------------------------------------- the API gate
@pytest.fixture()
def client(Session, signer, monkeypatch):
    from fastapi.testclient import TestClient
    from cse_advisor import ingest, main
    monkeypatch.setenv("FIREBASE_PROJECT_ID", PID)
    monkeypatch.setenv("ADMIN_EMAILS", "")
    monkeypatch.setattr(main, "SessionLocal", Session)
    monkeypatch.setattr(ingest, "SessionLocal", Session)               # never the real database
    monkeypatch.setattr(auth.time, "time", lambda: NOW)
    monkeypatch.setattr(accounts, "refresh_review", lambda s, uid, sym: None)    # no price history here
    return TestClient(main.app)


def _h(signer, uid, email):
    return {"Authorization": "Bearer " + _token(signer, sub=uid, email=email)}


def test_api_needs_a_token_except_the_public_config(client, signer):
    assert client.get("/api/auth/config").json()["enabled"] is True
    r = client.get("/api/watchlist")
    assert r.status_code == 401 and r.headers.get("www-authenticate") == "Bearer"
    assert client.get("/api/watchlist", headers={"Authorization": "Bearer junk"}).status_code == 401
    ok = client.get("/api/watchlist", headers=_h(signer, "uid-a", "a@example.com"))
    assert ok.status_code == 200 and ok.json() == []
    assert ok.headers["x-frame-options"] == "SAMEORIGIN" and "frame-ancestors 'self'" in ok.headers["content-security-policy"]
    assert "cross-origin-opener-policy" not in ok.headers                      # COOP would break the Google pop-up


def test_accounts_cannot_see_or_touch_each_other(client, signer, Session):
    ha, hb = _h(signer, "uid-a", "a@example.com"), _h(signer, "uid-b", "b@example.com")
    with Session.begin() as s:
        s.add(WatchlistItem(user_id="uid-a", symbol="JKH.N0000"))
    assert client.post("/api/me", json={"first_name": "Ann", "last_name": "A"}, headers=ha).json()["first_name"] == "Ann"
    assert client.put("/api/prefs/catalog", json={"mine": 1}, headers=ha).status_code == 200
    assert client.get("/api/prefs/catalog", headers=hb).json() is None
    assert client.get("/api/prefs/catalog", headers=ha).json() == {"mine": 1}
    body = {"symbol": "JKH.N0000", "entry_date": "2026-01-05", "entry_price": 100, "quantity": 10}
    assert client.post("/api/positions", json=body, headers=ha).status_code == 200
    pid = client.get("/api/positions", headers=ha).json()[0]["id"]
    assert client.get("/api/positions", headers=hb).json() == []
    assert client.delete(f"/api/positions/{pid}", headers=hb).status_code == 404
    assert client.post(f"/api/positions/{pid}/close", json={"exit_date": "2026-02-01", "exit_price": 110}, headers=hb).status_code == 404
    assert [w["symbol"] for w in client.get("/api/watchlist", headers=ha).json()] == ["JKH.N0000"]
    assert client.get("/api/watchlist", headers=hb).json() == []
    assert client.delete("/api/watchlist/JKH.N0000", headers=hb).status_code == 200   # removes nothing of A's
    assert len(client.get("/api/watchlist", headers=ha).json()) == 1
    assert client.delete("/api/me", headers=ha).json()["deleted"]["positions"] == 1
    assert client.get("/api/positions", headers=ha).json() == []


def test_shared_data_writes_are_for_the_admin(client, signer, monkeypatch):
    h = _h(signer, "uid-a", "a@example.com")
    for method, url in (("post", "/api/stocks/JKH.N0000/corporate-actions"), ("put", "/api/stocks/JKH.N0000/sector"),
                        ("post", "/api/jobs/eod"), ("post", "/api/screener/run"), ("post", "/api/me/claim-local")):
        assert getattr(client, method)(url, json={}, headers=h).status_code == 403, url
    assert client.post("/api/stocks/JKH.N0000/analyze", headers=h).status_code == 403     # not on A's watchlist
    st = client.get("/api/status", headers=h).json()
    assert st["admin"] is False and all(j["message"] is None for j in st["jobs"])
    monkeypatch.setenv("ADMIN_EMAILS", "a@example.com")
    assert client.put("/api/stocks/JKH.N0000/sector", json={"sector": "cap"}, headers=h).json()["ok"] is True


# ---------------------------------------------------------------- named lists (home page tabs)
def test_lists_default_create_rename_delete_and_the_untrack_rule(Session):
    with Session.begin() as s:
        s.add_all([WatchlistItem(user_id="uid-a", symbol="JKH.N0000"), WatchlistItem(user_id="uid-a", symbol="COMB.N0000")])
    with Session.begin() as s:
        gs = accounts.groups(s, "uid-a")                                   # first call: "Watchlist 1" holding both tracked stocks
        assert [g.name for g in gs] == ["Watchlist 1"]
        first = gs[0].id
        assert sorted(accounts.group_symbols(s, "uid-a", first)) == ["COMB.N0000", "JKH.N0000"]
        two = accounts.create_group(s, "uid-a", "  Swing   ideas ").id    # whitespace is tidied
        assert [g["name"] for g in accounts.lists_overview(s, "uid-a")] == ["Watchlist 1", "Swing ideas"]
        for bad, msg in (("", "Give the list a name."), ("x" * 41, "at most 40"), ("swing IDEAS", "already have")):
            with pytest.raises(accounts.ListError, match=msg):
                accounts.create_group(s, "uid-a", bad)
        accounts.rename_group(s, "uid-a", two, "Long term")
        with pytest.raises(accounts.ListError, match="already have"):
            accounts.rename_group(s, "uid-a", two, "watchlist 1")
        accounts.add_to_group(s, "uid-a", two, "JKH.N0000")                # a stock can sit on several lists
        assert accounts.symbol_lists(s, "uid-a")["JKH.N0000"] == [first, two]
    with Session.begin() as s:
        assert accounts.remove_from_group(s, "uid-a", first, "JKH.N0000") is False     # still on "Long term": stays tracked
        assert "JKH.N0000" in accounts.watchlist_symbols(s, "uid-a")
        assert accounts.remove_from_group(s, "uid-a", two, "JKH.N0000") is True        # off its last list: no longer tracked
        assert "JKH.N0000" not in accounts.watchlist_symbols(s, "uid-a")
        accounts.add_to_group(s, "uid-a", two, "HNB.N0000")
        s.add(WatchlistItem(user_id="uid-a", symbol="HNB.N0000"))
        s.flush()
        assert accounts.delete_group(s, "uid-a", two) == ["HNB.N0000"]                 # only on the deleted list
        assert accounts.watchlist_symbols(s, "uid-a") == ["COMB.N0000"]
        with pytest.raises(accounts.ListError, match="at least one"):
            accounts.delete_group(s, "uid-a", first)
        with pytest.raises(accounts.ListError, match="not found"):
            accounts.group_symbols(s, "uid-b", first)                          # someone else's list
        for i in range(9):
            accounts.create_group(s, "uid-a", f"L{i}")
        with pytest.raises(accounts.ListError, match="at most 10"):
            accounts.create_group(s, "uid-a", "one more")


def test_claim_local_merges_lists_by_name(Session):
    with Session.begin() as s:
        s.add_all([WatchlistItem(user_id=LOCAL_UID, symbol="JKH.N0000"), WatchlistItem(user_id=LOCAL_UID, symbol="COMB.N0000"),
                   WatchlistItem(user_id="uid-a", symbol="HNB.N0000")])
        s.flush()
        accounts.groups(s, "uid-a")
        extra = accounts.create_group(s, LOCAL_UID, "Banks")
        accounts.add_to_group(s, LOCAL_UID, extra.id, "COMB.N0000")
    with Session.begin() as s:
        accounts.claim_local(s, "uid-a")
    with Session() as s:
        by_name = {g["name"]: g["id"] for g in accounts.lists_overview(s, "uid-a")}
        assert set(by_name) == {"Watchlist 1", "Banks"}
        assert sorted(accounts.group_symbols(s, "uid-a", by_name["Watchlist 1"])) == ["COMB.N0000", "HNB.N0000", "JKH.N0000"]
        assert accounts.group_symbols(s, "uid-a", by_name["Banks"]) == ["COMB.N0000"]
        assert accounts.lists_overview(s, LOCAL_UID) == [{"id": accounts.groups(s, LOCAL_UID)[0].id, "name": "Watchlist 1", "count": 0}]


def test_lists_api_and_all_stocks(client, signer, Session, monkeypatch):
    from cse_advisor import main
    h = _h(signer, "uid-a", "a@example.com")
    with Session.begin() as s:
        s.add_all([WatchlistItem(user_id="uid-a", symbol="JKH.N0000"), WatchlistItem(user_id="uid-a", symbol="COMB.N0000"),
                   Position(user_id="uid-a", symbol="HNB.N0000", entry_date=date(2026, 1, 5), entry_price=100, quantity=10)])
    got = client.get("/api/lists", headers=h).json()
    assert [(x["name"], x["count"]) for x in got["lists"]] == [("Watchlist 1", 2)] and got["portfolio"] == 1
    new = client.post("/api/lists", json={"name": "Swing"}, headers=h).json()
    assert client.post("/api/lists", json={"name": "swing"}, headers=h).status_code == 400
    assert client.put(f"/api/lists/{new['id']}", json={"name": "Ideas"}, headers=h).json()["name"] == "Ideas"
    first = got["lists"][0]["id"]
    assert sorted(r["symbol"] for r in client.get(f"/api/watchlist?list={first}", headers=h).json()) == ["COMB.N0000", "JKH.N0000"]
    assert client.get(f"/api/watchlist?list={new['id']}", headers=h).json() == []
    assert [r["symbol"] for r in client.get("/api/watchlist?list=portfolio", headers=h).json()] == ["HNB.N0000"]
    assert len(client.get("/api/watchlist", headers=h).json()) == 2                    # no `list`: everything tracked
    assert client.get("/api/watchlist?list=abc", headers=h).status_code == 400
    hb = _h(signer, "uid-b", "b@example.com")
    assert client.get(f"/api/watchlist?list={first}", headers=hb).status_code == 404   # someone else's list
    assert client.delete(f"/api/lists/{first}", headers=hb).status_code == 404         # not B's list
    # all stocks
    monkeypatch.setattr(main, "_all_listed", lambda: [
        {"symbol": "JKH.N0000", "name": "JKH", "price": 20.0, "change_pct": 1.5, "volume": 10, "turnover": 200.0, "trades": 2, "market_cap": 1.0, "traded": "2026-11-05"},
        {"symbol": "ZZZ.N0000", "name": "ZZZ", "price": None, "change_pct": None, "volume": 0, "turnover": 0.0, "trades": 0, "market_cap": None, "traded": None}])
    allx = client.get("/api/stocks/all", headers=h).json()
    jk = next(r for r in allx["rows"] if r["symbol"] == "JKH.N0000")
    assert jk["lists"] == [first] and jk["held"] is False
    assert next(r for r in allx["rows"] if r["symbol"] == "ZZZ.N0000")["lists"] == []
    assert next(r for r in client.get("/api/stocks/all", headers=hb).json()["rows"] if r["symbol"] == "JKH.N0000")["lists"] == []
    # removing from the only list untracks; the list can't be deleted while it is the last one
    assert client.delete(f"/api/watchlist/JKH.N0000?list={first}", headers=h).json()["untracked"] is True
    assert client.delete(f"/api/lists/{new['id']}", headers=h).json()["ok"] is True
    assert client.delete(f"/api/lists/{first}", headers=h).status_code == 400


# ---------------------------------------------------------------- bugs found by running the real app
def test_refresh_review_accepts_the_json_ready_analysis(Session, monkeypatch):
    """analyze_symbol returns JSON-ready values (the date is text); the stored review needs a real date."""
    from cse_advisor import engine as eng
    from cse_advisor.db import Analysis, PositionReview
    d = date(2026, 11, 5)
    with Session.begin() as s:
        s.add(Analysis(symbol="JKH.N0000", date=d, verdict="BUY", score=72, patterns=[], levels={}, liquidity={}, trend={}, recommendation={}))
        s.add(Position(user_id="uid-a", symbol="JKH.N0000", entry_date=date(2026, 10, 1), entry_price=20, quantity=100))
    fake = {"date": "2026-11-05", "verdict": "HOLD", "score": 70.0, "verdict_analyst": "HOLD", "score_analyst": 61.0,
            "verdict_smc": "WATCH", "score_smc": 40.0, "recommendation": {"original": {"verdict": "HOLD"}}}
    seen = {}
    monkeypatch.setattr(eng, "analyze_symbol", lambda s, sym, save=True, position=None, **kw: seen.update(pos=position, save=save) or fake)
    with Session.begin() as s:
        row = accounts.refresh_review(s, "uid-a", "JKH.N0000")
        assert (row.date, row.verdict, row.verdict_smc) == (d, "HOLD", "WATCH")
    assert seen["save"] is False and seen["pos"]["quantity"] == 100               # reviewed with the account's own position
    with Session() as s:
        assert s.scalar(select(PositionReview.date)) == d
        assert accounts.view(s, "uid-a", s.scalar(select(Analysis))).verdict == "HOLD"
    with Session.begin() as s:                                                    # closing the position drops the review
        s.execute(text("UPDATE positions SET closed = 1"))
    with Session.begin() as s:
        assert accounts.refresh_review(s, "uid-a", "JKH.N0000") is None
    with Session() as s:
        assert s.scalar(select(PositionReview)) is None


def test_profile_calls_do_not_write_when_nothing_changed(Session):
    """/api/me runs on every page load: it must not take SQLite's write lock unless something changed."""
    with Session.begin() as s:
        first = accounts.ensure_profile(s, A, "Kavi", "Perera")
        seen, updated = first.last_seen, first.last_updated
    with Session.begin() as s:
        row = accounts.ensure_profile(s, A)
        assert (row.last_seen, row.last_updated) == (seen, updated)
        assert not s.dirty                                                        # nothing to flush: no write transaction
