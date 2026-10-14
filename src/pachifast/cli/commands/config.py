"""``pachifast config`` commands: the JSON Schema of the configuration file."""

import json
from pathlib import Path
from typing import Annotated

import typer

from pachifast.cli.helpers import console, handle_errors

app = typer.Typer(help="Tools for the pachifast.yaml configuration file.", no_args_is_help=True)


@app.command("schema")
@handle_errors
def schema(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write the schema to this file instead of printing it."),
    ] = None,
) -> None:
    """Print (or write) the JSON Schema of pachifast.yaml, for completion and validation in the editor."""
    from pachifast.config.schema import build_config_schema

    text = json.dumps(build_config_schema(), indent=2, ensure_ascii=False) + "\n"
    if output is None:
        typer.echo(text, nl=False)
        return
    output.write_text(text, encoding="utf-8")
    console.print(f"Schema written to {output}", highlight=False)
