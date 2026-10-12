"""Inert Candidate file tools. All filesystem access goes through CandidateManager."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from arona.domain.models import PermissionLevel
from arona.tools.models import ToolExecutionContext
from arona.tools.registry import ToolRegistry, ToolSpec
from arona.workspace.manager import CandidateManager


class CandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)


class FileInput(CandidateInput):
    path: str = Field(min_length=1, max_length=500)


class ListInput(CandidateInput):
    offset: int = Field(default=0, ge=0, le=2_147_483_647)
    limit: int = Field(default=1000, ge=1, le=1000)


class WriteInput(FileInput):
    content: str = Field(max_length=20_000)


class ListOutput(CandidateInput):
    files: list[str] = Field(max_length=1000)


class ReadOutput(FileInput):
    content: str = Field(max_length=20_000)


class WriteOutput(FileInput):
    status: Literal["written"]


def _prefix(context: ToolExecutionContext) -> str:
    # A fixed-size, case-stable component derived only from trusted identity.
    # JSON tuple encoding separates pairs such as ('ab','c') and ('a','bc').
    identity = context.identity
    encoded = json.dumps([identity.owner_id, identity.workspace_id], ensure_ascii=True).encode()
    return "scopes/" + hashlib.sha256(encoded).hexdigest() + "/"


def register_candidate_tools(registry: ToolRegistry, manager: CandidateManager) -> None:
    if type(manager) is not CandidateManager:
        raise TypeError("A trusted CandidateManager is required")

    def list_files(arguments, context):
        prefix = _prefix(context)
        names = manager.list_files(check_deadline=context.check_deadline)
        # Validate the complete tree before paging; a hidden unsafe entry must
        # still fail closed. Pagination applies only to this trusted scope.
        scoped = [name[len(prefix):] for name in names if name.startswith(prefix)]
        offset = arguments["offset"]
        return {"files": scoped[offset:offset + arguments["limit"]]}

    def read_file(arguments, context):
        content = manager.read(_prefix(context) + arguments["path"], check_deadline=context.check_deadline)
        return {"path": arguments["path"], "content": content}

    def write_file(arguments, context):
        manager.write(_prefix(context) + arguments["path"], arguments["content"],
                      check_deadline=context.check_deadline)
        return {"path": arguments["path"], "status": "written"}

    for name, action, permission, handler, input_schema, output_schema in (
        ("candidate.list", "list", PermissionLevel.P0, list_files, ListInput, ListOutput),
        ("candidate.read", "read", PermissionLevel.P0, read_file, FileInput, ReadOutput),
        ("candidate.write", "write", PermissionLevel.P2, write_file, WriteInput, WriteOutput),
    ):
        registry.register(ToolSpec(
            name=name, description=f"{action.capitalize()} inert files in the authenticated Candidate workspace",
            action=action, permission=permission, handler=handler,
            allowed_scopes=frozenset({"candidate"}), input_schema=input_schema, output_schema=output_schema,
            timeout_seconds=30.0, audit_policy="required", pass_context=True,
            resource_argument="path" if action != "list" else None,
        ))
    # Delete intentionally has no registration until a trusted confirmation flow exists.
