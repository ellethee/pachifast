"""The documentation has no broken relative links or anchors."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = sorted([*(ROOT / "docs").rglob("*.md"), ROOT / "README.md", ROOT / "README.it.md", ROOT / "CHANGELOG.md"])
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
FENCE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
EXTERNAL = ("http://", "https://", "mailto:")


def _slug(heading: str) -> str:
    """GitHub-style anchor of a heading."""
    text = re.sub(r"[`*]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    body = FENCE.sub("", path.read_text(encoding="utf-8"))
    return {_slug(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+)$", body, re.M)}


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(ROOT)))
def test_links_resolve(page: Path) -> None:
    body = FENCE.sub("", page.read_text(encoding="utf-8"))
    problems = []
    for target in LINK.findall(body):
        if target.startswith(EXTERNAL):
            continue
        file_part, _, anchor = target.partition("#")
        destination = (page.parent / file_part).resolve() if file_part else page
        if not destination.exists():
            problems.append(f"{target}: file not found")
        elif anchor and destination.suffix == ".md" and anchor not in _anchors(destination):
            problems.append(f"{target}: no such heading")
    assert not problems, "\n".join(problems)
