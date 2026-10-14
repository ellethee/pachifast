"""Helpers to work inside a generated project directory."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def project_context(base_dir: str) -> Iterator[Path]:
    """Temporarily change the working directory to the project root.

    Args:
        base_dir: Project root directory.

    Yields:
        The resolved project root.
    """
    root = Path(base_dir).resolve()
    previous_cwd = Path.cwd()
    os.chdir(root)
    try:
        yield root
    finally:
        os.chdir(previous_cwd)


def load_project_modules_if_present(project_root: Path) -> tuple:
    """Load ``MODULES`` from ``project_modules.py`` if the file exists, else an empty tuple."""
    from pachifast.migrations.runtime import _load_project_modules

    if not (project_root / "project_modules.py").exists():
        return ()
    return _load_project_modules(project_root)


def ensure_app_module_exists(root: Path, app: str, command: str) -> None:
    """Check that the module of an ASGI import string (``main:app``) exists in the project directory.

    Args:
        root: Project root.
        app: ASGI application import string.
        command: CLI command name, used in the error message (``dev`` or ``serve``).

    Raises:
        FileNotFoundError: With a hint on how to run the command from a generated project.
    """
    module_name = app.partition(":")[0]
    module_path = Path(*module_name.split("."))
    if not (root / module_path.with_suffix(".py")).exists() and not (root / module_path / "__init__.py").exists():
        raise FileNotFoundError(
            f"Module {module_name!r} not found in {root}. "
            f"Run `pachifast {command}` from a generated project (see `pachifast new project <name>`), "
            "or use --base-dir / --app."
        )
