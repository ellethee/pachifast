"""``pachifast audit`` commands: maintain the activity log."""

from pathlib import Path
from typing import Annotated

import typer

from pachifast.cli.helpers import console, handle_errors
from pachifast.cli.services import audit as audit_service

app = typer.Typer(help="Maintain the activity log of the project.", no_args_is_help=True)


@app.command("purge")
@handle_errors
def purge(
    base_dir: Annotated[
        Path, typer.Option("--base-dir", help="Project root containing .env and project_modules.py.")
    ] = Path("."),
    days: Annotated[
        int | None,
        typer.Option("--days", min=1, help="Keep this many days (default: activity_log_retention_days)."),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only count the rows that would be deleted.")] = False,
) -> None:
    """Delete the activity log rows older than the retention. Run it from cron or a systemd timer."""
    keep, rows = audit_service.purge_activity_log(base_dir=str(base_dir), days=days, dry_run=dry_run)
    verb = "Would delete" if dry_run else "Deleted"
    console.print(f"[green]{verb}[/green] {rows} activity log row(s) older than {keep} day(s).", highlight=False)
