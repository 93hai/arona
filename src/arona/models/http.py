from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
import math
import re
from typing import Literal, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from arona.models.gateway import GatewayResponse, ModelRequest, ProviderError, Usage
from arona.prompts.contracts import AUTHORITY_FIELDS, ModelOutput


class HttpTransport(Protocol):
    def __call__(self, request: Request, timeout: float) -> tuple[int, bytes]: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urlopen_transport(request: Request, timeout: float) -> tuple[int, bytes]:
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return response.status, response.read(2_000_001)
    except HTTPError as exc:
        exc.close()
        raise ProviderError("Provider HTTP request failed") from None
    except (URLError, TimeoutError, OSError):
        raise ProviderError("Provider network request failed") from None


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    model: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 45.0
    input_cost_per_million: float | None = None
    output_cost_per_million: float | None = None
    capability: Literal["chat", "code"] = "chat"


class JsonHttpProvider:
    def __init__(self, config: ProviderConfig, *, transport: HttpTransport = urlopen_transport) -> None:
        if not config.name or not config.model or not config.api_key:
            raise ValueError("Provider name, model and API key are required")
        if not math.isfinite(config.timeout_seconds) or not 0 < config.timeout_seconds <= 120:
            raise ValueError("Provider timeout must be positive")
        if not re.fullmatch(r"[A-Za-z0-9._-]+", config.model):
            raise ValueError("Invalid provider model identifier")
        if any(value is None or isinstance(value, bool) or not math.isfinite(value) or value < 0
               for value in (config.input_cost_per_million, config.output_cost_per_million)):
            raise ValueError("Explicit finite model token prices are required")
        self.name = config.name
        self.model = config.model
        if config.capability not in ("chat", "code"):
            raise ValueError("Invalid provider capability")
        self.capability = config.capability
        self._api_key = config.api_key
        self._timeout = config.timeout_seconds
        self._transport = transport
        self._input_price = config.input_cost_per_million
        self._output_price = config.output_cost_per_million

    def _cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self._input_price + output_tokens * self._output_price) / 1_000_000

    def estimate_usage(self, request: ModelRequest) -> Usage:
        # Text-only conservative byte bound, including message framing. No network
        # token-count request is needed before the budget reservation.
        input_bound = len(json.dumps(self._messages(request), ensure_ascii=True).encode("utf-8")) + 32 * len(request.messages) + 32
        return Usage(input_tokens=input_bound, output_tokens=request.max_output_tokens,
                     estimated_cost=self._cost(input_bound, request.max_output_tokens))

    def generate(self, request: ModelRequest) -> GatewayResponse:
        try:
            status, raw = self._transport(self._request(request), self._timeout)
            if status < 200 or status >= 300 or len(raw) > 2_000_000:
                raise ProviderError("Provider returned an invalid HTTP response")
            payload = json.loads(raw)
            return self._parse_response(payload)
        except Exception:
            raise ProviderError("Provider returned a malformed response or failed") from None

    def _request(self, request: ModelRequest) -> Request:
        raise NotImplementedError

    def _parse_response(self, payload: object) -> GatewayResponse:
        raise NotImplementedError

    @staticmethod
    def _messages(request: ModelRequest) -> list[dict[str, str]]:
        return [dict(message) for message in request.messages]

    def _usage(self, payload: Mapping[str, object], *, input_key: str, output_key: str) -> Usage:
        usage = payload.get("usage")
        if not isinstance(usage, Mapping):
            raise ProviderError("Provider returned no usage metadata")
        input_tokens = usage.get(input_key)
        output_tokens = usage.get(output_key)
        if type(input_tokens) is not int or type(output_tokens) is not int or min(input_tokens, output_tokens) < 0:
            raise ProviderError("Provider returned invalid usage metadata")
        return Usage(input_tokens=input_tokens, output_tokens=output_tokens,
                     estimated_cost=self._cost(input_tokens, output_tokens))

    @staticmethod
    def _response(text: object, usage: Usage) -> GatewayResponse:
        if not isinstance(text, str):
            raise ProviderError("Provider returned invalid text")
        try:
            structured = json.loads(text)
        except json.JSONDecodeError:
            return GatewayResponse(text=text, usage=usage)
        if not isinstance(structured, Mapping):
            return GatewayResponse(text=text, usage=usage)
        if AUTHORITY_FIELDS.intersection(structured):
            raise ProviderError("Model output cannot declare authority")
        if not {"text", "tool_proposal"}.intersection(structured):
            return GatewayResponse(text=text, usage=usage)
        output = ModelOutput.model_validate(structured)
        return GatewayResponse(text=output.text, tool_proposal=output.tool_proposal, usage=usage)


class GeminiProvider(JsonHttpProvider):
    def _request(self, request: ModelRequest) -> Request:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        contents = []
        system_parts = []
        for message in self._messages(request):
            part = {"text": message["content"]}
            if message["role"] == "system":
                system_parts.append(part)
                continue
            role = "model" if message["role"] == "assistant" else "user"
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": role, "parts": [part]})
        if not contents:
            raise ProviderError("Gemini requires conversation content")
        body = {"contents": contents,
                "generationConfig": {"maxOutputTokens": request.max_output_tokens, "candidateCount": 1}}
        if system_parts:
            body["systemInstruction"] = {"parts": system_parts}
        return Request(url, data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key}, method="POST")

    def _parse_response(self, payload: object) -> GatewayResponse:
        if not isinstance(payload, Mapping):
            raise ProviderError("Provider returned malformed response")
        candidates = payload.get("candidates")
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], Mapping):
            raise ProviderError("Provider returned no candidates")
        content = candidates[0].get("content")
        if not isinstance(content, Mapping):
            raise ProviderError("Provider returned no content")
        parts = content.get("parts", [])
        if not isinstance(parts, list) or not parts or not isinstance(parts[0], Mapping):
            raise ProviderError("Provider returned no text")
        metadata = payload.get("usageMetadata")
        if not isinstance(metadata, Mapping):
            raise ProviderError("Provider returned no usage")
        usage = self._usage({"usage": metadata}, input_key="promptTokenCount", output_key="candidatesTokenCount")
        thoughts = metadata.get("thoughtsTokenCount", 0)
        total = metadata.get("totalTokenCount", usage.total_tokens + thoughts if type(thoughts) is int else None)
        if type(thoughts) is not int or thoughts < 0 or type(total) is not int or total < usage.total_tokens + thoughts:
            raise ProviderError("Provider returned inconsistent usage")
        usage = Usage(input_tokens=usage.input_tokens, output_tokens=total - usage.input_tokens,
                      estimated_cost=self._cost(usage.input_tokens, total - usage.input_tokens))
        texts = [part["text"] for part in parts if isinstance(part, Mapping) and part.get("thought") is not True and isinstance(part.get("text"), str)]
        return self._response("".join(texts), usage)


class MistralProvider(JsonHttpProvider):
    def __init__(self, config: ProviderConfig, *, endpoint: str, transport: HttpTransport = urlopen_transport) -> None:
        self._endpoint = endpoint
        super().__init__(config, transport=transport)

    def _request(self, request: ModelRequest) -> Request:
        body = {"model": self.model, "messages": self._messages(request), "max_tokens": request.max_output_tokens}
        return Request(
            self._endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )

    def _parse_response(self, payload: object) -> GatewayResponse:
        if not isinstance(payload, Mapping):
            raise ProviderError("Provider returned malformed response")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise ProviderError("Provider returned no choices")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise ProviderError("Provider returned no message")
        usage = self._usage(payload, input_key="prompt_tokens", output_key="completion_tokens")
        total = payload["usage"].get("total_tokens", usage.total_tokens)
        if type(total) is not int or total != usage.total_tokens:
            raise ProviderError("Provider returned inconsistent usage")
        return self._response(self._content_text(message.get("content")), usage)

    @staticmethod
    def _content_text(content: object) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            raise ProviderError("Provider returned invalid content")
        texts = []
        for chunk in content:
            if not isinstance(chunk, Mapping):
                raise ProviderError("Provider returned invalid content chunk")
            if chunk.get("type") == "text" and isinstance(chunk.get("text"), str):
                texts.append(chunk["text"])
            elif chunk.get("type") == "thinking":
                thinking = chunk.get("thinking")
                if not isinstance(thinking, list) or any(
                    not isinstance(part, Mapping) or part.get("type") != "text"
                    or not isinstance(part.get("text"), str) for part in thinking
                ):
                    raise ProviderError("Provider returned invalid thinking chunk")
                # Reasoning is not user output or an actionable JSON proposal.
            else:
                raise ProviderError("Provider returned unsupported content chunk")
        return "".join(texts)
