from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from threading import Event
import traceback
import warnings

import pytest

from arona.app import StartupError, create_application
from arona.core.lifecycle import Lifecycle, Readiness
from arona.domain.models import RequestContext
from arona.models.gateway import BudgetExceeded, GatewayResponse, ModelGateway, ModelRequest, ProviderError, ToolProposal, Usage
from arona.models.http import GeminiProvider, MistralProvider, ProviderConfig
from arona.interaction import AuthenticatedIdentity, InteractionGateway, InteractionRequest
from arona.audit.sink import JsonlAuditSink
from arona.domain.models import PermissionLevel
from arona.tools.registry import ToolSpec


class MeteredProvider:
    name = "test"
    model = "offline"

    def __init__(self, *, estimate=None, result=None):
        self.estimate = estimate
        self.result = result or GatewayResponse(text="ok", usage=Usage(input_tokens=2, output_tokens=3, estimated_cost=0.1))
        self.calls = 0
        self.closed = 0

    def estimate_usage(self, request):
        return self.estimate or Usage(input_tokens=2, output_tokens=request.max_output_tokens, estimated_cost=0.1)

    def generate(self, request):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def close(self):
        self.closed += 1


def request():
    return ModelRequest(request_id="r", messages=({"role": "user", "content": "hi"},), max_output_tokens=3)


def context():
    return RequestContext(request_id="r", owner_id="alice", workspace_id="w", conversation_id="c",
                          timestamp=datetime.now(timezone.utc), current_user_text="hi")


def test_cost_is_reserved_before_provider_call():
    provider = MeteredProvider()
    gateway = ModelGateway((provider,), daily_cost_budget=0.05)
    with pytest.raises(BudgetExceeded):
        gateway.generate(request())
    assert provider.calls == 0


def test_concurrent_requests_cannot_spend_the_same_remaining_budget():
    started, release = Event(), Event()
    provider = MeteredProvider()
    original = provider.generate

    def blocking(req):
        started.set()
        assert release.wait(5)
        return original(req)

    provider.generate = blocking
    gateway = ModelGateway((provider,), daily_token_budget=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(gateway.generate, request())
        assert started.wait(5)
        second = pool.submit(gateway.generate, request())
        release.set()
        first.result(timeout=5)
        with pytest.raises(BudgetExceeded):
            second.result(timeout=5)
    assert provider.calls == 1


def test_failed_attempt_is_not_free_for_fallback():
    primary = MeteredProvider(result=ProviderError("unknown billing after timeout"))
    fallback = MeteredProvider()
    fallback.name = "fallback"
    gateway = ModelGateway((primary, fallback), daily_cost_budget=0.15)
    with pytest.raises(BudgetExceeded):
        gateway.generate(request())
    assert primary.calls == 1
    assert fallback.calls == 0


def test_over_budget_response_is_still_accounted():
    provider = MeteredProvider(result=GatewayResponse(text="ok", usage=Usage(input_tokens=2, output_tokens=100, estimated_cost=2)))
    gateway = ModelGateway((provider,), request_token_budget=10)
    with pytest.raises(BudgetExceeded):
        gateway.generate(request())
    assert gateway.usage.total_tokens == 102


def test_forged_usage_model_cannot_poison_accounting():
    bad = Usage.model_construct(input_tokens=-1000, output_tokens=0, estimated_cost=-10)
    provider = MeteredProvider(result=GatewayResponse.model_construct(text="ok", usage=bad, tool_proposal=None))
    gateway = ModelGateway((provider,))
    with pytest.raises(ProviderError):
        gateway.generate(request())
    assert gateway.usage.total_tokens >= 0


def test_gemini_enforces_requested_output_and_keeps_key_out_of_url():
    provider = GeminiProvider(ProviderConfig("gemini", "offline", "synthetic-key", input_cost_per_million=1, output_cost_per_million=2))
    wire = provider._request(request())
    assert "synthetic-key" not in wire.full_url
    assert json.loads(wire.data)["generationConfig"]["maxOutputTokens"] == 3


@pytest.mark.parametrize("usage", [None, {}, {"prompt_tokens": -1, "completion_tokens": 2}, {"prompt_tokens": True, "completion_tokens": 2}])
def test_missing_or_invalid_usage_fails_safely(usage):
    payload = {"choices": [{"message": {"content": "ok"}}], "usage": usage}
    provider = MistralProvider(ProviderConfig("mistral", "offline", "synthetic-key", input_cost_per_million=1, output_cost_per_million=2), endpoint="https://example.invalid",
                              transport=lambda *_: (200, json.dumps(payload).encode()))
    with pytest.raises(ProviderError):
        provider.generate(request())


def test_audit_failure_prevents_readiness(settings_factory, monkeypatch):
    def fail(*args):
        raise OSError("synthetic audit failure")

    monkeypatch.setattr(JsonlAuditSink, "write", fail)
    provider = MeteredProvider()
    with pytest.raises(StartupError):
        create_application(settings_factory(), providers=(provider,))
    assert provider.closed == 1


def test_shutdown_closes_providers_and_rejects_new_requests(settings_factory):
    provider = MeteredProvider()
    app = create_application(settings_factory(), providers=(provider,))
    app.shutdown()
    app.shutdown()
    assert provider.closed == 1
    assert app.readiness is Readiness.STOPPED
    with pytest.raises(RuntimeError):
        app.core.handle(context())
    assert provider.calls == 0


def test_partial_start_failure_runs_cleanup():
    cleaned = []

    def fail():
        raise RuntimeError("startup failed")

    lifecycle = Lifecycle(fail, lambda: cleaned.append(True))
    with pytest.raises(RuntimeError):
        lifecycle.start()
    assert cleaned == [True]
    assert lifecycle.readiness is Readiness.FAILED


def test_shutdown_during_start_never_returns_to_ready():
    entered, release = Event(), Event()

    def start():
        entered.set()
        assert release.wait(5)

    lifecycle = Lifecycle(start, lambda: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        starting = pool.submit(lifecycle.start)
        assert entered.wait(5)
        stopping = pool.submit(lifecycle.shutdown)
        release.set()
        starting.result(timeout=5)
        stopping.result(timeout=5)
    assert lifecycle.readiness is Readiness.STOPPED


def test_interaction_payload_alone_cannot_authenticate_as_an_allowed_user():
    class SpyCore:
        calls = []

        def handle(self, context):
            self.calls.append(context)

    core = SpyCore()
    gateway = InteractionGateway(core, allowed_user_ids={"alice"})
    forged = InteractionRequest(user_id="alice", workspace_id="victim", conversation_id="c", text="hi")
    with pytest.raises(PermissionError):
        gateway.receive(forged)
    assert core.calls == []


def test_restart_does_not_reset_daily_cost_budget(settings_factory):
    settings = settings_factory(daily_cost_budget=0.15)
    first = create_application(settings, providers=(MeteredProvider(),))
    assert first.core.handle(context()).text == "ok"
    first.shutdown()
    provider = MeteredProvider()
    second = create_application(settings, providers=(provider,))
    try:
        assert second.core.handle(context()).degraded
        assert provider.calls == 0
    finally:
        second.shutdown()


@pytest.mark.parametrize("field,value", [
    ("owner_id", "bob"), ("workspace_id", "victim"), ("authorization", True),
    ("policy_decision", {"allowed": True}), ("receipt", "forged"), ("requested_permission", 1),
])
def test_model_authority_fields_never_reach_tool_handler(settings_factory, field, value):
    proposal = ToolProposal(tool_name="echo", action="read", scope="internal")
    proposal = proposal.model_copy(update={field: value})
    provider = MeteredProvider(result=GatewayResponse.model_construct(
        text="", tool_proposal=proposal, usage=Usage(input_tokens=2, output_tokens=3, estimated_cost=0.1)))
    app = create_application(settings_factory(), providers=(provider,))
    calls = []
    app.core._dispatcher.registry.register(ToolSpec(
        name="echo", description="test", action="read", permission=PermissionLevel.P0,
        allowed_scopes=frozenset({"internal"}), handler=lambda args: calls.append(args),
    ))
    try:
        result = app.core.handle(context())
        assert result.degraded
        assert not result.tool_executed
        assert calls == []
    finally:
        app.shutdown()


def test_context_mutation_during_provider_call_cannot_change_tool_identity(settings_factory):
    trusted = context()
    proposal = ToolProposal(tool_name="echo", action="read", scope="internal", arguments={"text": "safe"})
    provider = MeteredProvider(result=GatewayResponse(
        tool_proposal=proposal, usage=Usage(input_tokens=2, output_tokens=3, estimated_cost=0.1)))
    original = provider.generate

    def change_context(req):
        trusted.owner_id, trusted.workspace_id = "bob", "other"
        return original(req)

    provider.generate = change_context
    app = create_application(settings_factory(), providers=(provider,))
    calls = []
    app.core._dispatcher.registry.register(ToolSpec(
        name="echo", description="test", action="read", permission=PermissionLevel.P0,
        allowed_scopes=frozenset({"internal"}), handler=lambda args: calls.append(args) or "done",
    ))
    try:
        assert app.core.handle(trusted).tool_executed
        records = [json.loads(line) for line in app.audit.path.read_text().splitlines()]
        intent = next(item["metadata"] for item in records if item["event"] == "tool_request")
        assert (intent["owner_id"], intent["workspace_id"]) == ("alice", "w")
        assert calls == [{"text": "safe"}]
    finally:
        app.shutdown()


@pytest.mark.parametrize("field,value", [("user_id", "bob"), ("workspace_id", "other"), ("channel_id", "other")])
def test_transport_identity_binds_user_workspace_and_channel(field, value):
    class SpyCore:
        def handle(self, _):
            pytest.fail("Untrusted identity reached Core")

    gateway = InteractionGateway(SpyCore(), allowed_user_ids={"alice", "bob"})
    req = InteractionRequest(user_id="alice", workspace_id="w", channel_id="channel", conversation_id="c", text="hi")
    with pytest.raises(PermissionError):
        gateway.receive(req.model_copy(update={field: value}), identity=AuthenticatedIdentity("alice", "w", "channel"))


def test_budget_state_failure_executes_zero_and_state_is_exclusively_owned(settings_factory, monkeypatch):
    settings = settings_factory()
    provider = MeteredProvider()
    app = create_application(settings, providers=(provider,))
    try:
        other = MeteredProvider()
        with pytest.raises(StartupError):
            create_application(settings, providers=(other,))
        assert other.closed == 1

        def fail(_):
            raise OSError("synthetic-only")

        monkeypatch.setattr(app.gateway._budget_store, "save", fail)
        assert app.core.handle(context()).degraded
        assert provider.calls == 0
    finally:
        app.shutdown()


def test_unknown_billing_survives_restart(settings_factory):
    settings = settings_factory(daily_cost_budget=0.15)
    first = create_application(settings, providers=(MeteredProvider(result=ProviderError("timeout")),))
    assert first.core.handle(context()).degraded
    assert first.gateway.charged_usage.estimated_cost == 0.1
    first.shutdown()
    provider = MeteredProvider()
    second = create_application(settings, providers=(provider,))
    try:
        assert second.core.handle(context()).degraded
        assert provider.calls == 0
    finally:
        second.shutdown()


def test_cumulative_request_budget_blocks_fallback():
    primary = MeteredProvider(result=ProviderError("timeout"))
    fallback = MeteredProvider()
    fallback.name = "fallback"
    gateway = ModelGateway((primary, fallback), request_token_budget=9)
    with pytest.raises(BudgetExceeded):
        gateway.generate(request())
    assert primary.calls == 1 and fallback.calls == 0
    assert gateway.charged_usage.total_tokens == 5


def test_day_rollover_keeps_inflight_reservation():
    now = [datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)]
    entered, release = Event(), Event()
    provider = MeteredProvider()
    original = provider.generate

    def block(req):
        entered.set()
        assert release.wait(5)
        return original(req)

    provider.generate = block
    gateway = ModelGateway((provider,), clock=lambda: now[0], daily_token_budget=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(gateway.generate, request())
        assert entered.wait(5)
        now[0] = datetime(2026, 1, 2, tzinfo=timezone.utc)
        try:
            with pytest.raises(BudgetExceeded):
                gateway.generate(request())
        finally:
            release.set()
        running.result(timeout=5)
    assert provider.calls == 1
    assert gateway.generate(request()).text == "ok"
    assert provider.calls == 2


def test_shutdown_drains_request_before_closing_provider(settings_factory, monkeypatch):
    entered, release, stopping = Event(), Event(), Event()
    provider = MeteredProvider()
    original = provider.generate

    def block(req):
        entered.set()
        assert release.wait(5)
        return original(req)

    provider.generate = block
    app = create_application(settings_factory(), providers=(provider,))
    original_stop = app.core.shutdown

    def drain():
        # Mark the same guarded state before signalling the test thread.
        with app.core._idle:
            app.core._stopping = True
        stopping.set()
        original_stop()

    monkeypatch.setattr(app.core, "shutdown", drain)
    with ThreadPoolExecutor(max_workers=3) as pool:
        running = pool.submit(app.core.handle, context())
        assert entered.wait(5)
        stopped = pool.submit(app.shutdown)
        assert stopping.wait(5)
        try:
            assert provider.closed == 0
            assert not stopped.done()
            with pytest.raises(RuntimeError):
                app.core.handle(context())
        finally:
            release.set()
        assert running.result(timeout=5).text == "ok"
        stopped.result(timeout=5)
    assert provider.closed == 1
    assert app.readiness == Readiness.STOPPED


@pytest.mark.parametrize("stage", ["migration", "memory"])
def test_partial_composition_failure_cleans_up_without_secret_output(settings_factory, monkeypatch, stage):
    def fail(*args, **kwargs):
        raise RuntimeError("password: synthetic-only")

    target = "arona.app.DatabaseMigrations.run_migrations" if stage == "migration" else "arona.app.MemoryService"
    monkeypatch.setattr(target, fail)
    provider = MeteredProvider()
    with pytest.raises(StartupError) as error:
        create_application(settings_factory(), providers=(provider,))
    assert provider.closed == 1
    assert "synthetic-only" not in "".join(traceback.format_exception(error.value))


def test_memory_recall_uses_settings_and_never_auto_writes(settings_factory):
    provider = MeteredProvider()
    app = create_application(settings_factory(memory_recall_limit=2, memory_min_confidence=0.9, auto_memory_enabled=True), providers=(provider,))
    calls = []

    class MemorySpy:
        def recall(self, owner, workspace, query, **kwargs):
            calls.append((owner, workspace, kwargs))
            return []

        def create_memory(self, *args, **kwargs):
            pytest.fail("Conversation was automatically written to long-term memory")

    app.core._memory = MemorySpy()
    try:
        assert app.core.handle(context()).text == "ok"
        assert calls == [("alice", "w", {"limit": 2, "min_confidence": 0.9})]
    finally:
        app.shutdown()


def test_missing_prices_fail_startup_and_price_mapping_is_explicit(settings_factory):
    from arona.app import _configured_providers

    with pytest.raises(StartupError):
        create_application(settings_factory(gemini_api_key="synthetic-key", gemini_model="offline"))
    providers = _configured_providers(settings_factory(
        gemini_api_key="synthetic-key", gemini_model="offline", gemini_timeout_seconds=7,
        gemini_input_cost_per_million=3, gemini_output_cost_per_million=9,
    ))
    assert providers[0]._timeout == 7
    assert providers[0]._cost(1_000_000, 1_000_000) == 12


@pytest.mark.parametrize("bad_usage", [
    {"input_tokens": True, "output_tokens": 1, "estimated_cost": 0.0},
    {"input_tokens": 1, "output_tokens": "1", "estimated_cost": 0.0},
    {"input_tokens": 1, "output_tokens": 1, "estimated_cost": float("nan")},
    {"input_tokens": 1, "output_tokens": 1, "estimated_cost": float("inf")},
])
def test_malformed_provider_usage_is_not_accepted(bad_usage):
    provider = MeteredProvider(result=GatewayResponse.model_construct(text="ok", usage=bad_usage, tool_proposal=None))
    gateway = ModelGateway((provider,))
    with pytest.raises(ProviderError):
        gateway.generate(request())
    assert gateway.usage.total_tokens == 0
    assert gateway.charged_usage.total_tokens == 5


def test_malformed_model_request_does_not_emit_secret_warning():
    provider = MeteredProvider()
    gateway = ModelGateway((provider,))
    bad = request().model_copy(update={"request_id": ["password: synthetic-only"]})
    with warnings.catch_warnings(record=True) as caught:
        with pytest.raises(ProviderError) as error:
            gateway.generate(bad)
    assert "synthetic-only" not in str(error.value)
    assert not caught
    assert provider.calls == 0


def test_provider_cannot_inject_a_raw_budget_exception():
    provider = MeteredProvider(result=BudgetExceeded("password: synthetic-only"))
    gateway = ModelGateway((provider,))
    with pytest.raises(ProviderError) as error:
        gateway.generate(request())
    assert "synthetic-only" not in "".join(traceback.format_exception(error.value))
    assert gateway.charged_usage.estimated_cost == 0.1


@pytest.mark.parametrize("change", [
    {"enabled": False}, {"action": "other"}, {"allowed_scopes": frozenset({"other"})},
    {"permission": PermissionLevel.P5}, {"confirmation_required": True}, {"name": "different"},
])
def test_core_cannot_bypass_policy_for_disallowed_tool(settings_factory, change):
    proposal = ToolProposal(tool_name="echo", action="read", scope="internal")
    provider = MeteredProvider(result=GatewayResponse(tool_proposal=proposal,
        usage=Usage(input_tokens=2, output_tokens=3, estimated_cost=0.1)))
    app = create_application(settings_factory(), providers=(provider,))
    calls = []
    spec = dict(name="echo", description="test", action="read", permission=PermissionLevel.P0,
                allowed_scopes=frozenset({"internal"}), handler=lambda args: calls.append(args))
    spec.update(change)
    app.core._dispatcher.registry.register(ToolSpec(**spec))
    try:
        assert app.core.handle(context()).degraded
        assert calls == []
        records = [json.loads(line) for line in app.audit.path.read_text().splitlines()]
        assert any(record["event"] == "policy_decision" for record in records)
    finally:
        app.shutdown()


def test_core_audit_failure_never_executes_handler(settings_factory, monkeypatch):
    proposal = ToolProposal(tool_name="echo", action="read", scope="internal")
    provider = MeteredProvider(result=GatewayResponse(tool_proposal=proposal,
        usage=Usage(input_tokens=2, output_tokens=3, estimated_cost=0.1)))
    app = create_application(settings_factory(), providers=(provider,))
    calls = []
    app.core._dispatcher.registry.register(ToolSpec(
        name="echo", description="test", action="read", permission=PermissionLevel.P0,
        allowed_scopes=frozenset({"internal"}), handler=lambda args: calls.append(args)))
    original = JsonlAuditSink.write

    def fail_intent(self, event, metadata):
        if event == "tool_request":
            raise OSError("password: synthetic-only")
        return original(self, event, metadata)

    monkeypatch.setattr(JsonlAuditSink, "write", fail_intent)
    try:
        result = app.core.handle(context())
        assert result.degraded and not result.tool_executed
        assert "synthetic-only" not in result.text
        assert calls == []
    finally:
        app.shutdown()


@pytest.mark.parametrize("payload", [
    {"candidates": [None]}, {"candidates": [{"content": None}]},
    {"candidates": [{"content": {"parts": []}}]},
])
def test_malformed_gemini_content_is_normalized(payload):
    provider = GeminiProvider(ProviderConfig("gemini", "offline", "synthetic-key", input_cost_per_million=1, output_cost_per_million=2),
                              transport=lambda *_: (200, json.dumps(payload).encode()))
    with pytest.raises(ProviderError):
        provider.generate(request())


@pytest.mark.parametrize("status,raw", [(500, b"private response"), (200, b"{bad"), (302, b"redirect")])
def test_http_errors_and_malformed_json_do_not_escape(status, raw):
    provider = MistralProvider(ProviderConfig("mistral", "offline", "synthetic-key", input_cost_per_million=1, output_cost_per_million=2),
                               endpoint="https://example.invalid", transport=lambda *_: (status, raw))
    with pytest.raises(ProviderError) as error:
        provider.generate(request())
    assert "private response" not in str(error.value)


def test_transport_timeout_is_normalized_and_config_repr_hides_key():
    config = ProviderConfig("mistral", "offline", "synthetic-key", input_cost_per_million=1, output_cost_per_million=2)
    assert "synthetic-key" not in repr(config)

    def fail(*_):
        raise TimeoutError("password: synthetic-only")

    provider = MistralProvider(config, endpoint="https://example.invalid", transport=fail)
    with pytest.raises(ProviderError) as error:
        provider.generate(request())
    assert "synthetic-only" not in "".join(traceback.format_exception(error.value))


def test_gemini_thinking_usage_is_included_in_cost():
    provider = GeminiProvider(ProviderConfig("gemini", "offline", "synthetic-key", input_cost_per_million=1, output_cost_per_million=2))
    response = provider._parse_response({
        "candidates": [{"content": {"parts": [{"text": "hidden", "thought": True}, {"text": "answer"}]}}],
        "usageMetadata": {"promptTokenCount": 2, "candidatesTokenCount": 3, "thoughtsTokenCount": 5, "totalTokenCount": 10},
    })
    assert response.text == "answer"
    assert response.usage.output_tokens == 8
    assert response.usage.estimated_cost == pytest.approx(18 / 1_000_000)


def test_daily_state_corruption_blocks_startup_without_discarding_it(settings_factory):
    settings = settings_factory()
    app = create_application(settings, providers=(MeteredProvider(),))
    app.shutdown()
    state = settings.state_dir / "model_budget.json"
    state.write_text("{corrupt", encoding="utf-8")
    provider = MeteredProvider()
    with pytest.raises(StartupError):
        create_application(settings, providers=(provider,))
    assert provider.closed == 1
    assert state.read_text(encoding="utf-8") == "{corrupt"


def test_per_request_cost_budget_is_checked_before_call():
    provider = MeteredProvider()
    gateway = ModelGateway((provider,), request_cost_budget=0.05)
    with pytest.raises(BudgetExceeded):
        gateway.generate(request())
    assert provider.calls == 0


def test_lifecycle_does_not_report_stopped_when_cleanup_failed():
    def fail():
        raise RuntimeError("password: synthetic-only")

    lifecycle = Lifecycle(lambda: None, fail)
    lifecycle.start()
    for _ in range(2):
        with pytest.raises(RuntimeError) as error:
            lifecycle.shutdown()
        assert "synthetic-only" not in str(error.value)
        assert lifecycle.readiness == Readiness.FAILED


def test_disallowed_degraded_startup_never_reports_ready():
    lifecycle = Lifecycle(lambda: None, lambda: None, degraded_ok=False)
    with pytest.raises(RuntimeError):
        lifecycle.start(degraded=True)
    assert lifecycle.readiness == Readiness.FAILED


def test_core_real_recall_does_not_mix_owner_or_workspace(settings_factory):
    from arona.domain.models import MemorySensitivity, MemoryType

    provider = MeteredProvider()
    requests = []
    original = provider.generate

    def observe(req):
        requests.append(req)
        return original(req)

    provider.generate = observe
    app = create_application(settings_factory(), providers=(provider,))
    try:
        for owner, workspace, content in (
            ("alice", "w", "orchid chosen green"), ("bob", "w", "orchid chosen red"),
            ("alice", "other", "orchid chosen blue"),
        ):
            app.core._memory.create_memory(owner_id=owner, workspace_id=workspace,
                memory_type=MemoryType.SEMANTIC, content=content, source="user", confidence=0.95,
                sensitivity=MemorySensitivity.PUBLIC)
        req = context()
        req.current_user_text = "orchid"
        assert app.core.handle(req).text == "ok"
        messages = json.dumps(requests[0].messages)
        assert "orchid chosen green" in messages
        assert "orchid chosen red" not in messages
        assert "orchid chosen blue" not in messages
    finally:
        app.shutdown()


def test_service_restores_signal_handlers_even_if_shutdown_fails(monkeypatch, settings_factory):
    import arona.operations.service as service

    calls = []

    class Application:
        readiness = Readiness.READY

        def shutdown(self):
            raise RuntimeError("cleanup failure")

    class ImmediateEvent:
        def wait(self):
            return True

        def set(self):
            pass

    def signal(number, handler):
        calls.append((number, handler))
        return "previous-handler"

    monkeypatch.setattr(service, "create_application", lambda _: Application())
    monkeypatch.setattr(service, "Event", ImmediateEvent)
    monkeypatch.setattr(service.signal, "signal", signal)
    with pytest.raises(RuntimeError):
        service.run_service(settings_factory())
    assert len(calls) == 4
    assert all(handler == "previous-handler" for _, handler in calls[2:])
