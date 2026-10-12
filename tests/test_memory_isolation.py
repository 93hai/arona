from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Final

from arona.database.connection import DatabaseConnection
from arona.database.migrations import DatabaseMigrations
from arona.domain.models import (
    MemoryRecord,
    MemorySensitivity,
    MemoryStatus,
    MemoryType,
)
from arona.memory.admission import (
    AdmissionStatus,
    MemoryAdmissionDecision,
    MemoryAdmissionPolicy,
)
from arona.memory.repository import MemoryRepository
from arona.memory.service import MemoryService

# =============================================================================
# HẰNG SỐ CỐ ĐỊNH
# =============================================================================

# Thời gian sống ngắn cho memory test
TEST_MEMORY_TTL_SECONDS: Final[int] = 1

# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Fixture tạo đường dẫn file SQLite tạm thời."""
    return tmp_path / "test_memory_isolation.db"

@pytest.fixture
def db_connection(temp_db_path: Path) -> DatabaseConnection:
    """Fixture tạo DatabaseConnection với file SQLite tạm thời."""
    DatabaseMigrations(temp_db_path).run_migrations()
    return DatabaseConnection(temp_db_path)

@pytest.fixture
def memory_repository(db_connection: DatabaseConnection) -> MemoryRepository:
    """Fixture tạo MemoryRepository với file SQLite tạm thời."""
    return MemoryRepository(db_connection)

@pytest.fixture
def admission_policy() -> MemoryAdmissionPolicy:
    """Fixture tạo MemoryAdmissionPolicy."""
    return MemoryAdmissionPolicy()

@pytest.fixture
def memory_service(
    memory_repository: MemoryRepository,
    admission_policy: MemoryAdmissionPolicy,
) -> MemoryService:
    """Fixture tạo MemoryService với file SQLite tạm thời."""
    return MemoryService(
        repository=memory_repository,
        admission_policy=admission_policy,
        default_ttl_seconds=TEST_MEMORY_TTL_SECONDS,
    )

# =============================================================================
# TESTS
# =============================================================================

def test_memory_isolation_by_owner(
    memory_service: MemoryService,
    db_connection: DatabaseConnection,
) -> None:
    """Test owner A không đọc, sửa hoặc xóa memory của owner B."""
    # Tạo memory cho owner A
    owner_a_memory = memory_service.create_memory(
        owner_id="owner_a",
        workspace_id="workspace_a",
        memory_type=MemoryType.SEMANTIC,
        content="Memory của owner A",
        source="test",
        confidence=0.9,
        sensitivity=MemorySensitivity.PUBLIC,
    )
    assert isinstance(owner_a_memory, MemoryRecord)

    # Tạo memory cho owner B
    owner_b_memory = memory_service.create_memory(
        owner_id="owner_b",
        workspace_id="workspace_b",
        memory_type=MemoryType.SEMANTIC,
        content="Memory của owner B",
        source="test",
        confidence=0.9,
        sensitivity=MemorySensitivity.PUBLIC,
    )
    assert isinstance(owner_b_memory, MemoryRecord)

    # Owner A không đọc được memory của owner B
    recalled_memories = memory_service.recall(
        owner_id="owner_a",
        workspace_id="workspace_a",
        query="Memory của owner B",
    )
    assert len(recalled_memories) == 0

    # Owner A không sửa được memory của owner B
    with pytest.raises(ValueError):
        memory_service.update_memory(
            memory_id=owner_b_memory.memory_id,
            owner_id="owner_a",
            workspace_id="workspace_b",
            content="Memory đã bị sửa",
            confidence=0.9,
            sensitivity=MemorySensitivity.PUBLIC,
        )

    # Owner A không xóa được memory của owner B
    deleted_memory = memory_service.delete_memory(
        memory_id=owner_b_memory.memory_id,
        owner_id="owner_a",
        workspace_id="workspace_b",
    )
    assert deleted_memory is None

def test_memory_isolation_by_workspace(
    memory_service: MemoryService,
    db_connection: DatabaseConnection,
) -> None:
    """Test workspace A không đọc memory của workspace B."""
    # Tạo memory cho workspace A
    workspace_a_memory = memory_service.create_memory(
        owner_id="owner_a",
        workspace_id="workspace_a",
        memory_type=MemoryType.SEMANTIC,
        content="Memory của workspace A",
        source="test",
        confidence=0.9,
        sensitivity=MemorySensitivity.PUBLIC,
    )
    assert isinstance(workspace_a_memory, MemoryRecord)

    # Tạo memory cho workspace B
    workspace_b_memory = memory_service.create_memory(
        owner_id="owner_a",
        workspace_id="workspace_b",
        memory_type=MemoryType.SEMANTIC,
        content="Memory của workspace B",
        source="test",
        confidence=0.9,
        sensitivity=MemorySensitivity.PUBLIC,
    )
    assert isinstance(workspace_b_memory, MemoryRecord)

    # Workspace A không đọc được memory của workspace B
    recalled_memories = memory_service.recall(
        owner_id="owner_a",
        workspace_id="workspace_a",
        query="Memory của workspace B",
    )
    assert len(recalled_memories) == 0

def test_soft_delete_not_recallable(
    memory_service: MemoryService,
    db_connection: DatabaseConnection,
) -> None:
    """Test soft delete không còn xuất hiện trong recall."""
    # Tạo memory
    memory = memory_service.create_memory(
        owner_id="owner_a",
        workspace_id="workspace_a",
        memory_type=MemoryType.SEMANTIC,
        content="Memory sẽ bị xóa",
        source="test",
        confidence=0.9,
        sensitivity=MemorySensitivity.PUBLIC,
    )
    assert isinstance(memory, MemoryRecord)

    # Xóa mềm memory
    deleted_memory = memory_service.delete_memory(
        memory_id=memory.memory_id,
        owner_id="owner_a",
        workspace_id="workspace_a",
    )
    assert deleted_memory is not None
    assert deleted_memory.status == MemoryStatus.DELETED

    # Memory đã xóa không xuất hiện trong recall
    recalled_memories = memory_service.recall(
        owner_id="owner_a",
        workspace_id="workspace_a",
        query="Memory sẽ bị xóa",
    )
    assert len(recalled_memories) == 0

def test_expired_memory_not_recallable(
    memory_service: MemoryService,
    db_connection: DatabaseConnection,
) -> None:
    """Test memory hết hạn không còn xuất hiện."""
    # Tạo memory với thời gian sống ngắn
    memory = memory_service.create_memory(
        owner_id="owner_a",
        workspace_id="workspace_a",
        memory_type=MemoryType.SEMANTIC,
        content="Memory sẽ hết hạn",
        source="test",
        confidence=0.9,
        sensitivity=MemorySensitivity.PUBLIC,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=TEST_MEMORY_TTL_SECONDS),
    )
    assert isinstance(memory, MemoryRecord)

    # Chờ memory hết hạn
    import time
    time.sleep(TEST_MEMORY_TTL_SECONDS + 1)

    # Memory đã hết hạn không xuất hiện trong recall
    recalled_memories = memory_service.recall(
        owner_id="owner_a",
        workspace_id="workspace_a",
        query="Memory sẽ hết hạn",
    )
    assert len(recalled_memories) == 0

def test_confirmation_required_not_saved(
    memory_service: MemoryService,
    db_connection: DatabaseConnection,
) -> None:
    """Test confirmation_required không ghi database."""
    # Tạo memory với độ tin cậy thấp
    decision = memory_service.create_memory(
        owner_id="owner_a",
        workspace_id="workspace_a",
        memory_type=MemoryType.SEMANTIC,
        content="Memory cần xác nhận",
        source="test",
        confidence=0.5,
        sensitivity=MemorySensitivity.PUBLIC,
    )
    assert isinstance(decision, MemoryAdmissionDecision)
    assert decision.status == AdmissionStatus.CONFIRMATION_REQUIRED

    # Memory không được ghi vào database
    recalled_memories = memory_service.recall(
        owner_id="owner_a",
        workspace_id="workspace_a",
        query="Memory cần xác nhận",
    )
    assert len(recalled_memories) == 0

def test_secret_sensitivity_denied(
    memory_service: MemoryService,
    db_connection: DatabaseConnection,
) -> None:
    """Test sensitivity=secret bị từ chối."""
    # Tạo memory với sensitivity=secret
    decision = memory_service.create_memory(
        owner_id="owner_a",
        workspace_id="workspace_a",
        memory_type=MemoryType.SEMANTIC,
        content="Memory nhạy cảm",
        source="test",
        confidence=0.9,
        sensitivity=MemorySensitivity.SECRET,
    )
    assert isinstance(decision, MemoryAdmissionDecision)
    assert decision.status == AdmissionStatus.DENIED

    # Memory không được ghi vào database
    recalled_memories = memory_service.recall(
        owner_id="owner_a",
        workspace_id="workspace_a",
        query="Memory nhạy cảm",
    )
    assert len(recalled_memories) == 0

def test_content_like_api_key_denied(
    memory_service: MemoryService,
    db_connection: DatabaseConnection,
) -> None:
    """Test nội dung giống API key bị từ chối."""
    # Tạo nội dung giống API key
    fake_api_key = "sk-" + ("x" * 24)
    decision = memory_service.create_memory(
        owner_id="owner_a",
        workspace_id="workspace_a",
        memory_type=MemoryType.SEMANTIC,
        content=f"API key là {fake_api_key}",
        source="test",
        confidence=0.9,
        sensitivity=MemorySensitivity.PUBLIC,
    )
    assert isinstance(decision, MemoryAdmissionDecision)
    assert decision.status == AdmissionStatus.DENIED

    # Memory không được ghi vào database
    recalled_memories = memory_service.recall(
        owner_id="owner_a",
        workspace_id="workspace_a",
        query=fake_api_key,
    )
    assert len(recalled_memories) == 0


def test_forget_owner_removes_memories_across_workspaces(
    memory_service: MemoryService,
) -> None:
    for workspace_id in ("workspace_a", "workspace_b"):
        memory = memory_service.create_memory(
            owner_id="owner_a",
            workspace_id=workspace_id,
            memory_type=MemoryType.SEMANTIC,
            content=f"Owner memory in {workspace_id}",
            source="test",
            confidence=0.9,
            sensitivity=MemorySensitivity.PUBLIC,
        )
        assert isinstance(memory, MemoryRecord)

    assert memory_service.forget_owner("owner_a") == 2

    for workspace_id in ("workspace_a", "workspace_b"):
        assert memory_service.recall(
            owner_id="owner_a",
            workspace_id=workspace_id,
            query="Owner memory",
        ) == []
