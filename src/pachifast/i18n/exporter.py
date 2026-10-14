"""Export of the catalogs for a vue-i18n frontend: one nested JSON file per language.

The files are generated: nobody edits them, and the export never reads the ones that exist (except to compare them
with ``check``). A text that an end user wants to change goes in a separate override file merged after the generated
one by the frontend, or in the project's ``locale/`` directory when the backend text itself must change.
"""

import json
import string
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pachifast.i18n.loader import Catalogs

# Characters with a meaning in the vue-i18n message format: a literal one is written as {'x'}.
_VUE_SPECIAL = frozenset("{}@|")  # "$" is not special in vue-i18n 11 (verified): it is left as it is


def to_vue_i18n(template: str) -> str:
    """Convert a ``str.format`` template into a vue-i18n message.

    The ``{fields}`` stay as they are (vue-i18n uses the same named interpolation); the literal text has its special
    characters (``{ } @ |``, and the ``{{ }}`` of a Python template) escaped.
    """
    parts: list[str] = []
    for literal, field_name, _, _ in string.Formatter().parse(template):
        parts.append("".join(f"{{'{char}'}}" if char in _VUE_SPECIAL else char for char in literal))
        if field_name is not None:
            parts.append(f"{{{field_name}}}")
    return "".join(parts)


def nest(messages: dict[str, str]) -> dict[str, Any]:
    """Turn dotted codes into nested objects: ``{"a.b": "x"}`` -> ``{"a": {"b": "x"}}``.

    Raises:
        ValueError: If a code is both a text and the prefix of another code.
    """
    tree: dict[str, Any] = {}
    for code in sorted(messages):
        *groups, leaf = code.split(".")
        node = tree
        for name in groups:
            child = node.setdefault(name, {})
            if not isinstance(child, dict):
                raise ValueError(f"{code}: {'.'.join(groups)} is already a text")
            node = child
        if isinstance(node.get(leaf), dict):
            raise ValueError(f"{code} is also the prefix of other codes")
        node[leaf] = to_vue_i18n(messages[code])
    return tree


# Roots that stay at the top of the file when a namespace wraps the rest: the texts of the user settings schema and
# the names of permissions and groups (their keys, sent by the API, are absolute).
DEFAULT_TOP_LEVEL = ("settings", "permissions", "groups")


def render_locale(messages: dict[str, str], namespace: str = "", top_level: Iterable[str] = DEFAULT_TOP_LEVEL) -> str:
    """The JSON text of one language (stable order, UTF-8, trailing newline).

    Without ``namespace`` the tree is the codes as they are. With it (``errors.codes``) the codes are wrapped under that
    path, except those whose first part is in ``top_level`` (default ``settings``): they stay at the root, so the file
    has several roots: ``{"errors": {"codes": {...}}, "settings": {...}}``.

    Raises:
        ValueError: If a code is both a text and a group, or the namespace collides with a top-level root.
    """
    roots = {name for name in top_level if name}
    wrapped = {code: text for code, text in messages.items() if not (namespace and code.split(".")[0] in roots)}
    kept = {code: text for code, text in messages.items() if code not in wrapped}
    tree: dict[str, Any] = nest(wrapped)
    for name in reversed([part for part in namespace.split(".") if part]):
        tree = {name: tree}
    for root, subtree in nest(kept).items():
        if root in tree:
            raise ValueError(f"The namespace {namespace!r} collides with the top-level root {root!r}")
        tree[root] = subtree
    return json.dumps(tree, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def render_all(catalogs: Catalogs, namespace: str = "", top_level: Iterable[str] = DEFAULT_TOP_LEVEL) -> dict[str, str]:
    """``{file name: text}`` for every language (``<language>.json``)."""
    return {
        f"{locale}.json": render_locale(messages, namespace, top_level) for locale, messages in sorted(catalogs.items())
    }


def _flatten(tree: Any, prefix: str = "") -> dict[str, str]:
    """``{"a": {"b": "x"}}`` -> ``{"a.b": "x"}``."""
    if not isinstance(tree, dict):
        return {prefix: str(tree)}
    flat: dict[str, str] = {}
    for key, value in tree.items():
        flat.update(_flatten(value, f"{prefix}.{key}" if prefix else key))
    return flat


def describe_difference(current: str | None, expected: str) -> str:
    """One line on how an existing generated file differs from what an export would write.

    ``current`` is ``None`` for a missing file. The keys are listed (up to a few) so that a CI log says what changed.
    """
    if current is None:
        return "file is missing"
    try:
        old, new = _flatten(json.loads(current)), _flatten(json.loads(expected))
    except ValueError:
        return "file is not valid JSON"
    added, removed = sorted(new.keys() - old.keys()), sorted(old.keys() - new.keys())
    changed = sorted(key for key in new.keys() & old.keys() if new[key] != old[key])
    parts = []
    for label, keys in (("missing", added), ("not generated any more", removed), ("different text", changed)):
        if keys:
            shown = ", ".join(keys[:5]) + (f" (+{len(keys) - 5} more)" if len(keys) > 5 else "")
            parts.append(f"{len(keys)} {label}: {shown}")
    return "; ".join(parts) or "same messages, different formatting"


@dataclass
class ExportReport:
    """What an export did (or, with ``check``, what it would do)."""

    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    outdated: list[str] = field(default_factory=list)
    differences: dict[str, str] = field(default_factory=dict)
    stale: list[str] = field(default_factory=list)


def write_files(destination: Path, files: dict[str, str], *, check: bool = False) -> ExportReport:
    """Write the generated files into ``destination`` (created if it does not exist, its parents must).

    With ``check`` nothing is written: the report lists the files that are missing or different (``outdated``).
    ``stale`` names the ``.json`` files of the folder that no language produces any more: they are only reported,
    never deleted.
    """
    report = ExportReport()
    if not check:
        destination.mkdir(exist_ok=True)
    for name, text in files.items():
        target = destination / name
        current = target.read_text(encoding="utf-8") if target.is_file() else None
        if current == text:
            report.unchanged.append(name)
        elif check:
            report.outdated.append(name)
            report.differences[name] = describe_difference(current, text)
        else:
            target.write_text(text, encoding="utf-8")
            report.written.append(name)
    if destination.is_dir():
        report.stale = sorted(path.name for path in destination.glob("*.json") if path.name not in files)
    return report
