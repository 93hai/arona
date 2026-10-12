"""Narrow final gap regressions; all provider I/O is offline."""
from __future__ import annotations

from asyncio import CancelledError
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import traceback
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from arona.app import create_application
from arona.core.lifecycle import Lifecycle, Readiness
from arona.interaction import AuthenticatedIdentity, InteractionGateway, InteractionRequest
from arona.models.budget import BudgetStore
from arona.models.gateway import BudgetExceeded, GatewayResponse, ModelGateway, ModelRequest, ProviderError, Usage
from arona.models.http import MistralProvider, ProviderConfig, urlopen_transport


class Provider:
    model = "offline"

    def __init__(self, name="primary", error=None):
        self.name, self.error = name, error
        self.calls = self.closed = 0

    def estimate_usage(self, request):
        return Usage(input_tokens=2, output_tokens=request.max_output_tokens, estimated_cost=0.1)

    def generate(self, request):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return GatewayResponse(text="ok", usage=Usage(input_tokens=2, output_tokens=2, estimated_cost=0.05))

    def close(self):
        self.closed += 1


def request():
    return ModelRequest(request_id="gap", messages=({"role": "user", "content": "hello"},), max_output_tokens=3)


def state():
    usage = {"input_tokens": 0, "output_tokens": 0, "estimated_cost": 0.0}
    return {"version": 1, "day": datetime.now(timezone.utc).date().isoformat(),
            "reported": usage, "charged": usage, "accounting_failed": False}


@pytest.mark.parametrize("total", [True, -1, 1, 100, "4"])
def test_mistral_rejects_inconsistent_total_usage(total):
    payload = {"choices": [{"message": {"content": "ok"}}],
               "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": total}}
    provider = MistralProvider(
        ProviderConfig("mistral", "offline", "synthetic-key", input_cost_per_million=1, output_cost_per_million=2),
        endpoint="https://example.invalid", transport=lambda *_: (200, json.dumps(payload).encode()),
    )
    with pytest.raises(ProviderError):
        provider.generate(request())


def test_fallback_configuration_is_snapshotted():
    original = Provider(error=ProviderError("down"))
    configured = [original]
    gateway = ModelGateway(configured)
    injected = Provider(name="primary")
    configured.append(injected)
    with pytest.raises(ProviderError):
        gateway.generate(request())
    assert original.calls == 1
    assert injected.calls == 0


def test_boolean_budget_version_is_rejected(tmp_path):
    path = tmp_path / "budget.json"
    invalid = state()
    invalid["version"] = True
    path.write_text(json.dumps(invalid), encoding="utf-8")
    store = BudgetStore(path)
    try:
        with pytest.raises(ValueError):
            ModelGateway((Provider(),), budget_store=store)
    finally:
        store.close()


def test_duplicate_budget_fields_are_rejected(tmp_path):
    path = tmp_path / "budget.json"
    payload = json.dumps(state())[:-1] + ', "accounting_failed": true, "accounting_failed": false}'
    path.write_text(payload, encoding="utf-8")
    store = BudgetStore(path)
    try:
        with pytest.raises(ValueError):
            ModelGateway((Provider(),), budget_store=store)
        assert path.read_text(encoding="utf-8") == payload
    finally:
        store.close()


def test_cancelled_start_runs_cleanup_and_marks_failure():
    cleaned = []

    def start():
        raise CancelledError()

    lifecycle = Lifecycle(start, lambda: cleaned.append(True))
    with pytest.raises(CancelledError):
        lifecycle.start()
    assert cleaned == [True]
    assert lifecycle.readiness == Readiness.FAILED


def test_cancelled_composition_closes_owned_provider(settings_factory, monkeypatch):
    def cancel(*args):
        raise CancelledError()

    monkeypatch.setattr("arona.app.DatabaseMigrations.run_migrations", cancel)
    provider = Provider()
    with pytest.raises(CancelledError):
        create_application(settings_factory(), providers=(provider,))
    assert provider.closed == 1


def test_cancelled_cleanup_still_closes_other_providers():
    first, second = Provider(), Provider("fallback")

    def cancel():
        first.closed += 1
        raise CancelledError()

    first.close = cancel
    gateway = ModelGateway((first, second))
    with pytest.raises(CancelledError):
        gateway.close()
    assert first.closed == second.closed == 1
    gateway.close()
    assert second.closed == 1


def test_cancelled_shutdown_does_not_stick_in_stopping():
    def cancel():
        raise CancelledError()

    lifecycle = Lifecycle(lambda: None, cancel)
    lifecycle.start()
    with pytest.raises(CancelledError):
        lifecycle.shutdown()
    assert lifecycle.readiness == Readiness.FAILED
    with pytest.raises(RuntimeError):
        lifecycle.shutdown()
    assert lifecycle.readiness == Readiness.FAILED


def test_constructed_interaction_with_extra_authority_fails_closed():
    class CoreSpy:
        called = False

        def handle(self, context):
            self.called = True

    core = CoreSpy()
    gateway = InteractionGateway(core, allowed_user_ids={"alice"})
    malformed = InteractionRequest(user_id="alice", workspace_id="w", conversation_id="c", text="hi").model_copy(update={"trusted": True})
    with pytest.raises(ValueError):
        gateway.receive(malformed, identity=AuthenticatedIdentity("alice", "w"))
    assert not core.called


def test_cancelled_request_keeps_reservation_across_restart(tmp_path):
    path = tmp_path / "budget.json"
    primary, fallback = Provider(error=CancelledError()), Provider("fallback")
    gateway = ModelGateway((primary, fallback), daily_token_budget=5, budget_store=BudgetStore(path))
    with pytest.raises(CancelledError):
        gateway.generate(request())
    assert primary.calls == 1 and fallback.calls == 0
    assert gateway.usage.total_tokens == 0
    assert gateway.charged_usage.total_tokens == 5
    gateway.close()
    assert primary.closed == fallback.closed == 1
    next_provider = Provider()
    resumed = ModelGateway((next_provider,), daily_token_budget=5, budget_store=BudgetStore(path))
    try:
        with pytest.raises(BudgetExceeded):
            resumed.generate(request())
        assert next_provider.calls == 0
    finally:
        resumed.close()


def test_commit_failure_preserves_durable_reservation(tmp_path, monkeypatch):
    path = tmp_path / "budget.json"
    store = BudgetStore(path)
    provider = Provider()
    gateway = ModelGateway((provider,), daily_cost_budget=0.1, budget_store=store)
    original = store.save
    saves = []

    def fail_commit(value):
        saves.append(True)
        if len(saves) == 2:
            raise OSError("synthetic-only")
        original(value)

    monkeypatch.setattr(store, "save", fail_commit)
    with pytest.raises(BudgetExceeded):
        gateway.generate(request())
    assert provider.calls == 1
    assert gateway.usage.total_tokens == 4
    with pytest.raises(BudgetExceeded):
        gateway.generate(request())
    assert provider.calls == 1
    gateway.close()
    resumed_provider = Provider()
    resumed = ModelGateway((resumed_provider,), daily_cost_budget=0.1, budget_store=BudgetStore(path))
    try:
        assert resumed.charged_usage.estimated_cost == 0.1
        with pytest.raises(BudgetExceeded):
            resumed.generate(request())
        assert resumed_provider.calls == 0
    finally:
        resumed.close()


def test_cancelled_budget_initialization_releases_store_lock(tmp_path, monkeypatch):
    path = tmp_path / "budget.json"
    store = BudgetStore(path)

    def cancel():
        raise CancelledError()

    monkeypatch.setattr(store, "load", cancel)
    with pytest.raises(CancelledError):
        ModelGateway((Provider(),), budget_store=store)
    replacement = BudgetStore(path)
    replacement.close()


def test_all_failures_cooldown_and_recovery_have_bounded_accounting():
    now = [datetime(2026, 1, 1, tzinfo=timezone.utc)]
    first, second = Provider(error=ProviderError("down")), Provider("fallback", ProviderError("down"))
    gateway = ModelGateway((first, second), failure_threshold=1, cooldown_seconds=60, clock=lambda: now[0])
    for _ in range(2):
        with pytest.raises(ProviderError):
            gateway.generate(request())
    assert first.calls == second.calls == 1
    assert gateway.charged_usage.total_tokens == 10
    assert gateway.usage.total_tokens == 0
    assert all(not health.available for health in gateway.health())
    now[0] += timedelta(seconds=61)
    first.error = None
    assert gateway.generate(request()).text == "ok"
    assert first.calls == 2 and second.calls == 1
    assert gateway.usage.total_tokens == 4
    assert gateway.charged_usage.total_tokens == 14
    assert gateway.health()[0].failures == 0


def test_fallback_success_commits_actual_and_keeps_unknown_charge():
    first, second = Provider(error=ProviderError("timeout")), Provider("fallback")
    gateway = ModelGateway((first, second))
    assert gateway.generate(request()).text == "ok"
    assert gateway.usage.total_tokens == 4
    assert gateway.usage.estimated_cost == 0.05
    assert gateway.charged_usage.total_tokens == 9
    assert gateway.charged_usage.estimated_cost == pytest.approx(0.15)


@pytest.mark.parametrize("error_kind", ["http", "connection", "timeout"])
def test_real_transport_error_paths_close_and_hide_response_data(monkeypatch, error_kind):
    body = BytesIO(b"synthetic-private-body")
    error = (HTTPError("https://example.invalid", 403, "synthetic-only", {}, body) if error_kind == "http" else
             URLError("synthetic-only") if error_kind == "connection" else TimeoutError("synthetic-only"))

    class Opener:
        def open(self, request, timeout):
            assert timeout == 2
            raise error

    monkeypatch.setattr("arona.models.http.build_opener", lambda *_: Opener())
    with pytest.raises(ProviderError) as caught:
        urlopen_transport(Request("https://example.invalid"), 2)
    assert "synthetic-only" not in "".join(traceback.format_exception(caught.value))
    if error_kind == "http":
        assert body.closed
