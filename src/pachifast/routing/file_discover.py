"""File-scan router discovery — alternativa all'elenco esplicito in AppModule.router_modules."""

import importlib
import logging
from pathlib import Path

from pachifast.routing.core_filters import router_key

log = logging.getLogger(__name__)


def scan_router_dirs(dirs: tuple[str, ...], root: Path) -> list:
    """Scansiona `dirs` cercando file Python che contengono `router = get_router`.

    Ritorna la lista di router trovati. Analoga a `routing/discover.py`,
    ma come funzione standalone riusabile da qualsiasi AppModule.

    Args:
        dirs: nomi di cartelle relative a `root` da scansionare (es. ``("core", "apps")``).
        root: directory radice del progetto (tipicamente ``Path.cwd()``).
    """
    routers = []
    for folder in dirs:
        folder_path = root / folder
        if not folder_path.exists():
            log.warning("scan_router_dirs: directory %s non trovata, saltata", folder_path)
            continue
        for path in folder_path.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8")
                if "router = get_router" not in text and "import router" not in text:
                    continue
                module_path = path.relative_to(root).with_suffix("").as_posix().replace("/", ".")
                log.debug("file-scan: importo %s", module_path)
                mod = importlib.import_module(module_path)
                if getattr(mod, "disable_router", False):
                    continue
                router = getattr(mod, "router", None)
                if router is not None:
                    router.pachifast_router_key = router_key(module_path)
                    routers.append(router)
            except Exception:
                log.exception("file-scan: errore nell'import di %s", path)
    return routers
