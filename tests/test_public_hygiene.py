"""The public files do not leak local paths or internal hosts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ("git.internal", "/dati/luca", "/home/luca")
SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".txt", ".html", ".j2"}


def _files() -> list[Path]:
    """Files to scan: sources, docs, READMEs and pyproject."""
    found = [ROOT / "pyproject.toml", *ROOT.glob("README*.md")]
    for folder in ("src", "docs"):
        found += [p for p in (ROOT / folder).rglob("*") if p.is_file() and p.suffix in SUFFIXES]
    return [p for p in found if p.is_file()]


def test_no_local_paths_or_internal_hosts() -> None:
    """Fails if a public file mentions a local path or an internal host."""
    hits = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{path.relative_to(ROOT)}: {word}" for word in FORBIDDEN if word in text]
    assert not hits, "\n".join(hits)
