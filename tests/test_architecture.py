"""Layering of the pachifast packages and top-level modules: who may import whom.

The layers, from the bottom: ``foundation`` < ``infrastructure`` < ``features`` < ``entrypoints``. A package may
import from its own layer and from the ones below; an import that goes *up* is a violation. The violations that
exist today are listed in ``KNOWN_VIOLATIONS`` (technical debt, each with the reason). The list can only shrink:
a new violation fails the test, and so does an entry that is no longer needed (delete it).
"""

from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

import pachifast

LAYERS: dict[str, int] = {
    # foundation: no knowledge of the web framework or of the database
    **dict.fromkeys(
        ("utils", "errors", "logging", "config", "modules", "i18n", "cache", "schemas", "ratelimit", "signals"), 0
    ),
    # infrastructure: engines, routing, migrations, OpenAPI
    **dict.fromkeys(("database", "migrations", "openapi"), 1),
    # features: what the core provides to an application (routing is here: routes carry the authentication)
    **dict.fromkeys(
        (
            "routing",
            "router_factory",
            "auth",
            "mfa",
            "audit",
            "events",
            "admin",
            "health",
            "infos",
            "version",
            "dependencies",
        ),
        2,
    ),
    # entry points: assemble everything
    **dict.fromkeys(("cli", "scaffold", "udp", "app_factory", "startup", "module", "presets", "(root)"), 3),
}

#: (importer, imported): upward imports that exist today.
KNOWN_VIOLATIONS: dict[tuple[str, str], str] = {}

SRC = Path(pachifast.__file__).parent


def _unit(parts: tuple[str, ...]) -> str:
    """The package (or top-level module) a file belongs to; the package ``__init__`` is ``(root)``."""
    if len(parts) > 1:
        return parts[0]
    return "(root)" if parts[0] == "__init__.py" else parts[0].removesuffix(".py")


def _is_unit(name: str) -> bool:
    """True if ``pachifast.<name>`` is a package or a top-level module."""
    return (SRC / name).is_dir() or (SRC / f"{name}.py").is_file()


def _imports() -> dict[tuple[str, str], set[str]]:
    """Cross-package imports as ``{(importer, imported): {files}}``, lazy imports included."""
    found: dict[tuple[str, str], set[str]] = defaultdict(set)
    for file in SRC.rglob("*.py"):
        parts = file.relative_to(SRC).parts
        importer = _unit(parts)
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            elif isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                continue
            for module in modules:
                head = module.split(".")
                if head[0] != "pachifast":
                    continue
                if len(head) > 1:
                    imported_units = [head[1]]
                elif isinstance(node, ast.ImportFrom):  # ``from pachifast import dependencies, X``
                    imported_units = [a.name if _is_unit(a.name) else "(root)" for a in node.names]
                else:
                    imported_units = ["(root)"]
                for imported in imported_units:
                    if imported != importer:
                        found[(importer, imported)].add(str(file.relative_to(SRC)))
    return found


def _upward(imports: dict[tuple[str, str], set[str]]) -> dict[tuple[str, str], set[str]]:
    return {edge: files for edge, files in imports.items() if LAYERS[edge[0]] < LAYERS[edge[1]]}


def test_every_package_has_a_layer() -> None:
    """A new top-level package or module must be placed in ``LAYERS`` (and a package needs its ``__init__.py``)."""
    units = {p.name for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"}
    units |= {p.stem for p in SRC.glob("*.py") if p.stem != "__init__"}
    assert units <= set(LAYERS), sorted(units - set(LAYERS))
    assert not [u for u in units if (SRC / u).is_dir() and not (SRC / u / "__init__.py").exists()]


def test_no_new_upward_imports() -> None:
    """A lower layer must not import a higher one (the known exceptions are listed above)."""
    new = {edge: sorted(files) for edge, files in _upward(_imports()).items() if edge not in KNOWN_VIOLATIONS}
    assert not new, f"upward imports not in KNOWN_VIOLATIONS: {new}"


def test_known_violations_are_still_needed() -> None:
    """When a violation is fixed its entry must go, so that the debt list only shrinks."""
    stale = sorted(set(KNOWN_VIOLATIONS) - set(_upward(_imports())))
    assert not stale, f"remove from KNOWN_VIOLATIONS: {stale}"
