"""Offline production-composition regressions for the integration gap closure."""
from __future__ import annotations

import ast
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from arona.app import _configured_providers, create_application
from arona.audit.sink import JsonlAuditSink
from arona.domain.models import MemoryRecord, MemorySensitivity, MemoryStatus, MemoryType
from arona.interaction import AuthenticatedIdentity, InteractionGateway, InteractionRequest
from arona.models.gateway import BudgetExceeded, ModelGateway, ModelRequest, ProviderError
from arona.prompts.contracts import ToolProposal
from tests.integration.test_final_spec_alignment import Provider, context


@pytest.fixture
def application(settings_factory):
    provider = Provider()
    app = create_application(settings_factory(), providers=(provider,))
    try:
        yield app, provider
    finally:
        app.shutdown()


def remember(app, owner="alice", workspace="work", content="My preferred editor is Vim"):
    record = app.core._memory.create_memory(owner, workspace, MemoryType.SEMANTIC,
        content, "user", 0.95, MemorySensitivity.PUBLIC)
    assert isinstance(record, MemoryRecord)
    return record


def test_code_route_selects_code_provider(settings_factory):
    general, code = Provider(), Provider()
    general.name, code.name = "general", "specialist"
    general.capability, code.capability = "chat", "code"
    app = create_application(settings_factory(), providers=(general, code))
    try:
        assert app.core.handle(context(current_user_text="write Python code")).text == "ok"
        assert len(code.requests) == 1
        assert not general.requests
        assert code.requests[0].provider_capability == "code"
    finally:
        app.shutdown()


def test_memory_events_use_shared_audit(application):
    app, _ = application
    remember(app)
    app.core._memory.recall("alice", "work", "editor")
    rows = [json.loads(line) for line in (app.settings.log_dir / "audit.jsonl").read_text().splitlines()]
    assert {"service_started", "memory_created", "memory_recall"} <= {row["event"] for row in rows}
    assert "My preferred editor" not in json.dumps(rows)


@pytest.mark.parametrize("failure", ["memory", "unavailable", "denied", "execution"])
def test_degraded_responses_are_in_history(application, monkeypatch, failure):
    app, provider = application
    if failure == "memory":
        def fail(*args, **kwargs):
            raise RuntimeError("raw private exception payload")
        monkeypatch.setattr(app.core._memory, "recall", fail)
    else:
        provider.proposal = ToolProposal(tool_name="candidate.read", action="read",
            scope="candidate", resource="absent.txt", arguments={"path": "absent.txt"})
        if failure == "unavailable":
            app.core._dispatcher = None
        elif failure == "denied":
            provider.proposal = provider.proposal.model_copy(update={"tool_name": "unregistered"})
    response = app.core.handle(context())
    assert response.degraded
    history = app.core._conversation.history("alice", "work", "chat")
    assert [(message.role, message.content) for message in history] == [
        ("user", "hello"), ("assistant", response.text)]
    assert "raw private" not in str(history)
    assert not app.core._conversation.history("bob", "work", "chat")


def test_candidate_large_list_has_bounded_pages(application):
    app, provider = application
    scope = hashlib.sha256(json.dumps(["alice", "work"], ensure_ascii=True).encode()).hexdigest()
    directory = app.settings.candidate_dir / "scopes" / scope
    directory.mkdir(parents=True)
    expected = [f"file_{index:04}.txt" for index in range(1001)]
    for name in expected:
        (directory / name).write_text("inert", encoding="utf-8")
    found = []
    for arguments in ({}, {"offset": 1000, "limit": 1000}):
        provider.proposal = ToolProposal(tool_name="candidate.list", action="list",
            scope="candidate", arguments=arguments)
        response = app.core.handle(context())
        assert response.tool_executed, response.text
        page = ast.literal_eval(response.text)["files"]
        assert len(page) <= 1000
        found.extend(page)
    assert found == expected


def test_memory_user_view_is_wired_without_provider(application):
    app, provider = application
    record = remember(app)
    response = app.core.handle(context(current_user_text="/memory view {}"))
    assert response.tool_executed
    assert record.memory_id in response.text
    assert not provider.requests
    assert app.core._dispatcher.registry.get("memory.view") is not None


def test_configured_codestral_has_code_capability(settings_factory):
    providers = _configured_providers(settings_factory(mistral_api_key="offline-placeholder",
        mistral_general_model="general", mistral_code_model="specialist",
        mistral_input_cost_per_million=0, mistral_output_cost_per_million=0,
        codestral_input_cost_per_million=0, codestral_output_cost_per_million=0))
    assert [(item.name, item.capability) for item in providers] == [("mistral", "chat"), ("codestral", "code")]


def test_code_fallback_preserves_health_and_attempt_charges():
    general, code = Provider(), Provider()
    general.name, code.name = "general", "specialist"
    code.capability = "code"
    attempts = []

    def fail(request):
        attempts.append(request)
        raise ProviderError("offline failure")

    code.generate = fail
    gateway = ModelGateway((general, code), failure_threshold=1)
    request = ModelRequest(request_id="route", messages=({"role": "user", "content": "code"},),
                           max_output_tokens=10, provider_capability="code")
    try:
        assert gateway.generate(request).text == "ok"
        assert len(attempts) == len(general.requests) == 1
        assert gateway.charged_usage.total_tokens == 13  # failed reservation 11 + actual fallback 2
        assert gateway.health()[1].available is False
        assert gateway.generate(request).text == "ok"
        assert len(attempts) == 1 and len(general.requests) == 2
        assert gateway.charged_usage.total_tokens == 15
    finally:
        gateway.close()


def test_code_routing_never_skips_budget_checks():
    general, code = Provider(), Provider()
    general.name, code.name, code.capability = "general", "specialist", "code"
    gateway = ModelGateway((general, code), daily_token_budget=10)
    try:
        with pytest.raises(BudgetExceeded):
            gateway.generate(ModelRequest(request_id="route", messages=({"role": "user", "content": "code"},),
                max_output_tokens=10, provider_capability="code"))
        assert not general.requests and not code.requests
    finally:
        gateway.close()


def test_code_provider_proposal_still_requires_policy(settings_factory):
    general, code = Provider(), Provider()
    general.name, code.name, code.capability = "general", "specialist", "code"
    code.proposal = ToolProposal(tool_name="candidate.write", action="write", scope="wrong",
        resource="draft.py", arguments={"path": "draft.py", "content": "inert"})
    app = create_application(settings_factory(), providers=(general, code))
    try:
        result = app.core.handle(context(current_user_text="write Python code"))
        assert result.degraded and not result.tool_executed
        assert len(code.requests) == 1 and not general.requests
        assert not list(app.settings.candidate_dir.rglob("draft.py"))
    finally:
        app.shutdown()


@pytest.mark.parametrize("result_kind", ["plain", "provider_error", "tool"])
def test_history_records_success_and_provider_failure_once(application, result_kind):
    app, provider = application
    if result_kind == "provider_error":
        def fail(request):
            raise ProviderError("raw private exception payload")
        provider.generate = fail
    elif result_kind == "tool":
        provider.proposal = ToolProposal(tool_name="candidate.list", action="list", scope="candidate")
    result = app.core.handle(context())
    history = app.core._conversation.history("alice", "work", "chat")
    assert [(item.role, item.content) for item in history] == [("user", "hello"), ("assistant", result.text)]
    assert "raw private" not in str(history)


def test_history_redacts_credentials(application):
    app, provider = application
    secret = "sk-" + "x" * 32
    original = provider.generate
    provider.generate = lambda request: original(request).model_copy(update={"text": secret})
    response = app.core.handle(context(current_user_text=secret))
    assert response.text == "[REDACTED]"
    assert secret not in str(app.core._conversation.history("alice", "work", "chat"))


@pytest.mark.parametrize("arguments", [{"offset": -1}, {"limit": 1001}, {"limit": True}, {"offset": "1"}])
def test_candidate_page_arguments_are_strict(application, arguments):
    app, provider = application
    provider.proposal = ToolProposal(tool_name="candidate.list", action="list", scope="candidate", arguments=arguments)
    assert app.core.handle(context()).degraded


def test_candidate_paging_still_checks_unsafe_entries_outside_page(application):
    app, provider = application
    target = app.settings.state_dir / "protected.txt"
    target.write_text("protected", encoding="utf-8")
    (app.settings.candidate_dir / "hidden.txt").symlink_to(target)
    provider.proposal = ToolProposal(tool_name="candidate.list", action="list", scope="candidate",
        arguments={"offset": 1000, "limit": 1})
    assert app.core.handle(context()).degraded
    assert target.read_text(encoding="utf-8") == "protected"


def control(app, action, arguments=None, **identity):
    gateway = InteractionGateway(app.core, allowed_user_ids={"alice", "bob"})
    owner, workspace = identity.get("owner", "alice"), identity.get("workspace", "work")
    return gateway.receive(InteractionRequest(user_id=owner, workspace_id=workspace, conversation_id="controls",
        text=f"/memory {action} " + json.dumps(arguments or {})),
        identity=AuthenticatedIdentity(owner, workspace))


def test_user_memory_controls_end_to_end_are_scoped_and_audited(application):
    app, provider = application
    record = remember(app)
    other_owner = remember(app, owner="bob")
    other_workspace = remember(app, workspace="other")
    updated_text = "My preferred editor is Emacs"
    assert control(app, "update", {"memory_id": record.memory_id, "content": updated_text,
        "confidence": 0.95, "sensitivity": "PUBLIC"}).tool_executed
    view = ast.literal_eval(control(app, "view").text)
    assert [item["content"] for item in view["memories"]] == [updated_text]
    assert control(app, "delete", {"memory_id": record.memory_id}).tool_executed
    assert ast.literal_eval(control(app, "view").text)["memories"] == []
    exported = ast.literal_eval(control(app, "export").text)["memories"]
    assert [item["memory_id"] for item in exported] == [record.memory_id]
    assert exported[0]["status"] == MemoryStatus.DELETED.value
    assert control(app, "forget").text == "{'deleted_count': 1}"
    assert ast.literal_eval(control(app, "export").text)["memories"] == []
    assert other_owner.memory_id in control(app, "view", owner="bob").text
    assert other_workspace.memory_id in control(app, "view", workspace="other").text
    app.core._memory.set_auto_memory("alice", "work", True)
    assert control(app, "disable_auto").text == "{'enabled': False}"
    assert not app.core._memory.auto_memory_enabled("alice", "work")
    assert not provider.requests
    rows = [json.loads(line) for line in (app.settings.log_dir / "audit.jsonl").read_text().splitlines()]
    for action in ("view", "update", "delete", "forget", "export", "disable_auto"):
        assert any(row["event"] == "tool_request" and row["metadata"]["tool"] == f"memory.{action}" for row in rows)
    assert updated_text not in json.dumps(rows)
    history = str(app.core._conversation.history("alice", "work", "controls"))
    assert updated_text not in history and record.memory_id not in history


@pytest.mark.parametrize("action", ["view", "export"])
def test_memory_control_pages_are_complete(application, action):
    app, _ = application
    expected = {remember(app, content=f"Useful stable item number {index}").memory_id for index in range(3)}
    seen = set()
    offset = 0
    while offset is not None:
        page = ast.literal_eval(control(app, action, {"offset": offset, "limit": 1}).text)
        assert len(page["memories"]) == 1
        assert page["memories"][0]["memory_id"] not in seen
        seen.add(page["memories"][0]["memory_id"])
        offset = page["next_offset"]
    assert seen == expected


def test_memory_view_filters_expired_and_superseded(application):
    app, _ = application
    old = remember(app)
    newer = app.core._memory.create_memory("alice", "work", MemoryType.SEMANTIC,
        "My preferred editor is Emacs", "user", 0.99, MemorySensitivity.PUBLIC)
    temporary = remember(app, content="A separate historical fact")
    app.core._memory.repository.update(temporary.model_copy(update={
        "expires_at": datetime.now(timezone.utc) - timedelta(days=1), "version": 2}))
    ids = {row["memory_id"] for row in ast.literal_eval(control(app, "view").text)["memories"]}
    assert ids == {newer.memory_id}
    exported = control(app, "export").text
    assert old.memory_id in exported and temporary.memory_id in exported


@pytest.mark.parametrize("action", ["view", "update", "delete", "forget", "export", "disable_auto"])
def test_model_cannot_issue_user_memory_controls(application, action):
    app, provider = application
    record = remember(app)
    provider.proposal = ToolProposal(tool_name=f"memory.{action}", action=action, scope="memory")
    result = app.core.handle(context())
    assert result.degraded and not result.tool_executed
    assert record.memory_id in control(app, "view").text
    system = provider.requests[0].messages[0]["content"]
    assert f'"name":"memory.{action}"' not in system.replace(" ", "")


@pytest.mark.parametrize("arguments", [{"owner_id": "bob"}, {"workspace_id": "other"},
    {"confirmed": True}, {"permission": "P5"}, {"unexpected": True}])
def test_memory_controls_reject_authority_and_extra_arguments(application, arguments):
    app, provider = application
    record = remember(app)
    result = control(app, "forget", arguments)
    assert result.degraded and not result.tool_executed
    assert record.memory_id in control(app, "view").text
    assert not provider.requests


@pytest.mark.parametrize("action", ["update", "delete"])
def test_memory_control_id_does_not_cross_scope(application, action):
    app, _ = application
    record = remember(app, owner="bob")
    arguments = {"memory_id": record.memory_id}
    if action == "update":
        arguments.update(content="An unauthorized change", confidence=0.95, sensitivity="PUBLIC")
    assert control(app, action, arguments).degraded
    assert record.content in control(app, "view", owner="bob").text


@pytest.mark.parametrize("sensitivity", ["SENSITIVE", "SECRET"])
def test_memory_controls_preserve_admission_gate(application, sensitivity):
    app, _ = application
    record = remember(app)
    assert control(app, "update", {"memory_id": record.memory_id, "content": "Private update",
        "confidence": 0.95, "sensitivity": sensitivity}).degraded
    assert record.content in control(app, "view").text


def test_memory_controls_require_durable_dispatch_intent(application, monkeypatch):
    app, _ = application
    record = remember(app)
    original = JsonlAuditSink.write

    def fail(sink, event, metadata):
        if event == "tool_request":
            raise OSError("private audit failure")
        return original(sink, event, metadata)

    with monkeypatch.context() as patch:
        patch.setattr(JsonlAuditSink, "write", fail)
        assert control(app, "forget").degraded
    assert record.memory_id in control(app, "view").text


def test_memory_callback_keeps_best_effort_contract(application, monkeypatch, caplog):
    app, _ = application
    original = JsonlAuditSink.write

    def fail(sink, event, metadata):
        if event == "memory_created":
            raise OSError("private audit failure")
        return original(sink, event, metadata)

    monkeypatch.setattr(JsonlAuditSink, "write", fail)
    record = remember(app)
    assert record.memory_id in control(app, "view").text
    assert "Memory audit callback failed" in caplog.text
    assert "private audit failure" not in caplog.text


def test_code_fallback_cannot_exceed_cumulative_request_budget():
    general, code = Provider(), Provider()
    general.name, code.name, code.capability = "general", "specialist", "code"
    attempts = []

    def fail(request):
        attempts.append(request)
        raise ProviderError("offline")

    code.generate = fail
    gateway = ModelGateway((general, code), request_token_budget=15)
    try:
        with pytest.raises(BudgetExceeded):
            gateway.generate(ModelRequest(request_id="route", messages=({"role": "user", "content": "code"},),
                max_output_tokens=10, provider_capability="code"))
        assert len(attempts) == 1 and not general.requests
        assert gateway.charged_usage.total_tokens == 11
    finally:
        gateway.close()


def test_chat_routing_prefers_general_and_falls_back_without_specialist():
    general, code = Provider(), Provider()
    general.name, code.name, code.capability = "general", "specialist", "code"
    gateway = ModelGateway((code, general))
    request = ModelRequest(request_id="route", messages=({"role": "user", "content": "hello"},), max_output_tokens=10)
    try:
        assert gateway.generate(request).text == "ok"
        assert len(general.requests) == 1 and not code.requests
    finally:
        gateway.close()
    gateway = ModelGateway((general,))
    try:
        assert gateway.generate(request.model_copy(update={"provider_capability": "code"})).text == "ok"
        assert len(general.requests) == 2
    finally:
        gateway.close()


def test_memory_commands_reject_transport_identity_mismatch(application):
    app, provider = application
    record = remember(app)
    gateway = InteractionGateway(app.core, allowed_user_ids={"alice", "bob"})
    request = InteractionRequest(user_id="alice", workspace_id="work", conversation_id="controls", text="/memory forget")
    with pytest.raises(PermissionError):
        gateway.receive(request, identity=AuthenticatedIdentity("bob", "work"))
    assert record.memory_id in control(app, "view").text
    assert not provider.requests


def test_memory_invalid_secret_input_never_enters_audit_or_history(application):
    app, provider = application
    record = remember(app)
    secret = "sk-" + "x" * 32
    result = control(app, "update", {"memory_id": record.memory_id, "content": secret,
        "confidence": 0.95, "sensitivity": "PUBLIC"})
    assert result.degraded
    malformed = app.core.handle(context(current_user_text='/memory forget {"secret": "' + secret))
    assert malformed.degraded
    assert record.content in control(app, "view").text
    for conversation in ("controls", "chat"):
        assert secret not in str(app.core._conversation.history("alice", "work", conversation))
    assert secret not in (app.settings.log_dir / "audit.jsonl").read_text()
    assert not provider.requests
