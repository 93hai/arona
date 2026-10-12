from __future__ import annotations

import sqlite3
from contextlib import closing
from functools import lru_cache
from importlib.resources import files

CURRENT_SCHEMA_VERSION = 4
MEMORY_COLUMNS = (
    "memory_id",
    "owner_id",
    "workspace_id",
    "memory_type",
    "content",
    "source",
    "created_at",
    "updated_at",
    "confidence",
    "sensitivity",
    "expires_at",
    "status",
    "version",
)


def schema_version(conn: sqlite3.Connection) -> int:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        return 0
    versions = [
        row[0]
        for row in conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version LIMIT ?",
            (CURRENT_SCHEMA_VERSION + 1,),
        )
    ]
    if not versions:
        return 0
    if any(type(v) is not int for v in versions):
        raise ValueError("Migration history is not sequential")
    if versions[-1] > CURRENT_SCHEMA_VERSION:
        raise ValueError("Database schema is newer than this release")
    if any(version != index for index, version in enumerate(versions, start=1)):
        raise ValueError("Migration history is not sequential")
    return versions[-1]


def validate_schema(conn: sqlite3.Connection, *, allow_legacy: bool = False) -> int:
    version = schema_version(conn)
    if version not in ((1, 2, 3, 4) if allow_legacy else (CURRENT_SCHEMA_VERSION,)):
        raise ValueError("Unsupported Arona schema")
    columns = tuple(row[1] for row in conn.execute("PRAGMA table_info(memories)"))
    if columns != MEMORY_COLUMNS:
        raise ValueError("Unexpected memory columns; refusing data loss")
    if version == 1:
        expected = _expected_objects(legacy=True)
        for name, kind, sql in conn.execute(
            "SELECT name,type,sql FROM sqlite_master WHERE tbl_name='memories' "
            "OR tbl_name='memories_fts' OR tbl_name GLOB 'memories_fts_*'"
        ):
            if expected.get(name) != (kind, sql):
                raise ValueError("Unexpected legacy schema object; refusing to discard it")
    if version >= 2:
        objects = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT name,type,sql FROM sqlite_master")}
        required = {
            "memories",
            "memories_fts",
            "memories_ai",
            "memories_au",
            "memories_ad",
            "maintenance_events",
        }
        if version >= 3:
            required.update({"memory_preferences", "memory_events"})
        if not required <= objects.keys():
            raise ValueError("Incomplete Arona schema")
        if version >= 4:
            required.update({"memory_lookup", "memory_lookup_ai", "memory_lookup_au",
                             "memory_lookup_ad", "idx_memory_lookup_pending",
                             "idx_memory_lookup_duplicate", "idx_memory_lookup_subject"})
            if not required <= objects.keys():
                raise ValueError("Incomplete Memory lookup schema")
        expected = _expected_objects()
        for name in required:
            if objects[name] != expected[name]:
                raise ValueError("Unexpected Arona schema definition")
        projection = (
            "rowid,content,source,memory_id,owner_id,workspace_id,memory_type,"
            "confidence,sensitivity,expires_at,status"
        )
        for left, right in (("memories", "memories_fts"), ("memories_fts", "memories")):
            if conn.execute(
                f"SELECT 1 FROM (SELECT {projection} FROM {left} "
                f"EXCEPT SELECT {projection} FROM {right}) LIMIT 1"
            ).fetchone():
                raise ValueError("Memory and FTS contents are not synchronized")
        if version >= 4:
            from arona.memory.keys import normalize_content, subject_key

            projection = "memory_id,owner_id,workspace_id,memory_type"
            active = f"SELECT {projection} FROM memories WHERE status='ACTIVE'"
            lookup = f"SELECT {projection} FROM memory_lookup"
            for left, right in ((active, lookup), (lookup, active)):
                if conn.execute(f"SELECT 1 FROM ({left} EXCEPT {right}) LIMIT 1").fetchone():
                    raise ValueError("Memory lookup scope is not synchronized")
            # Pending keys are safe: admission repairs them atomically before lookup.
            for content, normalized, subject in conn.execute(
                "SELECT m.content,k.normalized_content,k.subject_key FROM memory_lookup k "
                "JOIN memories m ON m.memory_id=k.memory_id WHERE k.normalized_content IS NOT NULL"
            ):
                if normalized != normalize_content(content) or subject != subject_key(content):
                    raise ValueError("Memory lookup keys are not synchronized")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Foreign key integrity check failed")
    return version


@lru_cache(maxsize=2)
def _expected_objects(legacy: bool = False) -> dict[str, tuple[str, str]]:
    # Only immutable, packaged SQL is executed, in a disposable in-memory DB.
    # Comparing SQLite's own definitions detects same-name no-op triggers too.
    with closing(sqlite3.connect(":memory:")) as reference:
        names = ("0001_legacy.sql",) if legacy else ("0001_legacy.sql", "0002_repair_memory.sql", "0003_memory_controls.sql", "0004_memory_lookup.sql")
        for name in names:
            reference.executescript(files("migrations").joinpath("versions", name).read_text(encoding="utf-8"))
        return {r[0]: (r[1], r[2]) for r in reference.execute("SELECT name,type,sql FROM sqlite_master")}
