"""Offline Memory scale and durable-write benchmark; no providers or credentials."""
from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
from contextlib import contextmanager, closing
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

from arona.database.migrations import DatabaseMigrations  # noqa: E402
from arona.memory.db import DatabaseConnection  # noqa: E402
from arona.memory.repository import MemoryRepository  # noqa: E402
from arona.memory.service import MemoryService  # noqa: E402
from arona.memory.admission import MemoryAdmissionPolicy  # noqa: E402
from arona.domain.models import MemoryRecord, MemoryType, MemorySensitivity  # noqa: E402


class BenchmarkDatabase(DatabaseConnection):
    """Same transaction boundaries on a private RAM snapshot; benchmark only."""

    def __init__(self, path):
        super().__init__(path)
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        with closing(super().connect(readonly=True)) as source:
            source.backup(self.connection)
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA recursive_triggers=ON")

    @contextmanager
    def transaction(self, *, write=True):
        with self._transaction_guard():
            self.connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise


def run(root: Path) -> dict:
    path = root / "memory.db"
    DatabaseMigrations(path).run_migrations()
    db = BenchmarkDatabase(path)
    service = MemoryService(MemoryRepository(db), MemoryAdmissionPolicy())
    result = {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
              "platform": platform.platform(), "storage": "private SQLite RAM snapshot; one transaction per create", "create": {}}
    start = perf_counter()
    for index in range(5000):
        record = service.create_memory("bench", "work", MemoryType.SEMANTIC,
                                       f"Benchmark shared fact {index}: value {index}",
                                       "offline benchmark", 0.9, MemorySensitivity.PUBLIC)
        assert isinstance(record, MemoryRecord)
        if index + 1 in (1000, 2500, 5000):
            result["create"][str(index + 1)] = round(perf_counter() - start, 6)
            print(json.dumps({"create": result["create"]}), flush=True)
    # Recall setup is separate from timed create: double through repository writes.
    with service.repository.atomic() as repository:
        for index in range(5000, 10000):
            repository.create(record.model_copy(update={"memory_id": f"bench_{index}",
                "content": f"Benchmark shared fact {index}: value {index}"}))
    timings = []
    for _ in range(3):
        start = perf_counter()
        matches = service.recall("bench", "work", "Benchmark shared", limit=5)
        timings.append(perf_counter() - start)
        assert len(matches) == 5
    result["recall_10000_seconds"] = timings
    result["recall_median_seconds"] = statistics.median(timings)
    statements = []
    db.connection.set_trace_callback(statements.append)
    service.recall("bench", "work", "Benchmark shared", limit=5)
    db.connection.set_trace_callback(None)
    search_sql = next(sql for sql in statements if "bm25(memories_fts)" in sql)
    with db.transaction(write=False) as conn:
        result["query_plan"] = [tuple(row) for row in conn.execute(
            "EXPLAIN QUERY PLAN " + search_sql)]
    db.connection.close()
    disk_service = MemoryService(MemoryRepository(DatabaseConnection(path)), MemoryAdmissionPolicy())
    start = perf_counter()
    for index in range(25):
        disk_service.create_memory("disk", "work", MemoryType.SEMANTIC,
            f"Benchmark disk fact {index}: value {index}", "offline benchmark",
            0.9, MemorySensitivity.PUBLIC)
    result["disk_25_create_seconds"] = perf_counter() - start
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="arona-bench-", dir=args.output.resolve().parent) as temp:
        result = run(Path(temp))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
