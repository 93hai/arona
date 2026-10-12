"""arona.output.v1: model data, never authorization. Compatible with Arona 3.0."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


AUTHORITY_FIELDS = frozenset({
    "owner", "owner_id", "workspace", "workspace_id", "request_id", "identity", "trusted",
    "authorization", "allowed", "policy_decision", "receipt", "permission", "permission_level",
    "requested_permission",
})


class ToolProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True, revalidate_instances="always")

    tool_name: str = Field(min_length=1, max_length=100)
    action: str = Field(min_length=1, max_length=100)
    arguments: dict[str, object] = Field(default_factory=dict)
    scope: str = Field(min_length=1, max_length=500)
    resource: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def reject_authority(self):
        if AUTHORITY_FIELDS.intersection(self.arguments):
            raise ValueError("Tool arguments cannot supply authorization or identity")
        return self


class ModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    text: str = Field(default="", max_length=100_000)
    tool_proposal: ToolProposal | None = None

    @model_validator(mode="after")
    def require_content(self):
        if not self.text.strip() and self.tool_proposal is None:
            raise ValueError("Model output requires text or a proposal")
        return self
