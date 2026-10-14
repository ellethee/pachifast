"""``pachifast dev`` reload filter: .env and the YAML config restart the server, noise does not (any hash seed)."""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SCRIPT = textwrap.dedent(
    """
    import json, os, sys
    from pathlib import Path
    from uvicorn.config import Config
    from uvicorn.supervisors.watchfilesreload import FileFilter

    from pachifast.cli.services.dev import RELOAD_INCLUDES, reload_excludes

    root = Path(sys.argv[1])
    os.chdir(root)  # like dev: the project is the current directory, so excluded directories resolve
    config = Config("main:app", reload=True, reload_dirs=[str(root)],
                    reload_includes=RELOAD_INCLUDES, reload_excludes=reload_excludes(root))
    accepts = FileFilter(config)
    names = json.loads(sys.argv[2])
    print(json.dumps({name: accepts(root / name) for name in names}))
    """
)

RELOADS = ["main.py", "apps/billing/root.py", ".env", ".env.local", "prod.env", "pachifast.yaml", "pachifast.yml"]
IGNORED = [
    ".git/index",
    ".git/refs/heads/main",
    ".venv/lib/python3.12/site-packages/a.py",
    "node_modules/x/index.py",
    "__pycache__/main.cpython-312.pyc",
    "apps/__pycache__/x.cpython-312.pyc",
    "notes.txt",
    "db.sqlite3",
]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project directory with the files above and the excluded directories present (as in a real project)."""
    for name in (*RELOADS, *IGNORED):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return tmp_path


@pytest.mark.parametrize("seed", range(8))
def test_reload_filter_is_stable_across_hash_seeds(project: Path, seed: int) -> None:
    """uvicorn keeps its patterns in a set: the result must not depend on its order (PYTHONHASHSEED)."""
    import json

    names = [*RELOADS, *IGNORED]
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT, str(project), json.dumps(names)],
        env={"PYTHONHASHSEED": str(seed), "PATH": "", "SYSTEMROOT": ""},
        capture_output=True,
        text=True,
        check=True,
    )
    decisions = json.loads(result.stdout.strip().splitlines()[-1])
    assert [name for name in RELOADS if not decisions[name]] == [], "these must restart the server"
    assert [name for name in IGNORED if decisions[name]] == [], "these must not restart the server"
