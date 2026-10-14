"""``pachifast dev`` command."""

from pathlib import Path
from typing import Annotated

import typer

from pachifast.cli.helpers import handle_errors
from pachifast.cli.services.dev import run_dev_server


@handle_errors
def dev(
    base_dir: Annotated[Path, typer.Option("--base-dir", help="Project root containing main.py.")] = Path("."),
    app: Annotated[str, typer.Option("--app", help="ASGI app import string.")] = "main:app",
    host: Annotated[str | None, typer.Option("--host", help="Bind host (default: config, else 127.0.0.1).")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Bind port (default: config, else 8000).")] = None,
) -> None:
    """Run a generated project with uvicorn reload enabled."""
    run_dev_server(base_dir=str(base_dir), app=app, host=host, port=port)
