from __future__ import annotations

from copy import deepcopy
import json
from time import monotonic
from typing import Any

from arona.audit.sink import redact, require_audit, write_audit
from arona.policy.engine import PolicyEngine
from arona.policy.models import PolicyDecision, PolicyRequest, PolicyStatus
from arona.tools.models import ToolExecutionContext, ToolInvocation, TrustedContext
from arona.tools.registry import JSON_ARGUMENTS, JSON_RESULT, ToolRegistry


class ToolDispatcher:
    def __init__(self, registry: ToolRegistry, policy: PolicyEngine, *, audit) -> None:
        if type(registry) is not ToolRegistry or type(policy) is not PolicyEngine:
            raise TypeError("Trusted registry and policy instances are required")
        self._sink = require_audit(audit)
        if self._sink is not policy._sink:
            raise ValueError("Policy and Dispatcher must share the durable audit sink")
        policy.bind_registry(registry)
        self._registry = registry
        self._policy = policy

    @property
    def registry(self):
        return self._registry

    @property
    def policy(self):
        return self._policy

    def dispatch(self, invocation: ToolInvocation, context: TrustedContext,
                 decision: PolicyDecision | None) -> Any:
        metadata: dict[str, object] = {"status": "denied"}
        try:
            if type(invocation) is not ToolInvocation or type(context) is not TrustedContext:
                raise ValueError
            # Snapshot mutable arguments before authorization or user validators run.
            invocation = ToolInvocation.model_validate(deepcopy(invocation.model_dump()))
            metadata.update({
                "request_id": invocation.request_id, "tool": invocation.tool_name,
                "action": invocation.action, "owner_id": invocation.owner_id,
                "workspace_id": invocation.workspace_id, "scope": invocation.scope,
                "resource": invocation.resource,
            })
            self._assert_context(invocation, context)
            spec = self.registry._registered(invocation.tool_name)
            request = PolicyRequest(**invocation.model_dump())
            if (
                spec is None or not spec.enabled or decision is None
                or not self.policy.verify(decision, request)
                or decision.status != PolicyStatus.ALLOWED
                or decision.tool_name != spec.name or decision.action != spec.action
                or decision.permission_level != spec.permission
                or decision.requires_confirmation or spec.confirmation_required
                or invocation.scope not in spec.allowed_scopes
                or spec.audit_policy != "required"
                or (spec.resource_argument is not None and
                    invocation.resource != invocation.arguments.get(spec.resource_argument))
            ):
                raise PermissionError
            for key in ("request_id", "tool_name", "action", "owner_id", "workspace_id", "scope", "resource"):
                if key in invocation.arguments and invocation.arguments[key] != getattr(invocation, key):
                    raise PermissionError
        except (ValueError, TypeError, AttributeError, PermissionError):
            write_audit(self._sink, "tool_result", metadata)
            raise PermissionError("Invocation is not authorized") from None
        try:
            arguments = JSON_ARGUMENTS.validate_python(deepcopy(invocation.arguments), strict=True)
            json.dumps(arguments, allow_nan=False)
            if spec.input_schema is not None:
                arguments = spec.input_schema.model_validate(arguments, strict=True).model_dump()
            spec.validate_arguments(deepcopy(invocation.arguments))
        except Exception:
            write_audit(self._sink, "tool_result", {**metadata, "status": "invalid_arguments"})
            raise ValueError("Tool arguments are invalid") from None
        execution = ToolExecutionContext(
            TrustedContext(invocation.request_id, invocation.owner_id, invocation.workspace_id),
            monotonic() + spec.timeout_seconds,
        )
        if not self.policy.consume(decision, request):
            write_audit(self._sink, "tool_result", {**metadata, "status": "denied"})
            raise PermissionError("Policy decision is expired or already consumed")
        # This durable intent precedes every handler call. Failure executes nothing.
        write_audit(self._sink, "tool_request", {**metadata, "status": "started"})
        try:
            execution.check_deadline()
            result = spec.handler(arguments, execution) if spec.pass_context else spec.handler(arguments)
            execution.check_deadline()
            if spec.output_schema is not None:
                result = spec.output_schema.model_validate(result, strict=True).model_dump()
            result = JSON_RESULT.validate_python(result, strict=True)
            json.dumps(result, allow_nan=False)
            safe_result = redact(result, truncate=False)
            # Redaction must not silently break an output contract.
            if spec.output_schema is not None:
                spec.output_schema.model_validate(safe_result, strict=True)
            execution.check_deadline()
        except Exception:
            write_audit(self._sink, "tool_result", {**metadata, "status": "failed"})
            raise RuntimeError("Tool execution failed") from None
        write_audit(self._sink, "tool_result", {**metadata, "status": "succeeded"})
        return safe_result

    @staticmethod
    def _assert_context(invocation: ToolInvocation, context: TrustedContext) -> None:
        if (
            invocation.request_id != context.request_id
            or invocation.owner_id != context.owner_id
            or invocation.workspace_id != context.workspace_id
        ):
            raise PermissionError("Invocation identity does not match trusted context")
