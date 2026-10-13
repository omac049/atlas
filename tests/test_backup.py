"""The nightly backup: verified, compressed, restorable, and rotated safely."""

import gzip
import importlib.util
import shutil
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def backup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("atlas_backup", ROOT / "deploy" / "atlas_backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = tmp_path / "atlas.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("create table learning_examples (example_id text primary key, label text)")
        connection.executemany(
            "insert into learning_examples values (?, ?)",
            [("a", "APPROVED_EQUIVALENT"), ("b", "REJECTED"), ("c", "UNLABELED")],
        )
    monkeypatch.setattr(module, "DB", database)
    monkeypatch.setattr(module, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(module, "LOG", tmp_path / "backup.log")
    monkeypatch.setattr(module, "vacuum_live_database", lambda: None)
    # Arm A's data: absent unless a test puts it there.
    monkeypatch.setattr(module, "BOOKS_DB", tmp_path / "making" / "books.sqlite3")
    monkeypatch.setattr(module, "TAPES", tmp_path / "making" / "tapes")
    return module


def _seed(folder: Path, *stamps: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for stamp in stamps:
        with gzip.open(folder / f"atlas-auto-{stamp}.sqlite3.gz", "wb") as handle:
            handle.write(b"older snapshot")


def _restored_labels(archive: Path, into: Path) -> int:
    with gzip.open(archive, "rb") as source, into.open("wb") as sink:
        shutil.copyfileobj(source, sink)
    with sqlite3.connect(into) as connection:
        assert connection.execute("pragma quick_check").fetchone()[0] == "ok"
        return connection.execute(
            "select count(*) from learning_examples where label != 'UNLABELED'"
        ).fetchone()[0]


def test_a_snapshot_is_compressed_and_restores_with_every_label(backup, tmp_path):
    backup.main()
    folder = tmp_path / "backups"
    archives = sorted(folder.glob("atlas-auto-*.sqlite3.gz"))
    assert len(archives) == 1
    assert not list(folder.glob("atlas-auto-*.sqlite3"))
    assert _restored_labels(archives[0], tmp_path / "restored.sqlite3") == 2
    assert "compressed" in (tmp_path / "backup.log").read_text()


def test_rotation_keeps_only_the_newest_compressed_snapshots(backup, tmp_path):
    folder = tmp_path / "backups"
    _seed(folder, "20260801-0330", "20260802-0330", "20260803-0330", "20260804-0330")
    backup.main()
    names = sorted(p.name for p in folder.glob("atlas-auto-*.sqlite3.gz"))
    assert len(names) == backup.KEEP
    assert "atlas-auto-20260801-0330.sqlite3.gz" not in names
    assert "atlas-auto-20260802-0330.sqlite3.gz" not in names


def test_older_uncompressed_snapshots_are_compressed_not_deleted(backup, tmp_path):
    folder = tmp_path / "backups"
    folder.mkdir()
    older = folder / "atlas-auto-20260810-0330.sqlite3"
    shutil.copy(backup.DB, older)
    backup.main()
    assert not older.exists()
    archive = folder / "atlas-auto-20260810-0330.sqlite3.gz"
    assert _restored_labels(archive, tmp_path / "older.sqlite3") == 2


def test_hand_made_checkpoints_are_never_rotated(backup, tmp_path):
    folder = tmp_path / "backups"
    _seed(folder, "20260801-0330", "20260802-0330", "20260803-0330")
    checkpoint = folder / "atlas-before-label-rebuild.sqlite3"
    checkpoint.write_bytes(b"hand made")
    backup.main()
    assert checkpoint.exists()


def test_a_failed_compression_keeps_the_raw_snapshot_and_rotates_nothing(backup, tmp_path, monkeypatch):
    def refuse(path):
        raise OSError("simulated compression failure")

    monkeypatch.setattr(backup, "compress", refuse)
    folder = tmp_path / "backups"
    _seed(folder, "20260801-0330", "20260802-0330", "20260803-0330", "20260804-0330")
    backup.main()
    assert len(list(folder.glob("atlas-auto-*.sqlite3.gz"))) == 4
    assert len(list(folder.glob("atlas-auto-*.sqlite3"))) == 1
    assert "compression failed" in (tmp_path / "backup.log").read_text()


def _to_wal(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("pragma journal_mode=wal")


def test_a_snapshot_of_a_wal_database_is_one_self_contained_file(backup, tmp_path):
    _to_wal(backup.DB)
    backup.main()
    folder = tmp_path / "backups"
    assert sorted(p.suffix for p in folder.iterdir()) == [".gz"]
    restored = tmp_path / "restored.sqlite3"
    assert _restored_labels(next(folder.glob("*.gz")), restored) == 2
    with sqlite3.connect(restored) as connection:
        assert connection.execute("pragma journal_mode").fetchone()[0] == "delete"


def test_vacuum_of_a_wal_database_hands_the_space_back(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("atlas_backup", ROOT / "deploy" / "atlas_backup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = tmp_path / "atlas.sqlite3"
    monkeypatch.setattr(module, "DB", database)
    monkeypatch.setattr(module, "LOG", tmp_path / "backup.log")
    _to_wal(database)
    with sqlite3.connect(database) as connection:
        connection.execute("create table quotes (body text)")
        connection.executemany("insert into quotes values (?)", [("x" * 4000,)] * 500)
        connection.execute("delete from quotes")
    monitor = sqlite3.connect(database)  # an idle connection, like the monitor's
    try:
        module.vacuum_live_database()
        wal = database.with_name(database.name + "-wal")
        assert not wal.exists() or wal.stat().st_size == 0
        assert database.stat().st_size < 100_000
    finally:
        monitor.close()


def _recorded_books(backup, rows: int = 3) -> None:
    backup.BOOKS_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(backup.BOOKS_DB) as connection:
        connection.execute(
            "create table if not exists orderbook_snapshots (snapshot_id integer primary key, payload_json text)"
        )
        connection.executemany(
            "insert into orderbook_snapshots (payload_json) values (?)", [("{}",)] * rows
        )


def test_arm_a_books_are_snapshotted_verified_and_restorable(backup, tmp_path):
    """The recorded order books cannot be re-recorded, and until 2026-10-13 they
    existed in one file that no backup touched."""
    _recorded_books(backup, rows=3)
    backup.main()
    folder = tmp_path / "backups"
    archives = sorted(folder.glob("books-auto-*.sqlite3.gz"))
    assert len(archives) == 1
    assert sorted(p.name for p in folder.iterdir() if p.is_file()) == sorted(
        [archives[0].name, next(folder.glob("atlas-auto-*.sqlite3.gz")).name]
    )
    restored = tmp_path / "books-restored.sqlite3"
    with gzip.open(archives[0], "rb") as source, restored.open("wb") as sink:
        shutil.copyfileobj(source, sink)
    with sqlite3.connect(restored) as connection:
        assert connection.execute("pragma quick_check").fetchone()[0] == "ok"
        assert connection.execute("select count(*) from orderbook_snapshots").fetchone()[0] == 3
    assert "books backup ok" in (tmp_path / "backup.log").read_text()


def test_books_snapshots_rotate_on_their_own_and_leave_the_main_ones_alone(backup, tmp_path):
    _recorded_books(backup)
    folder = tmp_path / "backups"
    folder.mkdir()
    for stamp in ("20261001-0330", "20261002-0330", "20261003-0330", "20261004-0330"):
        with gzip.open(folder / f"books-auto-{stamp}.sqlite3.gz", "wb") as handle:
            handle.write(b"older books")
    backup.main()
    books = sorted(p.name for p in folder.glob("books-auto-*.sqlite3.gz"))
    assert len(books) == backup.KEEP
    assert "books-auto-20261001-0330.sqlite3.gz" not in books
    assert len(list(folder.glob("atlas-auto-*.sqlite3.gz"))) == 1


def test_arm_a_tapes_are_copied_once_and_read_back_identical(backup, tmp_path):
    backup.TAPES.mkdir(parents=True)
    tape = backup.TAPES / "KXFEDDECISION-26OCT-H0__1789669309__1791912200.json"
    tape.write_text('[{"count": 1}]')
    (backup.TAPES / "KXMLBGAME-26AUG151310CWSDET-DET__1__2.json").write_text("[]")
    backup.main()
    copies = sorted((tmp_path / "backups" / "tapes").iterdir())
    assert [p.name for p in copies] == [tape.name + ".gz"]
    with gzip.open(copies[0], "rb") as handle:
        assert handle.read() == tape.read_bytes()
    assert tape.exists()  # the original stays where the runner looks for it
    first = copies[0].stat().st_mtime_ns
    backup.main()
    assert copies[0].stat().st_mtime_ns == first  # prints are immutable: copied once


def test_with_no_arm_a_data_the_main_backup_is_unchanged(backup, tmp_path):
    backup.main()
    folder = tmp_path / "backups"
    assert [p.name.startswith("atlas-auto-") for p in folder.iterdir()] == [True]
    assert "ERROR" not in (tmp_path / "backup.log").read_text()


def test_a_broken_books_database_is_reported_and_does_not_stop_the_vacuum(backup, tmp_path, monkeypatch):
    backup.BOOKS_DB.parent.mkdir(parents=True)
    backup.BOOKS_DB.write_bytes(b"this is not a database, " * 100)
    vacuumed = []
    monkeypatch.setattr(backup, "vacuum_live_database", lambda: vacuumed.append(True))
    backup.main()
    text = (tmp_path / "backup.log").read_text()
    assert "ERROR books backup failed" in text
    assert "backup ok: atlas-auto-" in text
    assert vacuumed == [True]
    assert not list((tmp_path / "backups").glob("books-auto-*"))


def test_arm_a_is_backed_up_even_when_the_main_database_is_missing(backup, tmp_path):
    backup.DB.unlink()
    _recorded_books(backup)
    backup.main()
    assert len(list((tmp_path / "backups").glob("books-auto-*.sqlite3.gz"))) == 1
    assert "ERROR source database missing" in (tmp_path / "backup.log").read_text()
