from __future__ import annotations

from dataclasses import dataclass

from arona.audit.sink import JsonlAuditSink, write_audit
from arona.common.paths import private_directory
from arona.config.settings import Settings
from arona.core.conversation import ConversationStore
from arona.core.lifecycle import Lifecycle, Readiness
from arona.core.orchestrator import CoreOrchestrator
from arona.core.router import Router
from arona.database.migrations import DatabaseMigrations
from arona.memory.admission import MemoryAdmissionPolicy
from arona.memory.repository import MemoryRepository
from arona.memory.service import MemoryService
from arona.models.gateway import ModelGateway
from arona.models.budget import BudgetStore
from arona.models.http import GeminiProvider, MistralProvider, ProviderConfig
from arona.policy.engine import PolicyEngine
from arona.tools.dispatcher import ToolDispatcher
from arona.tools.registry import ToolRegistry
from arona.tools.candidate import register_candidate_tools
from arona.tools.memory import register_memory_tools
from arona.workspace.manager import CandidateManager


class StartupError(RuntimeError):
    """Safe startup failure without provider credentials or raw paths."""


@dataclass
class AronaApplication:
    settings: Settings
    core: CoreOrchestrator
    gateway: ModelGateway
    audit: JsonlAuditSink
    lifecycle: Lifecycle

    @property
    def readiness(self) -> Readiness:
        state = self.lifecycle.readiness
        if state == Readiness.READY and (not self.gateway.available or any(item.failures for item in self.gateway.health())):
            return Readiness.DEGRADED
        return state

    def start(self) -> None:
        self.lifecycle.start()

    def shutdown(self) -> None:
        self.lifecycle.shutdown()


def create_application(
    settings: Settings,
    *,
    providers: tuple[object, ...] | None = None,
    history_limit: int = 20,
) -> AronaApplication:
    """Build all Phase 4 dependencies in one safe composition root."""
    if type(settings) is not Settings:
        raise StartupError("Valid Settings are required")
    if providers is None:
        # Configuration-only preflight, before any resource has been opened.
        missing_prices = []
        for prefix, enabled in (
            ("gemini", settings.gemini_api_key.get_secret_value() and settings.gemini_model),
            ("mistral", settings.mistral_api_key.get_secret_value() and settings.mistral_general_model),
            ("codestral", settings.mistral_api_key.get_secret_value() and settings.mistral_code_model),
        ):
            if enabled:
                for direction in ("input", "output"):
                    field = f"{prefix}_{direction}_cost_per_million"
                    if getattr(settings, field) is None:
                        missing_prices.append(Settings.model_fields[field].validation_alias)
        if missing_prices:
            raise StartupError("Configure explicit token prices: " + ", ".join(missing_prices))
    selected = providers or ()
    gateway = None
    budget_store = None
    try:
        selected = providers if providers is not None else _configured_providers(settings)
        _prepare_runtime_directories(settings)
        migrations = DatabaseMigrations(settings.database_path, backup_dir=settings.backup_dir, actor_id="arona")
        migrations.run_migrations()
        audit = JsonlAuditSink(settings.log_dir / "audit.jsonl")
        repository = MemoryRepository(migrations.db)
        memory = MemoryService(
            repository,
            MemoryAdmissionPolicy(),
            default_ttl_seconds=settings.memory_default_ttl_seconds,
            auto_memory_enabled=settings.auto_memory_enabled,
            audit_callback=lambda event, owner, workspace, details: write_audit(audit, event, {
                "owner_id": owner, "workspace_id": workspace, "details": details,
            }),
        )
        registry = ToolRegistry()
        candidate = CandidateManager(settings.candidate_dir, forbidden_roots=(
            settings.runtime_source_dir, settings.database_path.parent, settings.backup_dir,
            settings.state_dir, settings.log_dir,
        ))
        register_candidate_tools(registry, candidate)
        register_memory_tools(registry, memory)
        policy = PolicyEngine(audit=audit.write, registry=registry)
        dispatcher = ToolDispatcher(registry, policy, audit=audit.write)
        budget_store = BudgetStore(settings.state_dir / "model_budget.json")
        gateway = ModelGateway(
            tuple(selected),
            daily_token_budget=settings.daily_token_budget,
            daily_cost_budget=settings.daily_cost_budget,
            request_token_budget=settings.max_request_tokens,
            failure_threshold=settings.provider_failure_threshold,
            cooldown_seconds=settings.provider_cooldown_seconds,
            budget_store=budget_store,
        )
        core = CoreOrchestrator(
            router=Router(), conversation=ConversationStore(history_limit=history_limit),
            model_gateway=gateway, memory=memory, dispatcher=dispatcher,
            memory_recall_limit=settings.memory_recall_limit,
            memory_min_confidence=settings.memory_min_confidence,
            max_output_tokens=min(4000, max(1, settings.max_request_tokens // 4)),
        )
        def stop():
            core.shutdown()
            try:
                write_audit(audit, "service_stopped", {"status": "stopped"})
            finally:
                gateway.close()

        lifecycle = Lifecycle(lambda: write_audit(audit, "service_started", {
            "status": "ready", "readiness_scope": "core", "external_transport_ready": False,
        }), stop)
        app = AronaApplication(settings, core, gateway, audit, lifecycle)
        app.start()
        return app
    except BaseException as exc:
        # Memory connections and audit descriptors are operation-scoped and have
        # already closed. Providers may own clients even if composition failed.
        if gateway is not None:
            try:
                gateway.close()
            except BaseException:
                pass
        else:
            if budget_store is not None:
                budget_store.close()
            for provider in selected:
                try:
                    close = getattr(provider, "close", None)
                    if close is not None:
                        close()
                except BaseException:
                    pass
        if not isinstance(exc, Exception):
            raise
        raise StartupError("Arona startup failed before readiness") from None


def _prepare_runtime_directories(settings: Settings) -> None:
    for path in (
        settings.data_dir,
        settings.database_path.parent,
        settings.backup_dir,
        settings.candidate_dir,
        settings.state_dir,
        settings.log_dir,
    ):
        private_directory(path)


def _configured_providers(settings: Settings) -> tuple[object, ...]:
    providers: list[object] = []
    if settings.gemini_api_key.get_secret_value() and settings.gemini_model:
        providers.append(GeminiProvider(ProviderConfig(
            name="gemini", model=settings.gemini_model,
            api_key=settings.gemini_api_key.get_secret_value(),
            timeout_seconds=settings.gemini_timeout_seconds,
            input_cost_per_million=settings.gemini_input_cost_per_million,
            output_cost_per_million=settings.gemini_output_cost_per_million,
        )))
    if settings.mistral_api_key.get_secret_value() and settings.mistral_general_model:
        providers.append(MistralProvider(ProviderConfig(
            name="mistral", model=settings.mistral_general_model,
            api_key=settings.mistral_api_key.get_secret_value(),
            timeout_seconds=settings.mistral_timeout_seconds,
            input_cost_per_million=settings.mistral_input_cost_per_million,
            output_cost_per_million=settings.mistral_output_cost_per_million,
        ), endpoint=settings.mistral_api_url))
    if settings.mistral_api_key.get_secret_value() and settings.mistral_code_model:
        providers.append(MistralProvider(ProviderConfig(
            name="codestral", model=settings.mistral_code_model,
            capability="code",
            api_key=settings.mistral_api_key.get_secret_value(),
            timeout_seconds=settings.mistral_timeout_seconds,
            input_cost_per_million=settings.codestral_input_cost_per_million,
            output_cost_per_million=settings.codestral_output_cost_per_million,
        ), endpoint=settings.mistral_api_url))
    if not providers:
        raise StartupError("No configured model provider")
    return tuple(providers)


__all__ = ["AronaApplication", "StartupError", "create_application"]
