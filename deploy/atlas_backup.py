"""Consistent daily snapshot of the Atlas database.

The labels are the irreplaceable asset here: every trusted label is evidence of a
settled cross-venue outcome that Kalshi prunes from its public API after ~6 weeks,
so a lost database cannot simply be re-harvested. This takes a snapshot through
sqlite3's online backup API rather than copying the file, so it is safe to run
while the API and monitor hold the database open — a plain ``cp`` of a live
SQLite file can capture a torn write.

Run daily by com.atlas.backup. Snapshots are named ``atlas-auto-*.sqlite3``; only
those are rotated, so hand-made checkpoints (``atlas-before-*.sqlite3``) are never
deleted by this script.
"""

import sqlite3
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB = REPO_ROOT / "data" / "atlas.sqlite3"
BACKUP_DIR = REPO_ROOT / "data" / "backups"
LOG = Path.home() / "Library" / "Logs" / "atlas-backup.log"

# Three days of history against a bad write, at ~1 GB each. Raising this is a
# disk-space decision: check `df -h /` before increasing it.
KEEP = 3
PREFIX = "atlas-auto-"


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def main() -> None:
    if not DB.exists():
        log(f"ERROR source database missing at {DB}")
        return
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime())
    target = BACKUP_DIR / f"{PREFIX}{stamp}.sqlite3"

    try:
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as source, sqlite3.connect(
            target
        ) as destination:
            source.backup(destination)
    except sqlite3.Error as exc:
        log(f"ERROR backup failed: {exc}")
        target.unlink(missing_ok=True)
        return

    # A backup that cannot be opened is worse than none: it reads as protection
    # that does not exist. Verify before rotating older snapshots out.
    try:
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as check:
            if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("quick_check did not return ok")
            labels = check.execute(
                "SELECT COUNT(*) FROM learning_examples WHERE label != 'UNLABELED'"
            ).fetchone()[0]
    except sqlite3.Error as exc:
        log(f"ERROR verification failed, keeping older backups: {exc}")
        target.unlink(missing_ok=True)
        return

    size_mb = target.stat().st_size / 1_048_576
    log(f"backup ok: {target.name} ({size_mb:.0f} MB, {labels} trusted labels)")

    snapshots = sorted(BACKUP_DIR.glob(f"{PREFIX}*.sqlite3"))
    for stale in snapshots[:-KEEP]:
        stale.unlink()
        log(f"rotated out {stale.name}")

    vacuum_live_database()


def vacuum_live_database() -> None:
    """Reclaim the disk that the monitor's nightly prune only frees logically.

    ``store.prune()`` deletes rows but SQLite keeps the pages on its freelist,
    so the file only ever grows: by 2026-08-20 it had reached 1.05 GB with 76%
    of its pages free, and a manual VACUUM took it to 239 MB. Running it here —
    only AFTER a snapshot has been taken and verified — closes that loop
    nightly, at the quietest hour, with a fresh backup already on disk.

    VACUUM needs a moment of exclusive access. The monitor writes briefly every
    ~300s, so a 60s busy timeout is ample; if the database still cannot be
    locked, skipping is safe — tomorrow's run tries again, and the only cost is
    disk held one more day.
    """
    try:
        connection = sqlite3.connect(DB, timeout=60, isolation_level=None)
    except sqlite3.Error as exc:
        log(f"WARN vacuum skipped, could not open live database: {exc}")
        return
    try:
        before = DB.stat().st_size / 1_048_576
        connection.execute("PRAGMA busy_timeout = 60000")
        connection.execute("VACUUM")
        after = DB.stat().st_size / 1_048_576
        log(f"vacuum ok: {before:.0f} MB -> {after:.0f} MB")
    except sqlite3.Error as exc:
        log(f"WARN vacuum skipped ({exc}); retrying on the next nightly run")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
