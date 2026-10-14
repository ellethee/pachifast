"""Production server service behind ``pachifast serve``."""

import os
from pathlib import Path
from typing import Any

from pachifast.cli.helpers.project import ensure_app_module_exists
from pachifast.cli.services.dev import resolve_server_address

LOG_LEVELS = ("critical", "error", "warning", "info", "debug", "trace")


def build_uvicorn_options(
    *,
    base_dir: str = ".",
    app: str = "main:app",
    host: str | None = None,
    port: int | None = None,
    workers: int = 1,
    proxy_headers: bool = True,
    forwarded_allow_ips: str | None = None,
    log_level: str = "info",
) -> dict[str, Any]:
    """Validate the options and return the keyword arguments for ``uvicorn.run`` (no reload).

    ``host`` and ``port`` follow the usual priority: arguments, environment variables, ``.env``, YAML file,
    defaults. ``forwarded_allow_ips`` ``None`` keeps uvicorn's own default (trust ``127.0.0.1`` only, or the
    ``FORWARDED_ALLOW_IPS`` environment variable).

    Raises:
        FileNotFoundError: If the ASGI module is not in the project directory.
        ValueError: If ``workers`` is lower than 1 or ``log_level`` is unknown.
    """
    if workers < 1:
        raise ValueError("--workers must be at least 1.")
    if log_level not in LOG_LEVELS:
        raise ValueError(f"Invalid --log-level {log_level!r}: choose one of {', '.join(LOG_LEVELS)}.")
    root = Path(base_dir).resolve()
    ensure_app_module_exists(root, app, "serve")
    host, port = resolve_server_address(str(root), host, port)
    options: dict[str, Any] = {
        "host": host,
        "port": port,
        "workers": workers,
        "proxy_headers": proxy_headers,
        "log_level": log_level,
        "reload": False,
        "app_dir": str(root),
    }
    if forwarded_allow_ips is not None:
        options["forwarded_allow_ips"] = forwarded_allow_ips
    return options


def run_server(
    *,
    base_dir: str = ".",
    app: str = "main:app",
    host: str | None = None,
    port: int | None = None,
    workers: int = 1,
    proxy_headers: bool = True,
    forwarded_allow_ips: str | None = None,
    log_level: str = "info",
) -> None:
    """Run uvicorn for production (no reload) on a generated project.

    The process moves into the project directory first, so the ``.env`` and the YAML configuration file
    are found whatever ``base_dir`` was. Configuration comes from the environment (and the optional files),
    ``.env`` is never required.
    """
    import uvicorn

    options = build_uvicorn_options(
        base_dir=base_dir,
        app=app,
        host=host,
        port=port,
        workers=workers,
        proxy_headers=proxy_headers,
        forwarded_allow_ips=forwarded_allow_ips,
        log_level=log_level,
    )
    os.chdir(options["app_dir"])
    uvicorn.run(app, **options)
