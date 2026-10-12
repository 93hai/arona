# Arona 3.0 Domain Models

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, auto
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PermissionLevel(Enum):
    """Mức độ quyền hạn của Policy Engine."""
    
    P0 = auto()  # Internal processing
    P1 = auto()  # Read external data
    P2 = auto()  # Modify Candidate
    P3 = auto()  # Execute Sandbox
    P4 = auto()  # External actions requiring confirmation
    P5 = auto()  # System-level privileges always requiring confirmation and whitelisting


class MemoryType(Enum):
    """Loại memory trong hệ thống."""
    
    SEMANTIC = auto()
    EPISODIC = auto()
    PROCEDURAL = auto()
    TEMPORARY = auto()


class MemorySensitivity(Enum):
    """Độ nhạy cảm của memory."""
    
    PUBLIC = auto()
    INTERNAL = auto()
    SENSITIVE = auto()
    SECRET = auto()


class MemoryStatus(Enum):
    """Trạng thái của memory."""
    
    ACTIVE = auto()
    SUPERSEDED = auto()
    DELETED = auto()


class ProviderStatus(Enum):
    """Trạng thái của provider."""
    
    HEALTHY = auto()
    DEGRADED = auto()
    UNAVAILABLE = auto()
    COOLING_DOWN = auto()


class RequestIntent(Enum):
    """Mục đích của request."""
    
    CHAT = auto()
    CODE_REQUEST = auto()
    BUILDER_REQUEST = auto()
    SYSTEM_COMMAND = auto()
    HELP = auto()
    UNKNOWN = auto()


class ToolDecisionStatus(Enum):
    """Trạng thái của quyết định tool."""
    
    ALLOWED = auto()
    DENIED = auto()
    CONFIRMATION_REQUIRED = auto()
    UNAVAILABLE = auto()


class RequestContext(BaseModel):
    """Context của một request."""
    
    model_config = ConfigDict(extra="forbid")
    
    request_id: str = Field(description="ID của request")
    owner_id: str = Field(description="ID của chủ sở hữu")
    workspace_id: str = Field(description="ID của workspace")
    conversation_id: str = Field(description="ID của cuộc trò chuyện")
    channel_id: Optional[str] = Field(
        default=None,
        description="ID của channel (tùy chọn)",
    )
    timestamp: datetime = Field(description="Thời điểm tạo request")
    current_user_text: str = Field(description="Nội dung hiện tại của người dùng")


class MemoryRecord(BaseModel):
    """Một bản ghi memory trong hệ thống."""
    
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, allow_inf_nan=False)
    
    memory_id: str = Field(description="ID của memory")
    owner_id: str = Field(description="ID của chủ sở hữu")
    workspace_id: str = Field(description="ID của workspace")
    memory_type: MemoryType = Field(description="Loại memory")
    content: str = Field(description="Nội dung của memory")
    source: str = Field(description="Nguồn của memory")
    created_at: datetime = Field(description="Thời điểm tạo memory")
    updated_at: datetime = Field(description="Thời điểm cập nhật cuối cùng")
    confidence: float = Field(
        description="Độ tin cậy của memory (từ 0 đến 1)",
        ge=0.0,
        le=1.0,
    )
    sensitivity: MemorySensitivity = Field(description="Độ nhạy cảm của memory")
    expires_at: Optional[datetime] = Field(
        default=None,
        description="Thời điểm hết hạn của memory (tùy chọn)",
    )
    status: MemoryStatus = Field(description="Trạng thái của memory")
    version: int = Field(ge=1, description="Phiên bản của memory")

    @field_validator("created_at", "updated_at", "expires_at")
    @classmethod
    def normalize_timestamp(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        # Legacy SQLite CURRENT_TIMESTAMP values are naive UTC.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class ToolRequest(BaseModel):
    """Yêu cầu sử dụng tool."""
    
    model_config = ConfigDict(extra="forbid")
    
    tool_name: str = Field(description="Tên của tool")
    arguments: dict[str, object] = Field(description="Đối số của tool")
    requested_permission: PermissionLevel = Field(description="Mức quyền yêu cầu")
    reason: str = Field(description="Lý do yêu cầu")
    request_id: str = Field(description="ID của request")


class PolicyDecision(BaseModel):
    """Quyết định của Policy Engine."""
    
    model_config = ConfigDict(extra="forbid")
    
    status: ToolDecisionStatus = Field(description="Trạng thái của quyết định")
    permission_level: PermissionLevel = Field(description="Mức quyền được cấp")
    reason: str = Field(description="Lý do quyết định")
    requires_confirmation: bool = Field(description="Có yêu cầu xác nhận không")
    audit_tags: dict[str, str] = Field(description="Tag audit")


class ProviderHealth(BaseModel):
    """Trạng thái sức khỏe của provider."""
    
    model_config = ConfigDict(extra="forbid")
    
    provider_name: str = Field(description="Tên của provider")
    status: ProviderStatus = Field(description="Trạng thái của provider")
    failure_count: int = Field(description="Số lần thất bại liên tiếp")
    last_failure_time: Optional[datetime] = Field(
        default=None,
        description="Thời điểm thất bại cuối cùng",
    )
    last_success_time: Optional[datetime] = Field(
        default=None,
        description="Thời điểm thành công cuối cùng",
    )
    message: Optional[str] = Field(
        default=None,
        description="Thông báo trạng thái",
    )


class UsageRecord(BaseModel):
    """Bản ghi sử dụng của provider."""
    
    model_config = ConfigDict(extra="forbid")
    
    provider: str = Field(description="Tên của provider")
    model: str = Field(description="Model được sử dụng")
    input_tokens: int = Field(description="Số token đầu vào")
    output_tokens: int = Field(description="Số token đầu ra")
    estimated_cost: float = Field(description="Chi phí ước tính")
    request_id: str = Field(description="ID của request")
    created_at: datetime = Field(description="Thời điểm tạo bản ghi")


__all__ = [
    "PermissionLevel",
    "MemoryType",
    "MemorySensitivity",
    "MemoryStatus",
    "ProviderStatus",
    "RequestIntent",
    "ToolDecisionStatus",
    "RequestContext",
    "MemoryRecord",
    "ToolRequest",
    "PolicyDecision",
    "ProviderHealth",
    "UsageRecord",
]
