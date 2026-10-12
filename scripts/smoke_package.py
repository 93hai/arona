"""Run with an installed wheel's isolated Python; never contact providers."""
from __future__ import annotations

import argparse
from importlib import import_module, metadata, resources
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory


def smoke(workspace: Path) -> dict[str, object]:
    before = set(Path.cwd().iterdir())
    modules = (
        "arona", "arona.prompts", "migrations", "arona.database.migrations",
        "arona.memory.service", "arona.models.budget", "arona.models.http",
        "arona.tools.candidate", "arona.tools.memory", "arona.tools.dispatcher", "arona.workspace.manager",
        "arona.app", "arona.main", "arona.core.orchestrator", "arona.core.lifecycle",
    )
    for name in modules:
        module = import_module(name)
        assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), name
    arona = import_module("arona")
    assert metadata.version("arona-core") == arona.__version__
    migrations = import_module("arona.database.migrations")
    for filename in migrations.MIGRATION_FILES.values():
        assert resources.files("migrations").joinpath("versions", filename).read_text(encoding="utf-8").strip()
    for filename in ("core_v1.md", "candidate_v1.md"):
        assert resources.files("arona.prompts").joinpath(filename).read_text(encoding="utf-8").strip()
    assert set(Path.cwd().iterdir()) == before, "Imports created runtime files"

    workspace.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="package-smoke-", dir=workspace) as temporary:
        runner = migrations.DatabaseMigrations(Path(temporary) / "memory.db")
        runner.run_migrations()
        assert runner.get_schema_version() == migrations.CURRENT_SCHEMA_VERSION
        runner.run_migrations()
    return {"status": "PASS", "version": arona.__version__, "modules": len(modules),
            "schema_version": migrations.CURRENT_SCHEMA_VERSION}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    if not sys.flags.isolated:
        parser.error("Use python -I to smoke the installed wheel without source path injection")
    print(json.dumps(smoke(args.workspace.resolve()), sort_keys=True))
