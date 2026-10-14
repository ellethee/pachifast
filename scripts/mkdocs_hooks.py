"""MkDocs hooks: put the version of pachifast (from ``pyproject.toml``) in the site.

The version shows in the footer (``copyright``) and, as ``config.extra.version``, in the home page.
"""

from __future__ import annotations

import tomllib
from pathlib import Path


def on_config(config):  # type: ignore[no-untyped-def]
    """Read the version once and expose it to the theme."""
    pyproject = Path(config.config_file_path).parent / "pyproject.toml"
    version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    config.extra["version"] = version
    config.copyright = f"pachifast {version} &middot; MIT License &middot; alpha"
    return config
