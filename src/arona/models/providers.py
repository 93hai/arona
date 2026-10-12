from __future__ import annotations

from typing import Protocol

from arona.models.gateway import GatewayResponse, ModelRequest, Usage


class ModelProvider(Protocol):
    name: str
    model: str

    def generate(self, request: ModelRequest) -> GatewayResponse: ...

    def estimate_usage(self, request: ModelRequest) -> Usage: ...
