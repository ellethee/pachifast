"""Router discovery backed by the explicit module registry."""

import importlib
import logging

from pachifast.modules import AppModule, get_module_kind, resolve_modules
from pachifast.routing.core_filters import router_key

log = logging.getLogger(__name__)


def get_routers(modules: tuple[AppModule, ...] | None = None):
    """Return all routers declared by the configured modules."""
    from pathlib import Path

    routers = []

    for app_module in resolve_modules(modules):
        # Elenco esplicito di router_modules
        for module_path in app_module.router_modules:
            try:
                log.debug("Importing router module %s", module_path)
                mod = importlib.import_module(module_path)

                if getattr(mod, "disable_router", False):
                    continue

                router = getattr(mod, "router", None)
                if router is not None:
                    router.pachifast_module_name = app_module.name  # type: ignore[attr-defined]
                    router.pachifast_module_kind = get_module_kind(app_module)  # type: ignore[attr-defined]
                    router.pachifast_router_key = router_key(module_path)  # type: ignore[attr-defined]
                    routers.append(router)
            except Exception as exc:
                log.exception("Error importing router module %s: %s", module_path, exc)

        # File-scan opzionale via scan_dirs
        if app_module.scan_dirs:
            from pachifast.routing.file_discover import scan_router_dirs

            for router in scan_router_dirs(app_module.scan_dirs, Path.cwd()):
                router.pachifast_module_name = app_module.name  # type: ignore[attr-defined]
                router.pachifast_module_kind = get_module_kind(app_module)  # type: ignore[attr-defined]
                routers.append(router)

    return routers
