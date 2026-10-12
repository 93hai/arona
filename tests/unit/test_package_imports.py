from importlib import metadata, resources
from pathlib import Path
import subprocess
import sys

import arona


def test_package_metadata_and_sql_resources():
    assert metadata.version("arona-core") == arona.__version__
    for filename in ("0001_legacy.sql", "0002_repair_memory.sql", "0003_memory_controls.sql", "0004_memory_lookup.sql"):
        sql = resources.files("migrations").joinpath("versions", filename)
        assert sql.is_file() and sql.read_text(encoding="utf-8").strip()


def test_installed_imports_work_outside_checkout_without_runtime_io(tmp_path):
    code = '''
from pathlib import Path
before = set(Path.cwd().iterdir())
import arona.main
import arona.config.settings
import arona.memory.service
import arona.database.migrations
import arona.operations.backup
import arona.models.budget
import arona.tools.candidate
import arona.tools.memory
import arona.tools.dispatcher
import arona.workspace.manager
import arona.app
import arona.core.orchestrator
import arona.core.lifecycle
from importlib.resources import files
for name in ('0001_legacy.sql', '0002_repair_memory.sql', '0003_memory_controls.sql', '0004_memory_lookup.sql'):
    assert files('migrations').joinpath('versions', name).read_text(encoding='utf-8').strip()
for name in ('core_v1.md', 'candidate_v1.md'):
    assert files('arona.prompts').joinpath(name).read_text(encoding='utf-8').strip()
assert set(Path.cwd().iterdir()) == before
'''
    result = subprocess.run([sys.executable, "-I", "-c", code], cwd=tmp_path, capture_output=True, timeout=20)
    assert result.returncode == 0, "Installed package import failed outside checkout"
    assert not result.stdout and not result.stderr
    assert not list(Path(tmp_path).iterdir())
