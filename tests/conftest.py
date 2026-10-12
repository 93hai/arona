from __future__ import annotations

import pytest

from arona.config.settings import Settings


@pytest.fixture
def settings_factory(tmp_path, monkeypatch):
    # Never consume a developer's real provider credentials in tests.
    for name, field in Settings.model_fields.items():
        monkeypatch.delenv(name, raising=False)
        if isinstance(field.validation_alias, str):
            monkeypatch.delenv(field.validation_alias, raising=False)

    def make(**overrides):
        values = dict(
            environment="development",
            data_dir=tmp_path / "runtime",
            database_path=tmp_path / "runtime/memory/arona.db",
            backup_dir=tmp_path / "runtime/backups",
            candidate_dir=tmp_path / "runtime/candidate",
            state_dir=tmp_path / "runtime/state",
            log_dir=tmp_path / "logs",
            runtime_source_dir=tmp_path / "source",
        )
        values.update(overrides)
        return Settings(**values)

    return make


@pytest.fixture
def configured_environment(settings_factory, monkeypatch):
    settings = settings_factory()
    for name in (
        "environment",
        "data_dir",
        "database_path",
        "backup_dir",
        "candidate_dir",
        "state_dir",
        "log_dir",
        "runtime_source_dir",
    ):
        monkeypatch.setenv(
            Settings.model_fields[name].validation_alias, str(getattr(settings, name))
        )
    return settings
