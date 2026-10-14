"""Development server service."""

import os
from pathlib import Path

from pachifast.cli.helpers.project import ensure_app_module_exists, project_context

# Files whose change restarts the dev server: code, the YAML config file and the .env files.
# ``.*`` is required: uvicorn excludes hidden files by default, and with a ``*.env`` pattern a ``.env`` file would be
# accepted or rejected depending on the (hash randomized) order in which uvicorn tries the patterns.
RELOAD_INCLUDES = ["*.py", "*.yaml", "*.yml", "*.env", ".*"]
# Directories never watched. uvicorn compares them with absolute paths, so they are resolved against the project root.
RELOAD_EXCLUDED_DIRS = (".git", ".venv", "node_modules", ".pytest_cache", ".ruff_cache", ".mypy_cache")


def reload_excludes(root: Path) -> list[str]:
    """Absolute paths of the existing directories under ``root`` that must not restart the dev server.

    Only existing directories are returned: for any other path uvicorn tries to ``glob`` it and fails on an
    absolute pattern.
    """
    return [str(root / name) for name in RELOAD_EXCLUDED_DIRS if (root / name).is_dir()]


def resolve_server_address(base_dir: str, host: str | None = None, port: int | None = None) -> tuple[str, int]:
    """Return the ``(host, port)`` to listen on.

    Priority: explicit arguments (the CLI options), environment variables (``PACHIFAST_HOST``/``PACHIFAST_PORT``),
    the project's ``.env``, the YAML config file, then the defaults (``127.0.0.1``, ``8000``).
    """
    from pachifast.config import ServerSettings

    root = Path(base_dir).resolve()
    with project_context(str(root)):
        settings = ServerSettings(_env_file=str(root / ".env"))  # type: ignore[call-arg]
    return host or settings.host, port or settings.port


def run_dev_server(
    *, base_dir: str = ".", app: str = "main:app", host: str | None = None, port: int | None = None
) -> None:
    """Run uvicorn in reload mode for a generated project.

    Args:
        base_dir: Project root containing ``main.py``.
        app: ASGI application import string.
        host: Bind host; when omitted it comes from the configuration (default ``127.0.0.1``).
        port: Bind port; when omitted it comes from the configuration (default ``8000``).
    """
    import uvicorn

    root = Path(base_dir).resolve()
    ensure_app_module_exists(root, app, "dev")
    host, port = resolve_server_address(str(root), host, port)
    # The server (and its reloader process) must see the project as the current directory so that
    # the .env and the YAML config file are found, whatever --base-dir was.
    os.chdir(root)
    reload_dirs = [str(root)]
    for child_name in ("apps", "common"):
        child_path = root / child_name
        if child_path.exists():
            reload_dirs.append(str(child_path))

    env_file = root / ".env"
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=True,
        app_dir=str(root),
        reload_dirs=reload_dirs,
        reload_includes=RELOAD_INCLUDES,
        reload_excludes=reload_excludes(root),
        env_file=str(env_file) if env_file.exists() else None,
    )
