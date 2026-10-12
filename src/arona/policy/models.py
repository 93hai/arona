from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from arona.domain.models import PermissionLevel


class PolicyStatus(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"
    CONFIRMATION_REQUIRED = "confirmation_required"


class PolicyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    request_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=100)
    owner_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=500)
    resource: str | None = Field(default=None, max_length=1000)
    requested_permission: PermissionLevel
    arguments: dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False


class PolicyDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    decision_id: UUID
    request_fingerprint: str = Field(min_length=64, max_length=64)
    status: PolicyStatus
    permission_level: PermissionLevel
    reason: str = Field(min_length=1, max_length=500)
    owner_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=100)
    scope: str = Field(min_length=1, max_length=500)
    resource: str | None = Field(default=None, max_length=1000)
    requires_confirmation: bool
    rule_id: str = Field(min_length=1, max_length=100)
    issued_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    audit_metadata: dict[str, str] = Field(default_factory=dict)

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("mode", "json")
        return super().model_dump(*args, **kwargs)
