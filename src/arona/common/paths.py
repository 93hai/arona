from __future__ import annotations

import os
import stat
from pathlib import Path


def checked_path(path: Path) -> Path:
    """Reject relative paths, symlinks, junctions and linked regular files.

    This is configuration/maintenance validation, not the Phase 3 race-resistant
    Candidate file writer. Runtime directories must be private to the service user.
    """
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("An absolute filesystem path is required")
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Symlinks and junctions are not allowed")
        # Path.is_junction is unavailable on the minimum supported Python 3.11.
        try:
            attributes = getattr(part.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            continue
        if attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError("Reparse points are not allowed")
    if path.is_file() and path.stat().st_nlink > 1:
        raise ValueError("Hard-linked files are not allowed")
    return path.resolve()


def overlaps(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def private_directory(path: Path) -> Path:
    path = checked_path(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("A runtime directory is required")
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError("Runtime directory permissions must be 0700")
    return path
