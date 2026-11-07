"""Put a backup back as the live database.

    python -m cse_advisor.tools.restore                       # lists the backups it can see
    python -m cse_advisor.tools.restore <file.db | file.zip>  # restores that one (asks first)

Stop the app first. The database being replaced is kept next to it as cse_advisor.db.before-restore-<time>, so a restore
can itself be undone. After a restore the data is as old as the backup; start the app and the evening run catches up on
the sessions since (prices come back from CSE and TradingView).
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ._common import app_running, integrity_ok, table_counts

DB_NAME = "cse_advisor.db"


class RestoreError(Exception):
    pass


def candidates(data_dir: Path, offsite: Path | None = None) -> list[Path]:
    found = sorted((data_dir / "backups").glob("*.db"), reverse=True) if (data_dir / "backups").exists() else []
    if offsite and offsite.exists():
        found += sorted(offsite.glob("*.zip"), reverse=True)
    return found


def restore(backup: Path, data_dir: Path, running: Callable[[], bool] = app_running, now: datetime | None = None,
            log: Callable[[str], None] = print) -> dict[str, Any]:
    if not backup.exists():
        raise RestoreError(f"{backup} does not exist.")
    if running():
        raise RestoreError("The app is running. Close it and try again.")
    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data_dir) as tmp:
        staged = Path(tmp) / DB_NAME
        if backup.suffix.lower() == ".zip":
            with zipfile.ZipFile(backup) as z:
                dbs = [n for n in z.namelist() if n.lower().endswith(".db")]
                if not dbs:
                    raise RestoreError("That zip holds no .db file.")
                with z.open(dbs[0]) as f, open(staged, "wb") as out:
                    shutil.copyfileobj(f, out)
        else:
            shutil.copyfile(backup, staged)
        if not integrity_ok(staged):
            raise RestoreError("That backup failed SQLite's integrity check, so it was not restored.")
        counts = table_counts(staged)
        if "prices_daily" not in counts:
            raise RestoreError("That file is not a CSE Advisor database (no prices_daily table).")
        live = data_dir / DB_NAME
        kept = None
        if live.exists():
            kept = live.with_name(f"{DB_NAME}.before-restore-{(now or datetime.now()):%Y%m%d-%H%M%S}")
            live.rename(kept)
            for ext in ("-wal", "-shm"):                      # the old database's log files must not meet the restored one
                side = Path(str(live) + ext)
                if side.exists():
                    side.rename(Path(str(kept) + ext))
        shutil.move(str(staged), str(live))
    log(f"Restored {backup.name}: {len(counts)} tables, {sum(counts.values()):,} rows." + (f" The previous database is kept as {kept.name}." if kept else ""))
    return {"tables": len(counts), "rows": sum(counts.values()), "kept": kept}


def main(argv: list[str] | None = None) -> int:
    from .. import config
    from ..jobs import offsite_dir
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("backup", nargs="?", type=Path)
    ap.add_argument("--yes", action="store_true", help="do not ask")
    a = ap.parse_args(argv)
    if a.backup is None:
        for p in candidates(config.DATA_DIR, offsite_dir()):
            print(f"{p}  ({p.stat().st_size / 1e6:.0f} MB, {datetime.fromtimestamp(p.stat().st_mtime):%d %b %Y %H:%M})")
        print("\nRestore one with:  python -m cse_advisor.tools.restore <path>")
        return 0
    try:
        if not a.yes and input(f"Replace the live database in {config.DATA_DIR} with {a.backup}? [y/N] ").strip().lower() != "y":
            print("Cancelled; nothing changed.")
            return 1
        restore(a.backup, config.DATA_DIR)
    except RestoreError as exc:
        print(f"Not restored: {exc}")
        return 2
    print("Done. Start the app; the evening run will catch up on the sessions since the backup.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
