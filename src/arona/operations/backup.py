from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from arona.common.paths import checked_path, private_directory
from arona.database.schema import validate_schema
from arona.memory.db import DatabaseConnection


def validate_actor(actor_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", actor_id):
        raise ValueError("A nonempty local maintenance actor ID is required")
    return actor_id


class DatabaseBackupManager:
    """Verified SQLite snapshots. Restore ONLY to a new, unused database path."""

    def __init__(self, backup_dir: Path, *, actor_id: str = "local-maintenance") -> None:
        self.backup_dir = checked_path(backup_dir)
        self.actor_id = validate_actor(actor_id)

    def verify_database(self, database_path: Path) -> bool:
        path = checked_path(database_path)
        if not path.is_file():
            return False
        try:
            with closing(DatabaseConnection(path).connect(readonly=True)) as conn:
                if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
                    return False
                validate_schema(conn, allow_legacy=True)
                return True
        except (sqlite3.Error, ValueError):
            return False

    def _copy(self, source: Path, destination: Path) -> None:
        deadline = time.monotonic() + 30

        def progress(status: int, remaining: int, total: int) -> None:
            if time.monotonic() > deadline:
                raise TimeoutError("Database snapshot timed out")

        with closing(DatabaseConnection(source).connect(readonly=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst, pages=256, progress=progress, sleep=0.05)
                # A published snapshot must not depend on WAL/shm files. The
                # backup API also copies the source's persistent journal mode.
                if dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise ValueError("Cannot make snapshot self-contained")
        if not self.verify_database(destination):
            raise ValueError("Snapshot integrity or Arona schema validation failed")
        with destination.open("r+b") as stream:
            os.fsync(stream.fileno())

    def _receipt(self, path: Path, action: str) -> None:
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        receipt = path.with_name(path.name + ".receipt.json")
        data = {
            "actor_id": self.actor_id,
            "scope": "database",
            "action": action,
            "artifact": path.name,
            "sha256": digest,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        fd = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            receipt.unlink(missing_ok=True)
            raise

    def _publish(self, temporary: Path, destination: Path, action: str) -> None:
        receipt = destination.with_name(destination.name + ".receipt.json")
        receipt_existed = os.path.lexists(receipt)
        if receipt_existed:
            raise FileExistsError("An existing receipt must not be replaced")
        os.link(temporary, destination)
        try:
            self._receipt(destination, action)
        except BaseException:
            # Only remove the file this call published, never an existing target.
            destination.unlink(missing_ok=True)
            if not receipt_existed:
                receipt.unlink(missing_ok=True)
            raise

    def _verify_receipt(self, source: Path) -> str:
        if any(os.path.lexists(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")):
            raise ValueError("Backup must be self-contained without SQLite sidecars")
        receipt = checked_path(source.with_name(source.name + ".receipt.json"))
        try:
            with receipt.open("rb") as stream:
                payload = stream.read(4097)
            if len(payload) > 4096:
                raise ValueError
            data = json.loads(payload)
            if not isinstance(data, dict) or data.get("scope") != "database":
                raise ValueError
            if data.get("artifact") != source.name or data.get("action") != "backup":
                raise ValueError
            validate_actor(data.get("actor_id", ""))
            digest = data.get("sha256", "")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError
            created = datetime.fromisoformat(data["created_at"])
            if created.tzinfo is None:
                raise ValueError
            with source.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                    raise ValueError
        except (OSError, ValueError, TypeError, KeyError):
            raise ValueError("Backup receipt is missing, invalid or does not match the snapshot") from None
        return digest

    def create_backup(self, database_path: Path) -> Path:
        source = checked_path(database_path)
        if source.is_relative_to(self.backup_dir) or source.parent == self.backup_dir:
            raise ValueError("Backups must be separate from the live database")
        if not self.verify_database(source):
            raise ValueError("Source is not a supported, intact Arona database")
        directory = private_directory(self.backup_dir)
        destination = directory / ("arona_backup_" + uuid4().hex + ".db")
        fd, name = tempfile.mkstemp(prefix=".snapshot-", suffix=".tmp", dir=directory)
        os.close(fd)
        temporary = Path(name)
        try:
            self._copy(source, temporary)
            # Hard-link publication is atomic and refuses to overwrite an existing path.
            self._publish(temporary, destination, "backup")
            return destination
        finally:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)

    def restore_backup(
        self, backup_path: Path, database_path: Path, allow_replace: bool = False
    ) -> None:
        source = checked_path(backup_path)
        destination = checked_path(database_path)
        if source.parent != self.backup_dir:
            raise ValueError("Restore source must be inside the configured backup directory")
        if allow_replace:
            raise ValueError("Replacing a live database is prohibited; restore to a new path")
        if destination.exists() or any(
            os.path.lexists(str(destination) + suffix) for suffix in ("-wal", "-shm", "-journal")
        ):
            raise ValueError("Restore destination must be unused, including SQLite sidecars")
        if destination.is_relative_to(self.backup_dir):
            raise ValueError("Restore destination must be outside the backup directory")
        digest = self._verify_receipt(source)
        if not self.verify_database(source):
            raise ValueError("Backup is not a supported, intact Arona database")
        directory = private_directory(destination.parent)
        fd, name = tempfile.mkstemp(prefix=".restore-", suffix=".tmp", dir=directory)
        os.close(fd)
        temporary = Path(name)
        try:
            self._copy(source, temporary)
            if self._verify_receipt(source) != digest:
                raise ValueError("Backup changed during restore")
            self._publish(temporary, destination, "restore")
        finally:
            for suffix in ("", "-wal", "-shm", "-journal"):
                Path(str(temporary) + suffix).unlink(missing_ok=True)
