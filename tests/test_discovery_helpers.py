"""File-scan helpers: models, routers and per-app settings discovery."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest

from pachifast.config.settings_discovery import discover_app_settings
from pachifast.database.model_discovery import scan_models
from pachifast.routing.file_discover import scan_router_dirs


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A temp project root on ``sys.path``; imported throwaway modules are dropped afterwards."""
    monkeypatch.syspath_prepend(str(tmp_path))
    yield tmp_path
    for name, module in list(sys.modules.items()):
        if str(getattr(module, "__file__", "") or "").startswith(str(tmp_path)):
            del sys.modules[name]


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_models_returns_only_auto_create_tables(project: Path) -> None:
    _write(project, "scanapp/__init__.py", "")
    _write(
        project,
        "scanapp/models.py",
        "from sqlalchemy.orm import Mapped, mapped_column\n"
        "from pachifast.database import Base\n"
        "class Auto(Base):\n"
        "    __tablename__ = 'scan_auto'\n"
        "    __auto_create__ = True\n"
        "    id: Mapped[int] = mapped_column(primary_key=True)\n"
        "class Manual(Base):\n"
        "    __tablename__ = 'scan_manual'\n"
        "    id: Mapped[int] = mapped_column(primary_key=True)\n",
    )
    _write(project, "scanapp/_private.py", "raise RuntimeError('never imported')")
    _write(project, "scanapp/broken.py", "raise RuntimeError('broken')")
    tables = scan_models(project, ["scanapp", "missing_dir"])
    assert [t.name for t in tables] == ["scan_auto"]


def test_scan_router_dirs_collects_routers(project: Path) -> None:
    _write(project, "scanr/__init__.py", "")
    _write(
        project,
        "scanr/items.py",
        "from pachifast.routing.router_factory import get_router\nrouter = get_router(1, deps=['noauth'])\n",
    )
    _write(
        project,
        "scanr/off.py",
        "from pachifast.routing.router_factory import get_router\n"
        "router = get_router(1, deps=['noauth'])\ndisable_router = True\n",
    )
    _write(project, "scanr/plain.py", "x = 1\n")
    _write(project, "scanr/bad.py", "from nowhere import router\n")
    routers = scan_router_dirs(("scanr", "absent"), project)
    assert len(routers) == 1
    assert routers[0].pachifast_router_key == "scanr.items"


class _Target:
    pass


def test_discover_app_settings_variants(project: Path) -> None:
    _write(project, "dscapps/__init__.py", "")
    _write(project, "dscapps/reg/__init__.py", "")
    _write(project, "dscapps/reg/settings.py", "def register_settings(s):\n    s.from_register = 1\n")
    _write(project, "dscapps/plain/__init__.py", "")
    _write(
        project,
        "dscapps/plain/settings.py",
        "class _S:\n    def __init__(self):\n        self.a = 1\nplain_settings = _S()\nMAX_ITEMS = 7\n",
    )
    _write(project, "dscapps/boom/__init__.py", "")
    _write(project, "dscapps/boom/settings.py", "raise RuntimeError('boom')\n")
    target = _Target()
    discover_app_settings(target, scan_dirs=("dscapps", "nope"), root=project)
    assert target.from_register == 1
    assert target.plain.a == 1 and target.max_items == 7
