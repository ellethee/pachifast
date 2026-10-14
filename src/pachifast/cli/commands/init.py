"""``pachifast init`` command: one-shot bootstrap of a generated project."""

from pathlib import Path
from typing import Annotated

import typer

from pachifast.cli.helpers import console, handle_errors
from pachifast.cli.services.init import init_project


@handle_errors
def init(
    base_dir: Annotated[Path, typer.Option("--base-dir", help="Project root containing project_modules.py.")] = Path(
        "."
    ),
    username: Annotated[str, typer.Option("--username", "-u", help="Superuser username.")] = "admin",
    password: Annotated[
        str | None, typer.Option("--password", help="Superuser password (prompted if omitted).")
    ] = None,
    email: Annotated[str | None, typer.Option("--email", help="Superuser email.")] = None,
    database_url: Annotated[
        str | None,
        typer.Option("--database-url", help="Auth database URL (default: SQLite file db.sqlite3 in the project)."),
    ] = None,
    target_name: Annotated[str, typer.Option("--target-name", help="Migration target of the auth tables.")] = "auth",
    no_user: Annotated[bool, typer.Option("--no-user", help="Do not create the superuser.")] = False,
) -> None:
    """Prepare a generated project: .env, database migrations and first superuser.

    Safe to run again: existing .env values, migrations and users are left untouched.
    """
    if not no_user and password is None:
        password = typer.prompt(f"Password for {username}", hide_input=True, confirmation_prompt=True)

    report = init_project(
        base_dir=str(base_dir),
        username=username,
        password=password,
        email=email,
        database_url=database_url,
        target_name=target_name,
        create_user=not no_user,
    )

    if report.env_created:
        console.print(f"Created {report.env_file}", highlight=False)
    elif report.env_keys_written:
        console.print(f"Updated {report.env_file}: {', '.join(report.env_keys_written)}", highlight=False)
    else:
        console.print(f"Kept {report.env_file}", highlight=False)
    console.print(f"Migrations applied (target {target_name!r}).", highlight=False)
    if report.username:
        state = "Created superuser" if report.user_created else "Superuser already exists:"
        console.print(f"{state} {report.username}", highlight=False)
    console.print("Done. Start the server with: pachifast dev", highlight=False)
