"""Finding, loading and checking the message catalogs of the core, the modules and the project.

A *locale package* is a directory with one Python file per language (``en.py``, ``it.py``, ``pt-BR.py``: any file
name that is a language tag is a language). Each file defines ``MESSAGES: dict[str, str]``, a stable dotted code
(``billing.invoice_not_found``) mapped to a ``str.format`` template (``"Invoice {number} not found"``).

Layers, in increasing priority (a later one redefines the codes of the earlier ones):

1. the core (``pachifast.i18n.locale``);
2. the ``locale_package`` of every module of the project, in registration order;
3. the project's own ``locale/`` directory, for changing the default text of any code, core ones included.
"""

import importlib
import importlib.util
import re
import string
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pachifast.modules import AppModule

CORE_PACKAGE = "pachifast.i18n.locale"
PROJECT_LOCALE_DIR = "locale"

_LOCALE_TAG = re.compile(r"^[A-Za-z]{2,3}([_-][A-Za-z0-9]{2,8})*$")
_CODE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

LayerKind = Literal["core", "module", "override"]
Catalogs = dict[str, dict[str, str]]


class LocaleError(ValueError):
    """A locale package or file that cannot be loaded (missing package, no ``MESSAGES``, wrong types...)."""


@dataclass(frozen=True)
class LocaleLayer:
    """The messages of one source: ``messages[locale][code]``."""

    source: str
    kind: LayerKind
    messages: Catalogs = field(default_factory=dict)


@dataclass(frozen=True)
class Problem:
    """Something wrong in the catalogs found by :func:`check_layers`."""

    message: str


def check_locale_tag(tag: str) -> None:
    """Raise ``ValueError`` unless ``tag`` looks like a language tag (``it``, ``en-US``, ``pt_BR``)."""
    if not _LOCALE_TAG.match(tag):
        raise ValueError(f"Invalid language {tag!r}: expected a language tag such as 'it', 'en-US' or 'pt_BR'.")


def _load_messages_file(path: Path) -> dict[str, str]:
    """Execute a locale file and return its ``MESSAGES`` (validated)."""
    spec = importlib.util.spec_from_file_location(f"_pachifast_locale_{abs(hash(path))}", path)
    if spec is None or spec.loader is None:
        raise LocaleError(f"Cannot load the locale file {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise LocaleError(f"Error in the locale file {path}: {exc}") from exc
    messages = getattr(module, "MESSAGES", None)
    if not isinstance(messages, dict):
        raise LocaleError(f"{path} must define MESSAGES: dict[str, str].")
    for code, text in messages.items():
        if not isinstance(code, str) or not isinstance(text, str):
            raise LocaleError(f"{path}: MESSAGES must map str codes to str texts (bad entry {code!r}).")
    return dict(messages)


def load_locale_directory(directory: Path) -> Catalogs:
    """Load every ``<language>.py`` of a directory (files starting with ``_`` are ignored)."""
    catalogs: Catalogs = {}
    for path in sorted(directory.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        try:
            check_locale_tag(path.stem)
        except ValueError as exc:
            raise LocaleError(f"{path}: {exc}") from None
        catalogs[path.stem] = _load_messages_file(path)
    return catalogs


def _package_directory(package: str) -> Path:
    """Directory of an importable package (its parent packages are imported)."""
    try:
        spec = importlib.util.find_spec(package)
    except (ImportError, ValueError) as exc:
        raise LocaleError(f"Locale package {package!r} cannot be imported: {exc}") from exc
    if spec is None or not spec.submodule_search_locations:
        raise LocaleError(f"Locale package {package!r} not found: is it a package (a folder with __init__.py)?")
    return Path(next(iter(spec.submodule_search_locations)))


def load_core_catalogs() -> Catalogs:
    """The catalogs of the core only."""
    return load_locale_directory(_package_directory(CORE_PACKAGE))


def collect_layers(modules: Iterable[AppModule] = (), project_root: Path | None = None) -> list[LocaleLayer]:
    """The catalogs of the core, of the modules that declare a ``locale_package`` and of the project directory."""
    layers = [LocaleLayer("pachifast (core)", "core", load_core_catalogs())]
    seen: set[str] = set()
    for module in modules:
        package = module.locale_package
        if not package or package in seen:
            continue
        seen.add(package)
        layers.append(LocaleLayer(package, "module", load_locale_directory(_package_directory(package))))
    if project_root is not None and (project_root / PROJECT_LOCALE_DIR).is_dir():
        directory = project_root / PROJECT_LOCALE_DIR
        layers.append(LocaleLayer(f"{PROJECT_LOCALE_DIR}/ (project)", "override", load_locale_directory(directory)))
    return layers


def merge_layers(layers: Sequence[LocaleLayer]) -> Catalogs:
    """Merge the layers: for the same language and code the later layer wins."""
    merged: Catalogs = {}
    for layer in layers:
        for locale, messages in layer.messages.items():
            merged.setdefault(locale, {}).update(messages)
    return merged


def placeholders(template: str) -> set[str]:
    """Names of the ``{fields}`` of a template.

    Raises:
        ValueError: If the template is malformed or uses anything but plain names (``{a.b}``, ``{0}``, ``{a!r}``).
    """
    names: set[str] = set()
    for _, field_name, spec, conversion in string.Formatter().parse(template):
        if field_name is None:
            continue
        if not field_name.isidentifier() or spec or conversion:
            raise ValueError(f"unsupported field {{{field_name}}} (only plain names such as {{min_length}})")
        names.add(field_name)
    return names


def check_layers(layers: Sequence[LocaleLayer]) -> list[Problem]:
    """Find what would break a client: bad codes or templates, missing translations, mismatching fields.

    A code defined by two modules is a conflict; only the project directory may redefine a code of another layer.
    """
    problems: list[Problem] = []

    for layer in layers:
        for locale, messages in layer.messages.items():
            for code, text in messages.items():
                if not _CODE.match(code):
                    problems.append(Problem(f"{layer.source} [{locale}]: invalid code {code!r}"))
                try:
                    placeholders(text)
                except ValueError as exc:
                    problems.append(Problem(f"{layer.source} [{locale}] {code}: {exc}"))

    owners: dict[tuple[str, str], str] = {}
    for layer in layers:
        if layer.kind == "override":
            continue
        for locale, messages in layer.messages.items():
            for code in messages:
                first = owners.setdefault((locale, code), layer.source)
                if first != layer.source:
                    problems.append(Problem(f"[{locale}] {code} is defined by {first} and by {layer.source}"))

    merged = merge_layers(layers)
    codes = sorted({code for messages in merged.values() for code in messages})
    for locale, messages in sorted(merged.items()):
        missing = [code for code in codes if code not in messages]
        if missing:
            shown = ", ".join(missing[:8]) + (f" (+{len(missing) - 8} more)" if len(missing) > 8 else "")
            problems.append(Problem(f"[{locale}] missing {len(missing)} message(s): {shown}"))

    for code in codes:
        fields = {}
        for locale, messages in merged.items():
            if code in messages:
                try:
                    fields[locale] = placeholders(messages[code])
                except ValueError:
                    continue  # already reported above
        if len({frozenset(names) for names in fields.values()}) > 1:
            detail = "; ".join(f"{loc}: {sorted(names)}" for loc, names in sorted(fields.items()))
            problems.append(Problem(f"{code}: the languages use different fields ({detail})"))

    code_set = set(codes)
    for code in codes:
        if any(other.startswith(code + ".") for other in code_set):
            problems.append(Problem(f"{code} is also the prefix of other codes: it cannot be both a text and a group"))
    return problems
