"""Adversarial regressions for the four Phase 3 security boundaries only."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from inspect import getfile
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier
import traceback
from types import SimpleNamespace
from uuid import uuid4

import pytest

from arona.audit.sink import JsonlAuditSink
from arona.domain.models import PermissionLevel
from arona.policy.engine import PolicyEngine
from arona.policy.models import PolicyDecision, PolicyRequest, PolicyStatus
from arona.tools.dispatcher import ToolDispatcher
from arona.tools.models import ToolInvocation, TrustedContext
from arona.tools.registry import ToolRegistry, ToolSpec
from arona.workspace._filesystem import Directory
from arona.workspace.manager import CandidateManager


@pytest.fixture
def gate(tmp_path):
    calls = []
    path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(path)
    registry = ToolRegistry()
    spec = ToolSpec(
        name="echo", description="Test handler", action="run", permission=PermissionLevel.P0,
        allowed_scopes=frozenset({"local"}), handler=lambda args: calls.append(args) or args,
    )
    registry.register(spec)
    policy = PolicyEngine(audit=sink.write, registry=registry)
    dispatcher = ToolDispatcher(registry, policy, audit=sink.write)
    request = PolicyRequest(
        request_id="req", tool_name="echo", action="run", owner_id="alice",
        workspace_id="workspace", scope="local", resource="notes.txt",
        requested_permission=PermissionLevel.P0, arguments={"text": "hello"},
    )
    invocation = ToolInvocation(**request.model_dump(exclude={"confirmed"}))
    context = TrustedContext("req", "alice", "workspace")
    return SimpleNamespace(**locals())


def events(gate):
    return [json.loads(line) for line in gate.path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("attack", ["missing", "instantiate", "copy", "new_id", "allowed_boolean", "denied"])
def test_forged_decision_never_executes(gate, attack):
    decision = gate.policy.decide(gate.request)
    if attack == "missing":
        decision = None
    elif attack == "instantiate":
        decision = PolicyDecision.model_validate(decision.model_dump())
    elif attack == "copy":
        decision = decision.model_copy()
    elif attack == "new_id":
        decision = decision.model_copy(update={"decision_id": uuid4()})
    elif attack == "allowed_boolean":
        decision = {**decision.model_dump(), "allowed": True}
    else:
        decision = gate.policy.decide(gate.request.model_copy(update={"tool_name": "missing"}))
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
    assert gate.calls == []
    assert events(gate)[-1]["metadata"]["status"] == "denied"


@pytest.mark.parametrize("field,value", [
    ("status", PolicyStatus.DENIED), ("permission_level", PermissionLevel.P5),
    ("owner_id", "bob"), ("workspace_id", "other"), ("tool_name", "other"),
    ("action", "delete"), ("scope", "global"), ("resource", "runtime.py"),
    ("request_fingerprint", "0" * 64), ("requires_confirmation", True),
])
def test_original_receipt_tampering_never_executes(gate, field, value):
    decision = gate.policy.decide(gate.request)
    object.__setattr__(decision, field, value)
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
    assert gate.calls == []


@pytest.mark.parametrize("field,value", [
    ("request_id", "other"), ("owner_id", "bob"), ("workspace_id", "other"),
    ("tool_name", "other"), ("action", "delete"), ("scope", "global"),
    ("resource", "runtime.py"), ("requested_permission", PermissionLevel.P2),
    ("arguments", {"text": "different"}),
])
def test_receipt_binds_the_entire_request_even_with_matching_context(gate, field, value):
    decision = gate.policy.decide(gate.request)
    invocation = gate.invocation.model_copy(update={field: value})
    context = TrustedContext(invocation.request_id, invocation.owner_id, invocation.workspace_id)
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch(invocation, context, decision)
    assert gate.calls == []


def test_nested_metadata_and_arguments_are_bound(gate):
    decision = gate.policy.decide(gate.request)
    decision.audit_metadata["request_id"] = "changed"
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
    decision = gate.policy.decide(gate.request)
    gate.invocation.arguments["text"] = "changed"
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
    assert gate.calls == []


def test_receipt_expires(gate, monkeypatch):
    monkeypatch.setattr("arona.policy.engine.time.monotonic", lambda: 100)
    decision = gate.policy.decide(gate.request)
    monkeypatch.setattr("arona.policy.engine.time.monotonic", lambda: 161)
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
    assert gate.calls == []


def test_valid_decision_runs_once_and_audits_all_stages(gate):
    decision = gate.policy.decide(gate.request)
    assert gate.dispatcher.dispatch(gate.invocation, gate.context, decision) == {"text": "hello"}
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
    assert gate.calls == [{"text": "hello"}]
    records = events(gate)
    assert [record["event"] for record in records] == ["policy_decision", "tool_request", "tool_result", "tool_result"]
    assert records[0]["metadata"]["decision"]["status"] == "allowed"
    for record in records[1:]:
        assert record["metadata"]["owner_id"] == "alice"
        assert record["metadata"]["workspace_id"] == "workspace"
        assert record["metadata"]["scope"] == "local"
        assert record["metadata"]["resource"] == "notes.txt"
    assert records[2]["metadata"]["status"] == "succeeded"


def test_concurrent_reuse_executes_exactly_once(gate):
    decision = gate.policy.decide(gate.request)
    barrier = Barrier(2)

    def dispatch():
        barrier.wait(timeout=5)
        try:
            gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
            return "executed"
        except PermissionError:
            return "denied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: dispatch(), range(2)))
    assert sorted(results) == ["denied", "executed"]
    assert len(gate.calls) == 1


@pytest.mark.parametrize("metadata", [
    {"enabled": False}, {"permission": PermissionLevel.P2}, {"confirmation_required": True},
])
def test_caller_flags_cannot_override_registered_metadata(gate, metadata):
    gate.registry.register(replace(gate.spec, name="restricted", **metadata))
    request = gate.request.model_copy(update={"tool_name": "restricted", "confirmed": True})
    decision = gate.policy.decide(
        request, registered=True, enabled=True,
        required_permission=PermissionLevel.P0, confirmation_required=False,
    )
    assert decision.status != PolicyStatus.ALLOWED
    invocation = gate.invocation.model_copy(update={"tool_name": "restricted"})
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch(invocation, gate.context, decision)
    assert gate.calls == []


def test_registry_metadata_lookup_cannot_expose_or_replace_handler(gate):
    assert not hasattr(gate.registry.get("echo"), "handler")
    with pytest.raises(ValueError):
        gate.registry.register(gate.spec)
    object.__setattr__(gate.spec, "handler", lambda _: pytest.fail("hijacked"))
    decision = gate.policy.decide(gate.request)
    gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
    assert len(gate.calls) == 1
    with pytest.raises(AttributeError):
        gate.dispatcher.registry = ToolRegistry()
    with pytest.raises(AttributeError):
        gate.dispatcher.policy = None


@pytest.mark.parametrize("change", [
    {"allowed_scopes": frozenset()}, {"enabled": "yes"}, {"permission": "P0"},
    {"handler": None}, {"validate_arguments": None}, {"name": " "},
])
def test_registry_rejects_invalid_metadata(gate, change):
    with pytest.raises(ValueError):
        gate.registry.register(replace(gate.spec, **change))


def test_validator_cannot_change_authorized_arguments(gate):
    gate.registry.register(replace(
        gate.spec, name="validated", validate_arguments=lambda args: args.update(text="changed"),
    ))
    request = gate.request.model_copy(update={"tool_name": "validated"})
    invocation = gate.invocation.model_copy(update={"tool_name": "validated"})
    gate.dispatcher.dispatch(invocation, gate.context, gate.policy.decide(request))
    assert gate.calls == [{"text": "hello"}]


def test_argument_identity_override_and_malformed_input_execute_nothing(gate):
    request = gate.request.model_copy(update={"arguments": {"owner_id": "bob"}})
    invocation = gate.invocation.model_copy(update={"arguments": {"owner_id": "bob"}})
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch(invocation, gate.context, gate.policy.decide(request))
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch({"allowed": True}, gate.context, None)
    with pytest.raises(PermissionError):
        gate.policy.decide({"allowed": True})
    assert gate.calls == []


class FalseyAudit:
    def __bool__(self):
        return False

    def __call__(self, *args):
        pass


@pytest.mark.parametrize("audit", [None, lambda *args: None, FalseyAudit()])
def test_null_or_custom_audit_is_not_an_authority(gate, audit):
    with pytest.raises(TypeError):
        PolicyEngine(audit=audit, registry=gate.registry)
    with pytest.raises(TypeError):
        ToolDispatcher(gate.registry, gate.policy, audit=audit)
    assert gate.calls == []


def test_audit_failure_before_handler_is_fail_closed_and_secret_safe(gate, monkeypatch):
    decision = gate.policy.decide(gate.request)

    def fail(*args):
        raise OSError("password: synthetic-only")

    monkeypatch.setattr("arona.audit.sink.os.fsync", fail)
    with pytest.raises(OSError) as error:
        gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
    assert "synthetic-only" not in "".join(traceback.format_exception(error.value))
    assert gate.calls == []
    assert not gate.policy.verify(decision, gate.request)


@pytest.mark.parametrize("stage", ["handler", "validate_arguments"])
def test_tool_exceptions_do_not_expose_secret_in_traceback(gate, stage):
    def fail(*args):
        raise ValueError("password: synthetic-only")

    gate.registry.register(replace(gate.spec, name="failing", **{stage: fail}))
    request = gate.request.model_copy(update={"tool_name": "failing"})
    invocation = gate.invocation.model_copy(update={"tool_name": "failing"})
    with pytest.raises((ValueError, RuntimeError)) as error:
        gate.dispatcher.dispatch(invocation, gate.context, gate.policy.decide(request))
    assert "synthetic-only" not in "".join(traceback.format_exception(error.value))
    assert "synthetic-only" not in gate.path.read_text(encoding="utf-8")
    assert events(gate)[-1]["metadata"]["status"] == ("failed" if stage == "handler" else "invalid_arguments")


@pytest.mark.parametrize("label", ["api_key", "token", "password", "mật khẩu", "private_key"])
def test_structured_secrets_are_removed_from_audit_and_result(gate, label):
    secret = "synthetic-private-value"
    result = {label: secret, "message": "ordinary response"}
    gate.sink.write("test_event", {label: secret})
    gate.registry.register(replace(gate.spec, name="result", handler=lambda _: result))
    request = gate.request.model_copy(update={"tool_name": "result"})
    invocation = gate.invocation.model_copy(update={"tool_name": "result"})
    returned = gate.dispatcher.dispatch(invocation, gate.context, gate.policy.decide(request))
    assert secret not in json.dumps(returned)
    assert returned["message"] == "ordinary response"
    assert secret not in gate.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("secret", [
    "sk-" + "a" * 40, "password: synthetic-only", "mật khẩu: synthetic-only",
    "-----BEGIN PRIVATE KEY-----\nSYNTHETIC\n-----END PRIVATE KEY-----",
])
def test_plaintext_secrets_are_redacted(gate, secret):
    gate.sink.write("test_event", {"message": secret})
    assert events(gate)[-1]["metadata"]["message"] == "[REDACTED]"


@pytest.mark.parametrize("event,metadata", [
    ("password", {}), ("invalid\nevent", {}), ("test_event", []),
    ("test_event", {1: "value"}), ("test_event", {"value": object()}),
])
def test_malformed_audit_is_rejected_without_output(gate, event, metadata):
    with pytest.raises(ValueError):
        gate.sink.write(event, metadata)
    assert not gate.path.exists()


def test_audit_rejects_hardlink_without_changing_target(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("unchanged", encoding="utf-8")
    link = tmp_path / "audit.jsonl"
    os.link(target, link)
    with pytest.raises(ValueError):
        JsonlAuditSink(link).write("test_event", {})
    assert target.read_text(encoding="utf-8") == "unchanged"


def test_post_execution_audit_failure_does_not_allow_replay(gate, monkeypatch):
    decision = gate.policy.decide(gate.request)
    original = JsonlAuditSink.write

    def fail_result(self, event, metadata):
        if event == "tool_result" and metadata["status"] == "succeeded":
            raise OSError("unavailable")
        return original(self, event, metadata)

    monkeypatch.setattr(JsonlAuditSink, "write", fail_result)
    with pytest.raises(OSError):
        gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
    with pytest.raises(PermissionError):
        gate.dispatcher.dispatch(gate.invocation, gate.context, decision)
    assert len(gate.calls) == 1
    assert any(item["event"] == "tool_request" for item in events(gate))


@pytest.fixture
def candidate(tmp_path):
    protected = tuple(tmp_path / name for name in ("runtime", "database", "backups"))
    for path in protected:
        path.mkdir()
        (path / "keep.txt").write_text("unchanged", encoding="utf-8")
    manager = CandidateManager(tmp_path / "candidate", forbidden_roots=protected)
    return manager, protected


@pytest.mark.parametrize("name", [
    "../escape.txt", "..\\escape.txt", "nested/../../escape.txt", "nested\\..\\..\\escape.txt",
    "/tmp/escape.txt", "C:\\escape.txt", "C:escape.txt", "\\\\server\\share\\escape.txt",
    "nested/..\\escape.txt", "nested/./escape.txt", "nested//escape.txt", "escape.txt:stream",
    "nested. /escape.txt", "NUL.txt",
])
def test_candidate_path_escape_rejected(candidate, name):
    manager, protected = candidate
    with pytest.raises(ValueError):
        manager.write(name, "safe")
    assert manager.list_files() == []
    assert all((path / "keep.txt").read_text(encoding="utf-8") == "unchanged" for path in protected)


@pytest.mark.parametrize("change_cwd", [False, True])
def test_candidate_requires_protected_roots_and_rejects_overlap(candidate, monkeypatch, tmp_path, change_cwd):
    if change_cwd:
        monkeypatch.chdir(tmp_path)
    manager, protected = candidate
    with pytest.raises(ValueError):
        CandidateManager(manager.root)
    for path in protected:
        for root in (path, path / "nested", path.parent):
            with pytest.raises(ValueError):
                CandidateManager(root, forbidden_roots=protected)
    source_target = Path(getfile(CandidateManager)).resolve().parent / "_never_created"
    with pytest.raises(ValueError):
        CandidateManager(source_target, forbidden_roots=protected)
    assert not source_target.exists()


def test_candidate_cannot_be_added_to_import_paths(candidate, monkeypatch):
    manager, _ = candidate
    monkeypatch.setattr(sys, "path", [*sys.path, str(manager.root)])
    with pytest.raises(ValueError):
        manager.write("module.py", "raise RuntimeError('must not execute')")
    assert not (manager.root / "module.py").exists()


def make_directory_link(link, target, kind):
    if kind == "junction":
        if os.name != "nt":
            pytest.skip("Windows junction test")
        result = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, check=False,
        )
        assert result.returncode == 0, "Could not create test junction"
    else:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("Host does not permit test symlinks")


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_candidate_directory_links_cannot_escape(candidate, kind):
    manager, protected = candidate
    link = manager.root / "linked"
    make_directory_link(link, protected[0], kind)
    try:
        for operation in (
            manager.list_files, lambda: manager.read("linked/keep.txt"),
            lambda: manager.write("linked/keep.txt", "changed"), lambda: manager.delete("linked/keep.txt"),
        ):
            with pytest.raises(ValueError):
                operation()
        assert (protected[0] / "keep.txt").read_text(encoding="utf-8") == "unchanged"
    finally:
        if kind == "junction":
            os.rmdir(link)
        else:
            link.unlink()


def test_candidate_pins_parent_against_swap_before_write(candidate, monkeypatch):
    manager, protected = candidate
    manager.write("sub/keep.txt", "candidate")
    sub = manager.root / "sub"
    moved = manager.root / "moved"
    original = Directory.create_file
    attempts = []

    def attack(directory, name):
        attempts.append(True)
        try:
            sub.rename(moved)
        except PermissionError:
            # Windows handle deliberately excludes FILE_SHARE_DELETE.
            assert os.name == "nt"
        else:
            sub.symlink_to(protected[0], target_is_directory=True)
        return original(directory, name)

    monkeypatch.setattr(Directory, "create_file", attack)
    try:
        manager.write("sub/keep.txt", "new candidate")
        assert attempts == [True]
        assert (protected[0] / "keep.txt").read_text(encoding="utf-8") == "unchanged"
        assert sorted(path.name for path in protected[0].iterdir()) == ["keep.txt"]
        target = moved if moved.exists() else sub
        assert (target / "keep.txt").read_text(encoding="utf-8") == "new candidate"
    finally:
        if sub.is_symlink():
            sub.unlink()


def test_candidate_root_replacement_is_rejected(candidate):
    manager, protected = candidate
    root = manager.root
    root.rename(root.with_name("previous"))
    root.mkdir()
    with pytest.raises(ValueError):
        manager.write("escape.txt", "safe")
    assert not (root / "escape.txt").exists()
    assert (protected[0] / "keep.txt").read_text(encoding="utf-8") == "unchanged"


def test_candidate_read_rejects_externally_introduced_credentials(candidate):
    manager, _ = candidate
    (manager.root / "notes.txt").write_text("password: synthetic-only", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        manager.read("notes.txt")
    assert "synthetic-only" not in str(error.value)


def test_candidate_source_is_only_data_and_failed_replace_is_atomic(candidate, monkeypatch):
    manager, _ = candidate
    before = list(sys.path)
    source = "raise RuntimeError('Candidate was executed')\n"
    manager.write("module.py", source)
    assert manager.read("module.py") == source
    assert sys.path == before

    def fail(*args):
        raise OSError("replace failed")

    monkeypatch.setattr(Directory, "replace", fail)
    with pytest.raises(ValueError):
        manager.write("module.py", "changed")
    assert manager.read("module.py") == source
    assert manager.list_files() == ["module.py"]
