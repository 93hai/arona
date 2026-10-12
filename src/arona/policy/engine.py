from __future__ import annotations

import hashlib
import json
import time
from threading import Lock
from uuid import UUID, uuid4

from arona.audit.sink import require_audit, write_audit
from arona.domain.models import PermissionLevel
from arona.policy.models import PolicyDecision, PolicyRequest, PolicyStatus


class PolicyEngine:
    """Registry-backed authorization; issued decisions are single-use process capabilities."""

    def __init__(self, *, audit, registry=None) -> None:
        self._sink = require_audit(audit)
        self._registry = None
        if registry is not None:
            self.bind_registry(registry)
        self._issued: dict[UUID, tuple] = {}
        self._lock = Lock()

    def bind_registry(self, registry) -> None:
        from arona.tools.registry import ToolRegistry
        if type(registry) is not ToolRegistry:
            raise TypeError("A trusted ToolRegistry is required")
        if self._registry is not None and self._registry is not registry:
            raise ValueError("Policy is already bound to another registry")
        self._registry = registry

    @staticmethod
    def fingerprint(request: PolicyRequest) -> str:
        payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def decide(self, request: PolicyRequest, *, registered=None, enabled=None,
               required_permission=None, confirmation_required=None) -> PolicyDecision:
        # Legacy caller metadata is never an authority; the bound registry is.
        try:
            if type(request) is not PolicyRequest:
                raise ValueError
            request = PolicyRequest.model_validate(request.model_dump())
            fingerprint = self.fingerprint(request)
        except (ValueError, TypeError):
            write_audit(self._sink, "policy_decision", {"status": "denied", "reason": "malformed_request"})
            raise PermissionError("Malformed policy request") from None
        spec = self._registry._registered(request.tool_name) if self._registry is not None else None
        permission = spec.permission if spec else request.requested_permission
        status = PolicyStatus.DENIED
        rule_id = "deny.invalid-or-unregistered"
        reason = "Tool metadata or request is not authorized"
        if (
            spec is not None and spec.enabled and request.action == spec.action
            and request.scope in spec.allowed_scopes and request.requested_permission == spec.permission
        ):
            if permission in (PermissionLevel.P1, PermissionLevel.P3, PermissionLevel.P4, PermissionLevel.P5):
                status = PolicyStatus.CONFIRMATION_REQUIRED
                rule_id = "deny.capability-disabled"
                reason = "This capability is not enabled in Arona 3.0"
            elif spec.confirmation_required:
                # A model/caller boolean is not evidence of human confirmation.
                status = PolicyStatus.CONFIRMATION_REQUIRED
                rule_id = "confirm.trusted-flow-required"
                reason = "A trusted confirmation flow is required"
            else:
                status = PolicyStatus.ALLOWED
                rule_id = "allow.registered-tool"
                reason = "Registered tool and request scope are authorized"
        decision = PolicyDecision(
            decision_id=uuid4(), request_fingerprint=fingerprint, status=status,
            permission_level=permission, reason=reason, owner_id=request.owner_id,
            workspace_id=request.workspace_id, tool_name=request.tool_name, action=request.action,
            scope=request.scope, resource=request.resource,
            requires_confirmation=status == PolicyStatus.CONFIRMATION_REQUIRED,
            rule_id=rule_id, audit_metadata={"request_id": request.request_id},
        )
        write_audit(self._sink, "policy_decision", {"decision": decision.model_dump()})
        if status == PolicyStatus.ALLOWED:
            with self._lock:
                now = time.monotonic()
                self._issued = {key: value for key, value in self._issued.items() if value[4] > now}
                if len(self._issued) >= 4096:
                    raise PermissionError("Policy receipt capacity reached")
                self._issued[decision.decision_id] = (
                    decision, decision.model_dump_json(), fingerprint, spec, now + 60,
                )
        return decision

    def _valid(self, decision: PolicyDecision, request: PolicyRequest) -> bool:
        if type(decision) is not PolicyDecision or type(request) is not PolicyRequest:
            return False
        try:
            issued = self._issued.get(decision.decision_id)
            return bool(
                issued and issued[0] is decision and issued[1] == decision.model_dump_json()
                and issued[2] == self.fingerprint(request) and issued[4] > time.monotonic()
                and self._registry is not None
                and self._registry._registered(request.tool_name) is issued[3]
            )
        except (ValueError, TypeError):
            return False

    def verify(self, decision: PolicyDecision, request: PolicyRequest) -> bool:
        with self._lock:
            return self._valid(decision, request)

    def consume(self, decision: PolicyDecision, request: PolicyRequest) -> bool:
        with self._lock:
            if not self._valid(decision, request):
                return False
            del self._issued[decision.decision_id]
            return True
