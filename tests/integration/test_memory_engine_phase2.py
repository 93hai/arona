from __future__ import annotations

from datetime import datetime, timezone

import pytest

from arona.database.migrations import DatabaseMigrations
from arona.database.connection import DatabaseConnection
from arona.domain.models import MemoryRecord, MemorySensitivity, MemoryType
from arona.memory.admission import AdmissionStatus, MemoryAdmissionPolicy
from arona.memory.repository import MemoryRepository
from arona.memory.service import MemoryService


@pytest.fixture
def memory_service(tmp_path):
    database_path = tmp_path / "memory.db"
    DatabaseMigrations(database_path).run_migrations()
    repository = MemoryRepository(DatabaseConnection(database_path))
    return MemoryService(repository, MemoryAdmissionPolicy(), default_ttl_seconds=None)


def create(service, *, owner="alice", workspace="project", content, confidence=0.9, memory_type=MemoryType.SEMANTIC, source="user"):
    result = service.create_memory(
        owner_id=owner,
        workspace_id=workspace,
        memory_type=memory_type,
        content=content,
        source=source,
        confidence=confidence,
        sensitivity=MemorySensitivity.PUBLIC,
    )
    assert isinstance(result, MemoryRecord), result
    return result


def test_optional_ttl_and_special_query(memory_service):
    memory = create(memory_service, content="A stable preference without expiry")
    assert memory.expires_at is None
    assert memory_service.recall("alice", "project", 'stable "preference"')
    assert memory_service.recall("alice", "project", 'stable "preference" OR NOT crash') == []
    assert memory_service.recall("alice", "project", "!!!") == []


def test_ranking_combines_confidence_and_preserves_order(memory_service):
    low = create(memory_service, content="shared retrieval phrase", confidence=0.71)
    high = create(memory_service, content="shared retrieval phrase with detail", confidence=0.99)
    results = memory_service.recall("alice", "project", "shared retrieval phrase")
    assert [item.memory_id for item in results] == [high.memory_id, low.memory_id]


def test_duplicate_is_idempotent_and_conflict_supersedes_weaker(memory_service):
    original = create(memory_service, content="language: Python", confidence=0.8)
    duplicate = create(memory_service, content="  LANGUAGE:   python ", confidence=0.9)
    assert duplicate.memory_id == original.memory_id

    replacement = create(memory_service, content="language: Rust", confidence=0.95)
    assert replacement.memory_id != original.memory_id
    assert memory_service.repository.get_by_id(original.memory_id, "alice", "project").status.name == "SUPERSEDED"

    weaker = memory_service.create_memory(
        owner_id="alice",
        workspace_id="project",
        memory_type=MemoryType.SEMANTIC,
        content="language: Java",
        source="guess",
        confidence=0.7,
        sensitivity=MemorySensitivity.PUBLIC,
    )
    assert getattr(weaker, "status", None) == AdmissionStatus.DENIED


def test_admission_scans_source_and_preserves_false_positive(memory_service):
    for secret in ("password", "mật khẩu", "OTP", "API key", "token", "private key", "secret"):
        decision = memory_service.create_memory(
            owner_id="alice",
            workspace_id="project",
            memory_type=MemoryType.SEMANTIC,
            content="ordinary note",
            source=secret,
            confidence=0.9,
            sensitivity=MemorySensitivity.PUBLIC,
        )
        assert decision.status == AdmissionStatus.DENIED

    allowed = create(memory_service, content="The account has 12345678901234567890123456789012 seats")
    assert allowed.content.startswith("The account")


def test_pagination_and_isolation(memory_service):
    for index in range(3):
        create(memory_service, content=f"export item {index}")
    create(memory_service, owner="bob", content="export item other owner")

    first_page = memory_service.export_memories("alice", "project", limit=2, offset=0)
    second_page = memory_service.export_memories("alice", "project", limit=2, offset=2)
    assert len(first_page) == 2
    assert len(second_page) == 1
    assert all(item.owner_id == "alice" for item in first_page + second_page)


def test_expired_and_superseded_are_not_recalled(memory_service):
    expired = create(memory_service, content="expired record")
    memory_service.repository.update(expired.model_copy(update={"expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc), "version": 2}))
    assert memory_service.recall("alice", "project", "expired record") == []

    current = create(memory_service, content="status record")
    memory_service.repository.supersede(current.memory_id, owner_id="alice", workspace_id="project")
    assert memory_service.recall("alice", "project", "status record") == []
