from contextlib import closing
from importlib.resources import files
import json
import sqlite3

import pytest

from arona.database.migrations import DatabaseMigrations, execute_migration
from arona.database.schema import CURRENT_SCHEMA_VERSION, validate_schema
from arona.memory.db import DatabaseConnection
from arona.operations.backup import DatabaseBackupManager


@pytest.fixture
def live(tmp_path):
    path = tmp_path / "memory/live.db"
    path.parent.mkdir(mode=0o700)
    DatabaseMigrations(path).run_migrations()
    db = DatabaseConnection(path)
    with db.transaction() as conn:
        conn.execute("INSERT INTO memories VALUES ('id_text','alice','work','SEMANTIC','alpha','user','2026-01-01','2026-01-01',0.9,'PUBLIC',NULL,'ACTIVE',1)")
    return db


@pytest.mark.parametrize("nested", ["read", "write", "schema"])
def test_nested_transaction_rejects_without_losing_outer_work(live, nested):
    with live.transaction() as conn:
        conn.execute("UPDATE memories SET content='outer change'")
        context = live.schema_transaction() if nested == "schema" else live.transaction(write=nested == "write")
        with pytest.raises(RuntimeError, match="Nested"):
            with context:
                pytest.fail("Nested transaction must not start")
        assert conn.in_transaction
        assert conn.execute("SELECT content FROM memories").fetchone()[0] == "outer change"
    with live.transaction(write=False) as conn:
        assert conn.execute("SELECT content FROM memories").fetchone()[0] == "outer change"


def test_observer_sees_only_committed_data(live):
    with live.transaction() as conn:
        conn.execute("UPDATE memories SET content='new content'")
        with closing(live.connect(readonly=True)) as observer:
            assert observer.execute("SELECT content FROM memories").fetchone()[0] == "alpha"
    with closing(live.connect(readonly=True)) as observer:
        assert observer.execute("SELECT content FROM memories").fetchone()[0] == "new content"


@pytest.mark.parametrize("damage", ["missing-row", "extra-row", "metadata", "trigger"])
def test_migration_rerun_and_backup_reject_drift(live, tmp_path, damage):
    with live.transaction() as conn:
        if damage == "missing-row":
            conn.execute("DELETE FROM memories_fts")
        elif damage == "extra-row":
            conn.execute("INSERT INTO memories_fts(content,memory_id) VALUES ('orphan','other')")
        elif damage == "metadata":
            conn.execute("UPDATE memories_fts SET owner_id='other'")
        else:
            conn.execute("DROP TRIGGER memories_ai")
            conn.execute("CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN SELECT 1; END")
    with pytest.raises(ValueError):
        DatabaseMigrations(live.database_path).run_migrations()
    manager = DatabaseBackupManager(tmp_path / "backups")
    assert not manager.verify_database(live.database_path)
    with pytest.raises(ValueError):
        manager.create_backup(live.database_path)
    with live.transaction(write=False) as conn:
        assert conn.execute("SELECT content FROM memories").fetchone()[0] == "alpha"


def test_replace_and_rowid_update_preserve_fts(live):
    with live.transaction() as conn:
        conn.execute("INSERT OR REPLACE INTO memories SELECT memory_id,owner_id,workspace_id,memory_type,'replacement',source,created_at,updated_at,confidence,sensitivity,expires_at,status,version FROM memories")
        conn.execute("UPDATE memories SET rowid=91, memory_id='new_text_id'")
        assert conn.execute("SELECT rowid,memory_id FROM memories_fts").fetchall()[0][:] == (91, "new_text_id")
        assert conn.execute("SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'alpha'").fetchone()[0] == 0
        validate_schema(conn)


def make_v2(path):
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.execute("BEGIN")
        for name in ("0001_legacy.sql", "0002_repair_memory.sql"):
            execute_migration(conn, files("migrations").joinpath("versions", name).read_text(encoding="utf-8"))
        conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)")
        conn.executemany("INSERT INTO schema_migrations VALUES (?, 'original')", [(1,), (2,)])
        conn.execute("INSERT INTO memories VALUES ('old','alice','work','SEMANTIC','original data','user','2026-01-01','2026-01-01',0.9,'PUBLIC',NULL,'ACTIVE',1)")
        conn.commit()


def test_v2_upgrade_backs_up_and_preserves_data(tmp_path):
    path = tmp_path / "v2.db"
    make_v2(path)
    backups = tmp_path / "backups"
    DatabaseMigrations(path, backup_dir=backups).run_migrations()
    with closing(sqlite3.connect(path)) as conn:
        assert validate_schema(conn) == CURRENT_SCHEMA_VERSION
        assert conn.execute("SELECT content FROM memories").fetchone()[0] == "original data"
        assert conn.execute("SELECT count(*) FROM memory_preferences").fetchone()[0] == 0
    snapshot = next(backups.glob("*.db"))
    assert DatabaseMigrations(snapshot).get_schema_version() == 2


def test_v3_failure_rolls_back_then_can_retry(tmp_path, monkeypatch):
    from arona.database import migrations
    path = tmp_path / "v2.db"
    make_v2(path)
    real_execute = execute_migration

    def fail(conn, sql):
        real_execute(conn, sql)
        raise sqlite3.OperationalError("synthetic failure after v3 SQL")

    with monkeypatch.context() as patch:
        patch.setattr(migrations, "execute_migration", fail)
        with pytest.raises(sqlite3.OperationalError):
            DatabaseMigrations(path, backup_dir=tmp_path / "backups").run_migrations()
    assert DatabaseMigrations(path).get_schema_version() == 2
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name='memory_preferences'").fetchone() is None
        assert conn.execute("SELECT content FROM memories").fetchone()[0] == "original data"
    DatabaseMigrations(path, backup_dir=tmp_path / "backups").run_migrations()
    assert DatabaseMigrations(path).get_schema_version() == CURRENT_SCHEMA_VERSION


@pytest.mark.parametrize("damage", ["missing", "oversized", "json", "hash", "actor", "artifact", "scope", "timestamp"])
def test_restore_requires_matching_receipt(live, tmp_path, damage):
    manager = DatabaseBackupManager(tmp_path / "backups")
    snapshot = manager.create_backup(live.database_path)
    receipt = snapshot.with_name(snapshot.name + ".receipt.json")
    payload = json.loads(receipt.read_text())
    if damage == "missing":
        receipt.unlink()
    elif damage == "oversized":
        receipt.write_text(" " * 4097)
    elif damage == "json":
        receipt.write_text("null")
    else:
        key = {"hash": "sha256", "actor": "actor_id", "timestamp": "created_at"}.get(damage, damage)
        payload[key] = "invalid actor" if damage == "actor" else "invalid"
        receipt.write_text(json.dumps(payload))
    target = tmp_path / "restored.db"
    with pytest.raises(ValueError, match="receipt"):
        manager.restore_backup(snapshot, target)
    assert not target.exists()


def test_restore_detects_snapshot_changed_after_receipt(live, tmp_path):
    manager = DatabaseBackupManager(tmp_path / "backups")
    snapshot = manager.create_backup(live.database_path)
    with closing(sqlite3.connect(snapshot)) as conn:
        conn.execute("UPDATE memories SET content='changed but valid database'")
        conn.commit()
    assert manager.verify_database(snapshot)
    with pytest.raises(ValueError, match="receipt"):
        manager.restore_backup(snapshot, tmp_path / "restored.db")


def test_wal_backup_restore_has_no_sidecars(live, tmp_path):
    manager = DatabaseBackupManager(tmp_path / "backups")
    with closing(live.connect()) as conn:
        assert conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        conn.execute("UPDATE memories SET content='committed WAL content'")
        snapshot = manager.create_backup(live.database_path)
        target = tmp_path / "restored/new.db"
        manager.restore_backup(snapshot, target)
        with closing(sqlite3.connect(target)) as restored:
            assert restored.execute("SELECT content FROM memories").fetchone()[0] == "committed WAL content"
            assert restored.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert {p.name for p in manager.backup_dir.iterdir()} == {snapshot.name, snapshot.name + ".receipt.json"}
    assert {p.name for p in target.parent.iterdir()} == {target.name, target.name + ".receipt.json"}


def test_dangling_sidecar_and_existing_receipt_are_preserved(live, tmp_path):
    manager = DatabaseBackupManager(tmp_path / "backups")
    snapshot = manager.create_backup(live.database_path)
    target = tmp_path / "restored.db"
    sidecar = target.with_name(target.name + "-wal")
    try:
        sidecar.symlink_to(tmp_path / "absent")
    except OSError:
        pytest.skip("Host cannot create symlinks")
    with pytest.raises(ValueError, match="sidecars"):
        manager.restore_backup(snapshot, target)
    assert sidecar.is_symlink()
    sidecar.unlink()
    receipt = target.with_name(target.name + ".receipt.json")
    receipt.symlink_to(tmp_path / "absent")
    with pytest.raises(FileExistsError):
        manager.restore_backup(snapshot, target)
    assert receipt.is_symlink()
    assert not target.exists()


def test_backup_collision_never_overwrites(live, tmp_path, monkeypatch):
    from arona.operations import backup
    class SameID:
        hex = "fixed"
    monkeypatch.setattr(backup, "uuid4", SameID)
    manager = DatabaseBackupManager(tmp_path / "backups")
    snapshot = manager.create_backup(live.database_path)
    before = snapshot.read_bytes()
    with pytest.raises(FileExistsError):
        manager.create_backup(live.database_path)
    assert snapshot.read_bytes() == before
    assert not list(manager.backup_dir.glob(".snapshot-*"))


@pytest.mark.parametrize("object_type", ["index", "trigger", "constraint"])
def test_legacy_rebuild_refuses_to_discard_unknown_schema(tmp_path, object_type):
    path = tmp_path / "legacy.db"
    sql = files("migrations").joinpath("versions", "0001_legacy.sql").read_text(encoding="utf-8")
    if object_type == "constraint":
        sql = sql.replace("content TEXT NOT NULL", "content TEXT NOT NULL CHECK(length(content)>0)")
        assert "CHECK(length(content)>0)" in sql
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.executescript(sql)
        conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations VALUES (1)")
        if object_type == "index":
            conn.execute("CREATE INDEX custom_index ON memories(source)")
        elif object_type == "trigger":
            conn.execute("CREATE TRIGGER custom_trigger AFTER UPDATE ON memories BEGIN SELECT 1; END")
        before = conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
    with pytest.raises(ValueError, match="legacy"):
        DatabaseMigrations(path, backup_dir=tmp_path / "backups").run_migrations()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall() == before
        assert conn.execute("SELECT version FROM schema_migrations").fetchone()[0] == 1


def test_external_foreign_key_dependency_survives_v1_rebuild(tmp_path):
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.executescript(files("migrations").joinpath("versions", "0001_legacy.sql").read_text(encoding="utf-8"))
        conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
        conn.execute("INSERT INTO schema_migrations VALUES (1)")
        for trigger in ("memories_ai", "memories_au", "memories_ad"):
            conn.execute("DROP TRIGGER " + trigger)
        conn.execute("INSERT INTO memories VALUES ('old','alice','work','SEMANTIC','original','user','2026-01-01','2026-01-01',0.9,'PUBLIC','2099-01-01','ACTIVE',1)")
        conn.execute("CREATE TABLE child(memory_id TEXT REFERENCES memories(memory_id), payload TEXT)")
        conn.execute("INSERT INTO child VALUES ('old','preserved')")
    DatabaseMigrations(path, backup_dir=tmp_path / "backups").run_migrations()
    with DatabaseConnection(path).transaction(write=False) as conn:
        assert conn.execute("SELECT * FROM child").fetchone()[:] == ("old", "preserved")
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
