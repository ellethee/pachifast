"""Helper utilities for the pachifast CLI."""

from pachifast.cli.helpers.console import console, err_console, handle_errors
from pachifast.cli.helpers.git import get_git_identity, init_repository, set_local_identity
from pachifast.cli.helpers.project import ensure_app_module_exists, load_project_modules_if_present, project_context
from pachifast.cli.helpers.prompt import enable_line_editing
from pachifast.cli.helpers.version import get_installed_version

__all__ = [
    "console",
    "enable_line_editing",
    "ensure_app_module_exists",
    "err_console",
    "get_git_identity",
    "get_installed_version",
    "handle_errors",
    "init_repository",
    "load_project_modules_if_present",
    "project_context",
    "set_local_identity",
]
