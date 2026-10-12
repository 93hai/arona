"""Versioned, packaged prompts for Arona 3.0 only."""
from __future__ import annotations

from importlib.resources import files
import json

PROMPT_VERSION = "arona.core.v1"
OUTPUT_VERSION = "arona.output.v1"


def core_system_instruction(catalog: tuple[dict, ...] = ()) -> str:
    instruction = files(__package__).joinpath("core_v1.md").read_text(encoding="utf-8")
    if any(item["name"].startswith("candidate.") for item in catalog):
        instruction += "\n" + files(__package__).joinpath("candidate_v1.md").read_text(encoding="utf-8")
    return instruction + "\nRegistered tool contracts: " + json.dumps(catalog, separators=(",", ":"))


def memory_data_message(contents: list[str]) -> dict[str, str]:
    # Keep recalled content in a data/user role, including strings that imitate
    # system messages. Never interpolate it into the trusted system instruction.
    return {"role": "user", "content": json.dumps({"untrusted_memory_data": contents}, ensure_ascii=False)}
