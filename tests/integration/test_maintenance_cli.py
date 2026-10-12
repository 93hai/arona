import logging

import pytest

from arona.main import main


def test_check_config_is_readonly(configured_environment, caplog):
    with caplog.at_level(logging.INFO):
        assert main(["check-config"]) == 0
    assert not configured_environment.database_path.exists()
    assert not configured_environment.data_dir.exists()
    assert "Configuration validated" in caplog.text
    assert "serve checks provider pricing and Core startup readiness" in caplog.text
    assert "Phase 1" not in caplog.text


def test_migrate_backup_restore_cli(configured_environment):
    settings = configured_environment
    assert main(["migrate", "--operator-id", "tester"]) == 0
    assert settings.database_path.is_file()
    assert main(["backup", "--operator-id", "tester"]) == 0
    backup = next(settings.backup_dir.glob("*.db"))
    assert (
        main(
            [
                "restore",
                "--operator-id",
                "tester",
                "--backup-name",
                backup.name,
                "--restore-name",
                "recovered.db",
            ]
        )
        == 0
    )
    assert (settings.database_path.parent / "recovered.db").is_file()
    assert (
        main(
            [
                "restore",
                "--operator-id",
                "tester",
                "--backup-name",
                backup.name,
                "--restore-name",
                settings.database_path.name,
            ]
        )
        == 2
    )


def test_maintenance_requires_actor(configured_environment):
    with pytest.raises(SystemExit) as exc:
        main(["migrate"])
    assert exc.value.code == 2
    assert not configured_environment.database_path.exists()


@pytest.mark.parametrize("name", ["../bad.db", "/bad.db", "C:\\bad.db", "CON.db"])
def test_restore_path_escape_rejected(configured_environment, name):
    with pytest.raises(SystemExit):
        main(
            [
                "restore",
                "--operator-id",
                "tester",
                "--backup-name",
                name,
                "--restore-name",
                "safe.db",
            ]
        )


def test_invalid_configuration_is_safe(configured_environment, monkeypatch, caplog):
    value = "synthetic-credential invalid"
    monkeypatch.setenv("GEMINI_API_KEY", value)
    assert main(["check-config"]) == 1
    assert value not in caplog.text
