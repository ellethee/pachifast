"""High-level migration management used by the PachiFast CLI."""

from __future__ import annotations

import re
from pathlib import Path
from textwrap import dedent

from alembic import command
from alembic.config import Config

from pachifast.migrations.runtime import load_migration_target
from pachifast.modules import resolve_migration_targets


def list_targets(base_dir: str = ".") -> tuple[str, ...]:
    """List migration targets declared by a generated project."""
    project_root = Path(base_dir).resolve()
    modules = _load_modules(project_root)
    return tuple(target.name for target in resolve_migration_targets(modules))


def create_migration_layout(base_dir: str = ".", target_name: str | None = None) -> tuple[Path, ...]:
    """Create Alembic directories for one or all project migration targets."""
    project_root = Path(base_dir).resolve()
    modules = _load_modules(project_root)
    targets = resolve_migration_targets(modules)
    if not targets:
        raise ValueError("No migration targets declared in project_modules.py")

    selected = [target for target in targets if target_name in (None, target.name)]
    if not selected:
        raise ValueError(f"Unknown migration target: {target_name}")

    created: list[Path] = []
    for target in selected:
        root = project_root / "migrations" / target.name
        versions_dir = root / "versions"
        versions_dir.mkdir(parents=True, exist_ok=True)
        _write_file(root / "env.py", _env_template(target.name))
        _write_file(root / "script.py.mako", _script_template())
        _write_file(versions_dir / ".gitkeep", "")
        created.append(root)

    return tuple(created)


def revision(base_dir: str, target_name: str, message: str, autogenerate: bool = False) -> None:
    """Create a new Alembic revision for a project target."""
    _ensure_layout_exists(Path(base_dir).resolve(), target_name)
    command.revision(
        _build_config(Path(base_dir).resolve(), target_name),
        message=message,
        autogenerate=autogenerate,
    )


def upgrade(base_dir: str, target_name: str, revision_id: str = "head") -> None:
    """Upgrade a project target to the requested revision."""
    _ensure_layout_exists(Path(base_dir).resolve(), target_name)
    command.upgrade(_build_config(Path(base_dir).resolve(), target_name), revision_id)


def downgrade(base_dir: str, target_name: str, revision_id: str) -> None:
    """Downgrade a project target to the requested revision."""
    _ensure_layout_exists(Path(base_dir).resolve(), target_name)
    command.downgrade(_build_config(Path(base_dir).resolve(), target_name), revision_id)


_SQLMODEL_TYPE = re.compile(r"sqlmodel\.sql\.sqltypes\.AutoString\(")
_SQLMODEL_IMPORT = re.compile(r"^import sqlmodel[ \t]*\r?\n", re.MULTILINE)


def strip_sqlmodel(base_dir: str = ".", check: bool = False) -> tuple[Path, ...]:
    """Remove the ``sqlmodel`` dependency from the revision files of a project.

    Revisions generated before pachifast left SQLModel contain ``import sqlmodel`` and
    ``sqlmodel.sql.sqltypes.AutoString(...)``. The second one is a plain ``sa.String(...)``; the import is
    dropped (also from the ``script.py.mako`` templates, so that new revisions do not carry it).
    With ``check=True`` nothing is written. Returns the files that need (or got) the change.
    """
    changed: list[Path] = []
    migrations_dir = Path(base_dir).resolve() / "migrations"
    files = [*migrations_dir.glob("*/versions/*.py"), *migrations_dir.glob("*/script.py.mako")]
    for path in sorted(files):
        text = path.read_text(encoding="utf-8")
        new = _SQLMODEL_IMPORT.sub("", _SQLMODEL_TYPE.sub("sa.String(", text))
        if new != text:
            changed.append(path)
            if not check:
                path.write_text(new, encoding="utf-8")
    return tuple(changed)


def _build_config(project_root: Path, target_name: str) -> Config:
    root = project_root / "migrations" / target_name
    loaded = load_migration_target(project_root, target_name)

    config = Config()
    config.set_main_option("script_location", str(root))
    config.set_main_option("sqlalchemy.url", loaded.database_url)
    config.set_main_option("prepend_sys_path", str(project_root))
    config.set_main_option("path_separator", "os")
    config.config_file_name = str(root / "alembic.ini")
    return config


def _ensure_layout_exists(project_root: Path, target_name: str) -> None:
    root = project_root / "migrations" / target_name
    if not root.exists():
        raise FileNotFoundError(
            f"Missing migration layout for target {target_name!r}. "
            f"Run `pachifast migrations init --target-name {target_name}`."
        )


def _write_file(path: Path, content: str) -> None:
    path.write_text(dedent(content).lstrip(), encoding="utf-8")


def _env_template(target_name: str) -> str:
    return f"""
    from pathlib import Path

    from alembic import context

    from pachifast.migrations.runtime import run_env

    config = context.config
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    TARGET_NAME = "{target_name}"

    run_env(config, TARGET_NAME, PROJECT_ROOT)
    """


def _script_template() -> str:
    return """
    \"\"\"${message}\"\"\"

    from alembic import op
    import sqlalchemy as sa
    import pachifast.database.utils
    from sqlalchemy import Text
    ${imports if imports else ""}

    revision = ${repr(up_revision)}
    down_revision = ${repr(down_revision)}
    branch_labels = ${repr(branch_labels)}
    depends_on = ${repr(depends_on)}


    def upgrade():
        ${upgrades if upgrades else "pass"}


    def downgrade():
        ${downgrades if downgrades else "pass"}
    """


def _load_modules(project_root: Path):
    from pachifast.migrations.runtime import _load_project_modules

    return _load_project_modules(project_root)
