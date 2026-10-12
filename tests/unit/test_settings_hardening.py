import sys

import pytest
from pydantic import ValidationError

from arona.config.settings import SettingsError, load_settings


def test_public_settings_rejects_production_dotenv(settings_factory, monkeypatch, tmp_path):
    path = tmp_path / "development.env"
    path.write_text("ARONA_DAILY_TOKEN_BUDGET=137\n", encoding="utf-8")
    monkeypatch.setattr("arona.config.settings.sys.platform", "linux")
    monkeypatch.setattr("arona.config.settings.os.geteuid", lambda: 1001, raising=False)
    with pytest.raises(SettingsError, match="dotenv"):
        settings_factory(environment="production", _env_file=path)


def test_dotenv_cannot_select_production(configured_environment, monkeypatch, tmp_path):
    monkeypatch.delenv("ARONA_ENV")
    monkeypatch.setattr("arona.config.settings.sys.platform", "linux")
    monkeypatch.setattr("arona.config.settings.os.geteuid", lambda: 1001, raising=False)
    path = tmp_path / "development.env"
    path.write_text("ARONA_ENV=production\n", encoding="utf-8")
    with pytest.raises(SettingsError, match="dotenv"):
        load_settings(path)


@pytest.mark.parametrize("position", ["candidate", "parent", "child"])
def test_candidate_cannot_be_importable(settings_factory, tmp_path, monkeypatch, position):
    candidate = tmp_path / "runtime/candidate"
    entry = {"candidate": candidate, "parent": candidate.parent, "child": candidate / "package"}[position]
    monkeypatch.setattr(sys, "path", [*sys.path, str(entry)])
    with pytest.raises(ValidationError, match="import path"):
        settings_factory()


@pytest.mark.parametrize("field,value", [
    ("max_request_tokens", 100000), ("provider_failure_threshold", 10),
    ("memory_min_confidence", 0), ("memory_min_confidence", 1),
    ("daily_cost_budget", 10000), ("provider_cooldown_seconds", 3600),
    ("mistral_timeout_seconds", 120),
])
def test_each_upper_bound_is_accepted_independently(settings_factory, field, value):
    settings = settings_factory(**{field: value})
    assert getattr(settings, field) == value


def test_all_scalar_env_mappings(configured_environment, monkeypatch):
    from arona.config.settings import Settings
    values = {
        "log_level": "WARNING", "auto_memory_enabled": "true",
        "gemini_model": "configured-gemini", "mistral_general_model": "configured-general",
        "mistral_code_model": "configured-code", "mistral_timeout_seconds": "19",
        "daily_token_budget": "12345", "daily_cost_budget": "1.25",
        "max_request_tokens": "777", "provider_failure_threshold": "4",
        "provider_cooldown_seconds": "78", "memory_recall_limit": "7",
        "memory_min_confidence": "0.81", "memory_default_ttl_seconds": "1200",
        "allowed_user_ids": "1,2", "allowed_channel_ids": "3,4",
    }
    for name, value in values.items():
        monkeypatch.setenv(Settings.model_fields[name].validation_alias, value)
    settings = load_settings()
    for name, value in values.items():
        actual = getattr(settings, name)
        if name.startswith("allowed_"):
            assert actual == frozenset(map(int, value.split(",")))
        elif isinstance(actual, bool):
            assert actual is True
        elif isinstance(actual, (float, int)):
            assert actual == float(value)
        else:
            assert actual == value


def test_invalid_allowlist_env_does_not_echo_input(configured_environment, monkeypatch, caplog):
    marker = "synthetic-private-value"
    monkeypatch.setenv("ALLOWED_USER_IDS", "1," + marker)
    with pytest.raises(SettingsError) as caught:
        load_settings()
    assert marker not in str(caught.value)
    assert marker not in caplog.text
