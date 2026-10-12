"""Durable daily budget state, exclusively owned by one running application."""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile

from arona.common.paths import checked_path, private_directory


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate budget state field")
        result[key] = value
    return result


class BudgetStore:
    def __init__(self, path: Path):
        self.path = checked_path(path)
        private_directory(self.path.parent)
        lock_path = checked_path(path.with_suffix(".lock"))
        self._fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            info = os.fstat(self._fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("Invalid budget lock file")
            if os.name == "nt":
                import msvcrt
                if info.st_size == 0:
                    os.write(self._fd, b"0")
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as exc:
            self.close()
            if not isinstance(exc, Exception):
                raise
            raise RuntimeError("Budget state is unavailable or already in use") from None

    def load(self):
        path = checked_path(self.path)
        if not path.exists():
            return None
        if path.stat().st_size > 4096:
            raise ValueError("Invalid budget state")
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_fields)

    def save(self, state: dict) -> None:
        data = json.dumps(state, allow_nan=False, sort_keys=True).encode("utf-8")
        checked_path(self.path)
        fd, temporary = tempfile.mkstemp(prefix=".budget-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
            if os.name == "posix":
                directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def close(self):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
