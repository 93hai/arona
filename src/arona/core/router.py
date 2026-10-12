from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from arona.domain.models import RequestIntent


@dataclass(frozen=True)
class RoutingDecision:
    intent: RequestIntent
    provider_capability: str
    reason: str


class Router:
    """Deterministic routing; it never grants permission or executes tools."""

    def route(self, text: str) -> RoutingDecision:
        if not isinstance(text, str) or not text.strip():
            return RoutingDecision(RequestIntent.UNKNOWN, "chat", "empty request")
        normalized = unicodedata.normalize("NFKD", text.casefold()).replace("đ", "d")
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        words = re.findall(r"\w+", normalized)
        tokens = set(words)
        if tokens.intersection({"help", "giup"}):
            return RoutingDecision(RequestIntent.HELP, "chat", "help keyword")
        if tokens.intersection({"code", "python", "function", "bug"}):
            return RoutingDecision(RequestIntent.CODE_REQUEST, "code", "code keyword")
        if tokens.intersection({"tool", "execute", "run"}) or ("thuc", "hien") in zip(words, words[1:]):
            return RoutingDecision(RequestIntent.SYSTEM_COMMAND, "chat", "action keyword")
        return RoutingDecision(RequestIntent.CHAT, "chat", "default chat")
