"""``pachifast serve`` command: run the project with uvicorn for production."""

from pathlib import Path
from typing import Annotated

import typer

from pachifast.cli.helpers import handle_errors
from pachifast.cli.services.serve import LOG_LEVELS, run_server


@handle_errors
def serve(
    base_dir: Annotated[Path, typer.Option("--base-dir", help="Project root containing main.py.")] = Path("."),
    app: Annotated[str, typer.Option("--app", help="ASGI app import string.")] = "main:app",
    host: Annotated[str | None, typer.Option("--host", help="Bind host (default: config, else 127.0.0.1).")] = None,
    port: Annotated[int | None, typer.Option("--port", help="Bind port (default: config, else 8000).")] = None,
    workers: Annotated[int, typer.Option("--workers", min=1, help="Number of worker processes.")] = 1,
    proxy_headers: Annotated[
        bool,
        typer.Option(
            "--proxy-headers/--no-proxy-headers",
            help="Trust X-Forwarded-For/-Proto headers, from the addresses in --forwarded-allow-ips only.",
        ),
    ] = True,
    forwarded_allow_ips: Annotated[
        str | None,
        typer.Option(
            "--forwarded-allow-ips",
            help="Comma separated IPs allowed to send proxy headers. Default: uvicorn's (127.0.0.1 only, or the "
            "FORWARDED_ALLOW_IPS variable). Use '*' only when the server is reachable through the proxy alone.",
        ),
    ] = None,
    log_level: Annotated[
        str, typer.Option("--log-level", help=f"Log level: {', '.join(LOG_LEVELS)}.", case_sensitive=False)
    ] = "info",
) -> None:
    """Run the project for production: no reload, multiple workers, proxy headers.

    Host and port come from the options, else from PACHIFAST_HOST/PACHIFAST_PORT, .env or the YAML file.
    """
    run_server(
        base_dir=str(base_dir),
        app=app,
        host=host,
        port=port,
        workers=workers,
        proxy_headers=proxy_headers,
        forwarded_allow_ips=forwarded_allow_ips,
        log_level=log_level.lower(),
    )
