"""Version lookup for the installed package."""

from importlib.metadata import PackageNotFoundError, version


def get_installed_version() -> str:
    """Return the installed ``pachifast`` version, or ``unknown`` if not installed."""
    try:
        return version("pachifast")
    except PackageNotFoundError:
        return "unknown"
