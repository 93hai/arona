from __future__ import annotations

import sqlite3
from contextlib import closing
from importlib.resources import files
from pathlib import Path

from arona.database.schema import (
    CURRENT_SCHEMA_VERSION,
    MEMORY_COLUMNS,
    schema_version,
    validate_schema,
)
from arona.memory.db import DatabaseConnection
from arona.operations.backup import DatabaseBackupManager, validate_actor

MIGRATION_FILES = {1: "0001_legacy.sql", 2: "0002_repair_memory.sql", 3: "0003_memory_controls.sql", 4: "0004_memory_lookup.sql"}


def execute_migration(conn: sqlite3.Connection, sql: str) -> None:
    """Execute complete statements without executescript's implicit COMMIT."""
    statement = ""
    for char in sql:
        statement += char
        if char == ";" and sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""
    if any(line.strip() and not line.lstrip().startswith("--") for line in statement.splitlines()):
        raise ValueError("Migration contains an incomplete SQL statement")


class DatabaseMigrations:
    def __init__(
        self,
        database_path: Path,
        *,
        backup_dir: Path | None = None,
        actor_id: str = "local-maintenance",
    ) -> None:
        self.db = DatabaseConnection(database_path)
        self.backup_dir = backup_dir
        self.actor_id = validate_actor(actor_id)

    def get_schema_version(self) -> int:
        if not self.db.database_path.exists():
            return 0
        with closing(self.db.connect(readonly=True)) as conn:
            return schema_version(conn)

    def run_migrations(self) -> None:
        if not self.db.check_fts5_support():
            raise RuntimeError("SQLite with working FTS5 support is required")
        with self.db.schema_transaction() as conn:
            current = schema_version(conn)
            if current == CURRENT_SCHEMA_VERSION:
                validate_schema(conn)
                conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('integrity-check')")
                return
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            if current == 0 and tables - {"schema_migrations"}:
                raise ValueError("Unversioned database contains data; refusing automatic adoption")
            backup_name = None
            if current:
                validate_schema(conn, allow_legacy=True)
                if self.backup_dir is None:
                    raise ValueError("An explicit backup directory is required before upgrading")
                snapshot = DatabaseBackupManager(
                    self.backup_dir, actor_id=self.actor_id
                ).create_backup(self.db.database_path)
                backup_name = snapshot.name
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, "
                "applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
            for version in range(current + 1, CURRENT_SCHEMA_VERSION + 1):
                sql = (
                    files("migrations")
                    .joinpath("versions", MIGRATION_FILES[version])
                    .read_text(encoding="utf-8")
                )
                execute_migration(conn, sql)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
            validate_schema(conn)
            if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
                raise ValueError("Migrated database failed integrity check")
            conn.execute("INSERT INTO memories_fts(memories_fts) VALUES ('integrity-check')")
            conn.execute(
                "INSERT INTO maintenance_events(actor_id,scope,action,from_version,to_version,backup_name) "
                "VALUES (?, 'database', 'migration', ?, ?, ?)",
                (self.actor_id, current, CURRENT_SCHEMA_VERSION, backup_name),
            )


__all__ = ["DatabaseMigrations", "CURRENT_SCHEMA_VERSION", "MEMORY_COLUMNS"]
