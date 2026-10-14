"""Verifica che nel repository non compaiano riferimenti aziendali o legacy."""

import re
from pathlib import Path

FORBIDDEN = re.compile(
    r"essepaghe|wsp|etefast|epsso|fasteos|webazienda|zaccaria|wa-docker|django|auth_user|pbkdf2|poetry|eos_",
    re.IGNORECASE,
)
EXCLUDED_DIRS = {".git", ".venv", "__pycache__", "dist", "node_modules", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
EXCLUDED_FILES = {"uv.lock"}
ROOT = Path(__file__).resolve().parent.parent


def _iter_files() -> list[Path]:
    """Elenca i file del repository da controllare.

    Returns:
        Lista di percorsi, esclusi cartelle di sistema, lock e questo file.
    """
    this_file = Path(__file__).resolve()
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.resolve() == this_file:
            continue
        rel = path.relative_to(ROOT)
        if any(part in EXCLUDED_DIRS for part in rel.parts) or path.name in EXCLUDED_FILES:
            continue
        files.append(path)
    return files


def test_no_forbidden_terms() -> None:
    """Fallisce se un file (o il suo nome) contiene termini vietati."""
    hits: list[str] = []
    for path in _iter_files():
        rel = path.relative_to(ROOT)
        if FORBIDDEN.search(str(rel)):
            hits.append(f"{rel} (nome file)")
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            match = FORBIDDEN.search(line)
            if match:
                hits.append(f"{rel}:{lineno}: {match.group(0)}")
    assert not hits, "Riferimenti vietati trovati:\n" + "\n".join(hits)
