"""The migration lifecycle of a generated project, run in a subprocess for each step.

Process isolation matters: the models of the project register on the shared metadata at import time,
so two projects cannot live in the same interpreter.

The tests run on SQLite and, when ``PACHIFAST_TEST_POSTGRES_URL`` is set (an administrative URL such as
``postgresql://user:password@localhost:5432/postgres``), also on PostgreSQL: two throwaway databases are
created for each test and dropped at the end.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from pachifast.database import to_sync_url

PYTHON_CLI = "from pachifast.cli import app; app()"


def _cli(root: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``pachifast <args>`` in ``root``; the test fails with the output if it exits non-zero."""
    result = subprocess.run(
        [sys.executable, "-c", PYTHON_CLI, *args],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"pachifast {' '.join(args)}\n{result.stdout}\n{result.stderr}"
    return result


def _tables(url: str) -> set[str]:
    """Names of the tables of the database at ``url`` (async or sync URL)."""
    engine = create_engine(to_sync_url(url))
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def _foreign_key_targets(url: str, tables: tuple[str, ...]) -> set[str]:
    """Tables referenced by the foreign keys of ``tables``."""
    engine = create_engine(to_sync_url(url))
    try:
        inspector = inspect(engine)
        return {fk["referred_table"] for table in tables for fk in inspector.get_foreign_keys(table)}
    finally:
        engine.dispose()


def _newest_revision(root: Path, target: str) -> str:
    files = sorted((root / "migrations" / target / "versions").glob("*.py"), key=lambda p: p.stat().st_mtime)
    return files[-1].read_text(encoding="utf-8")


POSTGRES_ADMIN_URL = os.environ.get("PACHIFAST_TEST_POSTGRES_URL", "")


@dataclass
class Project:
    """A generated project, the environment to run it and the URLs of its two databases."""

    root: Path
    env: dict[str, str]
    auth_url: str
    orders_url: str


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not POSTGRES_ADMIN_URL, reason="PACHIFAST_TEST_POSTGRES_URL is not set"),
        ),
    ]
)
def project(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Project]:
    """A generated project with an ``orders`` app on its own database (SQLite files or PostgreSQL databases)."""
    created: list[str] = []
    if request.param == "sqlite":
        auth_url = f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"
        orders_url = f"sqlite+aiosqlite:///{tmp_path / 'orders.db'}"
    else:
        admin = make_url(POSTGRES_ADMIN_URL).set(drivername="postgresql+psycopg")
        suffix = uuid.uuid4().hex[:8]
        created = [f"pf_test_auth_{suffix}", f"pf_test_orders_{suffix}"]
        engine = create_engine(admin, isolation_level="AUTOCOMMIT")
        with engine.connect() as conn:
            for name in created:
                conn.execute(text(f'CREATE DATABASE "{name}"'))
        auth_url, orders_url = (admin.set(drivername="postgresql+asyncpg", database=name) for name in created)
        auth_url, orders_url = auth_url.render_as_string(False), orders_url.render_as_string(False)
    env = {
        **os.environ,
        "PACHIFAST_SECRET_KEY": "lifecycle-test-secret-key-0123456789-abcdef",
        "PACHIFAST_AUTH_DATABASE_URL": auth_url,
        "ORDERS_DATABASE_URL": orders_url,
    }
    env.pop("PACHIFAST_DATABASE_URL", None)
    _cli(tmp_path, env, "new", "project", "demo", "--output-dir", str(tmp_path))
    root = tmp_path / "demo"
    _cli(root, env, "new", "app", "orders", "--base-dir", str(root), "--with-db")
    _cli(root, env, "migrations", "init")
    try:
        yield Project(root, env, auth_url, orders_url)
    finally:
        if created:
            with engine.connect() as conn:
                for name in created:
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            engine.dispose()


def test_a_new_app_target_goes_up_down_and_up_without_drift(project: Project) -> None:
    """Autogenerate, upgrade, downgrade to base, upgrade again; a second autogenerate finds nothing to do."""
    root, env, orders_url = project.root, project.env, project.orders_url

    _cli(root, env, "migrations", "revision", "orders tables", "--target-name", "orders", "--autogenerate")
    assert "orders_example" in _newest_revision(root, "orders")

    _cli(root, env, "migrations", "upgrade", "--target-name", "orders")
    assert "orders_example" in _tables(orders_url)

    _cli(root, env, "migrations", "downgrade", "base", "--target-name", "orders")
    assert "orders_example" not in _tables(orders_url)

    _cli(root, env, "migrations", "upgrade", "--target-name", "orders")
    assert "orders_example" in _tables(orders_url)

    _cli(root, env, "migrations", "revision", "nothing", "--target-name", "orders", "--autogenerate")
    revision = _newest_revision(root, "orders")
    assert "op.create_table" not in revision and "op.add_column" not in revision and "op.drop" not in revision
    assert "def upgrade():\n    pass" in revision.replace(
        "    # ### commands auto generated by Alembic - please adjust! ###\n", ""
    )


def test_the_core_target_creates_every_core_table_and_goes_back_to_base(project: Project) -> None:
    """The auth target is generated from the core models and is reversible; a project target stays separate."""
    root, env, auth_url, orders_url = project.root, project.env, project.auth_url, project.orders_url

    _cli(root, env, "migrations", "revision", "core", "--target-name", "auth", "--autogenerate")
    _cli(root, env, "migrations", "upgrade", "--target-name", "auth")
    core = {
        "pachifast_user",
        "pachifast_permission",
        "pachifast_group",
        "pachifast_group_permission",
        "pachifast_user_group",
        "pachifast_user_permission",
        "pachifast_user_settings",
        "pachifast_refresh_token",
        "pachifast_mfa_config",
        "pachifast_mfa_pending",
        "pachifast_activity_log",
        "pachifast_ws_ticket",
    }
    assert core <= _tables(auth_url)
    assert "orders_example" not in _tables(auth_url)  # the targets are independent histories
    assert "pachifast_user" not in _tables(orders_url)
    fks = _foreign_key_targets(
        auth_url, ("pachifast_group_permission", "pachifast_user_group", "pachifast_user_permission")
    )
    assert {"pachifast_group", "pachifast_permission", "pachifast_user"} <= fks

    _cli(root, env, "migrations", "downgrade", "base", "--target-name", "auth")
    assert not core & _tables(auth_url)
