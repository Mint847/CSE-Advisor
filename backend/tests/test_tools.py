"""Moving the data out of OneDrive, restoring a backup, and the weekly off-site copy. All on temporary folders."""
import sqlite3
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest

from cse_advisor import jobs
from cse_advisor.tools import move_data, restore
from cse_advisor.tools._common import set_env_var, table_counts


def make_db(path: Path, rows: int = 5) -> None:
    for ext in ("", "-wal", "-shm"):
        Path(str(path) + ext).unlink(missing_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE prices_daily (id INTEGER PRIMARY KEY, symbol TEXT, close REAL)")
    con.execute("CREATE TABLE watchlist (id INTEGER PRIMARY KEY, symbol TEXT)")
    con.executemany("INSERT INTO prices_daily (symbol, close) VALUES (?, ?)", [("JKH", 18.0 + i) for i in range(rows)])
    con.execute("INSERT INTO watchlist (symbol) VALUES ('JKH')")
    con.commit()                                              # left in the WAL on purpose: a plain file copy would miss it
    con.close()


@pytest.fixture(autouse=True)
def no_real_home(tmp_path, monkeypatch):
    """Nothing here may touch the real home folder (the default off-site folder is inside it)."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))


@pytest.fixture()
def home(tmp_path):
    data = tmp_path / "project" / "data"
    (data / "backups").mkdir(parents=True)
    (data / "backtests" / "cache").mkdir(parents=True)
    make_db(data / "cse_advisor.db")
    (data / "backups" / "cse_advisor_2026-11-05.db").write_bytes(b"x" * 100)
    (data / "backtests" / "cache" / "ASPI.csv").write_text("date,aspi\n2026-11-05,21130\n")
    return tmp_path, data


def never(): return False


# ---------------------------------------------------------------- move_data
def test_move_copies_checks_switches_the_env_and_keeps_the_old_folder(home):
    tmp, src = home
    env, dst = tmp / "backend.env", tmp / "new data"
    env.write_text("TELEGRAM_TOKEN=abc\n")
    lines = []
    res = move_data.move(src, dst, env, today=date(2026, 11, 7), running=never, log=lines.append)
    assert table_counts(dst / "cse_advisor.db") == {"prices_daily": 5, "watchlist": 1}
    assert (dst / "backtests" / "cache" / "ASPI.csv").exists() and (dst / "backups" / "cse_advisor_2026-11-05.db").exists()
    assert f"CSE_DATA_DIR={dst.resolve()}" in env.read_text() and "TELEGRAM_TOKEN=abc" in env.read_text()
    assert not src.exists() and res["old"].name == "data.moved-20261107" and (res["old"] / "cse_advisor.db").exists()   # kept, not deleted
    assert res["rows"] == 6 and any("match" in x for x in lines)


def test_move_refuses_a_running_app_a_full_target_and_a_second_move(home):
    tmp, src = home
    env = tmp / ".env"
    with pytest.raises(move_data.MoveError, match="running"):
        move_data.move(src, tmp / "d", env, running=lambda: True)
    full = tmp / "full"
    full.mkdir()
    (full / "something").write_text("x")
    with pytest.raises(move_data.MoveError, match="not empty"):
        move_data.move(src, full, env, running=never)
    with pytest.raises(move_data.MoveError, match="inside"):
        move_data.move(src, src / "sub", env, running=never)
    assert src.exists() and not env.exists()                     # nothing changed in any of those cases
    move_data.move(src, tmp / "ok", env, running=never)
    with pytest.raises(move_data.MoveError, match="No database"):
        move_data.move(src, tmp / "again", env, running=never)    # the old folder is gone: already moved


def test_dry_run_changes_nothing(home):
    tmp, src = home
    res = move_data.move(src, tmp / "d", tmp / ".env", running=never, dry_run=True, log=lambda s: None)
    assert res["dry_run"] and src.exists() and not (tmp / "d").exists() and not (tmp / ".env").exists()


def test_a_copy_that_does_not_match_stops_before_the_switch(home, monkeypatch):
    tmp, src = home
    env = tmp / ".env"
    real = move_data.online_copy

    def lossy(a, b):
        real(a, b)
        c = sqlite3.connect(b)
        c.execute("DELETE FROM prices_daily WHERE id = 1")
        c.commit()
        c.close()
    monkeypatch.setattr(move_data, "online_copy", lossy)
    with pytest.raises(move_data.MoveError, match="does not match"):
        move_data.move(src, tmp / "d", env, running=never, log=lambda s: None)
    assert src.exists() and not env.exists()                     # the original and the settings are untouched


def test_env_var_is_replaced_not_duplicated(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\n# CSE_DATA_DIR=old\nB=2\n")
    set_env_var(env, "CSE_DATA_DIR", "C:/x")
    set_env_var(env, "CSE_DATA_DIR", "C:/y")
    assert env.read_text().count("CSE_DATA_DIR") == 1 and "CSE_DATA_DIR=C:/y" in env.read_text() and "A=1" in env.read_text()


# ---------------------------------------------------------------- restore
def test_restore_from_a_backup_keeps_the_database_it_replaces(home):
    tmp, data = home
    good = tmp / "good.db"
    make_db(good, rows=3)
    make_db(data / "cse_advisor.db", rows=9)
    res = restore.restore(good, data, running=never, now=datetime(2026, 11, 7, 12, 0), log=lambda s: None)
    assert table_counts(data / "cse_advisor.db")["prices_daily"] == 3
    assert res["kept"].name == "cse_advisor.db.before-restore-20261107-120000" and table_counts(res["kept"])["prices_daily"] == 9


def test_restore_from_a_zip_and_refusals(home):
    tmp, data = home
    src = tmp / "w.db"
    make_db(src, rows=4)
    z = tmp / "cse_advisor_2026-W45.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.write(src, "cse_advisor_2026-11-05.db")
    restore.restore(z, data, running=never, log=lambda s: None)
    assert table_counts(data / "cse_advisor.db")["prices_daily"] == 4
    with pytest.raises(restore.RestoreError, match="running"):
        restore.restore(z, data, running=lambda: True)
    junk = tmp / "junk.db"
    junk.write_bytes(b"not a database at all, just text" * 10)
    with pytest.raises(Exception):
        restore.restore(junk, data, running=never, log=lambda s: None)
    other = tmp / "other.db"
    c = sqlite3.connect(other)
    c.execute("CREATE TABLE t (x)")
    c.commit()
    c.close()
    with pytest.raises(restore.RestoreError, match="not a CSE Advisor"):
        restore.restore(other, data, running=never, log=lambda s: None)
    assert table_counts(data / "cse_advisor.db")["prices_daily"] == 4        # the live database survived the bad attempts


# ---------------------------------------------------------------- the weekly off-site copy
def test_offsite_backup_zips_the_newest_backup_once_a_week_and_prunes(home, monkeypatch):
    tmp, data = home
    monkeypatch.setattr("cse_advisor.config.DATA_DIR", data)
    (data / "backups" / "cse_advisor_2026-11-06.db").write_bytes(b"newest" * 50)
    dest = tmp / "off"
    r = jobs.offsite_backup(dest, today=date(2026, 11, 6))
    assert Path(r["file"]).name == "cse_advisor_2026-W45.zip"
    assert zipfile.ZipFile(r["file"]).namelist() == ["cse_advisor_2026-11-06.db"]
    assert "exists" in jobs.offsite_backup(dest, today=date(2026, 11, 7))["skipped"]        # same ISO week
    for w in range(30, 44):
        (dest / f"cse_advisor_2026-W{w}.zip").write_bytes(b"old")
    jobs.offsite_backup(dest, keep=8, today=date(2026, 11, 13))
    kept = sorted(p.name for p in dest.glob("*.zip"))
    assert len(kept) == 8 and kept[-1] == "cse_advisor_2026-W46.zip" and "cse_advisor_2026-W30.zip" not in kept
    assert "no off-site folder" in jobs.offsite_backup(None, today=date(2026, 11, 6))["skipped"]    # no OneDrive in the fake home


def test_offsite_setting_off_and_default(monkeypatch, tmp_path):
    from cse_advisor import config
    monkeypatch.setattr(config, "settings", lambda: {"backup": {"offsite_dir": "off"}})
    assert jobs.offsite_dir() is None
    monkeypatch.setattr(config, "settings", lambda: {"backup": {"offsite_dir": str(tmp_path / "mine")}})
    assert jobs.offsite_dir() == tmp_path / "mine"
