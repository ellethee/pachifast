"""Low-level helpers shared by the scaffolding generators."""

from pathlib import Path
from textwrap import dedent


def normalize_name(value: str) -> str:
    """Normalize and validate CLI-provided names for projects and modules.

    Args:
        value: Raw name typed by the user.

    Returns:
        A valid Python identifier (dashes and spaces become underscores).

    Raises:
        ValueError: If the result is not a valid identifier.
    """
    normalized = value.strip().replace("-", "_").replace(" ", "_")
    if not normalized or not normalized.isidentifier():
        raise ValueError(f"Invalid module name: {value!r}")
    return normalized


def ensure_package(path: Path) -> None:
    """Create a directory and an empty ``__init__.py`` inside it if missing."""
    path.mkdir(parents=True, exist_ok=True)
    init_path = path / "__init__.py"
    if not init_path.exists():
        init_path.write_text("", encoding="utf-8")


def write_file(path: Path, content: str) -> None:
    """Write a dedented template to disk, stripping leading blank lines."""
    path.write_text(dedent(content).lstrip(), encoding="utf-8")
