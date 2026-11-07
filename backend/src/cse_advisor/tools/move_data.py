"""Move the database, its backups and the study cache out of the project folder (and so out of OneDrive).

    python -m cse_advisor.tools.move_data               # to C:\\Users\\<you>\\CSE-Advisor-data, asks first
    python -m cse_advisor.tools.move_data --to D:\\cse   # somewhere else
    python -m cse_advisor.tools.move_data --dry-run      # show what would happen

Why: OneDrive syncing a live SQLite database (it keeps a write-ahead log) can corrupt it, and backups kept in the
same folder are lost with it. Stop the app first. The steps, in order, each checked before the next:
  1. the database is copied with SQLite's online backup, the backups and backtests folders are copied
  2. every table's row count is compared with the original, and the copy is integrity-checked
  3. only then CSE_DATA_DIR=<new folder> is written into backend/.env
  4. the old folder is renamed to data.moved-<date>. It is NOT deleted: delete it yourself once the app has run
     happily for a few days (until then OneDrive keeps syncing it).
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable

from ._common import app_running, integrity_ok, online_copy, set_env_var, size_mb, table_counts

DB_NAME = "cse_advisor.db"


class MoveError(Exception):
    pass


def move(src: Path, dst: Path, env_file: Path, today: date | None = None, running: Callable[[], bool] = app_running,
         dry_run: bool = False, log: Callable[[str], None] = print) -> dict[str, Any]:
    src, dst = src.resolve(), dst.resolve()
    db = src / DB_NAME
    if not db.exists():
        raise MoveError(f"No database at {db}. Is CSE_DATA_DIR already set?")
    if src == dst:
        raise MoveError("The data is already in that folder.")
    if dst.exists() and any(dst.iterdir()):
        raise MoveError(f"{dst} already exists and is not empty. Choose another folder or empty it.")
    if dst.is_relative_to(src) or src.is_relative_to(dst):
        raise MoveError("The new folder cannot be inside the old one (or the other way round).")
    if running():
        raise MoveError("The app is running. Close it (the window running start.bat) and try again.")
    plan = {"database": size_mb(db), "backups": size_mb(src / "backups"), "backtests": size_mb(src / "backtests")}
    log(f"From {src}\n  to {dst}\n  database {plan['database']:.0f} MB, backups {plan['backups']:.0f} MB, backtests {plan['backtests']:.0f} MB")
    if dry_run:
        return {"dry_run": True, "plan": plan}
    dst.mkdir(parents=True, exist_ok=True)
    online_copy(db, dst / DB_NAME)
    for name in ("backups", "backtests"):
        if (src / name).exists():
            shutil.copytree(src / name, dst / name)
    before, after = table_counts(db), table_counts(dst / DB_NAME)
    if before != after:
        diff = {k: (before.get(k), after.get(k)) for k in set(before) | set(after) if before.get(k) != after.get(k)}
        raise MoveError(f"The copy does not match the original ({diff}). Nothing was changed; the partial copy is in {dst}.")
    if not integrity_ok(dst / DB_NAME):
        raise MoveError(f"The copied database failed SQLite's integrity check. Nothing was changed; see {dst}.")
    log(f"Copied and checked: {len(after)} tables, {sum(after.values()):,} rows match.")
    set_env_var(env_file, "CSE_DATA_DIR", str(dst))
    log(f"Wrote CSE_DATA_DIR={dst} to {env_file}")
    old = src.with_name(f"{src.name}.moved-{(today or date.today()).strftime('%Y%m%d')}")
    renamed = None
    try:
        src.rename(old)
        renamed = old
        log(f"The old folder is now {old} (kept, not deleted).")
    except OSError as exc:
        log(f"Could not rename the old folder ({exc}); it stays where it is. Rename or delete it yourself once the app has run well.")
    return {"dry_run": False, "dst": dst, "tables": len(after), "rows": sum(after.values()), "old": renamed}


def main(argv: list[str] | None = None) -> int:
    from .. import config
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--to", type=Path, default=Path.home() / "CSE-Advisor-data", help="the new folder")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true", help="do not ask")
    a = ap.parse_args(argv)
    env_file = config.BACKEND_DIR / ".env"
    try:
        move(config.DATA_DIR, a.to, env_file, dry_run=True)
        if a.dry_run:
            return 0
        if not a.yes and input("Move it now? [y/N] ").strip().lower() != "y":
            print("Cancelled; nothing changed.")
            return 1
        res = move(config.DATA_DIR, a.to, env_file)
    except MoveError as exc:
        print(f"Not moved: {exc}")
        return 2
    print(f"\nDone: {res['rows']:,} rows in {res['tables']} tables now live in {res['dst']}.\n"
          "Next: start the app as usual, check Settings > Data health, and delete the old folder after a few days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
