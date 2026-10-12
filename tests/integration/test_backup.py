from contextlib import closing
import json
import sqlite3

import pytest

from arona.database.migrations import DatabaseMigrations
from arona.memory.db import DatabaseConnection
from arona.operations.backup import DatabaseBackupManager


@pytest.fixture
def backup_setup(tmp_path):
    live = tmp_path / "memory/live.db"
    live.parent.mkdir(mode=0o700)
    DatabaseMigrations(live).run_migrations()
    with DatabaseConnection(live).transaction() as conn:
        conn.execute(
            "INSERT INTO memories VALUES ('sample','a','w','SEMANTIC','durable data','user',"
            "'2026-09-05','2026-09-05',0.9,'PUBLIC','2099-01-01','ACTIVE',1)"
        )
    return live, DatabaseBackupManager(tmp_path / "backups", actor_id="test-operator")


def test_backup_restore_roundtrip_and_receipts(backup_setup, tmp_path):
    live, manager = backup_setup
    first = manager.create_backup(live)
    second = manager.create_backup(live)
    assert first != second
    assert manager.verify_database(first)
    destination = tmp_path / "restored/restored.db"
    manager.restore_backup(first, destination)
    with closing(sqlite3.connect(destination)) as conn:
        assert conn.execute("SELECT content FROM memories").fetchone()[0] == "durable data"
    receipt = json.loads(first.with_name(first.name + ".receipt.json").read_text())
    assert receipt["actor_id"] == "test-operator"
    assert receipt["scope"] == "database"
    assert len(receipt["sha256"]) == 64
    assert not list(manager.backup_dir.glob("*.tmp"))


def test_restore_never_overwrites_existing_db(backup_setup):
    live, manager = backup_setup
    backup = manager.create_backup(live)
    before = live.read_bytes()
    for replace in (False, True):
        with pytest.raises(ValueError):
            manager.restore_backup(backup, live, allow_replace=replace)
    assert live.read_bytes() == before


def test_invalid_and_unrelated_database_rejected(backup_setup, tmp_path):
    _, manager = backup_setup
    invalid = tmp_path / "invalid.db"
    invalid.write_bytes(b"not a database")
    assert not manager.verify_database(invalid)
    unrelated = tmp_path / "unrelated.db"
    with closing(sqlite3.connect(unrelated)) as conn:
        conn.execute("CREATE TABLE unrelated(value)")
    assert not manager.verify_database(unrelated)
    with pytest.raises(ValueError):
        manager.create_backup(unrelated)


def test_restore_refuses_leftover_wal(backup_setup, tmp_path):
    live, manager = backup_setup
    backup = manager.create_backup(live)
    target = tmp_path / "unused.db"
    target.with_name(target.name + "-wal").write_bytes(b"leftover")
    with pytest.raises(ValueError):
        manager.restore_backup(backup, target)
    assert not target.exists()


def test_failed_copy_leaves_no_published_backup(backup_setup, monkeypatch):
    live, manager = backup_setup

    def fail(*args):
        raise OSError("synthetic full disk")

    monkeypatch.setattr(manager, "_copy", fail)
    with pytest.raises(OSError):
        manager.create_backup(live)
    assert not list(manager.backup_dir.iterdir())


def test_hardlinked_database_rejected(backup_setup, tmp_path):
    import os

    live, manager = backup_setup
    other = tmp_path / "alias.db"
    os.link(live, other)
    with pytest.raises(ValueError, match="linked"):
        manager.create_backup(other)


def test_receipt_failure_does_not_publish_untracked_backup(backup_setup, monkeypatch):
    live, manager = backup_setup

    def fail(*args):
        raise OSError("synthetic receipt failure")

    monkeypatch.setattr(manager, "_receipt", fail)
    with pytest.raises(OSError):
        manager.create_backup(live)
    assert not list(manager.backup_dir.iterdir())


def test_partial_receipt_failure_does_not_leave_orphan(backup_setup, monkeypatch):
    live, manager = backup_setup

    def fail_after_receipt(path, action):
        path.with_name(path.name + ".receipt.json").write_text("partial")
        raise OSError("synthetic receipt publication failure")

    monkeypatch.setattr(manager, "_receipt", fail_after_receipt)
    with pytest.raises(OSError):
        manager.create_backup(live)
    assert not list(manager.backup_dir.iterdir())


def test_existing_restore_receipt_is_not_overwritten(backup_setup, tmp_path):
    live, manager = backup_setup
    snapshot = manager.create_backup(live)
    target = tmp_path / "restored/recovered.db"
    target.parent.mkdir(mode=0o700)
    receipt = target.with_name(target.name + ".receipt.json")
    receipt.write_text("existing operator record")
    with pytest.raises(FileExistsError):
        manager.restore_backup(snapshot, target)
    assert receipt.read_text() == "existing operator record"
    assert not target.exists()


def test_backup_includes_committed_wal_data(backup_setup):
    live, manager = backup_setup
    with closing(sqlite3.connect(live)) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.execute("UPDATE memories SET content='committed WAL data'")
        conn.commit()
        snapshot = manager.create_backup(live)
        with closing(sqlite3.connect(snapshot)) as copied:
            assert (
                copied.execute("SELECT content FROM memories").fetchone()[0] == "committed WAL data"
            )


def test_symlink_backup_root_rejected(backup_setup, tmp_path):
    live, _ = backup_setup
    directory = tmp_path / "outside"
    directory.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(directory, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation requires OS support/permission")
    with pytest.raises(ValueError):
        DatabaseBackupManager(link).create_backup(live)
