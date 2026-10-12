# Arona 3.0 Memory Service

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence
from uuid import uuid4
from arona.memory.keys import normalize_content, subject_key

from arona.memory.redaction import validate_identifier

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

class MemoryService:
    """Service cho các thao tác memory trong Arona 3.0.
    
    Class này chịu trách nhiệm quản lý các thao tác memory, bao gồm tạo, đọc,
    cập nhật và xóa. Nó không chứa logic AI, Discord hoặc Policy Engine.
    """
    
    def __init__(
        self,
        repository: MemoryRepository,
        admission_policy: MemoryAdmissionPolicy,
        default_ttl_seconds: int | None = 2_592_000,
        audit_callback: Optional[Callable[[str, str, str, str], None]] = None,
        *,
        auto_memory_enabled: bool = False,
    ) -> None:
        """Khởi tạo MemoryService với các dependency.
        
        Args:
            repository: MemoryRepository đã cấu hình.
            admission_policy: MemoryAdmissionPolicy đã cấu hình.
            default_ttl_seconds: TTL mặc định chỉ cho Temporary (mặc định: 30 ngày).
            audit_callback: Callback để ghi audit (tùy chọn).
        """
        if default_ttl_seconds is not None and (type(default_ttl_seconds) is not int or not 1 <= default_ttl_seconds <= 31_536_000):
            raise ValueError("Default TTL must be within one year")
        if type(auto_memory_enabled) is not bool:
            raise ValueError("Auto-memory default must be a boolean")
        self.auto_memory_default = auto_memory_enabled
        self.repository = repository
        self.admission_policy = admission_policy
        self.default_ttl_seconds = default_ttl_seconds
        self.audit_callback = audit_callback

    def create_memory(
        self,
        owner_id: str,
        workspace_id: str,
        memory_type: MemoryType,
        content: str,
        source: str,
        confidence: float,
        sensitivity: MemorySensitivity,
        expires_at: Optional[datetime] = None,
        *,
        automatic: bool = False,
        long_term_value: bool | None = None,
        confirmed: bool = False,
    ) -> MemoryRecord | MemoryAdmissionDecision:
        """Tạo một memory mới.
        
        Args:
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
            memory_type: Loại memory.
            content: Nội dung của memory.
            source: Nguồn của memory.
            confidence: Độ tin cậy của memory.
            sensitivity: Độ nhạy cảm của memory.
            expires_at: Thời điểm hết hạn của memory (tùy chọn).
        
        Returns:
            MemoryRecord | MemoryAdmissionDecision: Bản ghi memory đã được tạo nếu được chấp nhận,
                hoặc quyết định của MemoryAdmissionPolicy nếu bị từ chối.
        
        Raises:
            ValueError: Nếu owner_id hoặc workspace_id không hợp lệ.
        """
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        if not owner_id or not workspace_id:
            raise ValueError(
                "owner_id và workspace_id không được để trống"
            )

        # Đánh giá memory qua Admission Policy
        decision = self.admission_policy.evaluate(
            content=content,
            memory_type=memory_type,
            confidence=confidence,
            sensitivity=sensitivity,
            source=source,
            automatic=automatic,
            long_term_value=long_term_value,
            confirmed=confirmed,
        )

        # Nếu bị từ chối, trả về quyết định
        if decision.status == AdmissionStatus.DENIED:
            self._audit(
                "memory_create_denied",
                owner_id,
                workspace_id,
                decision.reason,
            )
            return decision

        # Nếu yêu cầu xác nhận, trả về quyết định
        if decision.status == AdmissionStatus.CONFIRMATION_REQUIRED:
            self._audit(
                "memory_create_confirmation_required",
                owner_id,
                workspace_id,
                decision.reason,
            )
            return decision

        with self.repository.atomic() as repository:
            if automatic and not repository.auto_memory_enabled(owner_id, workspace_id, default=self.auto_memory_default):
                return MemoryAdmissionDecision(status=AdmissionStatus.DENIED, reason="Auto-memory is disabled", sanitized_content="")
            existing = repository.admission_candidates(
                owner_id, workspace_id, memory_type, decision.sanitized_content,
            )
            normalized_content = self._normalize_content(decision.sanitized_content)
            for candidate in existing:
                if candidate.memory_type == memory_type and self._normalize_content(candidate.content) == normalized_content:
                    if expires_at is not None:
                        now = datetime.now(timezone.utc)
                        updated = MemoryRecord.model_validate({**candidate.model_dump(),
                            "expires_at": expires_at, "updated_at": now, "version": candidate.version + 1})
                        if updated.expires_at <= now:
                            raise ValueError("New Memory expiry must be in the future")
                        if updated.expires_at != candidate.expires_at:
                            candidate = repository.update(updated)
                            self._audit("memory_expiry_updated", owner_id, workspace_id, "explicit expiry applied")
                    self._audit("memory_duplicate", owner_id, workspace_id, "existing memory reused")
                    return candidate

            conflicts = []
            subject = self._subject_key(decision.sanitized_content)
            if subject:
                for candidate in existing:
                    if self._subject_key(candidate.content) != subject:
                        continue
                    if confidence <= candidate.confidence:
                        decision = MemoryAdmissionDecision(
                            status=AdmissionStatus.DENIED,
                            reason="Mâu thuẫn với memory đáng tin cậy hơn",
                            sanitized_content="",
                        )
                        self._audit("memory_conflict_denied", owner_id, workspace_id, "stronger memory retained")
                        return decision
                    conflicts.append(candidate)

            memory_id = f"memory_{uuid4().hex}"
            now = datetime.now(timezone.utc)

            # Semantic/Procedural/Episodic chỉ hết hạn khi caller đặt expires_at.
            # Roadmap không yêu cầu tự hết hạn Episodic; TTL mặc định dành cho Temporary.
            if expires_at is None and memory_type == MemoryType.TEMPORARY and self.default_ttl_seconds is not None:
                expires_at = now + timedelta(seconds=self.default_ttl_seconds)
            if expires_at is None and memory_type == MemoryType.TEMPORARY:
                raise ValueError("Temporary Memory requires an expiry")

            # Tạo bản ghi memory
            memory = MemoryRecord(
                memory_id=memory_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                memory_type=memory_type,
                content=decision.sanitized_content,
                source=source,
                created_at=now,
                updated_at=now,
                confidence=confidence,
                sensitivity=sensitivity,
                expires_at=expires_at,
                status=MemoryStatus.ACTIVE,
                version=1,
            )

            # Lưu memory vào cơ sở dữ liệu
            if memory.expires_at is not None and memory.expires_at <= now:
                raise ValueError("New Memory expiry must be in the future")
            for candidate in conflicts:
                repository.supersede(candidate.memory_id, owner_id=owner_id, workspace_id=workspace_id)
            created_memory = repository.create(memory)
        self._audit(
            "memory_created",
            owner_id,
            workspace_id,
            f"memory_id={memory_id}",
        )

        return created_memory

    def recall(
        self,
        owner_id: str,
        workspace_id: str,
        query: str,
        memory_types: Optional[Sequence[MemoryType]] = None,
        min_confidence: Optional[float] = None,
        limit: int = 5,
    ) -> list[MemoryRecord]:
        """Tìm kiếm memory theo truy vấn.
        
        Args:
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
            query: Truy vấn tìm kiếm.
            memory_types: Danh sách loại memory để lọc (tùy chọn).
            min_confidence: Độ tin cậy tối thiểu (tùy chọn).
            limit: Số lượng kết quả tối đa.
        
        Returns:
            list[MemoryRecord]: Danh sách các memory phù hợp.
        
        Raises:
            ValueError: Nếu owner_id hoặc workspace_id không hợp lệ.
        """
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        if not owner_id or not workspace_id:
            raise ValueError(
                "owner_id và workspace_id không được để trống"
            )

        memories = self.repository.search(
            owner_id=owner_id,
            workspace_id=workspace_id,
            query=query,
            memory_types=memory_types,
            min_confidence=min_confidence,
            limit=limit,
        )

        self._audit(
            "memory_recall",
            owner_id,
            workspace_id,
            f"count={len(memories)}",
        )

        return memories

    def update_memory(
        self,
        memory_id: str,
        owner_id: str,
        workspace_id: str,
        content: str,
        confidence: float,
        sensitivity: MemorySensitivity,
        *,
        confirmed: bool = False,
    ) -> MemoryRecord:
        """Cập nhật một memory.
        
        Args:
            memory_id: ID của memory.
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
            content: Nội dung mới của memory.
            confidence: Độ tin cậy mới của memory.
            sensitivity: Độ nhạy cảm mới của memory.
        
        Returns:
            MemoryRecord: Bản ghi memory đã được cập nhật.
        
        Raises:
            ValueError: Nếu owner_id hoặc workspace_id không hợp lệ.
        """
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        if not owner_id or not workspace_id:
            raise ValueError(
                "owner_id và workspace_id không được để trống"
            )

        with self.repository.atomic() as repository:
            # Lấy memory hiện tại
            current_memory = repository.get_by_id(
                memory_id=memory_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
            )
            if not current_memory or current_memory.status != MemoryStatus.ACTIVE:
                raise ValueError("Memory is unavailable in this scope")

            # Đánh giá nội dung mới qua Admission Policy
            decision = self.admission_policy.evaluate(
                content=content,
                memory_type=current_memory.memory_type,
                confidence=confidence,
                sensitivity=sensitivity,
                source=current_memory.source,
                confirmed=confirmed,
            )

            # Nếu bị từ chối, ném exception
            if decision.status == AdmissionStatus.DENIED:
                self._audit(
                    "memory_update_denied",
                    owner_id,
                    workspace_id,
                    decision.reason,
                )
                raise ValueError(decision.reason)

            # Nếu yêu cầu xác nhận, ném exception
            if decision.status == AdmissionStatus.CONFIRMATION_REQUIRED:
                self._audit(
                    "memory_update_confirmation_required",
                    owner_id,
                    workspace_id,
                    decision.reason,
                )
                raise ValueError(
                    f"Yêu cầu xác nhận cập nhật memory: {decision.reason}"
                )

            conflicts = []
            subject = self._subject_key(decision.sanitized_content)
            for candidate in repository.list_active(owner_id, workspace_id, limit=None):
                if candidate.memory_id == memory_id:
                    continue
                if candidate.memory_type == current_memory.memory_type and self._normalize_content(candidate.content) == self._normalize_content(decision.sanitized_content):
                    raise ValueError("Updated content duplicates another active Memory")
                if subject and self._subject_key(candidate.content) == subject:
                    if confidence <= candidate.confidence:
                        raise ValueError("Update conflicts with a more reliable Memory")
                    conflicts.append(candidate)
            for candidate in conflicts:
                repository.supersede(candidate.memory_id, owner_id=owner_id, workspace_id=workspace_id)

            # Cập nhật memory
            updated_memory = current_memory.model_copy(
                update={
                    "content": decision.sanitized_content,
                    "confidence": confidence,
                    "sensitivity": sensitivity,
                    "updated_at": datetime.now(timezone.utc),
                    "version": current_memory.version + 1,
                }
            )

            # Lưu memory vào cơ sở dữ liệu
            updated_memory = repository.update(updated_memory)
        self._audit(
            "memory_updated",
            owner_id,
            workspace_id,
            f"memory_id={memory_id}",
        )

        return updated_memory

    def delete_memory(
        self,
        memory_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Optional[MemoryRecord]:
        """Xóa mềm một memory.
        
        Args:
            memory_id: ID của memory.
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
        
        Returns:
            Optional[MemoryRecord]: Bản ghi memory đã được xóa mềm nếu tồn tại, ngược lại None.
        
        Raises:
            ValueError: Nếu owner_id hoặc workspace_id không hợp lệ.
        """
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        if not owner_id or not workspace_id:
            raise ValueError(
                "owner_id và workspace_id không được để trống"
            )

        deleted_memory = self.repository.soft_delete(
            memory_id=memory_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
        )

        if deleted_memory:
            self._audit(
                "memory_deleted",
                owner_id,
                workspace_id,
                f"memory_id={memory_id}",
            )

        return deleted_memory

    def forget_workspace(
        self,
        owner_id: str,
        workspace_id: str,
    ) -> int:
        """Quên tất cả memory của một workspace.
        
        Args:
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
        
        Returns:
            int: Số lượng memory đã được xóa.
        
        Raises:
            ValueError: Nếu owner_id hoặc workspace_id không hợp lệ.
        """
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        if not owner_id or not workspace_id:
            raise ValueError(
                "owner_id và workspace_id không được để trống"
            )

        deleted_count = self.repository.forget(owner_id, workspace_id)

        self._audit(
            "workspace_forgot",
            owner_id,
            workspace_id,
            f"deleted_count={deleted_count}",
        )

        return deleted_count

    def forget_owner(
        self,
        owner_id: str,
    ) -> int:
        """Quên tất cả memory của một chủ sở hữu.
        
        Args:
            owner_id: ID của chủ sở hữu.
        
        Returns:
            int: Số lượng memory đã được xóa.
        
        Raises:
            ValueError: Nếu owner_id không hợp lệ.
        """
        if not owner_id:
            raise ValueError("owner_id không được để trống")

        deleted_count = self.repository.forget(owner_id)

        self._audit(
            "owner_forgot",
            owner_id,
            "",
            f"deleted_count={deleted_count}",
        )

        return deleted_count

    def export_memories(
        self,
        owner_id: str,
        workspace_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[MemoryRecord]:
        """Xuất tất cả memory của một workspace.
        
        Args:
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
        
        Returns:
            list[MemoryRecord]: Danh sách các memory của workspace.
        
        Raises:
            ValueError: Nếu owner_id hoặc workspace_id không hợp lệ.
        """
        validate_identifier(owner_id)
        validate_identifier(workspace_id)
        if not owner_id or not workspace_id:
            raise ValueError(
                "owner_id và workspace_id không được để trống"
            )

        memories = self.repository.export(
            owner_id=owner_id,
            workspace_id=workspace_id,
            limit=limit,
            offset=offset,
        )

        self._audit(
            "memories_exported",
            owner_id,
            workspace_id,
            f"count={len(memories)}",
        )

        return memories

    def view_memories(
        self, owner_id: str, workspace_id: str, *, limit: int = 20, offset: int = 0,
    ) -> list[MemoryRecord]:
        if type(limit) is not int or not 1 <= limit <= 101 or type(offset) is not int or offset < 0:
            raise ValueError("Invalid Memory pagination")
        records = self.repository.list_active(owner_id, workspace_id, limit=limit, offset=offset)
        self._audit("memories_viewed", owner_id, workspace_id, f"count={len(records)}")
        return records

    def set_auto_memory(self, owner_id: str, workspace_id: str, enabled: bool) -> None:
        self.repository.set_auto_memory(owner_id, workspace_id, enabled)
        self._audit("auto_memory_updated", owner_id, workspace_id, f"enabled={enabled}")

    def auto_memory_enabled(self, owner_id: str, workspace_id: str) -> bool:
        return self.repository.auto_memory_enabled(owner_id, workspace_id, default=self.auto_memory_default)

    def _audit(
        self,
        action: str,
        owner_id: str,
        workspace_id: str,
        details: str,
    ) -> None:
        """Ghi audit cho các thao tác memory.
        
        Args:
            action: Hành động đã thực hiện.
            owner_id: ID của chủ sở hữu.
            workspace_id: ID của workspace.
            details: Chi tiết hành động.
        """
        if self.audit_callback:
            try:
                self.audit_callback(
                    action,
                    owner_id,
                    workspace_id,
                    details,
                )
            except Exception:
                logging.error("Memory audit callback failed")

    _normalize_content = staticmethod(normalize_content)
    _subject_key = staticmethod(subject_key)

__all__ = [
    "MemoryService",
]
