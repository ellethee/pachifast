"""Project metadata (the classic ``[project]`` table of ``pyproject.toml``) for new projects."""

import re
from dataclasses import dataclass, field

DEFAULT_VERSION = "0.1.0"
DEFAULT_DESCRIPTION = "FastAPI service built on pachifast."
DEFAULT_REQUIRES_PYTHON = ">=3.11"

# Simplified PEP 440 (release segment with optional pre/post/dev suffix) and PEP 440 version specifiers.
_VERSION_RE = re.compile(r"^\d+(\.\d+)*([.\-_]?(a|b|rc|post|dev)\d*)?$")
_SPECIFIER_RE = re.compile(r"^(>=|<=|==|~=|!=|>|<)\s*\d+(\.\d+)*(\.\*)?$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_URL_RE = re.compile(r"^https?://\S+$")
# PEP 508 requirement, simplified: name[extras] then optional version specifiers and/or an environment marker.
_REQUIREMENT_RE = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
    r"(?:\s*\[[A-Za-z0-9._, -]*\])?"
    r"(?:\s*(?:>=|<=|==|~=|!=|>|<)\s*[A-Za-z0-9.*+!_-]+(?:\s*,\s*(?:>=|<=|==|~=|!=|>|<)\s*[A-Za-z0-9.*+!_-]+)*)?"
    r"(?:\s*;\s*\S.*)?$"
)
_REQUIREMENT_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


@dataclass
class ProjectMetadata:
    """Values written in the ``[project]`` table of a new project's ``pyproject.toml``.

    Attributes:
        version: Project version (PEP 440).
        description: One-line description.
        requires_python: Python version specifier, e.g. ``>=3.11``.
        author: Author name; omitted from the file when empty.
        author_email: Author email; omitted from the file when empty.
        license: SPDX license expression (e.g. ``MIT``); omitted when empty.
        keywords: Search keywords.
        homepage: Homepage URL; omitted when empty.
        repository: Source repository URL; omitted when empty.
        packages: Extra runtime dependencies (PEP 508 strings), added after ``pachifast``.
        dev_packages: Development dependencies, written in the ``dev`` dependency group.
    """

    version: str = DEFAULT_VERSION
    description: str = DEFAULT_DESCRIPTION
    requires_python: str = DEFAULT_REQUIRES_PYTHON
    author: str | None = None
    author_email: str | None = None
    license: str | None = None
    keywords: list[str] = field(default_factory=list)
    homepage: str | None = None
    repository: str | None = None
    packages: list[str] = field(default_factory=list)
    dev_packages: list[str] = field(default_factory=list)

    def normalized(self) -> "ProjectMetadata":
        """Return a copy with whitespace stripped and empty optional values turned into ``None``."""
        return ProjectMetadata(
            version=self.version.strip(),
            description=self.description.strip(),
            requires_python=self.requires_python.strip(),
            author=(self.author or "").strip() or None,
            author_email=(self.author_email or "").strip() or None,
            license=(self.license or "").strip() or None,
            keywords=[keyword.strip() for keyword in self.keywords if keyword.strip()],
            homepage=(self.homepage or "").strip() or None,
            repository=(self.repository or "").strip() or None,
            packages=[package.strip() for package in self.packages if package.strip()],
            dev_packages=[package.strip() for package in self.dev_packages if package.strip()],
        )

    def validate(self) -> None:
        """Check the values that would make the generated ``pyproject.toml`` invalid.

        Raises:
            ValueError: With a message naming the offending field.
        """
        check_version(self.version)
        check_requires_python(self.requires_python)
        check_email(self.author_email)
        check_url("homepage", self.homepage)
        check_url("repository", self.repository)
        for requirement in (*self.packages, *self.dev_packages):
            check_requirement(requirement)


def check_version(value: str) -> None:
    """Raise ``ValueError`` if ``value`` is not a (simplified) PEP 440 version."""
    if not _VERSION_RE.match(value.strip()):
        raise ValueError(f"Invalid version: {value!r} (expected e.g. 0.1.0).")


def check_requires_python(value: str) -> None:
    """Raise ``ValueError`` if ``value`` is not a comma-separated list of version specifiers."""
    if not all(_SPECIFIER_RE.match(part.strip()) for part in value.split(",")):
        raise ValueError(f"Invalid requires-python: {value!r} (expected e.g. >=3.11).")


def check_email(value: str | None) -> None:
    """Raise ``ValueError`` if a non-empty ``value`` does not look like an email address."""
    if value and value.strip() and not _EMAIL_RE.match(value.strip()):
        raise ValueError(f"Invalid author email: {value!r}.")


def check_url(label: str, value: str | None) -> None:
    """Raise ``ValueError`` if a non-empty ``value`` is not an http(s) URL."""
    if value and value.strip() and not _URL_RE.match(value.strip()):
        raise ValueError(f"Invalid {label} URL: {value!r} (expected http:// or https://).")


def check_requirement(value: str) -> None:
    """Raise ``ValueError`` if ``value`` is not a (simplified) PEP 508 requirement, e.g. ``httpx>=0.27``."""
    if not _REQUIREMENT_RE.match(value.strip()):
        raise ValueError(f"Invalid package requirement: {value!r} (expected e.g. httpx or httpx>=0.27,<1).")


def requirement_name(value: str) -> str:
    """Return the normalized (PEP 503) distribution name of a requirement string."""
    match = _REQUIREMENT_NAME_RE.match(value.strip())
    return re.sub(r"[-_.]+", "-", match.group(0)).lower() if match else value.strip().lower()


def parse_requirements(value: str) -> list[str]:
    """Split a comma-separated list of requirements, keeping commas that belong to version specifiers.

    ``"httpx>=0.27,<1, rich"`` gives ``["httpx>=0.27,<1", "rich"]``.
    """
    return [part.strip() for part in re.split(r",\s*(?![<>=!~])", value) if part.strip()]


def parse_keywords(value: str) -> list[str]:
    """Split a comma-separated keyword string into a clean list."""
    return [keyword.strip() for keyword in value.split(",") if keyword.strip()]
