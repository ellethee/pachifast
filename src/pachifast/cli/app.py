"""Top-level Typer application for the ``pachifast`` executable."""

from typing import Annotated

import typer

from pachifast.cli.commands import audit, config, locale, migrations, new, users
from pachifast.cli.commands.dev import dev
from pachifast.cli.commands.init import init
from pachifast.cli.commands.serve import serve
from pachifast.cli.helpers import console, enable_line_editing, get_installed_version

enable_line_editing()


def _version_callback(value: bool) -> None:
    """Print the installed version and exit when ``--version`` is given."""
    if value:
        console.print(f"pachifast {get_installed_version()}", highlight=False)
        raise typer.Exit()


app = typer.Typer(
    name="pachifast",
    help="Scaffold and manage pachifast projects and modules.",
    add_completion=True,
    no_args_is_help=True,
)
app.add_typer(new.app, name="new")
app.add_typer(users.app, name="users")
app.add_typer(audit.app, name="audit")
app.add_typer(config.app, name="config")
app.add_typer(locale.app, name="locale")
app.add_typer(migrations.app, name="migrations")
app.command("dev")(dev)
app.command("init")(init)
app.command("serve")(serve)


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=_version_callback, is_eager=True, help="Show the version and exit."),
    ] = None,
) -> None:
    """Scaffold and manage pachifast projects and modules."""


if __name__ == "__main__":
    app()
