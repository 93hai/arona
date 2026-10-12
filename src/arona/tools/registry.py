from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
import math
from typing import Any

from pydantic import BaseModel, JsonValue, TypeAdapter

from arona.domain.models import PermissionLevel

JSON_ARGUMENTS = TypeAdapter(dict[str, JsonValue])
JSON_RESULT = TypeAdapter(JsonValue)


@dataclass(frozen=True)
class ToolMetadata:
    name: str
    description: str
    action: str
    permission: PermissionLevel
    enabled: bool
    confirmation_required: bool
    allowed_scopes: frozenset[str]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    timeout_seconds: float
    audit_policy: str


@dataclass(frozen=True)
class ToolSpec:
    """Validated JSON contracts with a cooperative, in-process timeout.

    Dispatcher checks the deadline before/after execution. Production file tools
    also check it during I/O and before publication. Blocking OS calls cannot be
    forcibly interrupted; no background handler survives a returned timeout.
    """
    name: str
    description: str
    action: str
    permission: PermissionLevel
    handler: Callable[..., Any]
    enabled: bool = True
    confirmation_required: bool = False
    allowed_scopes: frozenset[str] = frozenset()
    validate_arguments: Callable[[dict[str, Any]], None] = lambda arguments: None
    input_schema: type[BaseModel] | None = None
    output_schema: type[BaseModel] | None = None
    timeout_seconds: float = 30.0
    audit_policy: str = "required"
    pass_context: bool = False
    resource_argument: str | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if type(spec) is not ToolSpec:
            raise ValueError("Explicit ToolSpec registration is required")
        if (
            not all(isinstance(value, str) and value.strip() for value in (spec.name, spec.description, spec.action))
            or not isinstance(spec.permission, PermissionLevel)
            or type(spec.enabled) is not bool or type(spec.confirmation_required) is not bool
            or not isinstance(spec.allowed_scopes, frozenset) or not spec.allowed_scopes
            or not all(isinstance(scope, str) and scope.strip() for scope in spec.allowed_scopes)
            or not callable(spec.handler) or not callable(spec.validate_arguments)
            or type(spec.pass_context) is not bool or spec.audit_policy != "required"
            or type(spec.timeout_seconds) not in (int, float)
            or not math.isfinite(spec.timeout_seconds) or not 0 < spec.timeout_seconds <= 120
            or any(schema is not None and (not isinstance(schema, type) or not issubclass(schema, BaseModel))
                   for schema in (spec.input_schema, spec.output_schema))
            or (spec.resource_argument is not None and
                (not isinstance(spec.resource_argument, str) or not spec.resource_argument.strip()))
        ):
            raise ValueError("Invalid tool metadata or handler")
        for schema in (spec.input_schema, spec.output_schema):
            if schema is not None:
                if schema.model_config.get("extra") != "forbid":
                    raise ValueError("Tool schemas must reject extra fields")
                schema.model_json_schema()
        if spec.resource_argument is not None and (
            spec.input_schema is None or spec.resource_argument not in spec.input_schema.model_fields
        ):
            raise ValueError("Resource binding must name an input schema field")
        if spec.name in self._tools:
            raise ValueError("Tool is already registered")
        # Keep registration independent of later mutation of the caller's dataclass.
        self._tools[spec.name] = replace(spec)

    def get(self, name: str) -> ToolMetadata | None:
        spec = self._tools.get(name)
        return ToolMetadata(
            spec.name, spec.description, spec.action, spec.permission, spec.enabled,
            spec.confirmation_required, spec.allowed_scopes,
            spec.input_schema.model_json_schema() if spec.input_schema else JSON_ARGUMENTS.json_schema(),
            spec.output_schema.model_json_schema() if spec.output_schema else JSON_RESULT.json_schema(),
            spec.timeout_seconds, spec.audit_policy,
        ) if spec else None

    def catalog(self) -> tuple[dict[str, Any], ...]:
        """Data-only contracts for prompts; no handlers or authorization receipts."""
        return tuple({
            "name": metadata.name, "description": metadata.description,
            "action": metadata.action, "scopes": sorted(metadata.allowed_scopes),
            "input": metadata.input_schema, "output": metadata.output_schema,
        } for name in self._tools if (metadata := self.get(name)) is not None and metadata.enabled
            and metadata.permission in (PermissionLevel.P0, PermissionLevel.P2)
            and not metadata.confirmation_required)

    def _registered(self, name: str) -> ToolSpec | None:
        # Trusted infrastructure only; public lookup never exposes a callable.
        return self._tools.get(name)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
