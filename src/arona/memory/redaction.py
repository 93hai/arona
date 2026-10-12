"""Deterministic rejection of recognizable credentials; never echo rejected text."""
from __future__ import annotations

import re
import unicodedata

_LABEL = r"(?:password|passwd|passphrase|pwd|mật\s+khẩu|mat\s+khau|otp|mã\s+xác\s+thực|api[ _-]?key|private[ _-]?key|khóa\s+bí\s+mật|token|secret|credential)"
_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"AIza[0-9A-Za-z_-]{20,}", r"sk-[0-9A-Za-z_-]{20,}",
    r"(?:gh[pousr]_|github_pat_)[0-9A-Za-z_]{20,}",
    r"AKIA[0-9A-Z]{16}", r"xox[baprs]-[0-9A-Za-z-]{20,}",
    r"[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    rf"(?<!\w){_LABEL}(?!\w)(?:\s+của\s+(?:tôi|bạn))?\s*(?:[:=]|\b(?:is|là|la)\b)\s*\S+",
    r"\bBearer\s+[A-Za-z0-9._~+/-]{12,}",
    rf"--{_LABEL}\s+\S+",
    rf"(?:remember|save|nhớ|lưu)\s+(?:my\s+)?{_LABEL}\b",
))
_BARE_LABEL = re.compile(rf"^{_LABEL}$", re.IGNORECASE)


def contains_secret(value: str) -> bool:
    text = unicodedata.normalize("NFKC", value).strip()
    return bool(_BARE_LABEL.fullmatch(text) or any(p.search(text) for p in _PATTERNS))


def validate_identifier(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("A nonempty, bounded Memory identifier is required")
    if any(ord(char) < 32 for char in value) or contains_secret(value):
        raise ValueError("Memory identifiers must not contain credentials or control characters")
    return value
