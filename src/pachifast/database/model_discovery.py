import importlib
import inspect
import logging
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import Table

from .base import Base

log = logging.getLogger(__name__)


def scan_models(base_path: Path | str, scan_dirs: Iterable[str]) -> list[Table]:
    """Importa file .py in scan_dirs e ritorna __table__ per i modelli ORM (``Base``) con __auto_create__ = True.

    Usare __auto_create__ = True su una classe modello per includerla nella creazione automatica
    delle tabelle al bootstrap dell'applicazione.
    """
    base_path = Path(base_path)
    tables: list[Table] = []
    seen: set[str] = set()

    for d in scan_dirs:
        target = base_path / d
        if not target.exists():
            log.debug("scan_models: directory %s non trovata, skip", target)
            continue
        for py_file in sorted(target.rglob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = ".".join(py_file.relative_to(base_path).with_suffix("").parts)
            if module_name in seen:
                continue
            seen.add(module_name)
            try:
                mod = importlib.import_module(module_name)
            except Exception as exc:
                log.debug("scan_models: impossibile importare %s: %s", module_name, exc)
                continue
            for _, cls in inspect.getmembers(mod, inspect.isclass):
                if (
                    cls.__module__ == module_name
                    and issubclass(cls, Base)
                    and getattr(cls, "__auto_create__", False)
                    and hasattr(cls, "__table__")
                    and cls.__table__ not in tables
                ):
                    log.debug("scan_models: trovato %s (%s)", cls.__name__, module_name)
                    tables.append(cls.__table__)

    log.info("scan_models: %d tabelle auto-create trovate in %s", len(tables), list(scan_dirs))
    return tables
