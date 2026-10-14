"""Shared rich consoles and error handling for the CLI commands."""

from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

import typer
from rich.console import Console

console = Console()
err_console = Console(stderr=True)

F = TypeVar("F", bound=Callable[..., Any])


def handle_errors(func: F) -> F:
    """Decorate a command so unexpected exceptions become a clean error and exit code 1.

    ``typer.Exit`` and ``typer.Abort`` are propagated untouched.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except (typer.Exit, typer.Abort):
            raise
        except Exception as exc:
            err_console.print(f"[bold red]error:[/bold red] {exc}", markup=True, highlight=False)
            raise typer.Exit(code=1) from exc

    return wrapper  # type: ignore[return-value]
