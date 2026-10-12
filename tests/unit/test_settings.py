import logging
from inspect import getfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from arona.config.settings import Settings, SettingsError, load_settings


def test_defaults_and_no_side_effects(settings_factory):
    settings = settings_factory()
    assert settings.mistral_timeout_seconds == 45
    assert settings.daily_token_budget == 100000
    assert settings.memory_default_ttl_seconds == 2592000
    assert not settings.data_dir.exists()
    assert not settings.database_path.exists()
    assert not settings.discord_token.get_secret_value()


@pytest.mark.parametrize(
    "field,value",
    [
        ("mistral_timeout_seconds", 45),
        ("daily_token_budget", 1000000),
        ("daily_cost_budget", 100),
        ("provider_cooldown_seconds", 300),
        ("memory_default_ttl_seconds", 31536000),
        ("memory_recall_limit", 20),
    ],
)
def test_independent_bounds(settings_factory, field, value):
    assert getattr(settings_factory(**{field: value}), field) == value


@pytest.mark.parametrize(
    "field,value",
    [
        ("mistral_timeout_seconds", 0),
        ("mistral_timeout_seconds", 121),
        ("daily_token_budget", 0),
        ("daily_token_budget", 1000001),
        ("daily_cost_budget", -1),
        ("daily_cost_budget", float("nan")),
        ("daily_cost_budget", float("inf")),
        ("max_request_tokens", 100001),
        ("provider_failure_threshold", 11),
        ("provider_cooldown_seconds", 3601),
        ("memory_recall_limit", 21),
        ("memory_default_ttl_seconds", 0),
        ("memory_min_confidence", 1.1),
    ],
)
def test_invalid_bounds(settings_factory, field, value):
    with pytest.raises(ValidationError):
        settings_factory(**{field: value})


@pytest.mark.parametrize("value", ["1,broken", "1,-2", "0", "1,", "1,,2", [True], [1.5]])
def test_invalid_allowlist_rejected(settings_factory, value):
    with pytest.raises(ValidationError):
        settings_factory(allowed_user_ids=value)


def test_environment_aliases(configured_environment, monkeypatch):
    monkeypatch.setenv("ARONA_DAILY_TOKEN_BUDGET", "90000")
    monkeypatch.setenv("MISTRAL_TIMEOUT_SECONDS", "47")
    monkeypatch.setenv("ALLOWED_USER_IDS", "123, 456")
    monkeypatch.setenv("ALLOWED_CHANNEL_IDS", "")
    settings = load_settings()
    assert settings.database_path == configured_environment.database_path
    assert settings.daily_token_budget == 90000
    assert settings.mistral_timeout_seconds == 47
    assert settings.allowed_user_ids == frozenset({123, 456})
    assert settings.allowed_channel_ids == frozenset()


def test_no_implicit_dotenv(configured_environment, tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ARONA_DAILY_TOKEN_BUDGET=12\n")
    monkeypatch.chdir(tmp_path)
    assert load_settings().daily_token_budget == 100000
    assert load_settings(tmp_path / ".env").daily_token_budget == 12


def test_production_never_reads_dotenv(configured_environment, monkeypatch, tmp_path):
    monkeypatch.setenv("ARONA_ENV", "production")
    with pytest.raises(SettingsError, match="dotenv"):
        load_settings(tmp_path / "not-present.env")


def test_no_credential_leak(settings_factory, configured_environment, monkeypatch, caplog):
    credential = "synthetic-test-credential"
    settings = settings_factory(gemini_api_key=credential)
    assert credential not in repr(settings)
    assert credential not in str(settings.safe_dict())
    assert credential not in settings.model_dump_json()
    monkeypatch.setenv("GEMINI_API_KEY", credential + " invalid")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SettingsError) as exc:
            load_settings()
    assert credential not in str(exc.value)
    assert credential not in caplog.text


def test_settings_does_not_configure_logging(settings_factory):
    root = logging.getLogger()
    handlers = root.handlers[:]
    settings_factory()
    assert root.handlers == handlers


@pytest.mark.parametrize("target", ["source", "source/nested"])
def test_source_overlap_blocked(settings_factory, tmp_path, target):
    with pytest.raises(ValidationError):
        settings_factory(candidate_dir=tmp_path / target)


def test_actual_source_cannot_be_overridden(settings_factory):
    actual = Path(getfile(Settings)).resolve().parents[3]
    with pytest.raises(ValidationError):
        settings_factory(candidate_dir=actual / "generated")


def test_runtime_overlap_and_relative_paths(settings_factory, tmp_path):
    with pytest.raises(ValidationError):
        settings_factory(backup_dir=tmp_path / "runtime/memory/backups")
    with pytest.raises(ValidationError):
        settings_factory(database_path=Path("local.db"))


def test_production_platform_and_root_guard(settings_factory, monkeypatch):
    monkeypatch.setattr("arona.config.settings.sys.platform", "win32")
    with pytest.raises(ValidationError):
        settings_factory(environment="production")
    monkeypatch.setattr("arona.config.settings.sys.platform", "linux")
    monkeypatch.setattr("arona.config.settings.os.geteuid", lambda: 0, raising=False)
    with pytest.raises(ValidationError):
        settings_factory(environment="production")
    monkeypatch.setattr("arona.config.settings.os.geteuid", lambda: 1001)
    assert settings_factory(environment="production").environment == "production"


def test_symlink_configuration(settings_factory, tmp_path):
    target = tmp_path / "external"
    target.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation requires OS support/permission")
    with pytest.raises(ValidationError):
        settings_factory(candidate_dir=link / "candidate")


def test_windows_junction_configuration(settings_factory, tmp_path):
    import os
    import subprocess

    if os.name != "nt":
        pytest.skip("Windows junction test")
    target = tmp_path / "junction-target"
    target.mkdir()
    link = tmp_path / "junction-link"
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, "Could not create the junction test fixture"
    with pytest.raises(ValidationError):
        settings_factory(candidate_dir=link / "candidate")
