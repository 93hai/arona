from __future__ import annotations

from datetime import datetime, timezone
from copy import deepcopy
from dataclasses import dataclass
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from arona.core.orchestrator import CoreOrchestrator, CoreResponse
from arona.domain.models import RequestContext


class InteractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    user_id: str = Field(min_length=1, max_length=200)
    workspace_id: str = Field(min_length=1, max_length=200)
    conversation_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=100_000)
    channel_id: str | None = Field(default=None, max_length=200)


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """Supplied by the trusted transport adapter, never decoded from model/user JSON."""

    user_id: str
    workspace_id: str
    channel_id: str | None = None


class InteractionGateway:
    """Minimal trusted interaction adapter; no provider or tool access."""

    def __init__(self, core: CoreOrchestrator, *, allowed_user_ids=frozenset(), allowed_channel_ids=frozenset()) -> None:
        self._core = core
        self._allowed_users = frozenset(str(value) for value in allowed_user_ids)
        self._allowed_channels = frozenset(str(value) for value in allowed_channel_ids)

    def receive(self, request: InteractionRequest, *, identity: AuthenticatedIdentity | None = None) -> CoreResponse:
        if type(identity) is not AuthenticatedIdentity:
            raise PermissionError("Trusted transport identity is required")
        try:
            if type(request) is not InteractionRequest:
                raise ValueError
            request = InteractionRequest.model_validate(deepcopy(vars(request)))
        except Exception:
            raise ValueError("A normalized interaction request is required") from None
        if (request.user_id, request.workspace_id, request.channel_id) != (identity.user_id, identity.workspace_id, identity.channel_id):
            raise PermissionError("Interaction identity does not match the authenticated transport")
        if not self._allowed_users or identity.user_id not in self._allowed_users:
            raise PermissionError("Interaction user is not allowed")
        if self._allowed_channels and request.channel_id not in self._allowed_channels:
            raise PermissionError("Interaction channel is not allowed")
        context = RequestContext(
            request_id=f"interaction-{uuid4().hex}", owner_id=identity.user_id,
            workspace_id=identity.workspace_id, conversation_id=request.conversation_id,
            channel_id=identity.channel_id, timestamp=datetime.now(timezone.utc),
            current_user_text=request.text,
        )
        return self._core.handle(context)
