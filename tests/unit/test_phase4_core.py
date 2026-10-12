from datetime import datetime, timedelta, timezone

import pytest

from arona.core.conversation import ConversationStore
from arona.core.lifecycle import Lifecycle, Readiness
from arona.core.orchestrator import CoreOrchestrator
from arona.core.router import Router
from arona.audit.sink import JsonlAuditSink
from arona.domain.models import PermissionLevel, RequestContext, RequestIntent
from arona.models.gateway import (
    BudgetExceeded,
    GatewayResponse,
    ModelGateway,
    ModelRequest,
    ProviderError,
    ToolProposal,
    Usage,
)
from arona.policy.engine import PolicyEngine
from arona.tools.dispatcher import ToolDispatcher
from arona.tools.registry import ToolRegistry, ToolSpec


class FakeProvider:
    def __init__(self, name, responses):
        self.name = name
        self.model = "fake-model"
        self.responses = list(responses)

    def estimate_usage(self, request):
        return Usage(input_tokens=1, output_tokens=request.max_output_tokens, estimated_cost=0.01)

    def generate(self, request):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(text="ok", tokens=2):
    return GatewayResponse(text=text, usage=Usage(input_tokens=1, output_tokens=tokens, estimated_cost=0.01))


def test_conversation_isolation_and_bound():
    store = ConversationStore(history_limit=2)
    store.append("owner-a", "workspace-a", "same", role="user", content="one")
    store.append("owner-a", "workspace-a", "same", role="assistant", content="two")
    store.append("owner-a", "workspace-a", "same", role="user", content="three")
    assert [item.content for item in store.history("owner-a", "workspace-a", "same")] == ["two", "three"]
    assert store.history("owner-b", "workspace-a", "same") == ()
    assert store.history("owner-a", "workspace-b", "same") == ()


def test_router_is_deterministic_and_safe_fallback():
    router = Router()
    assert router.route("please write Python code").intent is RequestIntent.CODE_REQUEST
    assert router.route("").intent is RequestIntent.UNKNOWN
    assert router.route("ordinary message").intent is RequestIntent.CHAT


def test_gateway_falls_back_and_tracks_usage():
    primary = FakeProvider("primary", [ProviderError("timeout")])
    fallback = FakeProvider("fallback", [response()])
    gateway = ModelGateway((primary, fallback), failure_threshold=2)
    result = gateway.generate(ModelRequest(request_id="r1", messages=({"role": "user", "content": "hi"},), max_output_tokens=10))
    assert result.text == "ok"
    assert gateway.usage.total_tokens == 3
    assert gateway.health()[0].failures == 1


def test_gateway_enforces_budget_before_provider_call():
    provider = FakeProvider("primary", [response(tokens=20)])
    gateway = ModelGateway((provider,), daily_token_budget=3, request_token_budget=10)
    with pytest.raises(BudgetExceeded):
        gateway.generate(ModelRequest(request_id="r1", messages=({"role": "user", "content": "hi"},), max_output_tokens=10))
    assert provider.responses


def test_gateway_cooldown_is_deterministic():
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    provider = FakeProvider("primary", [ProviderError("down"), ProviderError("down"), response()])
    gateway = ModelGateway((provider,), failure_threshold=2, cooldown_seconds=60, clock=lambda: now[0])
    request = ModelRequest(request_id="r1", messages=({"role": "user", "content": "hi"},), max_output_tokens=10)
    with pytest.raises(ProviderError):
        gateway.generate(request)
    with pytest.raises(ProviderError):
        gateway.generate(request)
    assert gateway.health()[0].cooldown_until == now[0] + timedelta(seconds=60)
    now[0] += timedelta(seconds=61)
    assert gateway.generate(request).text == "ok"


def test_model_tool_proposal_has_no_authorization_fields():
    proposal = ToolProposal(
        tool_name="example", action="read", arguments={}, scope="internal",
    )
    assert not hasattr(proposal, "owner_id")


def test_core_tool_path_keeps_trusted_identity(tmp_path):
    audit = JsonlAuditSink(tmp_path / "audit.jsonl")
    registry = ToolRegistry()
    observed = []
    registry.register(ToolSpec(
        name="echo", description="echo", action="read", permission=PermissionLevel.P0,
        allowed_scopes=frozenset({"internal"}), handler=lambda arguments: observed.append(arguments) or "done",
    ))
    policy = PolicyEngine(audit=audit.write, registry=registry)
    dispatcher = ToolDispatcher(registry, policy, audit=audit.write)
    provider = FakeProvider("primary", [GatewayResponse(
        text="",
        tool_proposal=ToolProposal(
            tool_name="echo", action="read", arguments={"value": "x"}, scope="internal",
        ),
        usage=Usage(input_tokens=1, output_tokens=1, estimated_cost=0.01),
    )])
    core = CoreOrchestrator(
        router=Router(), conversation=ConversationStore(),
        model_gateway=ModelGateway((provider,)), dispatcher=dispatcher,
    )
    result = core.handle(RequestContext(
        request_id="req-1", owner_id="trusted-owner", workspace_id="trusted-workspace",
        conversation_id="conversation", current_user_text="use tool", timestamp=datetime.now(timezone.utc),
    ))
    assert result.tool_executed is True
    assert observed == [{"value": "x"}]


def test_lifecycle_is_idempotent_and_reports_readiness():
    events = []
    lifecycle = Lifecycle(lambda: events.append("start"), lambda: events.append("stop"))
    lifecycle.start()
    lifecycle.start()
    lifecycle.shutdown()
    lifecycle.shutdown()
    assert lifecycle.readiness is Readiness.STOPPED
    assert events == ["start", "stop"]
