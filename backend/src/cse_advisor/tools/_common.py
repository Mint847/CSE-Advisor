"""Shared by move_data and restore."""
from __future__ import annotations

import socket
import sqlite3
from pathlib import Path


def app_running(port: int = 8000) -> bool:
    """True if something is listening on the app's port: the app must be stopped while its files are moved or replaced."""
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def table_counts(db: Path) -> dict[str, int]:
    """Row count per table, opened read-only."""
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {n: con.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}
    finally:
        con.close()


def integrity_ok(db: Path) -> bool:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


def online_copy(src: Path, dst: Path) -> None:
    """A consistent copy of a SQLite database (its WAL included), safe even while it is open."""
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()


def size_mb(p: Path) -> float:
    if p.is_file():
        return p.stat().st_size / 1e6
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / 1e6 if p.exists() else 0.0


def set_env_var(env_file: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file: replaces an existing line, else appends (the file is created if missing)."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    out, done = [], False
    for ln in lines:
        if ln.strip().startswith(f"{key}=") or ln.strip().startswith(f"# {key}="):
            if not done:
                out.append(f"{key}={value}")
                done = True
            continue
        out.append(ln)
    if not done:
        out += ["", f"# where the database, backups and study cache live (moved out of OneDrive)", f"{key}={value}"]
    env_file.write_text("\n".join(out) + "\n", encoding="utf-8")
