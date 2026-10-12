"""Explicit user controls for internal Memory; never model-authored actions."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from arona.domain.models import MemorySensitivity, PermissionLevel
from arona.memory.service import MemoryService
from arona.prompts.contracts import ToolProposal
from arona.tools.registry import ToolRegistry, ToolSpec


MEMORY_ACTIONS = frozenset({"view", "update", "delete", "forget", "export", "disable_auto"})
MEMORY_TOOL_NAMES = frozenset(f"memory.{action}" for action in MEMORY_ACTIONS)


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True,
                              allow_inf_nan=False, hide_input_in_errors=True)


class PageInput(MemoryInput):
    offset: int = Field(default=0, ge=0, le=2_147_483_647)
    limit: int = Field(default=20, ge=1, le=100)


class RecordInput(MemoryInput):
    memory_id: str = Field(min_length=1, max_length=200)


class UpdateInput(RecordInput):
    content: str = Field(min_length=1, max_length=20_000)
    confidence: float = Field(ge=0, le=1)
    sensitivity: Literal["PUBLIC", "INTERNAL", "SENSITIVE", "SECRET"]


class PageOutput(MemoryInput):
    memories: list[dict[str, JsonValue]] = Field(max_length=100)
    next_offset: int | None = Field(ge=0)


class RecordOutput(RecordInput):
    status: Literal["updated", "deleted"]


class ForgetOutput(MemoryInput):
    deleted_count: int = Field(ge=0)


class AutoOutput(MemoryInput):
    enabled: Literal[False]


def is_memory_command(text: str) -> bool:
    return text.split(maxsplit=1)[0] == "/memory"


def parse_memory_command(text: str) -> ToolProposal:
    # Only called with current user text from a validated RequestContext. History,
    # recalled data and model output never enter this parser.
    parts = text.split(maxsplit=2)
    if len(parts) < 2 or parts[0] != "/memory" or parts[1] not in MEMORY_ACTIONS:
        raise ValueError("Invalid Memory command")
    arguments = json.loads(parts[2]) if len(parts) == 3 else {}
    return ToolProposal(tool_name=f"memory.{parts[1]}", action=parts[1],
                        scope="memory", arguments=arguments)


def register_memory_tools(registry: ToolRegistry, service: MemoryService) -> None:
    if type(service) is not MemoryService:
        raise TypeError("A trusted MemoryService is required")

    def page(arguments, context, *, export=False):
        identity = context.identity
        method = service.export_memories if export else service.view_memories
        records = method(identity.owner_id, identity.workspace_id,
                         limit=arguments["limit"] + 1, offset=arguments["offset"])
        limit = arguments["limit"]
        return {"memories": [record.model_dump(mode="json") for record in records[:limit]],
                "next_offset": arguments["offset"] + limit if len(records) > limit else None}

    def export(arguments, context):
        return page(arguments, context, export=True)

    def update(arguments, context):
        identity = context.identity
        record = service.update_memory(arguments["memory_id"], identity.owner_id, identity.workspace_id,
            arguments["content"], arguments["confidence"], MemorySensitivity[arguments["sensitivity"]])
        # Admission still rejects secrets / requires confirmation for sensitive
        # data. The command does not manufacture an admission confirmation.
        return {"memory_id": record.memory_id, "status": "updated"}

    def delete(arguments, context):
        identity = context.identity
        record = service.delete_memory(arguments["memory_id"], identity.owner_id, identity.workspace_id)
        if record is None:
            raise ValueError("Memory is unavailable in this scope")
        return {"memory_id": record.memory_id, "status": "deleted"}

    def forget(arguments, context):
        identity = context.identity
        return {"deleted_count": service.forget_workspace(identity.owner_id, identity.workspace_id)}

    def disable_auto(arguments, context):
        identity = context.identity
        service.set_auto_memory(identity.owner_id, identity.workspace_id, False)
        return {"enabled": False}

    for action, handler, input_schema, output_schema in (
        ("view", page, PageInput, PageOutput),
        ("export", export, PageInput, PageOutput),
        ("update", update, UpdateInput, RecordOutput),
        ("delete", delete, RecordInput, RecordOutput),
        ("forget", forget, MemoryInput, ForgetOutput),
        ("disable_auto", disable_auto, MemoryInput, AutoOutput),
    ):
        registry.register(ToolSpec(
            name=f"memory.{action}", description=f"User-directed {action} in authenticated Memory scope",
            action=action, permission=PermissionLevel.P0, handler=handler,
            allowed_scopes=frozenset({"memory"}), input_schema=input_schema,
            output_schema=output_schema, pass_context=True,
        ))
