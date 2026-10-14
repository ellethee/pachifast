import importlib
import inspect
import logging
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import Table

from .base import Base

log = logging.getLogger(__name__)


def scan_models(base_path: Path | str, scan_dirs: Iterable[str]) -> list[Table]:
    """Import .py files in scan_dirs and return __table__ for ORM models (``Base``) with __auto_create__ = True.

    Set __auto_create__ = True on a model class to include it in the automatic
    table creation at application bootstrap.
    """
    base_path = Path(base_path)
    tables: list[Table] = []
    seen: set[str] = set()

    for d in scan_dirs:
        target = base_path / d
        if not target.exists():
            log.debug("scan_models: directory %s not found, skipping", target)
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
