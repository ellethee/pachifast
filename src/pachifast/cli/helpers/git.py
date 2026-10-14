"""Read the git identity used as a suggestion when creating projects."""

import subprocess
from pathlib import Path


def _git_config(key: str, cwd: Path | None) -> str | None:
    """Return ``git config <key>`` (as seen from ``cwd``), or ``None`` if git or the key is missing."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", key],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=cwd if cwd is not None and cwd.is_dir() else None,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def get_git_identity(cwd: Path | None = None) -> tuple[str | None, str | None]:
    """Return ``(user.name, user.email)`` from git config, each ``None`` when not set.

    Args:
        cwd: Directory the lookup starts from, so a repository-local config wins over the global one.
    """
    return _git_config("user.name", cwd), _git_config("user.email", cwd)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    """Run ``git <args>`` in ``cwd``; ``None`` when git cannot be executed."""
    try:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=15, cwd=cwd, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def init_repository(path: Path) -> bool:
    """Run ``git init`` in ``path`` (branch ``main`` when supported). Returns ``True`` on success."""
    for args in (["init", "-b", "main"], ["init"]):
        result = _run_git(args, path)
        if result is None:
            return False
        if result.returncode == 0:
            return True
    return False


def set_local_identity(path: Path, name: str | None, email: str | None) -> list[str]:
    """Set ``user.name``/``user.email`` in the repository-local config when not already set there.

    Only the repository's own config is read and written: the global config never counts as "already set".

    Returns:
        The keys that were written (a subset of ``["user.name", "user.email"]``).
    """
    written: list[str] = []
    for key, value in (("user.name", name), ("user.email", email)):
        if not value:
            continue
        current = _run_git(["config", "--local", "--get", key], path)
        if current is None:
            break
        if current.returncode == 0 and current.stdout.strip():
            continue
        result = _run_git(["config", "--local", key, value], path)
        if result is not None and result.returncode == 0:
            written.append(key)
    return written
