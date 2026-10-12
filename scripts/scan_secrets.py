"""Scan tracked working-tree text for credential literals; emit locations only.

This heuristic is not a Git history scan or proof that all secrets are absent.
It never imports legacy files or prints matching text, values or exception data.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str


SECRET_NAME = re.compile(r"(?:api[_-]?key|password|passwd|secret|(?:access[_-]?)?token|credential)", re.I)
PROVIDER_LITERAL = re.compile(r"\b(?:AIza[\w-]{30,}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{25,})\b")
ASSIGNMENT = re.compile(r"(?i)(?:api[_-]?key|password|passwd|secret|token|credential)[\w]*\s*[=:]\s*['\"]([^'\"\r\n]+)['\"]")


def _suspicious(value: object) -> bool:
    if not isinstance(value, str) or len(value) < 32:
        return False
    # Common documented placeholders and low-diversity synthetic fixtures.
    if value.upper().startswith(("YOUR_", "REPLACE_", "EXAMPLE_", "PLACEHOLDER_")):
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9_./+=-]+", value) and len(set(value)) >= 12)


def scan_text(path: str, source: str) -> list[Finding]:
    locations = set()
    for line_no, line in enumerate(source.splitlines(), 1):
        if any(_suspicious(match.group()) for match in PROVIDER_LITERAL.finditer(line)):
            locations.add((line_no, "provider_literal"))
        if any(_suspicious(match.group(1)) for match in ASSIGNMENT.finditer(line)):
            locations.add((line_no, "credential_assignment"))
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", line):
            # A header alone in a redaction fixture is not a private key body.
            following = "\n".join(source.splitlines()[line_no:line_no + 3])
            if re.search(r"(?m)^[A-Za-z0-9+/]{40,}={0,2}$", following):
                locations.add((line_no, "private_key"))
    if path.endswith(".py"):
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in ("getenv", "get") or not node.args:
                    continue
                name = node.args[0]
                if not isinstance(name, ast.Constant) or not isinstance(name.value, str) or not SECRET_NAME.search(name.value):
                    continue
                defaults = node.args[1:2] + [item.value for item in node.keywords if item.arg == "default"]
                for default in defaults:
                    if isinstance(default, ast.Constant) and _suspicious(default.value):
                        locations.add((default.lineno, "credential_default"))
    return [Finding(path, line, rule) for line, rule in sorted(locations)]


def scan_tracked(root: Path) -> tuple[int, list[Finding]]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    scanned = 0
    findings = []
    for name in result.stdout.decode("utf-8").split("\0"):
        if not name:
            continue
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        try:
            source = raw.decode("utf-8-sig")
        except UnicodeError:
            continue
        scanned += 1
        findings.extend(scan_text(name, source))
    return scanned, findings


if __name__ == "__main__":
    try:
        count, findings = scan_tracked(Path(__file__).resolve().parents[1])
    except Exception:
        print("Secret scan failed; no source values emitted.")
        raise SystemExit(2) from None
    for finding in findings:
        print(f"{finding.path}:{finding.line}: {finding.rule}")
    print(f"Scanned {count} tracked text files; findings: {len(findings)} (values never emitted).")
    raise SystemExit(bool(findings))
