from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path

from arona.common.paths import checked_path


class DatabaseConnection:
    """One explicitly closed connection per transaction; no import-time I/O."""

    def __init__(self, database_path: Path, *, timeout: float = 5.0) -> None:
        self.database_path = checked_path(database_path)
        if not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("Database timeout must be in (0, 60]")
        self.timeout = timeout
        self._active: ContextVar[bool] = ContextVar("arona_database_transaction", default=False)

    @contextmanager
    def _transaction_guard(self) -> Iterator[None]:
        if self._active.get():
            raise RuntimeError("Nested transactions on one DatabaseConnection are not supported")
        token = self._active.set(True)
        try:
            yield
        finally:
            self._active.reset(token)

    def connect(self, *, readonly: bool = False) -> sqlite3.Connection:
        path = checked_path(self.database_path)
        if not readonly and not path.exists():
            # Exclusive creation avoids permissive default file modes on Linux.
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                checked_path(path)
            else:
                os.close(fd)
        conn = sqlite3.connect(
            path.as_uri() + ("?mode=ro" if readonly else "?mode=rw"),
            uri=True,
            timeout=self.timeout,
            isolation_level=None,
        )
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA recursive_triggers = ON")
            conn.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
            conn.row_factory = sqlite3.Row
            return conn
        except BaseException:
            conn.close()
            raise

    @contextmanager
    def transaction(self, *, write: bool = True) -> Iterator[sqlite3.Connection]:
        with self._transaction_guard(), closing(self.connect(readonly=not write)) as conn:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    @staticmethod
    def check_fts5_support() -> bool:
        # Probe actual capability, not a compile-option string or the live DB.
        with closing(sqlite3.connect(":memory:")) as conn:
            try:
                conn.execute("CREATE VIRTUAL TABLE fts_probe USING fts5(content)")
                conn.execute("INSERT INTO fts_probe VALUES ('arona')")
                return (
                    conn.execute(
                        "SELECT count(*) FROM fts_probe WHERE fts_probe MATCH 'arona'"
                    ).fetchone()[0]
                    == 1
                )
            except sqlite3.OperationalError:
                return False

    @contextmanager
    def schema_transaction(self) -> Iterator[sqlite3.Connection]:
        """SQLite table-rebuild protocol, with FK validation before commit.

        v1 references missing parent tables. FK enforcement must be disabled
        BEFORE BEGIN to rebuild that table; application connections keep it on.
        """
        with self._transaction_guard(), closing(self.connect()) as conn:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                if conn.execute("PRAGMA foreign_key_check").fetchone():
                    raise ValueError("Schema migration violates foreign keys")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
