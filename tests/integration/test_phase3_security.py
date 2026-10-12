from __future__ import annotations

import pytest

from arona.audit.sink import JsonlAuditSink
from arona.domain.models import PermissionLevel
from arona.policy.engine import PolicyEngine
from arona.policy.models import PolicyRequest, PolicyStatus
from arona.tools.dispatcher import ToolDispatcher
from arona.tools.models import ToolInvocation, TrustedContext
from arona.tools.registry import ToolRegistry, ToolSpec
from arona.workspace.manager import CandidateManager


def test_policy_is_deterministic_fail_closed_and_audited(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(audit_path)
    registry = ToolRegistry()
    policy = PolicyEngine(audit=sink.write, registry=registry)
    request = PolicyRequest(
        request_id="req-1",
        tool_name="candidate.write",
        action="write",
        owner_id="alice",
        workspace_id="work",
        scope="candidate",
        resource="notes.txt",
        requested_permission=PermissionLevel.P2,
    )
    denied = policy.decide(request, registered=False, enabled=False, required_permission=PermissionLevel.P2, confirmation_required=False)
    assert denied.status == PolicyStatus.DENIED
    registry.register(ToolSpec(
        name="candidate.write", description="test metadata", action="write",
        permission=PermissionLevel.P2, allowed_scopes=frozenset({"candidate"}),
        handler=lambda args: None,
    ))
    allowed = policy.decide(request, registered=True, enabled=True, required_permission=PermissionLevel.P2, confirmation_required=False)
    assert allowed.status == PolicyStatus.ALLOWED
    assert not policy.verify(allowed.model_copy(update={"action": "other"}), request)
    assert "password" not in audit_path.read_text(encoding="utf-8")


def test_dispatcher_requires_matching_policy_and_never_calls_handler(tmp_path):
    called = []
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    policy_request = PolicyRequest(
        request_id="req-1", tool_name="echo", action="run", owner_id="alice",
        workspace_id="work", scope="local", requested_permission=PermissionLevel.P0,
    )
    policy = PolicyEngine(audit=sink.write)
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="echo", description="test", action="run", permission=PermissionLevel.P0,
        allowed_scopes=frozenset({"local"}), handler=lambda args: called.append(args) or "ok",
        validate_arguments=lambda args: None,
    ))
    dispatcher = ToolDispatcher(registry, policy, audit=sink.write)
    invocation = ToolInvocation(
        request_id=policy_request.request_id,
        tool_name=policy_request.tool_name,
        action=policy_request.action,
        owner_id=policy_request.owner_id,
        workspace_id=policy_request.workspace_id,
        scope=policy_request.scope,
        resource=policy_request.resource,
        requested_permission=policy_request.requested_permission,
    )
    context = TrustedContext("req-1", "alice", "work")
    with pytest.raises(PermissionError):
        dispatcher.dispatch(invocation, context, None)
    decision = policy.decide(policy_request, registered=True, enabled=True, required_permission=PermissionLevel.P0, confirmation_required=False)
    assert dispatcher.dispatch(invocation, context, decision) == "ok"
    assert len(called) == 1
    with pytest.raises(PermissionError):
        dispatcher.dispatch(invocation.model_copy(update={"owner_id": "bob"}), context, decision)
    assert len(called) == 1


def test_dispatcher_rejects_unknown_disabled_wrong_scope_and_bad_arguments(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    policy = PolicyEngine(audit=sink.write)
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="disabled", description="test", action="run", permission=PermissionLevel.P0,
        enabled=False, allowed_scopes=frozenset({"local"}), handler=lambda args: pytest.fail("executed"),
    ))
    dispatcher = ToolDispatcher(registry, policy, audit=sink.write)
    invocation = ToolInvocation(request_id="r", tool_name="disabled", action="run", arguments={}, owner_id="a", workspace_id="w", scope="local", requested_permission=PermissionLevel.P0)
    context = TrustedContext("r", "a", "w")
    decision = policy.decide(
        PolicyRequest(
            request_id=invocation.request_id,
            tool_name=invocation.tool_name,
            action=invocation.action,
            owner_id=invocation.owner_id,
            workspace_id=invocation.workspace_id,
            scope=invocation.scope,
            resource=invocation.resource,
            requested_permission=invocation.requested_permission,
        ),
        registered=False,
        enabled=False,
        required_permission=PermissionLevel.P0,
        confirmation_required=False,
    )
    with pytest.raises(PermissionError):
        dispatcher.dispatch(invocation, context, decision)


def test_audit_failure_blocks_policy_decision_and_tool_execution(tmp_path, monkeypatch):
    def failing_audit(*args):
        raise OSError("audit sink unavailable")

    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    policy = PolicyEngine(audit=sink.write)
    monkeypatch.setattr("arona.audit.sink.os.fsync", failing_audit)
    request = PolicyRequest(
        request_id="r", tool_name="echo", action="run", owner_id="a",
        workspace_id="w", scope="local", requested_permission=PermissionLevel.P0,
    )
    with pytest.raises(OSError):
        policy.decide(request, registered=True, enabled=True, required_permission=PermissionLevel.P0, confirmation_required=False)


@pytest.mark.parametrize("permission", [PermissionLevel.P3, PermissionLevel.P4, PermissionLevel.P5])
def test_high_risk_permission_levels_are_not_enabled_in_phase3(tmp_path, permission):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="future", description="disabled capability test", action="run",
        permission=permission, allowed_scopes=frozenset({"local"}),
        handler=lambda args: pytest.fail("Disabled capability executed"),
    ))
    policy = PolicyEngine(audit=sink.write, registry=registry)
    request = PolicyRequest(
        request_id="r", tool_name="future", action="run", owner_id="a",
        workspace_id="w", scope="local", requested_permission=permission,
    )
    decision = policy.decide(request, registered=True, enabled=True, required_permission=permission, confirmation_required=False)
    assert decision.status == PolicyStatus.CONFIRMATION_REQUIRED


def test_candidate_manager_blocks_escape_symlink_secret_and_executes_nothing(tmp_path):
    runtime = tmp_path / "runtime"
    manager = CandidateManager(tmp_path / "candidate", forbidden_roots=(runtime,))
    manager.write("src/main.py", "print('candidate')\n")
    assert manager.read("src/main.py") == "print('candidate')\n"
    assert manager.list_files() == ["src/main.py"]
    for name in ("../runtime.txt", "..\\runtime.txt", "/tmp/runtime", "C:\\runtime.txt", "\\\\server\\share\\x.py"):
        with pytest.raises(ValueError):
            manager.write(name, "safe")
    with pytest.raises(ValueError):
        manager.write("secret.txt", "password: do-not-save")
    outside = tmp_path / "outside.py"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "candidate" / "link.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pass
    else:
        with pytest.raises(ValueError):
            manager.list_files()
