"""The package declares its version and says that it ships type hints."""

from importlib.metadata import version
from pathlib import Path

import pachifast


def test_the_version_is_the_installed_one() -> None:
    assert pachifast.__version__ == version("pachifast")


def test_the_package_is_marked_as_typed() -> None:
    assert (Path(pachifast.__file__).parent / "py.typed").is_file()
