"""Offline regression and structural scale checks for the final 3.0 gap pass."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from importlib.resources import files
import json
import sqlite3

import pytest

from arona.database.migrations import DatabaseMigrations, execute_migration
from arona.database.schema import CURRENT_SCHEMA_VERSION, validate_schema
from arona.domain.models import MemoryRecord, MemorySensitivity, MemoryStatus, MemoryType
from arona.memory.admission import AdmissionStatus, MemoryAdmissionPolicy
from arona.memory.db import DatabaseConnection
from arona.memory.repository import MemoryRepository
from arona.memory.service import MemoryService
from arona.models.gateway import ModelGateway, ModelRequest, ProviderError
from arona.models.http import GeminiProvider, MistralProvider, ProviderConfig


@pytest.fixture
def service(tmp_path):
    path = tmp_path / "memory.db"
    DatabaseMigrations(path).run_migrations()
    return MemoryService(MemoryRepository(DatabaseConnection(path)), MemoryAdmissionPolicy())


def create(service, content="language: Python", **overrides):
    values = dict(owner_id="alice", workspace_id="work", memory_type=MemoryType.SEMANTIC,
                  content=content, source="user", confidence=0.9,
                  sensitivity=MemorySensitivity.PUBLIC)
    values.update(overrides)
    return service.create_memory(**values)


def seed(service, start, stop):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with service.repository.atomic() as repository:
        for index in range(start, stop):
            repository.create(MemoryRecord(
                memory_id=f"scale_{index:06}", owner_id="alice", workspace_id="work",
                memory_type=MemoryType.SEMANTIC, content=f"shared note {index}: value {index}",
                source="user", confidence=0.9, sensitivity=MemorySensitivity.PUBLIC,
                created_at=now, updated_at=now, status=MemoryStatus.ACTIVE, version=1,
            ))


def test_scale_uses_indexed_candidates_and_one_fts_pass(service, monkeypatch):
    # VM work is deterministic enough to detect quadratic plans without wall-clock gates.
    original_connect = service.repository.db.connect
    work = [0]

    def connect(**kwargs):
        conn = original_connect(**kwargs)
        def progress():
            work[0] += 1
            return 0
        conn.set_progress_handler(progress, 100)
        return conn

    monkeypatch.setattr(service.repository.db, "connect", connect)
    original_convert = MemoryRepository._row_to_memory_record
    converted = []

    def convert(self, row):
        converted.append(row["memory_id"])
        return original_convert(self, row)

    monkeypatch.setattr(MemoryRepository, "_row_to_memory_record", convert)
    costs = []
    for start, stop in ((0, 300), (300, 1200)):
        seed(service, start, stop)
        work[0] = 0
        converted.clear()
        assert isinstance(create(service, f"unique subject {stop}: value"), MemoryRecord)
        create_cost = work[0]
        assert len(converted) == 1
        work[0] = 0
        converted.clear()
        assert len(service.recall("alice", "work", "shared", limit=5)) == 5
        assert len(converted) == 5
        costs.append((create_cost, work[0]))
    assert costs[1][0] < costs[0][0] * 2 + 30
    assert costs[1][1] < costs[0][1] * 6 + 30


def test_unicode_keys_updates_and_raw_sql_invalidation(service):
    first = create(service, "  Stra\u00dfe: caf\u00e9 ")
    assert create(service, "STRASSE: cafe\u0301").memory_id == first.memory_id
    service.update_memory(first.memory_id, "alice", "work", "editor: Vim", 0.9,
                          MemorySensitivity.PUBLIC)
    assert create(service, "EDITOR: vim").memory_id == first.memory_id
    assert create(service, "editor: Emacs", confidence=0.8).status == AdmissionStatus.DENIED
    with service.repository.db.transaction() as conn:
        conn.execute("UPDATE memories SET content='editor: Emacs' WHERE memory_id=?",
                     (first.memory_id,))
    assert create(service, "EDITOR: emacs").memory_id == first.memory_id
    with service.repository.db.transaction(write=False) as conn:
        assert validate_schema(conn) == CURRENT_SCHEMA_VERSION
    service.repository.hard_delete(first.memory_id, "alice", "work")
    with service.repository.db.transaction(write=False) as conn:
        assert not conn.execute("SELECT 1 FROM memory_lookup").fetchall()


def test_candidates_preserve_scope_type_expiry_and_duplicate_priority(service):
    original = create(service, "language: Python", confidence=0.8)
    # Raw repository writes can leave multiple active facts; duplicates must win first.
    with service.repository.atomic() as repository:
        repository.create(original.model_copy(update={"memory_id": "strong", "content": "language: Rust",
                                                      "confidence": 0.99}))
        repository.create(original.model_copy(update={"memory_id": "other", "owner_id": "bob",
                                                      "content": "editor: Vim", "confidence": 1.0}))
        repository.create(original.model_copy(update={"memory_id": "expired", "content": "editor: Emacs",
            "confidence": 1.0, "expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)}))
    future = datetime.now(timezone.utc) + timedelta(days=1)
    duplicate = create(service, "LANGUAGE: python", expires_at=future)
    assert duplicate.memory_id == original.memory_id and duplicate.expires_at == future
    assert create(service, "language: Go", memory_type=MemoryType.PROCEDURAL).status == AdmissionStatus.DENIED
    assert isinstance(create(service, "editor: Nano"), MemoryRecord)
    assert isinstance(create(service, "language: Go", workspace_id="other"), MemoryRecord)
    service.repository.supersede("strong", owner_id="alice", workspace_id="work")
    service.repository.soft_delete(original.memory_id, "alice", "work")
    assert isinstance(create(service, "language: Go"), MemoryRecord)


def test_lookup_changes_rollback_with_failed_insert(service, monkeypatch):
    first = create(service, confidence=0.8)
    real_create = MemoryRepository.create

    def fail(self, record):
        real_create(self, record)
        raise RuntimeError("synthetic failure after key update")

    monkeypatch.setattr(MemoryRepository, "create", fail)
    with pytest.raises(RuntimeError):
        create(service, "language: Rust", confidence=0.95)
    with service.repository.db.transaction(write=False) as conn:
        assert validate_schema(conn) == CURRENT_SCHEMA_VERSION
        assert conn.execute("SELECT memory_id FROM memory_lookup").fetchall()[0][0] == first.memory_id
    assert create(service).memory_id == first.memory_id


def test_v3_upgrade_preserves_rows_and_backfills_scoped_keys(tmp_path):
    path = tmp_path / "v3.db"
    with closing(sqlite3.connect(path)) as conn:
        for name in ("0001_legacy.sql", "0002_repair_memory.sql", "0003_memory_controls.sql"):
            execute_migration(conn, files("migrations").joinpath("versions", name).read_text(encoding="utf-8"))
        conn.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
        conn.executemany("INSERT INTO schema_migrations VALUES (?)", [(1,), (2,), (3,)])
        conn.execute("INSERT INTO memories VALUES ('old','alice','work','SEMANTIC','language: Python',"
                     "'user','2026-01-01','2026-01-01',0.9,'PUBLIC',NULL,'ACTIVE',1)")
        conn.commit()
        before = conn.execute("SELECT * FROM memories").fetchall()
    backups = tmp_path / "snapshots"
    DatabaseMigrations(path, backup_dir=backups).run_migrations()
    with closing(sqlite3.connect(path)) as conn:
        assert conn.execute("SELECT * FROM memories").fetchall() == before
        assert validate_schema(conn) == CURRENT_SCHEMA_VERSION
    snapshot = next(backups.glob("*.db"))
    assert DatabaseMigrations(snapshot).get_schema_version() == 3
    service = MemoryService(MemoryRepository(DatabaseConnection(path)), MemoryAdmissionPolicy())
    assert create(service, "LANGUAGE: python").memory_id == "old"
    assert create(service, "language: Rust", confidence=0.8).status == AdmissionStatus.DENIED
    # Fresh connection, no process-local cache dependency.
    assert create(MemoryService(MemoryRepository(DatabaseConnection(path)), MemoryAdmissionPolicy())).memory_id == "old"


def test_recall_order_matches_previous_full_ranking(service, monkeypatch):
    import arona.memory.repository as module

    fixed = datetime(2026, 9, 15, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    monkeypatch.setattr(module, "datetime", Clock)
    base = create(service, "shared ranking")
    with service.repository.atomic() as repository:
        for index in range(60):
            repository.create(base.model_copy(update={
                "memory_id": f"rank_{index:03}", "content": "shared " * (1 + index % 5),
                "confidence": 0.1 + (index % 10) / 10,
                "memory_type": list(MemoryType)[index % 4],
                "updated_at": fixed - timedelta(days=index % 7),
                "status": MemoryStatus.DELETED if index % 13 == 0 else MemoryStatus.ACTIVE,
                "expires_at": fixed - timedelta(days=1) if index % 11 == 0 else None,
                "owner_id": "bob" if index % 17 == 0 else "alice",
                "workspace_id": "other" if index % 19 == 0 else "work",
            }))
    with service.repository.db.transaction(write=False) as conn:
        rows = conn.execute(
            "SELECT m.*,bm25(memories_fts) AS text_rank FROM memories_fts "
            "JOIN memories m ON m.rowid=memories_fts.rowid WHERE memories_fts MATCH 'shared' "
            "AND m.owner_id='alice' AND m.workspace_id='work' AND m.status='ACTIVE' "
            "AND (m.expires_at IS NULL OR julianday(m.expires_at)>julianday('now')) "
            "AND m.confidence>=0.3 AND m.memory_type IN ('SEMANTIC','PROCEDURAL')"
        ).fetchall()
    best = max(-float(row["text_rank"]) for row in rows)
    weights = {MemoryType.SEMANTIC: 1, MemoryType.PROCEDURAL: 0.95}
    scored = []
    for row in rows:
        record = service.repository._row_to_memory_record(row)
        relevance = (-float(row["text_rank"]) / best) ** 0.5 if best > 0 else 0
        age = max((fixed - record.updated_at).total_seconds() / 86400, 0)
        score = relevance * 0.55 + record.confidence * 0.25 + 0.1 / (1 + age) + weights[record.memory_type] * 0.1
        scored.append((score, record))
    scored.sort(key=lambda item: (-item[0], item[1].memory_id))
    assert service.recall("alice", "work", "shared", min_confidence=0.3,
                          memory_types=[MemoryType.SEMANTIC, MemoryType.PROCEDURAL], limit=7) == [item[1] for item in scored[:7]]


def request(messages=None):
    return ModelRequest(request_id="offline", messages=messages or ({"role": "user", "content": "hello"},),
                        max_output_tokens=10)


def mistral(content, *, name="mistral", usage=None):
    payload = {"choices": [{"message": {"content": content}}],
               "usage": usage or {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5}}
    return MistralProvider(
        ProviderConfig(name, "offline", "synthetic-only", input_cost_per_million=1, output_cost_per_million=2),
        endpoint="https://example.invalid", transport=lambda *_: (200, json.dumps(payload).encode()))


@pytest.mark.parametrize("name", ["mistral", "codestral"])
@pytest.mark.parametrize("content", ["hello", [{"type": "text", "text": "hel"}, {"type": "text", "text": "lo"}],
    [{"type": "thinking", "thinking": [{"type": "text", "text": "private reasoning"}]},
     {"type": "text", "text": "hello"}]])
def test_mistral_supported_content_and_usage(name, content):
    result = mistral(content, name=name).generate(request())
    assert result.text == "hello"
    assert result.usage.total_tokens == 5
    assert result.usage.estimated_cost == 8 / 1_000_000


@pytest.mark.parametrize("content", [None, [], ["text"], [{"text": "hello"}],
    [{"type": "text", "text": 1}], [{"type": "image_url", "text": "hello"}],
    [{"type": "thinking", "thinking": "private"}],
    [{"type": "thinking", "thinking": [{"type": "image", "text": "private"}]}],
    [{"type": "thinking", "thinking": [{"type": "text", "text": "private"}]}],
    [{"type": "text", "text": "hello"}, {"type": "future", "payload": "synthetic-private"}]])
def test_mistral_unknown_or_malformed_chunks_fail_closed(content):
    with pytest.raises(ProviderError) as caught:
        mistral(content).generate(request())
    assert str(caught.value) == "Provider returned a malformed response or failed"


def test_chunks_preserve_proposal_validation_and_budget_accounting():
    chunks = [{"type": "text", "text": '{"text":"hello",'}, {"type": "text", "text": '"owner_id":"forged"}'}]
    provider = mistral(chunks)
    gateway = ModelGateway((provider,), daily_token_budget=10000, request_token_budget=10000)
    reserved = provider.estimate_usage(request())
    with pytest.raises(ProviderError):
        gateway.generate(request())
    assert gateway.charged_usage.total_tokens == reserved.total_tokens
    assert gateway.charged_usage.estimated_cost == reserved.estimated_cost
    assert gateway.usage.total_tokens == 0
    good = mistral([{"type": "text", "text": '{"text":'}, {"type": "text", "text": '"hello"}'}])
    assert good.generate(request()).text == "hello"
    with pytest.raises(ProviderError):
        mistral([{"type": "text", "text": "hello"}], usage={"prompt_tokens": 2,
            "completion_tokens": 3, "total_tokens": 6}).generate(request())


def test_mistral_chunked_proposal_uses_existing_authority_contract():
    proposal = {"tool_name": "memory.recall", "action": "recall", "scope": "memory",
                "arguments": {"query": "shared"}}
    text = json.dumps({"tool_proposal": proposal})
    content = [{"type": "text", "text": text[:30]}, {"type": "text", "text": text[30:]}]
    result = mistral(content).generate(request())
    assert result.tool_proposal.model_dump(exclude_none=True) == proposal
    proposal["arguments"]["owner_id"] = "forged"
    with pytest.raises(ProviderError):
        mistral([{"type": "text", "text": json.dumps({"tool_proposal": proposal})}]).generate(request())


@pytest.mark.parametrize("damage", ["index", "scope", "key"])
def test_lookup_validation_rejects_corrupt_derived_state(service, damage):
    create(service)
    with service.repository.db.transaction() as conn:
        if damage == "index":
            conn.execute("DROP INDEX idx_memory_lookup_subject")
        elif damage == "scope":
            conn.execute("UPDATE memory_lookup SET owner_id='other'")
        else:
            conn.execute("UPDATE memory_lookup SET normalized_content='stale'")
    with service.repository.db.transaction(write=False) as conn, pytest.raises(ValueError):
        validate_schema(conn)


def test_gemini_30_is_text_only_and_does_not_promote_provider_state():
    bodies = []
    def transport(req, timeout):
        bodies.append(json.loads(req.data))
        return 200, json.dumps({"candidates": [{"content": {"parts": [
            {"text": "private", "thought": True},
            {"text": "hello", "thoughtSignature": "opaque-untrusted"}]} }],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3,
                              "thoughtsTokenCount": 4, "totalTokenCount": 9}}).encode()
    provider = GeminiProvider(ProviderConfig("gemini", "offline", "synthetic-only",
        input_cost_per_million=1, output_cost_per_million=2), transport=transport)
    first = provider.generate(request())
    provider.generate(request(({"role": "user", "content": "hello"},
        {"role": "assistant", "content": first.text}, {"role": "user", "content": "continue"})))
    assert first.text == "hello" and first.usage.total_tokens == 9
    assert first.tool_proposal is None
    assert all(set(body) == {"contents", "generationConfig"} for body in bodies)
    assert bodies[1]["contents"][1] == {"role": "model", "parts": [{"text": "hello"}]}
    with pytest.raises(ValueError):
        request(({"role": "assistant", "content": "hello", "thoughtSignature": "forged"},))
    provider = GeminiProvider(ProviderConfig("gemini", "offline", "synthetic-only",
        input_cost_per_million=1, output_cost_per_million=2), transport=lambda *_: (200,
        json.dumps({"candidates": [{"content": {"parts": [{"functionCall": {
            "name": "memory.recall", "args": {}}, "thoughtSignature": "opaque"}]}}],
            "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3}}).encode()))
    with pytest.raises(ProviderError):
        provider.generate(request())
