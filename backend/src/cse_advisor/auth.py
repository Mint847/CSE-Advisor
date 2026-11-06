"""Sign-in for the API: Firebase ID tokens, checked on the server.

The browser signs in with Firebase (email + password or Google) and sends `Authorization: Bearer <ID token>` on every
/api call. The token is verified here against Google's public keys, so only the project id is needed: no service-account
key, no Firebase Admin SDK.

Auth is ON when FIREBASE_PROJECT_ID is set in backend/.env. Without it the app runs as before, single-user on this PC:
every request is the local user ("local"), who is also the admin.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt
from cryptography.x509 import load_pem_x509_certificate
from fastapi import HTTPException, Request

from .config import env

log = logging.getLogger(__name__)

LOCAL_UID = "local"
CERTS_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
LEEWAY_S = 60                                   # clock skew allowed on iat / exp / auth_time


@dataclass
class User:
    uid: str
    email: str = ""
    email_verified: bool = False
    name: str = ""
    picture: str = ""
    provider: str = ""                          # password | google.com | local
    admin: bool = False
    claims: dict[str, Any] = field(default_factory=dict)


def project_id() -> str | None:
    return env("FIREBASE_PROJECT_ID")


def enabled() -> bool:
    return bool(project_id())


def emulator_host() -> str | None:
    """Local testing against the Firebase Auth emulator (`firebase emulators:start --only auth`). Its tokens are unsigned,
    so this is only honoured for a `demo-*` project id, which Firebase reserves for emulators: it can never apply to a
    real project."""
    host = env("FIREBASE_AUTH_EMULATOR_HOST")
    pid = project_id() or ""
    if host and not pid.startswith("demo-"):
        log.warning("FIREBASE_AUTH_EMULATOR_HOST ignored: the project id %r is not a demo-* project", pid)
        return None
    return host


def admin_emails() -> set[str]:
    return {e.strip().lower() for e in (env("ADMIN_EMAILS", "") or "").split(",") if e.strip()}


def local_user() -> User:
    return User(uid=LOCAL_UID, provider="local", admin=True, name="Local user")


def public_config() -> dict[str, Any]:
    """What the browser needs to start Firebase. These values identify the project; they grant no access."""
    if not enabled():
        return {"enabled": False}
    pid = project_id()
    return {
        "enabled": True,
        "firebase": {
            "apiKey": env("FIREBASE_API_KEY", ""),
            "projectId": pid,
            "appId": env("FIREBASE_APP_ID", ""),
            "messagingSenderId": env("FIREBASE_MESSAGING_SENDER_ID", ""),
            "storageBucket": env("FIREBASE_STORAGE_BUCKET", f"{pid}.firebasestorage.app"),
            "authDomain": f"{pid}.firebaseapp.com",
        },
        # domains the app is served from: there the sign-in helper runs first-party on the app's own domain
        # (/__/auth/* is proxied to Firebase by main.py), which Safari and home-screen apps need
        "ownDomains": [d.strip().lower() for d in (env("APP_DOMAINS", "") or "").split(",") if d.strip()],
        "supportEmail": env("SUPPORT_EMAIL", ""),
        "appName": env("APP_NAME", "CSE Advisor"),
        **({"emulator": f"http://{emulator_host()}"} if emulator_host() else {}),
    }


# ---------------------------------------------------------------- Google's signing keys
class _Certs:
    """Google's current token-signing certificates, cached for as long as Google says (Cache-Control max-age)."""

    def __init__(self) -> None:
        self._keys: dict[str, Any] = {}
        self._expires = 0.0
        self._lock = threading.Lock()

    def set(self, pems: dict[str, str], max_age: float = 3600) -> None:
        self._keys = {kid: load_pem_x509_certificate(pem.encode()).public_key() for kid, pem in pems.items()}
        self._expires = time.time() + max_age

    def get(self, kid: str) -> Any:
        with self._lock:
            if time.time() >= self._expires or kid not in self._keys:
                self._refresh()
            return self._keys.get(kid)

    def _refresh(self) -> None:
        try:
            r = httpx.get(CERTS_URL, timeout=10)
            r.raise_for_status()
        except httpx.HTTPError as exc:
            if self._keys:                      # keep the old keys for a while rather than locking everyone out
                log.warning("could not refresh Google's signing keys: %s", exc)
                self._expires = time.time() + 300
                return
            raise HTTPException(503, "Can't check sign-in right now. Try again in a minute.") from exc
        m = re.search(r"max-age=(\d+)", r.headers.get("cache-control", ""))
        self.set(r.json(), float(m.group(1)) if m else 3600)


certs = _Certs()


def verify_token(token: str, pid: str | None = None, now: float | None = None) -> dict[str, Any]:
    """The token's claims if it is a valid, current Firebase ID token for this project; HTTPException(401) otherwise.
    Checks follow Firebase's 'verify ID tokens using a third-party JWT library': RS256, a known key id, audience = the
    project id, issuer = securetoken.google.com/<project>, a non-empty subject (the uid), and iat / exp / auth_time."""
    pid = pid or project_id()
    if not pid:
        raise HTTPException(500, "Sign-in is not configured")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise HTTPException(401, "Not signed in") from None
    now = time.time() if now is None else now
    opts = {"require": ["exp", "iat", "sub", "aud", "iss"], "verify_iat": False, "verify_exp": False}
    try:
        if emulator_host() and pid == project_id():          # emulator tokens are unsigned (demo-* projects only)
            claims = jwt.decode(token, options={**opts, "verify_signature": False}, audience=pid,
                                issuer=f"https://securetoken.google.com/{pid}")
            if claims.get("aud") != pid or claims.get("iss") != f"https://securetoken.google.com/{pid}":
                raise HTTPException(401, "Not signed in")
        else:
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise HTTPException(401, "Not signed in")
            key = certs.get(header["kid"])
            if key is None:
                raise HTTPException(401, "Not signed in")
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=pid, issuer=f"https://securetoken.google.com/{pid}",
                                leeway=LEEWAY_S, options=opts)
    except jwt.PyJWTError:
        raise HTTPException(401, "Not signed in") from None
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 128:
        raise HTTPException(401, "Not signed in")
    if float(claims["exp"]) <= now - LEEWAY_S:
        raise HTTPException(401, "Sign-in expired")
    if float(claims["iat"]) > now + LEEWAY_S or float(claims.get("auth_time", claims["iat"])) > now + LEEWAY_S:
        raise HTTPException(401, "Not signed in")
    return claims


def user_from_claims(claims: dict[str, Any]) -> User:
    email = (claims.get("email") or "").strip().lower()
    verified = bool(claims.get("email_verified"))
    fb = claims.get("firebase") or {}
    # admin: a custom claim set with the Admin SDK, or a verified email listed in ADMIN_EMAILS. Never a field the
    # browser can write. An unverified address is never trusted: anyone could sign up with the owner's email.
    admin = claims.get("admin") is True or (verified and email in admin_emails())
    return User(uid=claims["sub"], email=email, email_verified=verified, name=claims.get("name") or "",
                picture=claims.get("picture") or "", provider=fb.get("sign_in_provider") or "", admin=admin, claims=claims)


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization") or ""
    return h[7:].strip() if h[:7].lower() == "bearer " else None


def current_user(request: Request) -> User:
    """FastAPI dependency: who is calling. 401 when auth is on and the request has no valid token."""
    cached = getattr(request.state, "user", None)        # already verified by main.auth_gate
    if isinstance(cached, User):
        return cached
    if not enabled():
        return local_user()
    token = _bearer(request)
    if not token:
        raise HTTPException(401, "Not signed in")
    return user_from_claims(verify_token(token))


def require_admin(request: Request) -> User:
    u = current_user(request)
    if not u.admin:
        raise HTTPException(403, "Only the app's administrator can change shared market data.")
    return u
