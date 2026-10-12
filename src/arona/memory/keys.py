"""Canonical deduplication and explicit-fact keys (not search tokenization)."""
from __future__ import annotations

import re
import unicodedata


def normalize_content(content: str) -> str:
    return " ".join(unicodedata.normalize("NFC", content).casefold().split())


def subject_key(content: str) -> str:
    parts = re.split(r":|=|\s+(?:is|l\u00e0)\s+", content, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        subject = normalize_content(parts[0])
        return subject if len(subject) >= 3 and subject not in {"http", "https", "ftp"} else ""
    return ""
