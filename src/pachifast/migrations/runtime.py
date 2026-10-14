"""Runtime helpers used by generated Alembic environments."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import sys
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from alembic import context as alembic_context
from alembic.config import Config
from sqlalchemy import engine_from_config, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from pachifast.config import get_settings
from pachifast.database.base import Base
from pachifast.modules import AppModule, MigrationTarget, configure_modules, resolve_migration_targets

ASYNC_DRIVER_MARKERS = ("+asyncpg", "+aiosqlite")


@dataclass(frozen=True, slots=True)
class LoadedMigrationTarget:
    """Resolved migration target bound to a project."""

    project_root: Path
    modules: tuple[AppModule, ...]
    target: MigrationTarget
    database_url: str
    metadata: object
    table_names: tuple[str, ...]

    @property
    def version_table(self) -> str:
        """Nome della tabella di versione Alembic (override del target o ``<table_prefix>alembic_version_<target>``)."""
        if self.target.version_table:
            return self.target.version_table
        return f"{get_settings().table_prefix}alembic_version_{self.target.name}"


def load_migration_target(project_root: str | Path, target_name: str) -> LoadedMigrationTarget:
    """Resolve a migration target from a generated PachiFast project."""
    root = Path(project_root).resolve()
    modules = _load_project_modules(root)
    configure_modules(modules)
    get_settings.cache_clear()

    target = _get_target(target_name, modules)
    imported_modules = _import_model_modules(target)
    database_url = _resolve_database_url(target)
    if not database_url:
        raise RuntimeError(f"Migration target {target.name!r} has no configured database URL")

    table_names = _collect_table_names(imported_modules)
    return LoadedMigrationTarget(
        project_root=root,
        modules=modules,
        target=target,
        database_url=database_url,
        metadata=Base.metadata,
        table_names=tuple(sorted(table_names)),
    )


def run_env(config: Config, target_name: str, project_root: str | Path) -> None:
    """Entry point used by generated Alembic env.py files."""
    loaded = load_migration_target(project_root, target_name)
    config.set_main_option("sqlalchemy.url", loaded.database_url)

    if alembic_context.is_offline_mode():
        _run_migrations_offline(loaded)
        return

    if _is_async_database_url(loaded.database_url):
        asyncio.run(_run_async_migrations(config, loaded))
    else:
        _run_sync_migrations(config, loaded)


def _render_item(type_: str, obj: object, autogen_context: object) -> str | bool:
    """Write a column type that belongs to pachifast as the plain SQLAlchemy type it is stored as.

    A revision must not depend on the Python class of a type: ``EncryptedStr`` is a ``String`` in the database, so
    the revisions say ``sa.String`` (they also stay readable when the class changes).
    """
    from pachifast.database.encrypted import EncryptedStr

    if type_ == "type" and isinstance(obj, EncryptedStr):
        return f"sa.String(length={obj.impl.length})"  # type: ignore[attr-defined]
    return False


def _run_migrations_offline(loaded: LoadedMigrationTarget) -> None:
    alembic_context.configure(
        url=loaded.database_url,
        target_metadata=loaded.metadata,
        literal_binds=True,
        render_as_batch=_is_sqlite_url(loaded.database_url),
        compare_type=True,
        render_item=_render_item,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_build_include_object(loaded.table_names),
        version_table=loaded.version_table,
    )

    with alembic_context.begin_transaction():
        alembic_context.run_migrations()


def _run_sync_migrations(config: Config, loaded: LoadedMigrationTarget) -> None:
    section = config.get_section(config.config_ini_section) or {}
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        _configure_and_run(connection, loaded)


async def _run_async_migrations(config: Config, loaded: LoadedMigrationTarget) -> None:
    section = config.get_section(config.config_ini_section) or {}
    connectable = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_configure_and_run, loaded)

    await connectable.dispose()


def _configure_and_run(connection, loaded: LoadedMigrationTarget) -> None:
    alembic_context.configure(
        connection=connection,
        target_metadata=loaded.metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
        render_item=_render_item,
        compare_server_default=True,
        include_object=_build_include_object(loaded.table_names),
        version_table=loaded.version_table,
    )

    with alembic_context.begin_transaction():
        alembic_context.run_migrations()


def _is_sqlite_url(database_url: str) -> bool:
    """SQLite cannot ALTER constraints: its migrations are rendered in batch mode (table rebuild)."""
    return database_url.startswith("sqlite")


def _build_include_object(table_names: tuple[str, ...]) -> Callable:
    if not table_names:
        return lambda obj, name, type_, reflected, compare_to: True

    table_name_set = set(table_names)

    def include_object(obj, name, type_, reflected, compare_to):
        if type_ == "table":
            return name in table_name_set
        parent = getattr(obj, "table", None)
        if parent is not None:
            return parent.name in table_name_set
        return True

    return include_object


def _resolve_database_url(target: MigrationTarget) -> str:
    if target.database_url_resolver:
        callback = _import_reference(target.database_url_resolver)
        value = _invoke_callback(callback)
    elif target.database_url_field:
        settings = get_settings()
        value = getattr(settings, target.database_url_field, "")
    else:
        raise RuntimeError(f"Migration target {target.name!r} has no database URL resolver")

    if value is None:
        return ""
    return str(value)


def _invoke_callback(callback: Callable) -> object:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return callback()

    if not signature.parameters:
        return callback()

    settings = get_settings()
    return callback(settings)


def _import_model_modules(target: MigrationTarget) -> tuple[object, ...]:
    imported = []
    for module_path in target.model_modules:
        imported.append(importlib.import_module(module_path))
    return tuple(imported)


def _collect_table_names(imported_modules: tuple[object, ...]) -> set[str]:
    table_names: set[str] = set()
    for module in imported_modules:
        for value in vars(module).values():
            table = getattr(value, "__table__", None)
            if table is not None and getattr(table, "name", None):
                table_names.add(table.name)
    return table_names


def _get_target(target_name: str, modules: tuple[AppModule, ...]) -> MigrationTarget:
    for target in resolve_migration_targets(modules):
        if target.name == target_name:
            return target
    raise ValueError(f"Unknown migration target: {target_name}")


def _load_project_modules(project_root: Path) -> tuple[AppModule, ...]:
    with _project_sys_path(project_root):
        sys.modules.pop("project_modules", None)
        module = importlib.import_module("project_modules")

    modules = getattr(module, "MODULES", None)
    if modules is None:
        raise ValueError(f"Missing MODULES tuple in {project_root / 'project_modules.py'}")
    return tuple(modules)


@contextmanager
def _project_sys_path(project_root: Path):
    project_str = str(project_root)
    already_present = project_str in sys.path
    if not already_present:
        sys.path.insert(0, project_str)
    try:
        yield
    finally:
        if not already_present and project_str in sys.path:
            sys.path.remove(project_str)


def _import_reference(reference: str) -> Callable:
    module_path, separator, attr_name = reference.partition(":")
    if not separator:
        raise ValueError(f"Invalid migration reference: {reference!r}")
    module = importlib.import_module(module_path)
    callback = getattr(module, attr_name)
    if not callable(callback):
        raise TypeError(f"Migration reference {reference!r} is not callable")
    return callback


def _is_async_database_url(database_url: str) -> bool:
    return any(marker in database_url for marker in ASYNC_DRIVER_MARKERS)
