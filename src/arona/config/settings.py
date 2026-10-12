from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    SettingsConfigDict,
    SettingsError as SourceError,
)

from arona.common.paths import checked_path, overlaps
from arona.config import constants as limits

IdSet = Annotated[frozenset[int], NoDecode]


class Settings(BaseSettings):
    """Environment aliases are explicit. Development .env loading is opt-in."""

    def __init__(self, **values: Any) -> None:
        env_file = values.get("_env_file", self.model_config.get("env_file"))
        environment = values.get("ARONA_ENV", values.get("environment", os.getenv("ARONA_ENV")))
        if env_file and environment == "production":
            raise SettingsError("Production must not load dotenv files")
        super().__init__(**values)
        if env_file and self.environment == "production":
            raise SettingsError("Production must not load dotenv files")

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        case_sensitive=True,
        hide_input_in_errors=True,
        allow_inf_nan=False,
        frozen=True,
    )

    environment: Literal["development", "production"] = Field(
        "development", validation_alias="ARONA_ENV"
    )
    data_dir: Path = Field(Path("/var/lib/arona"), validation_alias="ARONA_DATA_DIR")
    database_path: Path = Field(
        Path("/var/lib/arona/memory/arona_memory.db"), validation_alias="ARONA_DB_PATH"
    )
    backup_dir: Path = Field(Path("/var/lib/arona/backups"), validation_alias="ARONA_BACKUP_DIR")
    candidate_dir: Path = Field(
        Path("/var/lib/arona/candidate"), validation_alias="ARONA_CANDIDATE_DIR"
    )
    state_dir: Path = Field(Path("/var/lib/arona/state"), validation_alias="ARONA_STATE_DIR")
    log_dir: Path = Field(Path("/var/log/arona"), validation_alias="ARONA_LOG_DIR")
    runtime_source_dir: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parents[3],
        validation_alias="ARONA_RUNTIME_SOURCE_DIR",
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        "INFO", validation_alias="ARONA_LOG_LEVEL"
    )
    auto_memory_enabled: bool = Field(False, validation_alias="ARONA_AUTO_MEMORY_ENABLED")
    discord_token: SecretStr = Field(SecretStr(""), validation_alias="DISCORD_TOKEN")
    allowed_user_ids: IdSet = Field(default_factory=frozenset, validation_alias="ALLOWED_USER_IDS")
    allowed_channel_ids: IdSet = Field(
        default_factory=frozenset, validation_alias="ALLOWED_CHANNEL_IDS"
    )
    gemini_api_key: SecretStr = Field(SecretStr(""), validation_alias="GEMINI_API_KEY")
    gemini_model: str = Field("", validation_alias="GEMINI_MODEL")
    mistral_api_key: SecretStr = Field(SecretStr(""), validation_alias="MISTRAL_API_KEY")
    mistral_general_model: str = Field("", validation_alias="MISTRAL_GENERAL_MODEL")
    mistral_code_model: str = Field("", validation_alias="MISTRAL_CODE_MODEL")
    mistral_api_url: Literal["https://api.mistral.ai/v1/chat/completions"] = Field(
        "https://api.mistral.ai/v1/chat/completions", validation_alias="MISTRAL_API_URL"
    )
    mistral_timeout_seconds: float = Field(
        45, gt=0, le=120, validation_alias="MISTRAL_TIMEOUT_SECONDS"
    )
    gemini_timeout_seconds: float = Field(45, gt=0, le=120, validation_alias="GEMINI_TIMEOUT_SECONDS")
    gemini_input_cost_per_million: float | None = Field(None, ge=0, validation_alias="GEMINI_INPUT_COST_PER_MILLION")
    gemini_output_cost_per_million: float | None = Field(None, ge=0, validation_alias="GEMINI_OUTPUT_COST_PER_MILLION")
    mistral_input_cost_per_million: float | None = Field(None, ge=0, validation_alias="MISTRAL_INPUT_COST_PER_MILLION")
    mistral_output_cost_per_million: float | None = Field(None, ge=0, validation_alias="MISTRAL_OUTPUT_COST_PER_MILLION")
    codestral_input_cost_per_million: float | None = Field(None, ge=0, validation_alias="CODESTRAL_INPUT_COST_PER_MILLION")
    codestral_output_cost_per_million: float | None = Field(None, ge=0, validation_alias="CODESTRAL_OUTPUT_COST_PER_MILLION")
    daily_token_budget: int = Field(
        100_000, gt=0, le=limits.MAX_DAILY_TOKEN_BUDGET, validation_alias="ARONA_DAILY_TOKEN_BUDGET"
    )
    daily_cost_budget: float = Field(
        100, gt=0, le=limits.MAX_DAILY_COST_BUDGET, validation_alias="ARONA_DAILY_COST_BUDGET"
    )
    max_request_tokens: int = Field(
        8000, gt=0, le=limits.MAX_REQUEST_TOKENS, validation_alias="ARONA_MAX_REQUEST_TOKENS"
    )
    provider_failure_threshold: int = Field(
        3,
        ge=1,
        le=limits.MAX_PROVIDER_FAILURE_THRESHOLD,
        validation_alias="ARONA_PROVIDER_FAILURE_THRESHOLD",
    )
    provider_cooldown_seconds: int = Field(
        300,
        ge=1,
        le=limits.MAX_PROVIDER_COOLDOWN_SECONDS,
        validation_alias="ARONA_PROVIDER_COOLDOWN_SECONDS",
    )
    memory_recall_limit: int = Field(
        5, ge=1, le=limits.MAX_MEMORY_RECALL_LIMIT, validation_alias="ARONA_MEMORY_RECALL_LIMIT"
    )
    memory_min_confidence: float = Field(
        0.7, ge=0, le=1, validation_alias="ARONA_MEMORY_MIN_CONFIDENCE"
    )
    memory_default_ttl_seconds: int = Field(
        2_592_000,
        ge=1,
        le=limits.MAX_MEMORY_TTL_SECONDS,
        validation_alias="ARONA_MEMORY_DEFAULT_TTL_SECONDS",
    )

    @field_validator("allowed_user_ids", "allowed_channel_ids", mode="before")
    @classmethod
    def parse_id_set(cls, value: Any) -> frozenset[int]:
        if isinstance(value, str):
            value = [] if not value.strip() else value.split(",")
        if not isinstance(value, (set, frozenset, list, tuple)):
            raise ValueError("IDs must be a comma-separated list of positive integers")
        if any(
            isinstance(item, bool)
            or not str(item).strip().isascii()
            or not str(item).strip().isdigit()
            or int(item) <= 0
            for item in value
        ):
            raise ValueError("Every ID must be a positive integer")
        return frozenset(int(item) for item in value)

    @field_validator("discord_token", "gemini_api_key", "mistral_api_key")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        if any(char.isspace() for char in value.get_secret_value()):
            raise ValueError("Secret must not contain whitespace")
        return value

    @field_validator(
        "data_dir",
        "database_path",
        "backup_dir",
        "candidate_dir",
        "state_dir",
        "log_dir",
        "runtime_source_dir",
    )
    @classmethod
    def validate_path(cls, value: Path) -> Path:
        return checked_path(value)

    @model_validator(mode="after")
    def validate_runtime_requirements(self) -> Self:
        stores = (
            self.database_path.parent,
            self.backup_dir,
            self.candidate_dir,
            self.state_dir,
            self.log_dir,
        )
        actual_source = Path(__file__).resolve().parents[3]
        for index, path in enumerate(stores):
            if any(overlaps(path, other) for other in stores[index + 1 :]):
                raise ValueError(
                    "Memory, backup, Candidate, state and log directories must be separate"
                )
            if any(overlaps(path, source) for source in (actual_source, self.runtime_source_dir)):
                raise ValueError("Runtime data must be outside source directories")
        if self.data_dir.is_relative_to(actual_source) or self.data_dir.is_relative_to(
            self.runtime_source_dir
        ):
            raise ValueError("Data directory must be outside source directories")
        import_paths = (Path(entry).resolve() for entry in sys.path if entry)
        if any(overlaps(self.candidate_dir, entry) for entry in import_paths):
            raise ValueError("Candidate must not be reachable through the Python import path")
        if self.environment == "production":
            if sys.platform != "linux" or getattr(os, "geteuid", lambda: 0)() == 0:
                raise ValueError("Production requires Linux and a non-root account")
        return self

    def safe_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class SettingsError(Exception):
    """Safe startup error that contains field names, never input values."""


def load_settings(env_file: Path | None = None) -> Settings:
    if env_file is not None and os.environ.get("ARONA_ENV") == "production":
        raise SettingsError("Production must not load dotenv files")
    try:
        settings = Settings(_env_file=env_file)
        if env_file is not None and settings.environment == "production":
            raise SettingsError("Production must not load dotenv files")
        return settings
    except ValidationError as exc:
        # No original exception chain, input, context, repr or raw provider data.
        fields = sorted({str(e["loc"][0]) if e["loc"] else "runtime" for e in exc.errors()})
        raise SettingsError("Invalid configuration fields: " + ", ".join(fields)) from None
    except (SourceError, OSError, UnicodeError):
        raise SettingsError("Cannot read configuration source") from None
