"""Only synthetic credentials; failures print counts/locations, never values."""
import ast
from pathlib import Path

from scripts.scan_secrets import scan_text, scan_tracked


def test_scan_detects_environment_default_without_echoing_value():
    synthetic = "".join(chr(65 + index % 26) for index in range(32))
    source = 'value = os.getenv("MISTRAL_API_KEY", ' + repr(synthetic) + ')'
    findings = scan_text("fixture.py", source)
    assert len(findings) == 1
    assert findings[0].rule == "credential_default"
    assert synthetic not in repr(findings)
    assert not scan_text("fixture.py", 'value = os.getenv("MISTRAL_API_KEY", "")')


def test_scan_detects_provider_literal_and_private_key_body():
    synthetic = "".join(chr(65 + index % 26) for index in range(48))
    assert scan_text("fixture.txt", "sk-" + synthetic)
    assert scan_text("fixture.txt", "-----BEGIN PRIVATE KEY-----\n" + synthetic)
    assert not scan_text("fixture.py", 'token = "YOUR_PROVIDER_API_KEY_PLACEHOLDER_HERE"')


def test_tracked_working_tree_has_no_matching_credentials():
    count, findings = scan_tracked(Path(__file__).resolve().parents[2])
    assert count > 0
    assert not findings, "Credential patterns found; run scripts/scan_secrets.py for locations only"


def test_identified_legacy_default_is_empty():
    root = Path(__file__).resolve().parents[2]
    name = "old version/discord_ai_assistant update 1.0.py"
    tree = ast.parse((root / name).read_text(encoding="utf-8-sig"))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "getenv" and node.args:
            key = node.args[0]
            if isinstance(key, ast.Constant) and key.value == "MISTRAL_API_KEY":
                found = True
                valid = len(node.args) == 1 or (isinstance(node.args[1], ast.Constant) and node.args[1].value == "")
                assert valid, "Legacy credential fallback must be empty"
    assert found
