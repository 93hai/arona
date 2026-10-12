# Arona 3.0 Memory Admission Policy

from __future__ import annotations

import re
import math
import unicodedata
from enum import Enum, auto
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from arona.domain.models import MemorySensitivity, MemoryType
from arona.memory.redaction import contains_secret

# =============================================================================
# HẰNG SỐ CỐ ĐỊNH
# =============================================================================

MAX_MEMORY_CONTENT_LENGTH: Final[int] = 10_000

# =============================================================================
# KIỂU DỮ LIỆU VÀ SCHEMA PYDANTIC
# =============================================================================

class AdmissionStatus(Enum):
    """Trạng thái của quyết định ghi nhớ."""
    
    ALLOWED = auto()
    DENIED = auto()
    CONFIRMATION_REQUIRED = auto()


class MemoryAdmissionDecision(BaseModel):
    """Quyết định của Memory Admission Policy."""
    
    model_config = ConfigDict(extra="forbid")
    
    status: AdmissionStatus = Field(description="Trạng thái của quyết định")
    reason: str = Field(description="Lý do quyết định")
    sanitized_content: str = Field(description="Nội dung đã được làm sạch")


# =============================================================================
# CLASS ADMISSION POLICY
# =============================================================================

class MemoryAdmissionPolicy:
    """Chính sách ghi nhớ cho Arona 3.0.
    
    Class này chịu trách nhiệm đánh giá và quyết định xem một memory có được ghi nhớ
    hay không. Nó không chứa logic AI, Discord hoặc Policy Engine.
    """
    
    def evaluate(
        self,
        content: str,
        memory_type: MemoryType,
        confidence: float,
        sensitivity: MemorySensitivity,
        source: str = "",
        *,
        confirmed: bool = False,
        long_term_value: bool | None = None,
        automatic: bool = False,
    ) -> MemoryAdmissionDecision:
        """Đánh giá và quyết định xem một memory có được ghi nhớ hay không.
        
        Args:
            content: Nội dung của memory.
            memory_type: Loại memory.
            confidence: Độ tin cậy của memory.
            sensitivity: Độ nhạy cảm của memory.
        
        Returns:
            MemoryAdmissionDecision: Quyết định của Memory Admission Policy.
        """
        
        if (
            not isinstance(content, str) or not isinstance(source, str)
            or not isinstance(memory_type, MemoryType)
            or not isinstance(sensitivity, MemorySensitivity)
            or isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0 <= confidence <= 1
            or type(confirmed) is not bool or type(automatic) is not bool
            or (long_term_value is not None and type(long_term_value) is not bool)
        ):
            return MemoryAdmissionDecision(status=AdmissionStatus.DENIED, reason="Invalid Memory metadata", sanitized_content="")
        # Làm sạch nội dung
        sanitized_content = content.strip()
        
        # Từ chối nội dung rỗng
        if not sanitized_content:
            return MemoryAdmissionDecision(
                status=AdmissionStatus.DENIED,
                reason="Nội dung rỗng",
                sanitized_content="",
            )
        if not source.strip():
            return MemoryAdmissionDecision(status=AdmissionStatus.DENIED, reason="Memory source is required", sanitized_content="")
        
        # Reject oversized input instead of silently changing user data.
        if len(sanitized_content) > MAX_MEMORY_CONTENT_LENGTH or len(source) > 1000:
            return MemoryAdmissionDecision(
                status=AdmissionStatus.DENIED,
                reason="Nội dung vượt quá giới hạn cho phép",
                sanitized_content="",
            )
        
        # Từ chối memory có sensitivity=secret
        if sensitivity == MemorySensitivity.SECRET:
            return MemoryAdmissionDecision(
                status=AdmissionStatus.DENIED,
                reason="Memory có độ nhạy cảm là SECRET",
                sanitized_content="",
            )
        
        # Inspect every field that can carry user-provided memory data.
        if any(contains_secret(value) for value in (sanitized_content, source)):
            return MemoryAdmissionDecision(status=AdmissionStatus.DENIED, reason="Nội dung chứa secret", sanitized_content="")
        text = unicodedata.normalize("NFC", sanitized_content).casefold()
        speculative = re.search(r"\b(?:maybe|perhaps|probably|i guess|i suspect|có lẽ|có thể|chắc là|tôi đoán|hình như)\b", text)
        emotion = re.search(r"\b(?:i(?:'m| am) (?:sad|angry|upset|happy)|tôi (?:đang )?(?:buồn|giận|tức|vui)|hôm nay.*(?:buồn|giận|tức))\b", text)
        if speculative or emotion or long_term_value is False:
            return MemoryAdmissionDecision(status=AdmissionStatus.DENIED, reason="Thông tin suy đoán, cảm xúc nhất thời hoặc không có giá trị lâu dài", sanitized_content="")
        
        # Đánh giá độ tin cậy và độ nhạy cảm
        if not confirmed and (
            confidence < 0.7 or sensitivity == MemorySensitivity.SENSITIVE
            or (automatic and long_term_value is not True)
        ):
            return MemoryAdmissionDecision(
                status=AdmissionStatus.CONFIRMATION_REQUIRED,
                reason="Độ tin cậy thấp hoặc nội dung nhạy cảm",
                sanitized_content=sanitized_content,
            )
        
        # Nếu không có vấn đề gì, chấp nhận ghi nhớ
        return MemoryAdmissionDecision(
            status=AdmissionStatus.ALLOWED,
            reason="Nội dung hợp lệ",
            sanitized_content=sanitized_content,
        )

__all__ = [
    "AdmissionStatus",
    "MemoryAdmissionDecision",
    "MemoryAdmissionPolicy",
]
