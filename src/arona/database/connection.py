"""Compatibility import; canonical persistence connection lives in memory/db.py."""

from arona.memory.db import DatabaseConnection

__all__ = ["DatabaseConnection"]
