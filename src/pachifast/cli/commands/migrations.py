"""``pachifast migrations`` commands: Alembic multi-target management."""

from pathlib import Path
from typing import Annotated

import typer

from pachifast import migrations as migrations_api
from pachifast.cli.helpers import console, handle_errors

app = typer.Typer(help="Manage Alembic migrations for a generated project.", no_args_is_help=True)

BaseDirOption = Annotated[Path, typer.Option("--base-dir", help="Project root containing project_modules.py.")]
TargetOption = Annotated[str, typer.Option("--target-name", help="Migration target name.")]


@app.command("list")
@handle_errors
def list_targets(base_dir: BaseDirOption = Path(".")) -> None:
    """List declared migration targets."""
    for target_name in migrations_api.list_targets(base_dir=str(base_dir)):
        console.print(target_name, highlight=False)


@app.command("init")
@handle_errors
def init(
    base_dir: BaseDirOption = Path("."),
    target_name: Annotated[
        str | None, typer.Option("--target-name", help="Initialize only one migration target.")
    ] = None,
) -> None:
    """Create Alembic directories for migration targets."""
    for path in migrations_api.create_migration_layout(base_dir=str(base_dir), target_name=target_name):
        console.print(str(path), highlight=False)


@app.command("revision")
@handle_errors
def revision(
    message: Annotated[str, typer.Argument(help="Revision message.")],
    target_name: TargetOption,
    base_dir: BaseDirOption = Path("."),
    autogenerate: Annotated[
        bool, typer.Option("--autogenerate", help="Generate migration operations from the models' metadata.")
    ] = False,
) -> None:
    """Create a new Alembic revision."""
    migrations_api.revision(base_dir=str(base_dir), target_name=target_name, message=message, autogenerate=autogenerate)


@app.command("upgrade")
@handle_errors
def upgrade(
    target_name: TargetOption,
    revision: Annotated[str, typer.Argument(help="Revision to upgrade to.")] = "head",
    base_dir: BaseDirOption = Path("."),
) -> None:
    """Apply migrations for a target."""
    migrations_api.upgrade(base_dir=str(base_dir), target_name=target_name, revision_id=revision)


@app.command("downgrade")
@handle_errors
def downgrade(
    revision: Annotated[str, typer.Argument(help="Revision to downgrade to, for example -1 or base.")],
    target_name: TargetOption,
    base_dir: BaseDirOption = Path("."),
) -> None:
    """Rollback migrations for a target."""
    migrations_api.downgrade(base_dir=str(base_dir), target_name=target_name, revision_id=revision)


@app.command("strip-sqlmodel")
@handle_errors
def strip_sqlmodel(
    base_dir: BaseDirOption = Path("."),
    check: Annotated[
        bool, typer.Option("--check", help="Only list the files that need the change (exit 1 if any).")
    ] = False,
) -> None:
    """Remove the SQLModel imports and types from the existing revision files (upgrade from 0.1.x)."""
    changed = migrations_api.strip_sqlmodel(base_dir=str(base_dir), check=check)
    for path in changed:
        console.print(str(path), highlight=False)
    if check and changed:
        raise typer.Exit(1)
