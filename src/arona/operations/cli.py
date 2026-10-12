from __future__ import annotations

import argparse
import logging
import ntpath
import re
import sqlite3
from pathlib import Path, PureWindowsPath

from arona.common.paths import private_directory
from arona.config.settings import SettingsError, load_settings
from arona.database.migrations import DatabaseMigrations
from arona.operations.backup import DatabaseBackupManager, validate_actor
from arona.operations.service import run_service


def database_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.db", value):
        raise argparse.ArgumentTypeError("Use a database filename, not a path")
    reserved = (
        ntpath.isreserved(value)
        if hasattr(ntpath, "isreserved")
        else PureWindowsPath(value).is_reserved()
    )
    if reserved:
        raise argparse.ArgumentTypeError("Reserved filenames are not allowed")
    return value


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Arona 3.0 COMPLETE CANDIDATE: Core service and offline maintenance"
    )
    parser.add_argument("--env-file", type=Path, help="Explicit development dotenv file")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-config", help="Validate configuration without creating runtime data")
    commands.add_parser("serve", help="Start the Core service")
    for command in ("migrate", "backup", "restore"):
        sub = commands.add_parser(command)
        sub.add_argument(
            "--operator-id", required=True, help="Local operator recorded in maintenance audit"
        )
        if command == "restore":
            sub.add_argument("--backup-name", required=True, type=database_name)
            sub.add_argument("--restore-name", required=True, type=database_name)
    args = parser.parse_args(argv)
    logger = logging.getLogger("arona.maintenance")
    try:
        settings = load_settings(args.env_file)
        logging.getLogger().setLevel(settings.log_level)
        if args.command == "check-config":
            logger.info("Configuration validated; serve checks provider pricing and Core startup readiness")
            return 0
        if args.command == "serve":
            return run_service(settings)
        validate_actor(args.operator_id)
        if args.command == "migrate":
            private_directory(settings.database_path.parent)
            DatabaseMigrations(
                settings.database_path, backup_dir=settings.backup_dir, actor_id=args.operator_id
            ).run_migrations()
            logger.info("Database schema validated")
        elif args.command == "backup":
            artifact = DatabaseBackupManager(
                settings.backup_dir, actor_id=args.operator_id
            ).create_backup(settings.database_path)
            logger.info("Verified backup created: %s", artifact.name)
        elif args.command == "restore":
            DatabaseBackupManager(settings.backup_dir, actor_id=args.operator_id).restore_backup(
                settings.backup_dir / args.backup_name,
                settings.database_path.parent / args.restore_name,
            )
            logger.info("Restore created a new database; live database was not replaced")
        return 0
    except SettingsError as exc:
        logger.error("%s", exc)
        return 1
    except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
        # The raw exception may contain a path, SQL values or configuration data.
        logger.error(
            "Maintenance failed (%s); check configuration, schema and directory permissions",
            type(exc).__name__,
        )
        return 2
