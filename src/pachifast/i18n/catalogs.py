"""The message catalogs in use: ``CATALOGS[locale][code]`` -> text template.

The core catalogs are loaded when this module is imported; :func:`configure_catalogs` (called by ``create_app``)
adds the ones of the project's modules and its ``locale/`` directory. The dictionary is updated in place, so the
references held by other modules stay valid. See :mod:`pachifast.i18n.loader` for the layers and the file format.
"""

from collections.abc import Iterable
from pathlib import Path

from pachifast.i18n.loader import Catalogs, collect_layers, load_core_catalogs, merge_layers
from pachifast.modules import AppModule

FALLBACK_LOCALE = "en"

CATALOGS: Catalogs = {}


def _replace(catalogs: Catalogs) -> None:
    CATALOGS.clear()
    CATALOGS.update(catalogs)


def configure_catalogs(modules: Iterable[AppModule] = (), project_root: Path | None = None) -> Catalogs:
    """Load the catalogs of the core, ``modules`` and ``project_root/locale`` and make them the active ones.

    Raises:
        pachifast.i18n.loader.LocaleError: If a locale package or file cannot be loaded.
    """
    catalogs = merge_layers(collect_layers(modules, project_root))
    _replace(catalogs)
    return catalogs


def supported_locales() -> tuple[str, ...]:
    """The languages that have a catalog."""
    return tuple(CATALOGS)


_replace(load_core_catalogs())
