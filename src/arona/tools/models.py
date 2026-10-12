from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from arona.domain.models import PermissionLevel


@dataclass(frozen=True)
class TrustedContext:
    request_id: str
    owner_id: str
    workspace_id: str


@dataclass(frozen=True)
class ToolExecutionContext:
    """Dispatcher-supplied identity and cooperative deadline; never model input."""

    identity: TrustedContext
    deadline: float

    def check_deadline(self) -> None:
        if monotonic() >= self.deadline:
            raise TimeoutError("Tool deadline exceeded")


class ToolInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    request_id: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    owner_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    scope: str = Field(min_length=1, max_length=500)
    resource: str | None = Field(default=None, max_length=1000)
    requested_permission: PermissionLevel
