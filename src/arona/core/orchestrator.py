from __future__ import annotations

from dataclasses import dataclass, replace
from copy import deepcopy
from threading import Condition

from arona.audit.sink import redact
from arona.core.conversation import ConversationStore
from arona.core.router import Router
from arona.domain.models import PermissionLevel, RequestContext
from arona.memory.service import MemoryService
from arona.models.gateway import BudgetExceeded, GatewayResponse, ModelGateway, ModelRequest, ProviderError
from arona.policy.models import PolicyRequest
from arona.prompts import core_system_instruction, memory_data_message
from arona.prompts.contracts import ToolProposal
from arona.tools.dispatcher import ToolDispatcher
from arona.tools.models import ToolInvocation, TrustedContext
from arona.tools.memory import MEMORY_TOOL_NAMES, is_memory_command, parse_memory_command


@dataclass(frozen=True)
class CoreResponse:
    request_id: str
    text: str
    degraded: bool = False
    tool_executed: bool = False


class CoreOrchestrator:
    def __init__(
        self,
        *,
        router: Router,
        conversation: ConversationStore,
        model_gateway: ModelGateway,
        memory: MemoryService | None = None,
        dispatcher: ToolDispatcher | None = None,
        memory_recall_limit: int = 5,
        memory_min_confidence: float = 0.7,
        max_output_tokens: int = 4000,
    ) -> None:
        self._router = router
        self._conversation = conversation
        self._gateway = model_gateway
        self._memory = memory
        self._dispatcher = dispatcher
        self._recall_limit = memory_recall_limit
        self._min_confidence = memory_min_confidence
        self._max_output_tokens = max_output_tokens
        self._idle = Condition()
        self._active = 0
        self._stopping = False

    def handle(self, context: RequestContext) -> CoreResponse:
        with self._idle:
            if self._stopping:
                raise RuntimeError("Core is stopping")
            self._active += 1
        try:
            try:
                if type(context) is not RequestContext:
                    raise ValueError
                # RequestContext predates Phase 4 and is mutable. Never retain the
                # adapter's object across provider I/O or concurrent callbacks.
                context = RequestContext.model_validate(deepcopy(context.model_dump(warnings=False)))
                if not all(value.strip() for value in (context.request_id, context.owner_id, context.workspace_id,
                                                       context.conversation_id, context.current_user_text)):
                    raise ValueError
            except Exception:
                raise ValueError("A trusted, nonempty RequestContext is required") from None
            response = self._handle(context)
            response = replace(response, text=redact(response.text, truncate=False))
            # Every returned response is represented exactly once, including
            # degraded paths. Bulk Memory data stays out of future model context.
            content = ("Memory control result delivered." if response.tool_executed
                       and is_memory_command(context.current_user_text) else response.text)
            self._conversation.append(context.owner_id, context.workspace_id, context.conversation_id,
                                      role="assistant", content=content)
            return response
        finally:
            with self._idle:
                self._active -= 1
                self._idle.notify_all()

    def shutdown(self) -> None:
        with self._idle:
            self._stopping = True
            self._idle.wait_for(lambda: self._active == 0)

    def _handle(self, context: RequestContext) -> CoreResponse:
        history = self._conversation.history(context.owner_id, context.workspace_id, context.conversation_id)
        self._conversation.append(
            context.owner_id, context.workspace_id, context.conversation_id,
            role="user", content=("Memory control request." if is_memory_command(context.current_user_text)
                                  else redact(context.current_user_text, truncate=False)),
        )
        if is_memory_command(context.current_user_text):
            try:
                proposal = parse_memory_command(context.current_user_text)
            except (ValueError, TypeError, RecursionError):
                return CoreResponse(context.request_id, "Invalid Memory command. Use /memory <action> <JSON object>.", degraded=True)
            return self._dispatch_proposal(context, proposal, "memory")
        decision = self._router.route(context.current_user_text)
        messages = [{"role": item.role, "content": item.content} for item in history[-97:]]
        messages.append({"role": "user", "content": context.current_user_text})
        if self._memory is not None:
            try:
                recalled = self._memory.recall(
                    context.owner_id, context.workspace_id, context.current_user_text,
                    limit=self._recall_limit, min_confidence=self._min_confidence,
                )
            except Exception:
                return CoreResponse(context.request_id, "Memory is temporarily unavailable.", degraded=True)
            if recalled:
                messages.insert(0, memory_data_message([item.content for item in recalled]))
        try:
            catalog = self._dispatcher.registry.catalog() if self._dispatcher else ()
            catalog = tuple(item for item in catalog if item["name"] not in MEMORY_TOOL_NAMES)
            messages.insert(0, {"role": "system", "content": core_system_instruction(catalog)})
            result = self._gateway.generate(
                ModelRequest(request_id=context.request_id, messages=tuple(messages), max_output_tokens=self._max_output_tokens,
                             provider_capability=decision.provider_capability)
            )
        except (BudgetExceeded, ProviderError, ValueError):
            text = "Arona is temporarily unavailable because the model service or budget is unavailable."
            return CoreResponse(context.request_id, text, degraded=True)
        if result.tool_proposal is not None:
            return self._handle_proposal(context, result, decision.provider_capability)
        return CoreResponse(context.request_id, result.text)

    def _handle_proposal(self, context: RequestContext, result: GatewayResponse, scope: str) -> CoreResponse:
        proposal = result.tool_proposal
        if proposal is not None and proposal.tool_name in MEMORY_TOOL_NAMES:
            return CoreResponse(context.request_id, "Memory controls require an explicit /memory command from you.", degraded=True)
        return self._dispatch_proposal(context, proposal, scope)

    def _dispatch_proposal(self, context: RequestContext, proposal: ToolProposal | None, scope: str) -> CoreResponse:
        if proposal is None or self._dispatcher is None:
            return CoreResponse(context.request_id, "The requested action is unavailable.", degraded=True)
        metadata = self._dispatcher.registry.get(proposal.tool_name)
        invocation = ToolInvocation(
            request_id=context.request_id,
            tool_name=proposal.tool_name,
            action=proposal.action,
            arguments=proposal.arguments,
            owner_id=context.owner_id,
            workspace_id=context.workspace_id,
            scope=proposal.scope or scope,
            resource=proposal.resource,
            # Model output never supplies a permission level. Registry metadata
            # is still evaluated independently by Policy before dispatch.
            requested_permission=metadata.permission if metadata else PermissionLevel.P0,
        )
        try:
            policy_request = PolicyRequest(**invocation.model_dump())
            decision = self._dispatcher.policy.decide(policy_request)
            if decision.status.value != "allowed":
                return CoreResponse(context.request_id, "I cannot perform that action.", degraded=True)
            tool_result = self._dispatcher.dispatch(
                invocation, TrustedContext(context.request_id, context.owner_id, context.workspace_id), decision,
            )
        except (OSError, ValueError, RuntimeError):
            return CoreResponse(context.request_id, "The action could not be completed.", degraded=True)
        text = str(tool_result) if tool_result is not None else "The action completed."
        return CoreResponse(context.request_id, text, tool_executed=True)
