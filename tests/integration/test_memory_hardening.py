from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from arona.database.migrations import DatabaseMigrations
from arona.database.schema import validate_schema
from arona.domain.models import MemoryRecord, MemorySensitivity, MemoryStatus, MemoryType
from arona.memory.admission import AdmissionStatus, MemoryAdmissionPolicy
from arona.memory.db import DatabaseConnection
from arona.memory.repository import MemoryRepository
from arona.memory.service import MemoryService


@pytest.fixture
def service(tmp_path):
    path = tmp_path / "memory.db"
    DatabaseMigrations(path).run_migrations()
    return MemoryService(MemoryRepository(DatabaseConnection(path)), MemoryAdmissionPolicy(), default_ttl_seconds=None)


def create(service, **changes):
    values = dict(owner_id="alice", workspace_id="work", memory_type=MemoryType.SEMANTIC,
                  content="A useful stable preference", source="user", confidence=0.9,
                  sensitivity=MemorySensitivity.PUBLIC)
    values.update(changes)
    return service.create_memory(**values)


def record(identifier="sample", **changes):
    now = datetime.now(timezone.utc)
    values = dict(memory_id=identifier, owner_id="alice", workspace_id="work",
                  memory_type=MemoryType.SEMANTIC, content="shared phrase", source="user",
                  confidence=0.9, sensitivity=MemorySensitivity.PUBLIC, created_at=now,
                  updated_at=now, expires_at=None, status=MemoryStatus.ACTIVE, version=1)
    values.update(changes)
    return MemoryRecord(**values)


@pytest.mark.parametrize("kind", list(MemoryType))
def test_all_types_persist_metadata_and_recall(service, kind):
    expiry = datetime.now(timezone.utc) + timedelta(days=1) if kind == MemoryType.TEMPORARY else None
    memory = create(service, memory_type=kind, expires_at=expiry)
    saved = service.repository.get_by_id(memory.memory_id, "alice", "work")
    assert saved == memory
    assert saved.memory_type == kind and saved.owner_id == "alice" and saved.workspace_id == "work"
    assert saved.created_at.tzinfo is not None and saved.version == 1
    assert service.recall("alice", "work", "stable preference", memory_types=[kind]) == [saved]


@pytest.mark.parametrize("failure", ["insert", "audit", "cancel"])
def test_supersede_create_failure_restores_old_fact_and_audit(service, monkeypatch, failure):
    original = create(service, content="language: Python", confidence=0.8)
    db = service.repository.db
    with db.transaction(write=False) as conn:
        before_events = conn.execute("SELECT * FROM memory_events").fetchall()
    if failure == "insert":
        # Fail at the actual INSERT, after the old fact was superseded.
        with db.transaction() as conn:
            conn.execute("CREATE TRIGGER reject_new_memory BEFORE INSERT ON memories BEGIN SELECT RAISE(ABORT, 'synthetic insert failure'); END")
        expected = sqlite3.IntegrityError
    elif failure == "audit":
        real_event = MemoryRepository._event
        def fail_event(conn, owner, workspace, action, memory_id=None, count=1):
            if action == "create":
                raise RuntimeError("synthetic audit failure")
            return real_event(conn, owner, workspace, action, memory_id, count)
        monkeypatch.setattr(MemoryRepository, "_event", staticmethod(fail_event))
        expected = RuntimeError
    else:
        def cancel(*args):
            raise KeyboardInterrupt()
        monkeypatch.setattr(MemoryRepository, "create", cancel)
        expected = KeyboardInterrupt
    with pytest.raises(expected):
        create(service, content="language: Rust", confidence=0.95)
    saved = service.repository.get_by_id(original.memory_id, "alice", "work")
    assert saved == original
    assert service.recall("alice", "work", "Python") == [original]
    assert service.recall("alice", "work", "Rust") == []
    with db.transaction(write=False) as conn:
        assert conn.execute("SELECT * FROM memory_events").fetchall() == before_events
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 1


@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
def test_process_exit_between_supersede_and_insert_recovers(service, journal_mode):
    with closing(service.repository.db.connect()) as conn:
        assert conn.execute("PRAGMA journal_mode=" + journal_mode).fetchone()[0] == journal_mode.lower()
    original = create(service, content="language: Python", confidence=0.8)
    # Exit without Python context-manager cleanup: exercise SQLite journal recovery.
    code = '''
import os, sys
from arona.memory.db import DatabaseConnection
from arona.memory.repository import MemoryRepository
from arona.memory.service import MemoryService
from arona.memory.admission import MemoryAdmissionPolicy
from arona.domain.models import MemoryType, MemorySensitivity
from pathlib import Path
MemoryRepository.create = lambda *args: os._exit(73)
service = MemoryService(MemoryRepository(DatabaseConnection(Path(sys.argv[1]))), MemoryAdmissionPolicy())
service.create_memory('alice','work',MemoryType.SEMANTIC,'language: Rust','user',0.95,MemorySensitivity.PUBLIC)
'''
    env = os.environ.copy()
    import arona
    env["PYTHONPATH"] = str(Path(arona.__file__).parent.parent)
    result = subprocess.run([sys.executable, "-c", code, str(service.repository.db.database_path)], env=env, capture_output=True, timeout=15)
    assert result.returncode == 73
    assert not result.stdout and not result.stderr
    assert service.repository.get_by_id(original.memory_id, "alice", "work") == original
    assert service.recall("alice", "work", "Rust") == []
    with service.repository.db.transaction(write=False) as conn:
        validate_schema(conn)
        assert conn.execute("SELECT action FROM memory_events").fetchall()[0][0] == "create"
        assert conn.execute("SELECT count(*) FROM memory_events").fetchone()[0] == 1


def test_concurrent_duplicate_is_one_record(service):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create(service), range(2)))
    assert results[0].memory_id == results[1].memory_id
    assert len(service.export_memories("alice", "work")) == 1


def test_conflict_checks_all_facts_before_superseding_any(service):
    repo = service.repository
    weak = repo.create(record("weak", content="language: Python", confidence=0.75))
    strong = repo.create(record("strong", content="language: Rust", confidence=0.99))
    result = create(service, content="language: Java", confidence=0.9)
    assert result.status == AdmissionStatus.DENIED
    assert repo.get_by_id(weak.memory_id, "alice", "work").status == MemoryStatus.ACTIVE
    assert repo.get_by_id(strong.memory_id, "alice", "work").status == MemoryStatus.ACTIVE


@pytest.mark.parametrize("factor", ["relevance", "confidence", "recency", "type"])
def test_each_ranking_factor_changes_order(service, factor):
    now = datetime.now(timezone.utc)
    low = record("a_low", updated_at=now)
    high = record("z_high", updated_at=now)
    if factor == "relevance":
        low = low.model_copy(update={"content": "shared phrase " + "filler " * 100})
        high = high.model_copy(update={"content": "shared phrase shared phrase shared phrase"})
    elif factor == "confidence":
        low = low.model_copy(update={"confidence": 0.71})
    elif factor == "recency":
        low = low.model_copy(update={"updated_at": now - timedelta(days=60)})
    else:
        low = low.model_copy(update={"memory_type": MemoryType.TEMPORARY})
    service.repository.create(low)
    service.repository.create(high)
    assert [item.memory_id for item in service.recall("alice", "work", "shared phrase")] == ["z_high", "a_low"]


def test_timezone_offsets_and_naive_legacy_dates_are_safe(service):
    now = datetime.now(timezone.utc)
    future = record("future", expires_at=(now + timedelta(hours=1)).astimezone(timezone(timedelta(hours=-8))))
    expired = record("expired", expires_at=(now - timedelta(hours=1)).astimezone(timezone(timedelta(hours=9))))
    naive = record("naive", updated_at=now.replace(tzinfo=None))
    for item in (future, expired, naive):
        service.repository.create(item)
    assert {m.memory_id for m in service.recall("alice", "work", "shared phrase")} == {"future", "naive"}


@pytest.mark.parametrize("query", ['"shared" (phrase)', "shared:phrase*", "shared + phrase", "shared\x00phrase", "!!!", '" OR NOT NEAR ^ : *'])
def test_arbitrary_text_does_not_become_fts_syntax(service, query):
    service.repository.create(record())
    results = service.recall("alice", "work", query)
    assert isinstance(results, list)
    assert all(m.owner_id == "alice" and m.workspace_id == "work" for m in results)


def test_query_size_rejected_explicitly(service):
    with pytest.raises(ValueError, match="query"):
        service.recall("alice", "work", " ".join("term" + str(i) for i in range(65)))


def test_forget_covers_all_statuses_and_workspaces_without_cap(service):
    repo = service.repository
    with repo.atomic() as scoped:
        for index in range(55):
            scoped.create(record(str(index), workspace_id="work" if index < 30 else "other",
                                 status=list(MemoryStatus)[index % 3],
                                 expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc)))
        scoped.create(record("bob", owner_id="bob"))
    assert service.forget_workspace("alice", "work") == 30
    assert service.forget_owner("alice") == 25
    with repo.db.transaction(write=False) as conn:
        assert conn.execute("SELECT owner_id FROM memories").fetchall()[0][0] == "bob"
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 1
        validate_schema(conn)


def test_export_defaults_to_all_records_and_has_stable_pages(service):
    with service.repository.atomic() as repo:
        for index in range(1005):
            repo.create(record(str(index), status=list(MemoryStatus)[index % 3]))
        repo.create(record("other-owner", owner_id="bob"))
        repo.create(record("other-workspace", workspace_id="elsewhere"))
    exported = service.export_memories("alice", "work")
    assert len(exported) == 1005
    pages = service.export_memories("alice", "work", limit=1000) + service.export_memories("alice", "work", limit=1000, offset=1000)
    assert [m.memory_id for m in pages] == [m.memory_id for m in exported]


def test_auto_memory_control_is_persistent_scoped_and_enforced(service):
    assert create(service, automatic=True, long_term_value=True).status == AdmissionStatus.DENIED
    service.set_auto_memory("alice", "work", True)
    restarted = MemoryService(MemoryRepository(DatabaseConnection(service.repository.db.database_path)), MemoryAdmissionPolicy(), default_ttl_seconds=None)
    assert restarted.auto_memory_enabled("alice", "work")
    assert not restarted.auto_memory_enabled("alice", "other")
    assert not restarted.auto_memory_enabled("bob", "work")
    assert isinstance(create(restarted, automatic=True, long_term_value=True), MemoryRecord)
    restarted.set_auto_memory("alice", "work", False)
    assert create(service, content="Another durable note", automatic=True, long_term_value=True).status == AdmissionStatus.DENIED
    assert isinstance(create(service, content="Explicitly requested note"), MemoryRecord)


@pytest.mark.parametrize("field", ["content", "source", "owner_id", "workspace_id"])
@pytest.mark.parametrize("kind", ["password", "vietnamese", "otp", "token", "api", "private"])
def test_secret_rejection_in_all_input_fields(service, field, kind):
    values = {"password": "password: fake-only", "vietnamese": "mật khẩu: fake-only",
              "otp": "OTP=111111", "token": "token=fake-only", "api": "API key=fake-only",
              "private": "-----BEGIN PRIVATE KEY-----"}
    try:
        result = create(service, **{field: values[kind]})
    except ValueError:
        result = None
    assert result is None or result.status == AdmissionStatus.DENIED
    with service.repository.db.transaction(write=False) as conn:
        assert conn.execute("SELECT count(*) FROM memories").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM memories_fts").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM memory_events").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["memory_id", "owner_id", "workspace_id", "content", "source"])
def test_repository_rejects_secret_even_after_model_copy(service, field):
    unsafe = record().model_copy(update={field: "password: synthetic-only"})
    with pytest.raises(ValueError) as caught:
        service.repository.create(unsafe)
    assert "synthetic-only" not in str(caught.value)


@pytest.mark.parametrize("content", ["Use a password manager", "Review token budget weekly", "API key rotation policy is documented", "The tokenizer supports Unicode", "The account has 12345678901234567890123456789012 seats"])
def test_legitimate_security_discussion_is_not_secret(service, content):
    assert isinstance(create(service, content=content), MemoryRecord)


@pytest.mark.parametrize("content", ["Maybe Alice uses Python", "Có lẽ tôi thích Rust", "I am sad right now", "Tôi đang buồn hôm nay"])
def test_speculation_and_transient_emotion_are_not_saved(service, content):
    assert create(service, content=content).status == AdmissionStatus.DENIED
    assert service.export_memories("alice", "work") == []


def test_value_uncertainty_confirmation_and_secret_rules(service):
    assert create(service, long_term_value=False).status == AdmissionStatus.DENIED
    service.set_auto_memory("alice", "work", True)
    assert create(service, automatic=True).status == AdmissionStatus.CONFIRMATION_REQUIRED
    assert create(service, confidence=0.5).status == AdmissionStatus.CONFIRMATION_REQUIRED
    assert create(service, sensitivity=MemorySensitivity.SENSITIVE).status == AdmissionStatus.CONFIRMATION_REQUIRED
    assert isinstance(create(service, sensitivity=MemorySensitivity.SENSITIVE, confirmed=True), MemoryRecord)
    assert create(service, content="password: synthetic-only", confirmed=True).status == AdmissionStatus.DENIED


def test_updates_and_deletes_preserve_scope_versions_and_do_not_resurrect(service):
    memory = create(service)
    for owner, workspace in (("bob", "work"), ("alice", "other")):
        assert service.repository.get_by_id(memory.memory_id, owner, workspace) is None
        assert service.delete_memory(memory.memory_id, owner, workspace) is None
        assert service.export_memories(owner, workspace) == []
        assert service.forget_workspace(owner, workspace) == 0
        with pytest.raises(ValueError):
            service.update_memory(memory.memory_id, owner, workspace, "other content", 0.9, MemorySensitivity.PUBLIC)
    updated = service.update_memory(memory.memory_id, "alice", "work", "corrected stable note", 0.95, MemorySensitivity.PUBLIC)
    assert updated.version == 2
    with pytest.raises(ValueError, match="changed"):
        service.repository.update(memory.model_copy(update={"version": 2, "content": "stale overwrite"}))
    deleted = service.delete_memory(memory.memory_id, "alice", "work")
    assert deleted.version == 3 and deleted.status == MemoryStatus.DELETED
    with pytest.raises(ValueError):
        service.update_memory(memory.memory_id, "alice", "work", "resurrection", 0.9, MemorySensitivity.PUBLIC)


def test_audit_failure_rolls_back_forget_and_preference(service, monkeypatch):
    memory = create(service)
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(MemoryRepository, "_event", staticmethod(fail))
    with pytest.raises(RuntimeError):
        service.forget_owner("alice")
    with pytest.raises(RuntimeError):
        service.set_auto_memory("alice", "work", True)
    assert service.repository.get_by_id(memory.memory_id, "alice", "work") == memory
    assert not service.auto_memory_enabled("alice", "work")


def test_callback_failure_and_recall_do_not_log_secret(service, caplog):
    marker = "synthetic-private-value"
    def fail(*args):
        raise RuntimeError(marker)
    service.audit_callback = fail
    memory = create(service)
    assert isinstance(memory, MemoryRecord)
    service.recall("alice", "work", marker)
    assert marker not in caplog.text
    with closing(service.repository.db.connect(readonly=True)) as conn:
        assert conn.execute("SELECT count(*) FROM memory_events WHERE action='create'").fetchone()[0] == 1


def test_temporary_requires_ttl_and_expired_replacement_cannot_supersede(service):
    with pytest.raises(ValueError, match="expiry"):
        create(service, memory_type=MemoryType.TEMPORARY)
    original = create(service, content="language: Python", confidence=0.8)
    with pytest.raises(ValueError, match="future"):
        create(service, content="language: Rust", confidence=0.95, expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
    assert service.repository.get_by_id(original.memory_id, "alice", "work") == original


@pytest.mark.parametrize("field", ["confirmed", "automatic", "long_term_value"])
def test_admission_flags_do_not_accept_truthy_strings(service, field):
    assert create(service, confidence=0.5, **{field: "false"}).status == AdmissionStatus.DENIED


@pytest.mark.parametrize("field", ["content", "source"])
def test_oversized_fields_are_rejected_without_truncation(service, field):
    assert create(service, **{field: "x" * 10001}).status == AdmissionStatus.DENIED
    assert not service.export_memories("alice", "work")


def test_update_rechecks_source_and_requires_confirmation(service):
    memory = create(service)
    with pytest.raises(ValueError):
        service.update_memory(memory.memory_id, "alice", "work", "new sensitive preference", 0.9, MemorySensitivity.SENSITIVE)
    updated = service.update_memory(memory.memory_id, "alice", "work", "new sensitive preference", 0.9, MemorySensitivity.SENSITIVE, confirmed=True)
    assert updated.version == 2
    with service.repository.db.transaction() as conn:
        conn.execute("UPDATE memories SET source='password: synthetic-only' WHERE memory_id=?", (memory.memory_id,))
    with pytest.raises(ValueError) as caught:
        service.update_memory(memory.memory_id, "alice", "work", "safe correction", 0.9, MemorySensitivity.PUBLIC)
    assert "synthetic-only" not in str(caught.value)


@pytest.mark.parametrize("prefix", ["Preferred language is", "Ngôn ngữ ưa thích là", "language:", "language="])
def test_explicit_fact_conflicts_keep_stronger_memory(service, prefix):
    original = create(service, content=prefix + " Python", confidence=0.95)
    assert create(service, content=prefix + " Rust", confidence=0.8).status == AdmissionStatus.DENIED
    assert service.repository.get_by_id(original.memory_id, "alice", "work") == original


def test_distinct_urls_are_not_treated_as_one_subject(service):
    first = create(service, content="https://example.com/one")
    second = create(service, content="https://example.com/two")
    assert isinstance(first, MemoryRecord) and isinstance(second, MemoryRecord)
