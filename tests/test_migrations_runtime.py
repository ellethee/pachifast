"""migrations.runtime: URL resolution, include filter, offline/sync/async branches (no server involved)."""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from alembic.config import Config

from pachifast.migrations import runtime
from pachifast.modules import AppModule, MigrationTarget


def test_async_and_sqlite_url_detection() -> None:
    assert runtime._is_async_database_url("postgresql+asyncpg://u@h/db")
    assert runtime._is_async_database_url("sqlite+aiosqlite:///x.db")
    assert not runtime._is_async_database_url("postgresql+psycopg://u@h/db")
    assert runtime._is_sqlite_url("sqlite:///x") and not runtime._is_sqlite_url("postgresql://u@h/db")


def test_include_object_filters_tables_and_children() -> None:
    allow_all = runtime._build_include_object(())
    assert allow_all(None, "t", "table", False, None)
    only = runtime._build_include_object(("keep",))
    assert only(None, "keep", "table", False, None)
    assert not only(None, "drop", "table", False, None)
    child = types.SimpleNamespace(table=types.SimpleNamespace(name="keep"))
    assert only(child, "ix", "index", False, None)
    assert not only(types.SimpleNamespace(table=types.SimpleNamespace(name="drop")), "ix", "index", False, None)
    assert only(object(), "col", "column", False, None)


def test_render_item_rewrites_encrypted_type_only() -> None:
    from pachifast.database.encrypted import EncryptedStr

    assert runtime._render_item("type", EncryptedStr(50), None) == "sa.String(length=50)"
    assert runtime._render_item("column", object(), None) is False


def test_import_reference_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("t_mig_ref")
    fake.value = 1
    fake.fn = lambda: "x"
    monkeypatch.setitem(sys.modules, "t_mig_ref", fake)
    assert runtime._import_reference("t_mig_ref:fn")() == "x"
    with pytest.raises(ValueError, match="Invalid migration reference"):
        runtime._import_reference("t_mig_ref")
    with pytest.raises(TypeError, match="not callable"):
        runtime._import_reference("t_mig_ref:value")


def test_resolve_database_url_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = types.ModuleType("t_mig_url")
    fake.no_args = lambda: "sqlite:///a.db"
    fake.with_settings = lambda settings: f"sqlite:///{type(settings).__name__}.db"
    fake.none = lambda: None
    monkeypatch.setitem(sys.modules, "t_mig_url", fake)

    def target(**kw: object) -> MigrationTarget:
        return MigrationTarget("t", (), **kw)  # type: ignore[arg-type]

    assert runtime._resolve_database_url(target(database_url_resolver="t_mig_url:no_args")) == "sqlite:///a.db"
    assert runtime._resolve_database_url(target(database_url_resolver="t_mig_url:with_settings")).endswith(".db")
    assert runtime._resolve_database_url(target(database_url_resolver="t_mig_url:none")) == ""
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", "sqlite:///f.db")
    runtime.get_settings.cache_clear()
    try:
        assert runtime._resolve_database_url(target(database_url_field="AUTH_DATABASE_URL")) == "sqlite:///f.db"
    finally:
        runtime.get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="no database URL resolver"):
        runtime._resolve_database_url(target())


def test_version_table_default_and_override() -> None:
    def loaded(**kw: object) -> runtime.LoadedMigrationTarget:
        return runtime.LoadedMigrationTarget(
            Path("."),
            (),
            MigrationTarget("demo", (), database_url_field="F", **kw),
            "sqlite://",
            None,
            (),  # type: ignore[arg-type]
        )

    assert loaded().version_table.endswith("alembic_version_demo")
    assert loaded(version_table="custom").version_table == "custom"


def _project(tmp_path: Path, body: str) -> Path:
    (tmp_path / "project_modules.py").write_text(body, encoding="utf-8")
    return tmp_path


def test_load_project_modules_requires_modules_tuple(tmp_path: Path) -> None:
    root = _project(tmp_path, "X = 1\n")
    with pytest.raises(ValueError, match="Missing MODULES"):
        runtime._load_project_modules(root)
    assert str(root) not in sys.path
    sys.modules.pop("project_modules", None)


def test_get_target_unknown_name() -> None:
    with pytest.raises(ValueError, match="Unknown migration target"):
        runtime._get_target("zzz", (AppModule("m"),))


def test_load_migration_target_without_url_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PACHIFAST_AUTH_DATABASE_URL", raising=False)
    root = _project(
        tmp_path,
        "from pachifast.modules import AppModule, MigrationTarget\n"
        "target = MigrationTarget('t', (), database_url_resolver='project_modules:url')\n"
        "MODULES = (AppModule('m', migration_targets=(target,)),)\n"
        "def url():\n    return ''\n",
    )
    try:
        with pytest.raises(RuntimeError, match="no configured database URL"):
            runtime.load_migration_target(root, "t")
    finally:
        sys.modules.pop("project_modules", None)
        runtime.configure_modules(())


@pytest.fixture
def fake_loaded(monkeypatch: pytest.MonkeyPatch):
    """Make ``load_migration_target`` return a canned target and record the alembic context calls."""
    calls: list[tuple[str, dict]] = []

    def make(url: str, offline: bool) -> runtime.LoadedMigrationTarget:
        from contextlib import nullcontext

        loaded = runtime.LoadedMigrationTarget(
            Path("."), (), MigrationTarget("t", (), database_url_field="F"), url, object(), ("keep",)
        )
        monkeypatch.setattr(runtime, "load_migration_target", lambda *_: loaded)
        monkeypatch.setattr(runtime.alembic_context, "is_offline_mode", lambda: offline)
        monkeypatch.setattr(runtime.alembic_context, "configure", lambda **kw: calls.append(("configure", kw)))
        monkeypatch.setattr(runtime.alembic_context, "begin_transaction", lambda: nullcontext())
        monkeypatch.setattr(runtime.alembic_context, "run_migrations", lambda: calls.append(("run", {})))
        return loaded

    make.calls = calls  # type: ignore[attr-defined]
    return make


def test_run_env_offline_renders_sql_without_connecting(fake_loaded) -> None:
    fake_loaded("postgresql://u@h/db", offline=True)
    runtime.run_env(Config(), "t", ".")
    kinds = [k for k, _ in fake_loaded.calls]
    assert kinds == ["configure", "run"]
    options = fake_loaded.calls[0][1]
    assert options["literal_binds"] is True and options["render_as_batch"] is False
    assert options["url"] == "postgresql://u@h/db"


def test_run_env_sync_uses_a_real_sqlite_connection(fake_loaded, tmp_path: Path) -> None:
    fake_loaded(f"sqlite:///{tmp_path / 'm.db'}", offline=False)
    runtime.run_env(Config(), "t", ".")
    options = fake_loaded.calls[0][1]
    assert options["render_as_batch"] is True and options["version_table"].endswith("alembic_version_t")
    assert options["connection"] is not None


def test_run_env_async_driver_goes_through_run_sync(fake_loaded, tmp_path: Path) -> None:
    fake_loaded(f"sqlite+aiosqlite:///{tmp_path / 'a.db'}", offline=False)
    runtime.run_env(Config(), "t", ".")
    assert [k for k, _ in fake_loaded.calls] == ["configure", "run"]
    assert fake_loaded.calls[0][1]["render_as_batch"] is True
