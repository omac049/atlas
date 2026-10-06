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
