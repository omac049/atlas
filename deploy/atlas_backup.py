"""Consistent daily snapshot of the Atlas database.

The labels are the irreplaceable asset here: every trusted label is evidence of a
settled cross-venue outcome that Kalshi prunes from its public API after ~6 weeks,
so a lost database cannot simply be re-harvested. This takes a snapshot through
sqlite3's online backup API rather than copying the file, so it is safe to run
while the API and monitor hold the database open — a plain ``cp`` of a live
SQLite file can capture a torn write.

Each snapshot is verified, then gzip-compressed (this data shrinks about 14x) and
proven to read back whole before the uncompressed copy is removed. Run daily by
com.atlas.backup. Snapshots are named ``atlas-auto-*.sqlite3.gz``; only those are
rotated, so hand-made checkpoints (``atlas-before-*.sqlite3``) are never deleted
by this script.

Restore: stop the agents, ``gunzip -k data/backups/atlas-auto-<stamp>.sqlite3.gz``,
and copy the resulting ``.sqlite3`` over ``data/atlas.sqlite3`` (deploy/README.md).

Arm A's input is backed up by the same run, on its own and whatever happens to
the main snapshot: the recorded order books (``data/making/books.sqlite3``) as
``books-auto-*.sqlite3.gz`` with the same verification and rotation, and the
archived October prints, each copied once into ``data/backups/tapes/``. The
books cannot be re-recorded, and until 2026-10-13 nothing copied them. These
copies sit on the same disk as the originals: they protect against a bad write
or a deleted file, not against losing the machine.
"""

import gzip
import shutil
import sqlite3
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DB = REPO_ROOT / "data" / "atlas.sqlite3"
BACKUP_DIR = REPO_ROOT / "data" / "backups"
LOG = Path.home() / "Library" / "Logs" / "atlas-backup.log"

# Three days of history against a bad write. Compressed, each snapshot is about a
# fourteenth of the database. Raising this is a disk-space decision: check
# `df -h /` before increasing it.
KEEP = 3
PREFIX = "atlas-auto-"
CHUNK = 8 * 1024 * 1024

# Arm A (docs/decisions/2026-09-08-market-making-charter.md, Amendment 1).
BOOKS_DB = REPO_ROOT / "data" / "making" / "books.sqlite3"
BOOKS_PREFIX = "books-auto-"
TAPES = REPO_ROOT / "data" / "making" / "tapes"
ARM_A_TAPES = "KXFEDDECISION-26OCT-*.json"


def log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {message}\n")


def verify_sqlite(path: Path) -> int:
    """A snapshot must pass quick_check. Returns its trusted-label count."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as check:
        if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("quick_check did not return ok")
        return check.execute(
            "SELECT COUNT(*) FROM learning_examples WHERE label != 'UNLABELED'"
        ).fetchone()[0]


def compress(path: Path, into: Path | None = None, keep: bool = False) -> Path:
    """Gzip a verified snapshot, prove the archive reads back whole, then remove the original.

    Reading the archive to the end checks gzip's CRC; comparing the byte count
    proves nothing was truncated. Only then is the uncompressed file deleted.
    ``into`` writes the archive to another folder; ``keep`` leaves the original
    in place, for a file something else still reads.
    """
    target = (into or path.parent) / (path.name + ".gz")
    partial = target.with_name(target.name + ".partial")
    with path.open("rb") as source, gzip.open(partial, "wb", compresslevel=1) as sink:
        shutil.copyfileobj(source, sink, CHUNK)
    restored = 0
    with gzip.open(partial, "rb") as reader:
        while block := reader.read(CHUNK):
            restored += len(block)
    expected = path.stat().st_size
    if restored != expected:
        partial.unlink(missing_ok=True)
        raise OSError(f"{path.name} reads back {restored} bytes compressed, expected {expected}")
    partial.replace(target)
    if not keep:
        path.unlink()
    return target


def main() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M", time.localtime())
    snapshotted = snapshot_atlas(stamp)
    # Arm A's data is a separate asset: back it up whatever happened above.
    try:
        snapshot_books(stamp)
        copy_tapes()
    except OSError as exc:
        log(f"ERROR Arm A backup failed: {exc}")
    # VACUUM only with a fresh, verified snapshot of the main database on disk.
    if snapshotted:
        vacuum_live_database()


def snapshot_atlas(stamp: str) -> bool:
    """Snapshot, verify, compress and rotate the main database. True when it all worked."""
    if not DB.exists():
        log(f"ERROR source database missing at {DB}")
        return False
    target = BACKUP_DIR / f"{PREFIX}{stamp}.sqlite3"

    try:
        with sqlite3.connect(f"file:{DB}?mode=ro", uri=True) as source, sqlite3.connect(
            target
        ) as destination:
            source.backup(destination)
            # The live database runs in WAL mode and the copy inherits it; opening a
            # WAL file leaves -wal/-shm files beside it that rotation never removes.
            # A snapshot should be one self-contained file.
            destination.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as exc:
        log(f"ERROR backup failed: {exc}")
        target.unlink(missing_ok=True)
        return False

    # A backup that cannot be opened is worse than none: it reads as protection
    # that does not exist. Verify before compressing or rotating anything.
    try:
        labels = verify_sqlite(target)
    except sqlite3.Error as exc:
        log(f"ERROR verification failed, keeping older backups: {exc}")
        target.unlink(missing_ok=True)
        return False

    raw_mb = target.stat().st_size / 1_048_576
    try:
        archive = compress(target)
    except OSError as exc:
        log(f"ERROR compression failed, keeping the uncompressed snapshot and older backups: {exc}")
        return False
    packed_mb = archive.stat().st_size / 1_048_576
    log(f"backup ok: {archive.name} ({raw_mb:.0f} MB raw, {packed_mb:.0f} MB compressed, "
        f"{labels} trusted labels)")

    # Snapshots written before compression existed are verified and compressed,
    # never deleted raw, so the switch loses no history.
    for legacy in sorted(BACKUP_DIR.glob(f"{PREFIX}*.sqlite3")):
        try:
            verify_sqlite(legacy)
            compress(legacy)
            log(f"compressed older snapshot {legacy.name}")
        except (sqlite3.Error, OSError) as exc:
            log(f"WARN older snapshot {legacy.name} left uncompressed: {exc}")

    snapshots = sorted(BACKUP_DIR.glob(f"{PREFIX}*.sqlite3.gz"))
    for stale in snapshots[:-KEEP]:
        stale.unlink()
        log(f"rotated out {stale.name}")
    return True


def snapshot_books(stamp: str) -> None:
    """Arm A's recorded order books: snapshot, verify, compress, keep the newest KEEP.

    The recorder writes every few seconds. The online backup holds a read lock
    for the copy, and the recorder keeps the books it reads in memory until it
    can write again (atlas/book_recorder.py), so nothing is lost while this runs.
    """
    if not BOOKS_DB.exists():
        return
    target = BACKUP_DIR / f"{BOOKS_PREFIX}{stamp}.sqlite3"
    try:
        with sqlite3.connect(f"file:{BOOKS_DB}?mode=ro", uri=True) as source, sqlite3.connect(
            target
        ) as destination:
            source.backup(destination)
            destination.execute("PRAGMA journal_mode=DELETE")
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as check:
            if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise sqlite3.DatabaseError("quick_check did not return ok")
            books = check.execute("SELECT COUNT(*) FROM orderbook_snapshots").fetchone()[0]
    except sqlite3.Error as exc:
        log(f"ERROR books backup failed, keeping older snapshots: {exc}")
        target.unlink(missing_ok=True)
        return
    raw_mb = target.stat().st_size / 1_048_576
    try:
        archive = compress(target)
    except OSError as exc:
        log(f"ERROR books compression failed, keeping the uncompressed snapshot: {exc}")
        return
    packed_mb = archive.stat().st_size / 1_048_576
    log(f"books backup ok: {archive.name} ({raw_mb:.0f} MB raw, {packed_mb:.0f} MB compressed, "
        f"{books} recorded books)")
    for stale in sorted(BACKUP_DIR.glob(f"{BOOKS_PREFIX}*.sqlite3.gz"))[:-KEEP]:
        stale.unlink()
        log(f"rotated out {stale.name}")


def copy_tapes() -> None:
    """Arm A's archived prints. A print never changes, so each file is copied once."""
    folder = BACKUP_DIR / "tapes"
    for tape in sorted(TAPES.glob(ARM_A_TAPES)):
        if (folder / (tape.name + ".gz")).exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        compress(tape, into=folder, keep=True)
        log(f"tape copied: {tape.name}")


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
        # In WAL mode VACUUM writes the rebuilt database into the -wal file; until a
        # truncating checkpoint copies it back, the space is not returned and the
        # -wal file can stay as large as the database.
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after = DB.stat().st_size / 1_048_576
        log(f"vacuum ok: {before:.0f} MB -> {after:.0f} MB")
    except sqlite3.Error as exc:
        log(f"WARN vacuum skipped ({exc}); retrying on the next nightly run")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
