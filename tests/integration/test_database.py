from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from importlib.resources import files
import sqlite3

import pytest

from arona.database.connection import DatabaseConnection
from arona.database.migrations import DatabaseMigrations
from arona.database.schema import CURRENT_SCHEMA_VERSION, validate_schema

INSERT = "INSERT INTO memories VALUES (" + ",".join("?" for _ in range(13)) + ")"


def record(identifier="memory_alpha", owner="alice", workspace="project", **changes):
    row = dict(
        memory_id=identifier,
        owner_id=owner,
        workspace_id=workspace,
        memory_type="SEMANTIC",
        content="arona foundation",
        source="user-confirmed",
        created_at="2026-09-05 00:00:00+00:00",
        updated_at="2026-09-05 00:00:00+00:00",
        confidence=0.9,
        sensitivity="PUBLIC",
        expires_at="2099-01-01 00:00:00+00:00",
        status="ACTIVE",
        version=1,
    )
    row.update(changes)
    return tuple(row.values())


@pytest.fixture
def migrated_db(tmp_path):
    path = tmp_path / "memory.db"
    DatabaseMigrations(path).run_migrations()
    return DatabaseConnection(path)


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.executescript(
            files("migrations").joinpath("versions/0001_legacy.sql").read_text(encoding="utf-8")
        )
        conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)")
        conn.execute("INSERT INTO schema_migrations VALUES (1,'legacy-timestamp')")
        # v1 had broken write triggers. Model a populated legacy DB imported while disabled.
        for trigger in ("memories_ai", "memories_au", "memories_ad"):
            conn.execute("DROP TRIGGER " + trigger)
        conn.execute(INSERT, record())
        conn.execute(INSERT, record("memory_beta", "bob", "other", status="DELETED"))
    return path


def test_probe_and_version_read_do_not_create_db(tmp_path):
    path = tmp_path / "absent.db"
    assert DatabaseConnection.check_fts5_support()
    assert DatabaseMigrations(path).get_schema_version() == 0
    assert not path.exists()


def test_fresh_migration_and_idempotence(migrated_db):
    with migrated_db.transaction(write=False) as conn:
        assert validate_schema(conn) == CURRENT_SCHEMA_VERSION
        assert conn.execute("SELECT count(*) FROM maintenance_events").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    DatabaseMigrations(migrated_db.database_path).run_migrations()
    with migrated_db.transaction(write=False) as conn:
        assert conn.execute("SELECT count(*) FROM maintenance_events").fetchone()[0] == 1


def test_transaction_rolls_back_all_changes_and_ddl(migrated_db):
    with pytest.raises(RuntimeError):
        with migrated_db.transaction() as conn:
            conn.execute(INSERT, record())
            conn.execute("CREATE TABLE interrupted(value TEXT)")
            raise RuntimeError("synthetic interruption")
    with migrated_db.transaction(write=False) as conn:
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 0
        assert (
            conn.execute("SELECT 1 FROM sqlite_master WHERE name='interrupted'").fetchone() is None
        )


def test_transaction_rolls_back_cancellation(migrated_db):
    with pytest.raises(KeyboardInterrupt):
        with migrated_db.transaction() as conn:
            conn.execute(INSERT, record())
            raise KeyboardInterrupt()
    with migrated_db.transaction(write=False) as conn:
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 0


def test_fts_insert_update_soft_delete_hard_delete(migrated_db):
    with migrated_db.transaction() as conn:
        conn.execute(INSERT, record())
        assert (
            conn.execute(
                "SELECT memory_id FROM memories_fts WHERE memories_fts MATCH 'foundation'"
            ).fetchone()[0]
            == "memory_alpha"
        )
        conn.execute("UPDATE memories SET content='changed content', status='DELETED'")
        assert (
            conn.execute(
                "SELECT count(*) FROM memories_fts WHERE memories_fts MATCH 'foundation'"
            ).fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT status FROM memories_fts WHERE memories_fts MATCH 'changed'"
            ).fetchone()[0]
            == "DELETED"
        )
        conn.execute("DELETE FROM memories")
        assert conn.execute("SELECT count(*) FROM memories_fts").fetchone()[0] == 0


def test_upgrade_preserves_records_and_has_pre_upgrade_backup(legacy_db, tmp_path):
    with closing(sqlite3.connect(legacy_db)) as conn:
        before = conn.execute("SELECT * FROM memories ORDER BY memory_id").fetchall()
    backups = tmp_path / "backups"
    DatabaseMigrations(legacy_db, backup_dir=backups, actor_id="test-operator").run_migrations()
    with closing(sqlite3.connect(legacy_db)) as conn:
        assert conn.execute("SELECT * FROM memories ORDER BY memory_id").fetchall() == before
        assert validate_schema(conn) == CURRENT_SCHEMA_VERSION
        assert conn.execute("SELECT count(*) FROM memories_fts").fetchone()[0] == 2
        event = conn.execute(
            "SELECT actor_id,scope,from_version,to_version,backup_name FROM maintenance_events"
        ).fetchone()
        assert event[:4] == ("test-operator", "database", 1, CURRENT_SCHEMA_VERSION)
    snapshot = backups / event[4]
    with closing(sqlite3.connect(snapshot)) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 1
        assert conn.execute("SELECT * FROM memories ORDER BY memory_id").fetchall() == before


def test_upgrade_requires_backup(legacy_db):
    with pytest.raises(ValueError, match="backup"):
        DatabaseMigrations(legacy_db).run_migrations()
    assert DatabaseMigrations(legacy_db).get_schema_version() == 1


def test_failed_upgrade_keeps_schema_and_rows(legacy_db, tmp_path, monkeypatch):
    from arona.database import migrations

    original = migrations.execute_migration

    def fail_after_sql(conn, sql):
        original(conn, sql)
        raise sqlite3.OperationalError("synthetic migration failure")

    monkeypatch.setattr(migrations, "execute_migration", fail_after_sql)
    with pytest.raises(sqlite3.OperationalError):
        DatabaseMigrations(legacy_db, backup_dir=tmp_path / "backups").run_migrations()
    assert DatabaseMigrations(legacy_db).get_schema_version() == 1
    with closing(sqlite3.connect(legacy_db)) as conn:
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 2
        assert (
            conn.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_events'").fetchone()
            is None
        )
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1


def test_future_schema_not_modified(tmp_path):
    path = tmp_path / "future.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE schema_migrations(version INTEGER)")
        conn.executemany("INSERT INTO schema_migrations VALUES (?)", [(v,) for v in range(1, CURRENT_SCHEMA_VERSION + 2)])
        conn.commit()
    with pytest.raises(ValueError, match="newer"):
        DatabaseMigrations(path).run_migrations()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == CURRENT_SCHEMA_VERSION + 1


def test_unknown_columns_not_discarded(legacy_db, tmp_path):
    with closing(sqlite3.connect(legacy_db)) as conn:
        conn.execute("ALTER TABLE memories ADD COLUMN extra TEXT")
        conn.commit()
    with pytest.raises(ValueError, match="columns"):
        DatabaseMigrations(legacy_db, backup_dir=tmp_path / "backups").run_migrations()
    assert DatabaseMigrations(legacy_db).get_schema_version() == 1


def test_readonly_and_closed_connections(migrated_db):
    with migrated_db.transaction(write=False) as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(INSERT, record())
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_reopen_preserves_committed_data(migrated_db):
    with migrated_db.transaction() as conn:
        conn.execute(INSERT, record())
    other = DatabaseConnection(migrated_db.database_path)
    with other.transaction(write=False) as conn:
        assert conn.execute("SELECT memory_id FROM memories").fetchone()[0] == "memory_alpha"


def test_concurrent_migration_is_idempotent(tmp_path):
    path = tmp_path / "concurrent.db"
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: DatabaseMigrations(path).run_migrations(), range(2)))
    assert DatabaseMigrations(path).get_schema_version() == CURRENT_SCHEMA_VERSION


def test_lock_timeout_is_bounded(migrated_db):
    impatient = DatabaseConnection(migrated_db.database_path, timeout=0.05)
    with migrated_db.transaction():
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            with impatient.transaction():
                pytest.fail("Second writer must not enter")


def test_constraints_preserve_owner_scope(migrated_db):
    with pytest.raises(sqlite3.IntegrityError):
        with migrated_db.transaction() as conn:
            conn.execute(INSERT, record(owner=""))
    with migrated_db.transaction(write=False) as conn:
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 0


def test_failed_fresh_migration_has_no_partial_schema(tmp_path, monkeypatch):
    from arona.database import migrations

    original = migrations.execute_migration

    def fail_after_sql(conn, sql):
        original(conn, sql)
        raise sqlite3.OperationalError("synthetic migration failure")

    monkeypatch.setattr(migrations, "execute_migration", fail_after_sql)
    path = tmp_path / "interrupted.db"
    with pytest.raises(sqlite3.OperationalError):
        DatabaseMigrations(path).run_migrations()
    with closing(sqlite3.connect(path)) as conn:
        assert (
            conn.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
            == []
        )


def test_invalid_legacy_rows_abort_without_data_loss(legacy_db, tmp_path):
    with closing(sqlite3.connect(legacy_db)) as conn:
        conn.execute("UPDATE memories SET owner_id=''")
        conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        DatabaseMigrations(legacy_db, backup_dir=tmp_path / "backups").run_migrations()
    assert DatabaseMigrations(legacy_db).get_schema_version() == 1
    with closing(sqlite3.connect(legacy_db)) as conn:
        assert conn.execute("SELECT count(*) FROM memories WHERE owner_id=''").fetchone()[0] == 2


@pytest.mark.parametrize("versions", [[1, 2, 10**12], [1, 3], [2], [0], ["invalid"]])
def test_malformed_history_is_rejected_without_large_allocation(tmp_path, versions):
    path = tmp_path / "bad-history.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE schema_migrations(version)")
        conn.executemany("INSERT INTO schema_migrations VALUES (?)", [(v,) for v in versions])
        conn.commit()
    with pytest.raises(ValueError):
        DatabaseMigrations(path).run_migrations()
