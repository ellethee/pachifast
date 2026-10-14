"""``pachifast locale`` commands: check the message catalogs and export them for the frontend."""

from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from pachifast.cli.helpers import console, err_console, handle_errors
from pachifast.cli.services import locale as locale_service
from pachifast.i18n import exporter

app = typer.Typer(help="Check the message catalogs and export them for the frontend.", no_args_is_help=True)

BaseDirOption = Annotated[Path, typer.Option("--base-dir", help="Project root containing project_modules.py.")]


def _print_problems(problems: list) -> None:
    for problem in problems:
        console.print(f"[red]-[/red] {escape(problem.message)}", highlight=False)


@app.command("check")
@handle_errors
def check(base_dir: BaseDirOption = Path(".")) -> None:
    """Check the catalogs of the core, the modules and the project: same codes and fields in every language."""
    layers, problems = locale_service.check_project(str(base_dir))
    for layer in layers:
        languages = ", ".join(sorted(layer.messages)) or "no language"
        count = sum(len(messages) for messages in layer.messages.values())
        console.print(f"{escape(layer.source)}: {languages} ({count} messages)", highlight=False)
    if problems:
        console.print(f"\n[red]{len(problems)} problem(s):[/red]", highlight=False)
        _print_problems(problems)
        raise typer.Exit(1)
    console.print("\n[green]Catalogs are consistent.[/green]")


@app.command("settings")
@handle_errors
def settings(
    base_dir: BaseDirOption = Path("."),
    missing: Annotated[
        bool, typer.Option("--missing", help="Only the texts that some language does not translate.")
    ] = False,
) -> None:
    """List the texts of the user settings schema that a catalog can translate, and which languages have them.

    The key is ``settings.<section>.<field>.<label|properties.<name>|options.<value>>``. A text with no entry
    keeps the default written in the code.
    """
    rows = locale_service.settings_texts(str(base_dir))
    if missing:
        rows = [row for row in rows if not all(row[2].values())]
    if not rows:
        console.print("No settings texts" + (" are missing." if missing else " found."))
        return
    languages = list(rows[0][2])
    table = Table()
    table.add_column("KEY", no_wrap=True, overflow="ignore")  # a key must stay on one line to be copied
    table.add_column("DEFAULT TEXT")
    for lang in languages:
        table.add_column(lang.upper())
    for key, default, present in rows:
        table.add_row(key, default, *["yes" if present[lang] else "no" for lang in languages])
    console.print(table)


@app.command("admin")
@handle_errors
def admin(
    base_dir: BaseDirOption = Path("."),
    missing: Annotated[
        bool, typer.Option("--missing", help="Only the texts that some language does not translate.")
    ] = False,
) -> None:
    """List the texts of the model admin schema that a catalog can translate, and which languages have them.

    The key is ``admin.<app>.<model>.title``, ``.<column>.label``, ``.<column>.options.<value>`` or
    ``.actions.<name>.label``. A text with no entry
    keeps the default written in the code.
    """
    rows = locale_service.admin_texts(str(base_dir))
    if missing:
        rows = [row for row in rows if not all(row[2].values())]
    if not rows:
        console.print("No admin texts" + (" are missing." if missing else " found."))
        return
    languages = list(rows[0][2])
    table = Table()
    table.add_column("KEY", no_wrap=True, overflow="ignore")  # a key must stay on one line to be copied
    table.add_column("DEFAULT TEXT")
    for lang in languages:
        table.add_column(lang.upper())
    for key, default, present in rows:
        table.add_row(key, default, *["yes" if present[lang] else "no" for lang in languages])
    console.print(table)


@app.command("permissions")
@handle_errors
def permissions(
    base_dir: BaseDirOption = Path("."),
    missing: Annotated[
        bool, typer.Option("--missing", help="Only the entries that some language does not translate.")
    ] = False,
) -> None:
    """List the permissions and groups of the auth database and which languages have a label for them.

    The key is ``permissions.<codename>.label`` / ``groups.<name>.label``. An identifier that is not a valid key
    (for example a group called "Billing admins") cannot be translated and is shown with a ``-``.
    """
    rows = locale_service.auth_entity_texts(base_dir=str(base_dir))
    if missing:
        rows = [row for row in rows if row[2] is not None and not all(row[3].values())]
    if not rows:
        console.print("No permissions or groups" + (" with missing labels." if missing else " found."))
        return
    languages = list(rows[0][3])
    table = Table()
    table.add_column("KIND")
    table.add_column("IDENTIFIER", no_wrap=True, overflow="ignore")
    table.add_column("KEY", no_wrap=True, overflow="ignore")
    for lang in languages:
        table.add_column(lang.upper())
    for kind, identifier, base, present in rows:
        key = f"{base}.label" if base else "-"
        table.add_row(
            kind, identifier, key, *[("yes" if present[lang] else "no") if base else "-" for lang in languages]
        )
    console.print(table)


@app.command("export")
@handle_errors
def export(
    dest_path: Annotated[
        Path | None,
        typer.Option("--dest-path", help="Folder that receives one <language>.json per language (required)."),
    ] = None,
    base_dir: BaseDirOption = Path("."),
    namespace: Annotated[
        str, typer.Option("--namespace", help="Wrap the messages in this dotted path, e.g. errors.codes.")
    ] = "",
    top_level: Annotated[
        list[str] | None,
        typer.Option(
            "--top-level",
            help="With --namespace: codes whose first part is this stay at the top of the file, not under the "
            "namespace (repeatable; default: settings, permissions, groups). Pass an empty value to wrap everything.",
        ),
    ] = None,
    core: Annotated[
        bool, typer.Option("--core/--no-core", help="Include the messages of the pachifast core (default: yes).")
    ] = True,
    check: Annotated[
        bool, typer.Option("--check", help="Write nothing; exit 1 if the files are missing or out of date (for CI).")
    ] = False,
) -> None:
    """Export the messages as nested JSON for vue-i18n.

    The files are generated: they are always overwritten and never read back. Keep the texts that end users
    change in a separate override file merged after them, or in the project's locale/ directory.
    """
    if dest_path is None:
        err_console.print(
            "[bold red]error:[/bold red] missing option '--dest-path': pass the folder that receives one "
            "<language>.json per language, for example `pachifast locale export --dest-path frontend/src/locales`.",
            highlight=False,
        )
        raise typer.Exit(code=2)
    report, problems = locale_service.export_project(
        str(base_dir),
        str(dest_path),
        namespace=namespace,
        top_level=tuple(top_level) if top_level is not None else exporter.DEFAULT_TOP_LEVEL,
        include_core=core,
        check=check,
    )
    if problems:
        console.print(f"[red]Not exported: {len(problems)} problem(s) in the catalogs[/red]", highlight=False)
        _print_problems(problems)
        raise typer.Exit(1)
    for name in report.written:
        console.print(f"[green]Wrote[/green] {escape(str(dest_path / name))}", highlight=False)
    for name in report.stale:
        console.print(f"[yellow]Not generated any more[/yellow] {escape(str(dest_path / name))}", highlight=False)
    if check:
        if report.outdated:
            for name in report.outdated:
                console.print(f"[red]Out of date[/red] {escape(str(dest_path / name))}", highlight=False)
                console.print(f"  {escape(report.differences[name])}", highlight=False)
            console.print(
                "\nRegenerate the files with the same command without --check, then commit them.", highlight=False
            )
            raise typer.Exit(1)
        console.print("[green]Exported files are up to date.[/green]")
        return
    if not report.written:
        console.print("Nothing to update.")
