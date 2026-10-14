"""Public package API for the reusable PachiFast core."""

from importlib.metadata import PackageNotFoundError, version

from pachifast.app_factory import create_app
from pachifast.modules import AppModule, MigrationTarget, ModuleKind

try:
    __version__ = version("pachifast")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0+unknown"

__all__ = ["AppModule", "MigrationTarget", "ModuleKind", "__version__", "create_app"]
