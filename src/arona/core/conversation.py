from __future__ import annotations

from dataclasses import dataclass
from threading import Lock


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    content: str


class ConversationStore:
    def __init__(self, *, history_limit: int = 20) -> None:
        if type(history_limit) is not int or history_limit < 1:
            raise ValueError("History limit must be positive")
        self.history_limit = history_limit
        self._messages: dict[tuple[str, str, str], list[ConversationMessage]] = {}
        self._lock = Lock()

    def append(self, owner_id: str, workspace_id: str, conversation_id: str, *, role: str, content: str) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (owner_id, workspace_id, conversation_id, role, content)):
            raise ValueError("Conversation identifiers and content are required")
        key = (owner_id, workspace_id, conversation_id)
        with self._lock:
            messages = self._messages.setdefault(key, [])
            messages.append(ConversationMessage(role=role, content=content))
            del messages[:-self.history_limit]

    def history(self, owner_id: str, workspace_id: str, conversation_id: str) -> tuple[ConversationMessage, ...]:
        with self._lock:
            return tuple(self._messages.get((owner_id, workspace_id, conversation_id), ()))