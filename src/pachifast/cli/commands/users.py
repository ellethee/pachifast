"""``pachifast users`` commands: manage auth users."""

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from pachifast.cli.helpers import console, handle_errors
from pachifast.cli.services import users as users_service

app = typer.Typer(help="Manage auth users in the configured authentication database.", no_args_is_help=True)

BaseDirOption = Annotated[Path, typer.Option("--base-dir", help="Project root containing .env and project_modules.py.")]


def _prompt_password(username: str) -> str:
    """Prompt twice (hidden) for a password."""
    return typer.prompt(f"Password for {username}", hide_input=True, confirmation_prompt=True)


@app.command("create")
@handle_errors
def create(
    username: Annotated[str, typer.Argument(help="Username for the new user.")],
    base_dir: BaseDirOption = Path("."),
    password: Annotated[
        str | None, typer.Option("--password", help="Plain-text password. If omitted, it is prompted.")
    ] = None,
    email: Annotated[str | None, typer.Option("--email", help="Email address.")] = None,
    first_name: Annotated[str | None, typer.Option("--first-name", help="First name.")] = None,
    last_name: Annotated[str | None, typer.Option("--last-name", help="Last name.")] = None,
    staff: Annotated[bool, typer.Option("--staff", help="Create the user with is_staff=true.")] = False,
    superuser: Annotated[bool, typer.Option("--superuser", help="Create the user with is_superuser=true.")] = False,
    inactive: Annotated[bool, typer.Option("--inactive", help="Create the user with is_active=false.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Bypass the password length policy.")] = False,
) -> None:
    """Create a new auth user."""
    raw_password = password if password is not None else _prompt_password(username)
    users_service.check_password_policy(raw_password, force=force)
    created = users_service.create_user(
        username,
        base_dir=str(base_dir),
        password=raw_password,
        email=email,
        first_name=first_name,
        last_name=last_name,
        is_active=not inactive,
        is_staff=staff,
        is_superuser=superuser,
    )
    console.print(f"[green]Created user[/green] {created}", highlight=False)


@app.command("update")
@handle_errors
def update(
    username: Annotated[str, typer.Argument(help="Username of the user to update.")],
    base_dir: BaseDirOption = Path("."),
    password: Annotated[
        str | None, typer.Option("--password", help="New plain-text password (see password_min_length and --force).")
    ] = None,
    prompt_password: Annotated[
        bool, typer.Option("--prompt-password", "-P", help="Prompt for the new password.")
    ] = False,
    email: Annotated[str | None, typer.Option("--email", help="New email address.")] = None,
    first_name: Annotated[str | None, typer.Option("--first-name", help="New first name.")] = None,
    last_name: Annotated[str | None, typer.Option("--last-name", help="New last name.")] = None,
    staff: Annotated[bool | None, typer.Option("--staff/--no-staff", help="Set is_staff flag.")] = None,
    superuser: Annotated[bool | None, typer.Option("--superuser/--no-superuser", help="Set is_superuser flag.")] = None,
    active: Annotated[bool | None, typer.Option("--active/--no-active", help="Set is_active flag.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Bypass the password length policy.")] = False,
) -> None:
    """Update an existing auth user."""
    if password is None and prompt_password:
        password = _prompt_password(username)
    updated = users_service.update_user(
        username,
        base_dir=str(base_dir),
        password=password,
        email=email,
        first_name=first_name,
        last_name=last_name,
        is_active=active,
        is_staff=staff,
        is_superuser=superuser,
        force=force,
    )
    console.print(f"[green]Updated user[/green] {updated}", highlight=False)


@app.command("delete")
@handle_errors
def delete(
    username: Annotated[str, typer.Argument(help="Username of the user to delete.")],
    base_dir: BaseDirOption = Path("."),
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Delete an auth user and the data linked to it (groups, permissions, settings, MFA). It cannot be undone."""
    if not yes:
        typer.confirm(f"Delete user {username!r}? This cannot be undone.", abort=True)
    deleted = users_service.delete_user(username, base_dir=str(base_dir))
    console.print(f"[green]Deleted user[/green] {deleted}", highlight=False)


@app.command("ls")
@handle_errors
def ls(
    filter: Annotated[
        str | None, typer.Argument(help="Substring filter on username or email (case-insensitive).")
    ] = None,
    base_dir: BaseDirOption = Path("."),
) -> None:
    """List auth users, optionally filtered."""
    users = users_service.list_users(filter, base_dir=str(base_dir))
    if not users:
        console.print("No users found.")
        return
    table = Table("ID", "USERNAME", "EMAIL", "ACTIVE", "STAFF", "SUPERUSER")
    for u in users:
        table.add_row(
            u["id"],
            u["username"],
            u["email"],
            "yes" if u["is_active"] else "no",
            "yes" if u["is_staff"] else "no",
            "yes" if u["is_superuser"] else "no",
        )
    console.print(table)


@app.command("encrypt-mfa-secrets")
@handle_errors
def encrypt_mfa_secrets(base_dir: BaseDirOption = Path(".")) -> None:
    """Encrypt the TOTP secrets stored as plain text (rows written before 0.10). Safe to run again."""
    count = users_service.encrypt_mfa_secrets(base_dir=str(base_dir))
    console.print(f"[green]Encrypted[/green] {count} TOTP secret(s).", highlight=False)
