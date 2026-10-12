from __future__ import annotations

import json

import pytest

from arona.app import StartupError, create_application
from arona.core.lifecycle import Readiness
from arona.domain.models import MemoryRecord, MemorySensitivity, MemoryType
from arona.interaction import AuthenticatedIdentity, InteractionGateway, InteractionRequest
from arona.memory.admission import MemoryAdmissionPolicy
from arona.memory.repository import MemoryRepository
from arona.memory.service import MemoryService
from arona.models.gateway import GatewayResponse, ModelGateway, ModelRequest, ProviderError, Usage
from arona.models.http import GeminiProvider, MistralProvider, ProviderConfig
from arona.operations.backup import DatabaseBackupManager


class FakeProvider:
    def __init__(self, name, responses):
        self.name = name
        self.model = "fake"
        self.responses = list(responses)

    def estimate_usage(self, request):
        return Usage(input_tokens=2, output_tokens=request.max_output_tokens, estimated_cost=0.01)

    def generate(self, request):
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def model_response(text="ok"):
    return GatewayResponse(text=text, usage=Usage(input_tokens=2, output_tokens=3, estimated_cost=0.01))


def fake_transport(payload):
    def transport(request, timeout):
        return 200, json.dumps(payload).encode("utf-8")

    return transport


def create_memory(service, *, owner="alice", workspace="project", content="stable fact"):
    memory = service.create_memory(
        owner_id=owner, workspace_id=workspace, memory_type=MemoryType.SEMANTIC,
        content=content, source="user", confidence=0.9, sensitivity=MemorySensitivity.PUBLIC,
    )
    assert isinstance(memory, MemoryRecord)
    return memory


def test_gemini_and_mistral_adapters_parse_usage_without_network():
    gemini = GeminiProvider(
        ProviderConfig(name="gemini", model="gemini-test", api_key="secret", input_cost_per_million=1, output_cost_per_million=2),
        transport=fake_transport({
            "candidates": [{"content": {"parts": [{"text": "hello"}]} }],
            "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 2},
        }),
    )
    mistral = MistralProvider(
        ProviderConfig(name="mistral", model="mistral-test", api_key="secret", input_cost_per_million=1, output_cost_per_million=2),
        endpoint="https://example.invalid",
        transport=fake_transport({
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }),
    )
    request = ModelRequest(request_id="r", messages=({"role": "user", "content": "hi"},), max_output_tokens=10)
    assert gemini.generate(request).usage.total_tokens == 6
    assert mistral.generate(request).usage.total_tokens == 8


def test_malformed_adapter_response_is_normalized():
    provider = MistralProvider(
        ProviderConfig(name="mistral", model="mistral-test", api_key="secret", input_cost_per_million=1, output_cost_per_million=2),
        endpoint="https://example.invalid", transport=fake_transport({"unexpected": True}),
    )
    with pytest.raises(ProviderError, match="malformed|no choices"):
        provider.generate(ModelRequest(request_id="r", messages=({"role": "user", "content": "hi"},), max_output_tokens=10))


def test_fallback_integration_updates_usage_and_health():
    primary = FakeProvider("primary", [ProviderError("timeout")])
    fallback = FakeProvider("fallback", [model_response("fallback")])
    # Both potentially billed attempts fit; request-budget refusal has separate regressions.
    gateway = ModelGateway((primary, fallback), failure_threshold=2, daily_token_budget=20, request_token_budget=20)
    result = gateway.generate(ModelRequest(request_id="r", messages=({"role": "user", "content": "hi"},), max_output_tokens=5))
    assert result.text == "fallback"
    assert gateway.usage.total_tokens == 5
    assert gateway.health()[0].failures == 1
    assert gateway.health()[1].failures == 0


def test_startup_restart_preserves_memory_and_identity_isolation(settings_factory):
    settings = settings_factory()
    provider = FakeProvider("fake", [model_response(), model_response()])
    first = create_application(settings, providers=(provider,))
    assert first.readiness is Readiness.READY
    create_memory(first.core._memory, content="restart fact")
    first.shutdown()
    second = create_application(settings, providers=(provider,))
    assert second.core._memory.recall("alice", "project", "restart fact")
    assert second.core._memory.recall("bob", "project", "restart fact") == []
    second.shutdown()


def test_startup_without_provider_never_reports_ready(settings_factory):
    with pytest.raises(StartupError):
        create_application(settings_factory())


def test_interaction_gateway_authenticates_before_core(settings_factory):
    app = create_application(settings_factory(), providers=(FakeProvider("fake", [model_response()]),))
    gateway = InteractionGateway(app.core, allowed_user_ids={"42"})
    request = InteractionRequest(user_id="42", workspace_id="w", conversation_id="c", text="hello")
    assert gateway.receive(request, identity=AuthenticatedIdentity("42", "w")).text == "ok"
    with pytest.raises(PermissionError):
        gateway.receive(request.model_copy(update={"user_id": "99"}), identity=AuthenticatedIdentity("99", "w"))
    app.shutdown()


def test_backup_restore_preserves_recall_and_failed_restore_is_non_destructive(settings_factory, tmp_path):
    settings = settings_factory()
    app = create_application(settings, providers=(FakeProvider("fake", [model_response()]),))
    create_memory(app.core._memory, content="backup fact")
    app.shutdown()
    manager = DatabaseBackupManager(settings.backup_dir)
    backup = manager.create_backup(settings.database_path)
    restored = tmp_path / "restored.db"
    manager.restore_backup(backup, restored)
    restored_service = MemoryService(
        MemoryRepository(type(app.core._memory.repository.db)(restored)),
        MemoryAdmissionPolicy(), default_ttl_seconds=None,
    )
    assert restored_service.recall("alice", "project", "backup fact")
    with pytest.raises(ValueError):
        manager.restore_backup(backup, restored)
    assert restored.is_file()
