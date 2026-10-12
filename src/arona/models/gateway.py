from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import math
from threading import Condition, Lock
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from arona.models.budget import BudgetStore
from arona.prompts.contracts import ToolProposal


class ProviderError(RuntimeError):
    """A provider failed without exposing provider response data."""


class BudgetExceeded(RuntimeError):
    pass


class ModelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    request_id: str = Field(min_length=1, max_length=200)
    messages: tuple[dict[str, str], ...] = Field(min_length=1, max_length=100)
    max_output_tokens: int = Field(gt=0, le=100_000, strict=True)
    provider_capability: Literal["chat", "code"] = "chat"

    @model_validator(mode="after")
    def validate_messages(self):
        for message in self.messages:
            if set(message) != {"role", "content"} or message["role"] not in {"system", "user", "assistant"}:
                raise ValueError("Invalid model message")
            if not message["content"].strip() or len(message["content"]) > 100_000:
                raise ValueError("Invalid model message content")
        return self


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False,
                              hide_input_in_errors=True, revalidate_instances="always")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class GatewayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    text: str = Field(default="", max_length=100_000)
    tool_proposal: ToolProposal | None = None
    usage: Usage

    @model_validator(mode="after")
    def require_content(self):
        if not self.text.strip() and self.tool_proposal is None:
            raise ValueError("Model response must contain text or a tool proposal")
        return self


class ProviderHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str
    model: str
    failures: int = 0
    last_failure: datetime | None = None
    cooldown_until: datetime | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def available(self) -> bool:
        return self.cooldown_until is None or self.cooldown_until <= self.checked_at


@dataclass
class _ProviderState:
    failures: int = 0
    last_failure: datetime | None = None
    cooldown_until: datetime | None = None


class ModelGateway:
    """The only provider entrypoint used by Core."""

    def __init__(
        self,
        providers: tuple[object, ...],
        *,
        daily_token_budget: int = 100_000,
        daily_cost_budget: float = 100.0,
        request_token_budget: int = 8_000,
        request_cost_budget: float | None = None,
        failure_threshold: int = 3,
        cooldown_seconds: int = 300,
        clock: Callable[[], datetime] | None = None,
        budget_store: BudgetStore | None = None,
    ) -> None:
        providers = tuple(providers)
        if not providers or any(not getattr(item, "name", "") or not getattr(item, "model", "") for item in providers):
            raise ValueError("At least one named provider is required")
        if len({item.name for item in providers}) != len(providers):
            raise ValueError("Provider names must be unique")
        if any(not callable(getattr(item, method, None)) for item in providers for method in ("generate", "estimate_usage")):
            raise ValueError("Providers must expose generate and a conservative usage estimate")
        if any(type(value) is not int or value <= 0 for value in (daily_token_budget, request_token_budget, failure_threshold, cooldown_seconds)):
            raise ValueError("Token and health limits must be positive integers")
        request_cost_budget = daily_cost_budget if request_cost_budget is None else request_cost_budget
        if any(isinstance(value, bool) or not math.isfinite(value) or value <= 0 for value in (daily_cost_budget, request_cost_budget)):
            raise ValueError("Cost limits must be finite and positive")
        self._providers = providers
        # Snapshot trusted configuration; routing only reorders the same bounded
        # fallback pool. Legacy injected providers retain their chat default.
        capabilities = {item.name: getattr(item, "capability", "chat") for item in providers}
        if any(value not in ("chat", "code") for value in capabilities.values()):
            raise ValueError("Invalid provider capability")
        self._routes = {
            capability: tuple(sorted(providers, key=lambda item: capabilities[item.name] != capability))
            for capability in ("chat", "code")
        }
        self._states = {item.name: _ProviderState() for item in providers}
        self._daily_token_budget = daily_token_budget
        self._daily_cost_budget = daily_cost_budget
        self._request_token_budget = request_token_budget
        self._request_cost_budget = request_cost_budget
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._used_input_tokens = 0
        self._used_output_tokens = 0
        self._used_cost = 0.0
        self._charged_input = self._charged_output = 0
        self._charged_cost = 0.0
        self._accounting_failed = False
        self._day = self._clock().date()
        self._lock = Lock()
        self._idle = Condition(self._lock)
        self._active = 0
        self._closing = self._closed = False
        self._budget_store = budget_store
        if budget_store is not None:
            try:
                state = budget_store.load()
                if state is not None:
                    if set(state) != {"version", "day", "reported", "charged", "accounting_failed"} or type(state["version"]) is not int or state["version"] != 1 or type(state["accounting_failed"]) is not bool:
                        raise ValueError
                    reported = Usage.model_validate(state["reported"])
                    charged = Usage.model_validate(state["charged"])
                    if charged.total_tokens < reported.total_tokens or charged.estimated_cost + 1e-9 < reported.estimated_cost:
                        raise ValueError
                    self._day = date.fromisoformat(state["day"])
                    self._used_input_tokens, self._used_output_tokens, self._used_cost = reported.input_tokens, reported.output_tokens, reported.estimated_cost
                    self._charged_input, self._charged_output, self._charged_cost = charged.input_tokens, charged.output_tokens, charged.estimated_cost
                    self._accounting_failed = state["accounting_failed"]
                self._reset_day()
                self._save_budget()
            except BaseException as exc:
                budget_store.close()
                if not isinstance(exc, Exception):
                    raise
                raise ValueError("Budget state could not be initialized safely") from None

    def generate(self, request: ModelRequest) -> GatewayResponse:
        try:
            if type(request) is not ModelRequest:
                raise ValueError
            request = ModelRequest.model_validate(deepcopy(vars(request)))
        except Exception:
            raise ProviderError("Invalid model request") from None
        with self._lock:
            if self._closing:
                raise ProviderError("Model gateway is stopping")
            self._reset_day()
            self._active += 1
        try:
            return self._generate(request)
        finally:
            with self._idle:
                self._active -= 1
                self._idle.notify_all()

    def _generate(self, request: ModelRequest) -> GatewayResponse:
        request_tokens, request_cost = 0, 0.0
        for provider in self._routes[request.provider_capability]:
            with self._lock:
                if not self._available(provider.name):
                    continue
            try:
                estimate = provider.estimate_usage(request.model_copy(deep=True))
                if type(estimate) is not Usage:
                    raise ValueError
                estimate = Usage.model_validate(deepcopy(vars(estimate)))
                if estimate.output_tokens < request.max_output_tokens:
                    raise ValueError
            except Exception:
                self._record_failure(provider.name)
                continue
            # Reserve the complete attempt, including input and potential output,
            # atomically before I/O. A timeout may already have incurred billing.
            with self._lock:
                if (self._accounting_failed
                    or request_tokens + estimate.total_tokens > self._request_token_budget
                    or request_cost + estimate.estimated_cost > self._request_cost_budget
                    or self._charged_input + self._charged_output + estimate.total_tokens > self._daily_token_budget
                    or self._charged_cost + estimate.estimated_cost > self._daily_cost_budget):
                    raise BudgetExceeded("Model budget exhausted before provider call")
                self._charged_input += estimate.input_tokens
                self._charged_output += estimate.output_tokens
                self._charged_cost += estimate.estimated_cost
                self._save_budget()
            request_tokens += estimate.total_tokens
            request_cost += estimate.estimated_cost
            try:
                response = provider.generate(request.model_copy(deep=True))
                if type(response) is not GatewayResponse:
                    raise ProviderError("Provider returned an invalid response")
                response = GatewayResponse.model_validate(deepcopy(vars(response)))
            except Exception:
                self._record_failure(provider.name)
                continue
            self._record_success(provider.name, response.usage, estimate)
            return response
        raise ProviderError("All configured model providers are unavailable") from None

    def health(self) -> tuple[ProviderHealth, ...]:
        with self._lock:
            return tuple(
                ProviderHealth(
                    provider=item.name, model=item.model, failures=self._states[item.name].failures,
                    last_failure=self._states[item.name].last_failure,
                    cooldown_until=self._states[item.name].cooldown_until,
                    checked_at=self._clock(),
                ) for item in self._providers
            )

    @property
    def usage(self) -> Usage:
        with self._lock:
            self._reset_day()
            return Usage(input_tokens=self._used_input_tokens, output_tokens=self._used_output_tokens, estimated_cost=self._used_cost)

    @property
    def charged_usage(self) -> Usage:
        """Budget consumed, including conservative charges for unknown outcomes."""
        with self._lock:
            self._reset_day()
            return Usage(input_tokens=self._charged_input, output_tokens=self._charged_output, estimated_cost=max(0.0, self._charged_cost))

    @property
    def available(self) -> bool:
        with self._lock:
            self._reset_day()
            return (not self._closing and not self._accounting_failed
                    and self._charged_input + self._charged_output < self._daily_token_budget
                    and self._charged_cost < self._daily_cost_budget
                    and any(self._available(provider.name) for provider in self._providers))

    def _save_budget(self):
        if self._budget_store is None:
            return
        try:
            self._budget_store.save({
                "version": 1, "day": self._day.isoformat(), "accounting_failed": self._accounting_failed,
                "reported": {"input_tokens": self._used_input_tokens, "output_tokens": self._used_output_tokens, "estimated_cost": self._used_cost},
                "charged": {"input_tokens": self._charged_input, "output_tokens": self._charged_output, "estimated_cost": max(0.0, self._charged_cost)},
            })
        except Exception:
            self._accounting_failed = True
            raise BudgetExceeded("Budget state could not be persisted; provider calls blocked") from None

    def _reset_day(self) -> None:
        today = self._clock().date()
        # Never drop an in-flight reservation at midnight or on a backward clock jump.
        if today > self._day and self._active == 0:
            self._day, self._used_input_tokens, self._used_output_tokens, self._used_cost = today, 0, 0, 0.0
            self._charged_input = self._charged_output = 0
            self._charged_cost = 0.0
            self._save_budget()

    def _available(self, name: str) -> bool:
        state = self._states[name]
        return state.cooldown_until is None or state.cooldown_until <= self._clock()

    def _record_success(self, name: str, usage: Usage, estimate: Usage) -> None:
        with self._lock:
            self._used_input_tokens += usage.input_tokens
            self._used_output_tokens += usage.output_tokens
            self._used_cost += usage.estimated_cost
            self._charged_input += usage.input_tokens - estimate.input_tokens
            self._charged_output += usage.output_tokens - estimate.output_tokens
            self._charged_cost += usage.estimated_cost - estimate.estimated_cost
            if (usage.input_tokens > estimate.input_tokens or usage.output_tokens > estimate.output_tokens
                or usage.estimated_cost > estimate.estimated_cost):
                self._accounting_failed = True
                self._save_budget()
                raise BudgetExceeded("Provider exceeded its reserved usage; further calls blocked")
            self._save_budget()
            self._states[name] = _ProviderState()

    def close(self) -> None:
        with self._idle:
            if self._closing:
                self._idle.wait_for(lambda: self._closed)
                return
            self._closing = True
            self._idle.wait_for(lambda: self._active == 0)
        failed = False
        cancellation = None
        try:
            for provider in self._providers:
                try:
                    close = getattr(provider, "close", None)
                    if close is not None:
                        close()
                except BaseException as exc:
                    failed = True
                    if not isinstance(exc, Exception) and cancellation is None:
                        cancellation = exc
        finally:
            try:
                if self._budget_store is not None:
                    self._budget_store.close()
            finally:
                with self._idle:
                    self._closed = True
                    self._idle.notify_all()
        if cancellation is not None:
            raise cancellation
        if failed:
            raise ProviderError("Provider cleanup failed") from None

    def _record_failure(self, name: str) -> None:
        with self._lock:
            state = self._states[name]
            state.failures += 1
            state.last_failure = self._clock()
            if state.failures >= self._failure_threshold:
                state.cooldown_until = self._clock() + timedelta(seconds=self._cooldown_seconds)
