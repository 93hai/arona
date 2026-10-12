from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path
from threading import Lock
from typing import Protocol

from arona.common.paths import checked_path, private_directory
from arona.memory.redaction import contains_secret


class AuditSink(Protocol):
    def write(self, event: str, metadata: Mapping[str, object]) -> None: ...


def redact(value: object, *, _depth: int = 0, truncate: bool = True) -> object:
    if _depth > 20:
        raise ValueError("Audit metadata nesting limit exceeded")
    if isinstance(value, str):
        return "[REDACTED]" if contains_secret(value) else (value[:2000] if truncate else value)
    if isinstance(value, Mapping):
        result = {}
        for index, (key, item) in enumerate(value.items()):
            if not isinstance(key, str):
                raise ValueError("Audit metadata keys must be strings")
            if contains_secret(key):
                result[f"[REDACTED_KEY_{index}]"] = "[REDACTED]"
            else:
                result[key[:2000] if truncate else key] = redact(item, _depth=_depth + 1, truncate=truncate)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item, _depth=_depth + 1, truncate=truncate) for item in value]
    if value is None or type(value) in (int, float, bool):
        return value
    raise ValueError("Audit metadata must contain only JSON data")


class JsonlAuditSink:
    def __init__(self, path: Path) -> None:
        self.path = checked_path(path)
        private_directory(self.path.parent)
        self._lock = Lock()

    def write(self, event: str, metadata: Mapping[str, object]) -> None:
        if not isinstance(event, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", event):
            raise ValueError("Invalid audit event name")
        if contains_secret(event) or not isinstance(metadata, Mapping):
            raise ValueError("Invalid audit event")
        payload = {"event": event, "metadata": redact(metadata)}
        line = (json.dumps(payload, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")
        with self._lock:
            path = checked_path(self.path)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("Audit requires an unlinked regular file")
                checked_path(path)
                if os.name == "posix" and info.st_mode & 0o077:
                    raise ValueError("Audit file permissions must be private")
                remaining = memoryview(line)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise OSError("Audit write failed")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)


def require_audit(callback) -> JsonlAuditSink:
    """Security entrypoints accept the implemented durable sink, never a no-op callback."""
    sink = getattr(callback, "__self__", None)
    if type(sink) is not JsonlAuditSink or getattr(callback, "__func__", None) is not JsonlAuditSink.write:
        raise TypeError("A durable JsonlAuditSink.write callback is required")
    return sink


def write_audit(sink: JsonlAuditSink, event: str, metadata: Mapping[str, object]) -> None:
    try:
        sink.write(event, metadata)
    except Exception:
        raise OSError("Security audit could not be persisted") from None
