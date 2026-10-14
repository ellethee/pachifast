"""The self-contained demo in ``examples/demo`` boots from scratch and serves its routes.

Each step runs in a subprocess: the models of a project register on the shared metadata at import time,
so the demo cannot live in the interpreter of the test suite.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"

PYTHON_CLI = "from pachifast.cli import app; app()"

SMOKE = """
from fastapi.testclient import TestClient

from main import app

with TestClient(app) as client:
    assert client.get("/docs").status_code == 200
    token = client.post("/api/auth/token", data={"username": "admin", "password": "change-me-please"})
    assert token.status_code == 200, token.text
    headers = {"Authorization": f"Bearer {token.json()['access_token']}"}
    items = client.get("/api/demo/items", headers=headers)
    assert items.status_code == 200, items.text
    assert items.json()["data"]["items"], "the startup seed inserted no sample records"
    assert client.get("/api/demo/items").status_code == 401
"""


def _run(root: Path, env: dict[str, str], *args: str) -> None:
    """Run ``python <args>`` in ``root``; the test fails with the output if it exits non-zero."""
    result = subprocess.run([sys.executable, *args], cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"{' '.join(args)}\n{result.stdout}\n{result.stderr}"


@pytest.mark.skipif(not DEMO.is_dir(), reason="examples/ is not part of the installed package")
def test_demo_boots_from_scratch(tmp_path: Path) -> None:
    """init + migrations of the demo target + a request with a token, on a fresh SQLite database."""
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns(".venv", ".env", "*.sqlite3", "__pycache__"))
    # `init` writes .env with a fresh secret key and refuses to run when the variable is already set
    env = {k: v for k, v in os.environ.items() if not k.startswith("PACHIFAST_")}
    env["PYTHONPATH"] = str(root)
    _run(root, env, "-c", PYTHON_CLI, "init", "--username", "admin", "--password", "change-me-please")
    _run(root, env, "-c", PYTHON_CLI, "migrations", "upgrade", "--target-name", "demo")
    _run(root, env, "-c", SMOKE)
