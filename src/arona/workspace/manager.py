from __future__ import annotations

import ntpath
import os
import re
import stat
import sys
from pathlib import Path, PureWindowsPath
from threading import RLock
from uuid import uuid4

from arona.common.paths import checked_path, overlaps, private_directory
from arona.memory.redaction import contains_secret
from arona.workspace._filesystem import pinned_directory


class CandidateManager:
    """Candidate file operations with pinned directories; never imports or executes files."""

    MAX_FILE_BYTES = 1_000_000
    MAX_TOTAL_BYTES = 10_000_000
    ALLOWED_EXTENSIONS = frozenset({".c", ".cpp", ".h", ".hpp", ".js", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"})

    def __init__(self, root: Path, *, forbidden_roots: tuple[Path, ...] = ()) -> None:
        if not forbidden_roots:
            raise ValueError("Explicit Runtime/database/backup roots are required")
        self._root = checked_path(root)
        self._forbidden = tuple(checked_path(path) for path in forbidden_roots) + (Path(__file__).resolve().parents[3],)
        self._assert_boundary()
        private_directory(self._root)
        info = self._root.stat()
        self._identity = (info.st_dev, info.st_ino)
        self._lock = RLock()

    @property
    def root(self) -> Path:
        return self._root

    def _assert_boundary(self) -> None:
        root = checked_path(self._root)
        if any(overlaps(root, path) for path in self._forbidden):
            raise ValueError("Candidate root overlaps a protected Runtime data root")
        if any(overlaps(root, Path(entry or os.curdir).resolve()) for entry in sys.path):
            raise ValueError("Candidate must not be reachable through Python import paths")

    def _parts(self, relative_name: str, *, extension_required: bool = True) -> tuple[str, ...]:
        if not isinstance(relative_name, str) or not relative_name or "\x00" in relative_name:
            raise ValueError("Invalid Candidate path")
        if relative_name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", relative_name):
            raise ValueError("Absolute Candidate paths are not allowed")
        parts = tuple(relative_name.replace("\\", "/").split("/"))
        for part in parts:
            reserved = ntpath.isreserved(part) if hasattr(ntpath, "isreserved") else PureWindowsPath(part).is_reserved()
            if part in ("", ".", "..") or ":" in part or part.rstrip(" .") != part or reserved:
                raise ValueError("Candidate traversal or special path is prohibited")
        if contains_secret(relative_name):
            raise ValueError("Candidate names must not contain credentials")
        if extension_required and Path(parts[-1]).suffix.lower() not in self.ALLOWED_EXTENSIONS:
            raise ValueError("Candidate file extension is not allowed")
        return parts

    def _walk(self, prefix: tuple[str, ...] = (), *, check_deadline=None):
        with pinned_directory(self.root, prefix, self._identity) as directory:
            for name in directory.names():
                if check_deadline is not None:
                    check_deadline()
                self._parts(name, extension_required=False)
                info = directory.stat(name)
                relative = (*prefix, name)
                if stat.S_ISDIR(info.st_mode):
                    yield from self._walk(relative, check_deadline=check_deadline)
                elif stat.S_ISREG(info.st_mode):
                    yield "/".join(relative), info.st_size
                else:
                    raise ValueError("Candidate contains a non-regular file")

    def list_files(self, *, check_deadline=None) -> list[str]:
        try:
            with self._lock:
                if check_deadline is not None:
                    check_deadline()
                self._assert_boundary()
                return sorted(name for name, _ in self._walk(check_deadline=check_deadline))
        except OSError:
            raise ValueError("Candidate listing failed safely") from None

    def read(self, relative_name: str, *, check_deadline=None) -> str:
        try:
            with self._lock:
                if check_deadline is not None:
                    check_deadline()
                self._assert_boundary()
                parts = self._parts(relative_name)
                with pinned_directory(self.root, parts[:-1], self._identity) as directory:
                    with directory.read_file(parts[-1]) as fd:
                        data = bytearray()
                        while len(data) <= self.MAX_FILE_BYTES:
                            if check_deadline is not None:
                                check_deadline()
                            chunk = os.read(fd, min(65536, self.MAX_FILE_BYTES + 1 - len(data)))
                            if not chunk:
                                break
                            data.extend(chunk)
                if len(data) > self.MAX_FILE_BYTES:
                    raise ValueError("Candidate file is too large")
                text = data.decode("utf-8")
                if contains_secret(text):
                    raise ValueError("Candidate content contains credentials")
                return text
        except (OSError, UnicodeError):
            raise ValueError("Candidate read failed safely") from None

    def write(self, relative_name: str, content: str, *, check_deadline=None) -> None:
        if not isinstance(content, str) or len(content) > self.MAX_FILE_BYTES:
            raise ValueError("Candidate content is too large")
        data = content.encode("utf-8")
        if len(data) > self.MAX_FILE_BYTES or contains_secret(content):
            raise ValueError("Candidate content is too large or contains credentials")
        try:
            with self._lock:
                if check_deadline is not None:
                    check_deadline()
                self._assert_boundary()
                parts = self._parts(relative_name)
                total = sum(size for _, size in self._walk(check_deadline=check_deadline))
                with pinned_directory(self.root, parts[:-1], self._identity, create=True) as directory:
                    try:
                        info = directory.stat(parts[-1])
                        if not stat.S_ISREG(info.st_mode):
                            raise ValueError("Candidate target must be a regular file")
                        old_size = info.st_size
                    except FileNotFoundError:
                        old_size = 0
                    if total - old_size + len(data) > self.MAX_TOTAL_BYTES:
                        raise ValueError("Candidate workspace quota exceeded")
                    temporary = ".candidate-" + uuid4().hex
                    fd = directory.create_file(temporary)
                    try:
                        try:
                            remaining = memoryview(data)
                            while remaining:
                                if check_deadline is not None:
                                    check_deadline()
                                written = os.write(fd, remaining)
                                if written <= 0:
                                    raise OSError("Candidate write failed")
                                remaining = remaining[written:]
                            os.fsync(fd)
                        finally:
                            os.close(fd)
                        if check_deadline is not None:
                            check_deadline()
                        directory.replace(temporary, parts[-1])
                    finally:
                        directory.unlink(temporary)
        except OSError:
            raise ValueError("Candidate write failed safely") from None

    def delete(self, relative_name: str) -> None:
        try:
            with self._lock:
                self._assert_boundary()
                parts = self._parts(relative_name)
                with pinned_directory(self.root, parts[:-1], self._identity) as directory:
                    try:
                        info = directory.stat(parts[-1])
                    except FileNotFoundError:
                        return
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("Candidate target must be a regular file")
                    directory.unlink(parts[-1])
        except OSError:
            raise ValueError("Candidate delete failed safely") from None
