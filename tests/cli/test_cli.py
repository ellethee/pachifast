"""Tests for the Typer based pachifast CLI."""

import os
import re
import tomllib
from pathlib import Path

from typer.testing import CliRunner

from pachifast.cli import app

runner = CliRunner()


def test_version() -> None:
    """`--version` prints the version and exits with 0."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "pachifast" in result.output


def test_removed_commands() -> None:
    """`update` and `self` no longer exist."""
    assert runner.invoke(app, ["update"]).exit_code != 0
    assert runner.invoke(app, ["self", "upgrade"]).exit_code != 0


def test_new_project(tmp_path: Path) -> None:
    """`new project` writes the expected skeleton without legacy packaging artefacts."""
    result = runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    root = tmp_path / "demo"
    for name in ("main.py", "project_modules.py", "pyproject.toml", ".env.example", ".gitignore", "README.md"):
        assert (root / name).is_file()
    assert (root / "apps" / "__init__.py").is_file()
    assert (root / "common" / "__init__.py").is_file()
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"pachifast>=' in pyproject
    assert "[tool." not in pyproject
    assert "[[tool." not in pyproject
    env_example = (root / ".env.example").read_text(encoding="utf-8")
    assert "PACHIFAST_SECRET_KEY=\n" in env_example
    assert "openssl rand -hex 32" in env_example


def test_new_project_existing_dir_fails(tmp_path: Path) -> None:
    """Creating a project twice fails with exit code 1."""
    runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path)])
    result = runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path)])
    assert result.exit_code == 1


def test_new_app_and_common(tmp_path: Path) -> None:
    """`new app` and `new common` create packages and register them."""
    runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path)])
    root = tmp_path / "demo"

    result = runner.invoke(app, ["new", "app", "orders", "--base-dir", str(root), "--with-db"])
    assert result.exit_code == 0, result.output
    assert (root / "apps" / "orders" / "models.py").is_file()
    assert (root / "apps" / "orders" / "settings.py").is_file()
    example = (root / ".env.example").read_text(encoding="utf-8")
    assert "# ORDERS_DATABASE_URL=sqlite+aiosqlite:///./orders.db" in example
    assert "PACHIFAST_SECRET_KEY=" in example

    result = runner.invoke(app, ["new", "common", "shared", "--base-dir", str(root)])
    assert result.exit_code == 0, result.output
    assert (root / "common" / "shared" / "module.py").is_file()

    registry = (root / "project_modules.py").read_text(encoding="utf-8")
    assert "orders_module" in registry
    assert "shared_common_module" in registry
    compile(registry, "project_modules.py", "exec")


def test_new_app_no_register(tmp_path: Path) -> None:
    """`--no-register` leaves the registry untouched."""
    runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path)])
    root = tmp_path / "demo"
    before = (root / "project_modules.py").read_text(encoding="utf-8")
    result = runner.invoke(app, ["new", "app", "x", "--base-dir", str(root), "--no-register"])
    assert result.exit_code == 0
    assert (root / "project_modules.py").read_text(encoding="utf-8") == before


def test_migrations_list(tmp_path: Path) -> None:
    """`migrations list` prints the targets declared by the project."""
    runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path)])
    root = tmp_path / "demo"
    runner.invoke(app, ["new", "app", "orders", "--base-dir", str(root), "--with-db"])
    result = runner.invoke(app, ["migrations", "list", "--base-dir", str(root)])
    assert result.exit_code == 0, result.output
    assert "orders" in result.output


def test_users_password_policy(tmp_path: Path) -> None:
    """A short password is rejected before touching the database unless --force is used."""
    result = runner.invoke(app, ["users", "create", "bob", "--base-dir", str(tmp_path), "--password", "short"])
    assert result.exit_code == 1
    assert "too short" in result.output


def test_users_create_ls_update_sqlite(tmp_path: Path, monkeypatch) -> None:
    """End-to-end users create/ls/update against a SQLite database."""
    runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path)])
    root = tmp_path / "demo"
    monkeypatch.setenv("PACHIFAST_SECRET_KEY", "test-secret-key-for-cli-tests-0123456789")
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", f"sqlite+aiosqlite:///{root / 'db.sqlite3'}")

    for args in (
        ["migrations", "init"],
        ["migrations", "revision", "init", "--target-name", "auth", "--autogenerate"],
        ["migrations", "upgrade", "--target-name", "auth"],
    ):
        step = runner.invoke(app, [*args, "--base-dir", str(root)])
        assert step.exit_code == 0, step.output

    created = runner.invoke(
        app, ["users", "create", "alice", "--base-dir", str(root), "--password", "s3cret-pass-1", "--email", "a@x.io"]
    )
    assert created.exit_code == 0, created.output
    listed = runner.invoke(app, ["users", "ls", "--base-dir", str(root)])
    assert "alice" in listed.output
    updated = runner.invoke(app, ["users", "update", "alice", "--base-dir", str(root), "--staff"])
    assert updated.exit_code == 0, updated.output

    # the migrated database has no MFA/activity tables: deleting a user must not depend on them
    declined = runner.invoke(app, ["users", "delete", "alice", "--base-dir", str(root)], input="n\n")
    assert declined.exit_code != 0 and "alice" in runner.invoke(app, ["users", "ls", "--base-dir", str(root)]).output
    deleted = runner.invoke(app, ["users", "delete", "alice", "--yes", "--base-dir", str(root)])
    assert deleted.exit_code == 0, deleted.output
    assert "alice" not in runner.invoke(app, ["users", "ls", "--base-dir", str(root)]).output
    missing = runner.invoke(app, ["users", "delete", "alice", "--yes", "--base-dir", str(root)])
    assert missing.exit_code == 1 and "User not found" in missing.output


def test_init_bootstraps_project_and_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    """``init`` writes .env, applies migrations and creates the superuser; a second run changes nothing."""
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if not k.startswith("PACHIFAST_")})
    assert runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path)]).exit_code == 0
    root = tmp_path / "demo"

    first = runner.invoke(app, ["init", "--base-dir", str(root), "--password", "s3cret-pass-1"])
    assert first.exit_code == 0, first.output
    env_text = (root / ".env").read_text(encoding="utf-8")
    assert re.search(r"^PACHIFAST_SECRET_KEY=[0-9a-f]{64}$", env_text, re.MULTILINE)
    assert f"PACHIFAST_AUTH_DATABASE_URL=sqlite+aiosqlite:///{root / 'db.sqlite3'}" in env_text
    assert (root / "db.sqlite3").exists()

    listed = runner.invoke(app, ["users", "ls", "--base-dir", str(root)])
    assert "admin" in listed.output

    # from another directory and with no PACHIFAST_* variables: the project's .env is what configures the command
    for key in [k for k in os.environ if k.startswith("PACHIFAST_")]:
        monkeypatch.delitem(os.environ, key)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    from_other_dir = runner.invoke(app, ["users", "ls", "--base-dir", str(root)])
    assert from_other_dir.exit_code == 0 and "admin" in from_other_dir.output, from_other_dir.output
    monkeypatch.chdir(tmp_path)

    second = runner.invoke(app, ["init", "--base-dir", str(root), "--password", "other-pass-2"])
    assert second.exit_code == 0, second.output
    assert "already exists" in second.output
    assert (root / ".env").read_text(encoding="utf-8") == env_text


def test_init_fails_outside_a_generated_project(tmp_path: Path) -> None:
    """Without project_modules.py ``init`` stops with a clear error and creates nothing."""
    result = runner.invoke(app, ["init", "--base-dir", str(tmp_path), "--password", "s3cret-pass-1"])
    assert result.exit_code == 1
    assert "project_modules.py not found" in result.output
    assert not (tmp_path / ".env").exists()


def _pyproject(root: Path) -> dict:
    """Parse the generated pyproject.toml (also proves it is valid TOML)."""
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))


def test_new_project_no_interactive_writes_given_options(tmp_path: Path) -> None:
    """With --no-interactive the options (incl. quotes/unicode) end up in a valid [project] table."""
    result = runner.invoke(
        app,
        [
            "new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive",
            "--project-version", "1.2.3",
            "--description", 'Servizio "prova" con accénti',
            "--requires-python", ">=3.11,<3.14",
            "--author", "Mario Rossi",
            "--author-email", "mario@example.org",
            "--license", "MIT",
            "-k", "fastapi", "-k", "demo",
            "--homepage", "https://example.org",
            "--repository", "https://example.org/repo",
        ],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    project = _pyproject(tmp_path / "demo")["project"]
    assert project["version"] == "1.2.3"
    assert project["description"] == 'Servizio "prova" con accénti'
    assert project["requires-python"] == ">=3.11,<3.14"
    assert project["authors"] == [{"name": "Mario Rossi", "email": "mario@example.org"}]
    assert project["license"] == "MIT"
    assert project["keywords"] == ["fastapi", "demo"]
    assert project["urls"] == {"Homepage": "https://example.org", "Repository": "https://example.org/repo"}
    assert any(dep.startswith("pachifast") for dep in project["dependencies"])


def test_new_project_defaults_omit_optional_fields(tmp_path: Path) -> None:
    """Without options nothing optional is written (no authors, license, keywords, urls)."""
    assert (
        runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive"]).exit_code == 0
    )
    project = _pyproject(tmp_path / "demo")["project"]
    assert project["version"] == "0.1.0"
    assert project["requires-python"] == ">=3.11"
    for key in ("authors", "license", "keywords", "urls"):
        assert key not in project


def test_new_project_interactive_prompts_only_for_missing_options(tmp_path: Path) -> None:
    """Interactive mode skips the fields given as options and re-asks a field that fails validation."""
    answers = "\n".join(
        [
            "2.0.0",  # Version
            "",  # Description -> default
            "",  # Requires Python -> default
            "Ada",  # Author name
            "not-an-email",  # invalid: asked again
            "ada@example.org",  # Author email
            "a, b",  # Keywords
            "",  # Homepage
            "",  # Repository
            "httpx>=0.27,<1, rich",  # Packages
            "pytest",  # Dev packages
        ]
    )
    result = runner.invoke(
        app,
        ["new", "project", "demo", "--output-dir", str(tmp_path), "--interactive", "--license", "Apache-2.0"],
        input=answers + "\n",
    )
    assert result.exit_code == 0, result.output
    assert "Invalid author email" in result.output
    project = _pyproject(tmp_path / "demo")["project"]
    assert project["version"] == "2.0.0"
    assert project["authors"] == [{"name": "Ada", "email": "ada@example.org"}]
    assert project["license"] == "Apache-2.0"
    assert project["keywords"] == ["a", "b"]
    assert project["dependencies"] == ["pachifast>=0.19.0", "httpx>=0.27,<1", "rich"]
    assert _pyproject(tmp_path / "demo")["dependency-groups"] == {"dev": ["pytest"]}


def test_new_project_invalid_option_fails_without_creating_files(tmp_path: Path) -> None:
    """An invalid value passed as option stops the command before writing anything."""
    result = runner.invoke(
        app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive", "--project-version", "abc"]
    )
    assert result.exit_code == 1
    assert "Invalid version" in result.output
    assert not (tmp_path / "demo").exists()


def test_new_project_interactive_suggests_git_identity(tmp_path: Path, monkeypatch) -> None:
    """The git identity is only a suggested answer: Enter accepts it, ``-`` clears it."""
    monkeypatch.setattr("pachifast.cli.commands.new.get_git_identity", lambda cwd=None: ("Git Name", "git@example.org"))
    # Version, Description, Requires Python: defaults; Author: Enter; Email: "-" (none); then the optional rest empty.
    answers = ["", "", "", "", "-", "", "", "", "", "", ""]
    result = runner.invoke(
        app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--interactive"], input="\n".join(answers) + "\n"
    )
    assert result.exit_code == 0, result.output
    assert _pyproject(tmp_path / "demo")["project"]["authors"] == [{"name": "Git Name"}]


def test_new_project_no_interactive_never_reads_git_identity(tmp_path: Path, monkeypatch) -> None:
    """--no-interactive must not pick up the (possibly global/company) git identity."""

    def fail(cwd=None):
        raise AssertionError("git identity must not be read in non-interactive mode")

    monkeypatch.setattr("pachifast.cli.commands.new.get_git_identity", fail)
    result = runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive"])
    assert result.exit_code == 0, result.output
    assert "authors" not in _pyproject(tmp_path / "demo")["project"]


def _git_local(root: Path, key: str) -> str | None:
    """Return a repository-local git config value (ignoring the global config), or ``None``."""
    import subprocess

    result = subprocess.run(
        ["git", "config", "--local", "--get", key], cwd=root, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or None


def test_new_project_inits_git_with_local_identity(tmp_path: Path) -> None:
    """By default the project becomes a git repo whose local identity is the author."""
    result = runner.invoke(
        app,
        ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive"]
        + ["--author", "Mario Rossi", "--author-email", "mario@example.org"],
    )
    assert result.exit_code == 0, result.output
    root = tmp_path / "demo"
    assert (root / ".git").is_dir()
    assert _git_local(root, "user.name") == "Mario Rossi"
    assert _git_local(root, "user.email") == "mario@example.org"


def test_new_project_git_without_author_sets_no_identity(tmp_path: Path) -> None:
    """Without author values the repo is created but no local identity is written."""
    assert (
        runner.invoke(app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive"]).exit_code == 0
    )
    root = tmp_path / "demo"
    assert (root / ".git").is_dir()
    assert _git_local(root, "user.name") is None
    assert _git_local(root, "user.email") is None


def test_new_project_no_git_skips_repository(tmp_path: Path) -> None:
    """--no-git creates no repository."""
    result = runner.invoke(
        app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive", "--no-git", "--author", "A"]
    )
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "demo" / ".git").exists()


def test_set_local_identity_keeps_existing_local_values(tmp_path: Path) -> None:
    """A value already set in the repo-local config is never overwritten."""
    import subprocess

    from pachifast.cli.helpers import init_repository, set_local_identity

    assert init_repository(tmp_path)
    subprocess.run(["git", "config", "--local", "user.name", "Existing"], cwd=tmp_path, check=True)
    written = set_local_identity(tmp_path, "New Name", "new@example.org")
    assert written == ["user.email"]
    assert _git_local(tmp_path, "user.name") == "Existing"
    assert _git_local(tmp_path, "user.email") == "new@example.org"


def test_new_project_packages_and_dev_packages(tmp_path: Path) -> None:
    """--package/--dev-package fill dependencies and the dev group; pachifast stays first and can be pinned."""
    result = runner.invoke(
        app,
        ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive", "--no-git"]
        + ["-p", "httpx>=0.27,<1", "-p", "pachifast>=0.1.0,<0.2", "-p", "rich[markdown]", "-p", "HTTPX==0.28"]
        + ["--dev-package", "pytest>=8", "--dev-package", "ruff"],
    )
    assert result.exit_code == 0, result.output
    data = _pyproject(tmp_path / "demo")
    # pachifast pin replaces the default and stays first; the repeated httpx keeps the last requirement.
    assert data["project"]["dependencies"] == ["pachifast>=0.1.0,<0.2", "HTTPX==0.28", "rich[markdown]"]
    assert data["dependency-groups"] == {"dev": ["pytest>=8", "ruff"]}


def test_new_project_without_packages_has_no_dev_group(tmp_path: Path) -> None:
    """No --dev-package means no [dependency-groups] table."""
    assert (
        runner.invoke(
            app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive", "--no-git"]
        ).exit_code
        == 0
    )
    data = _pyproject(tmp_path / "demo")
    assert data["project"]["dependencies"] == ["pachifast>=0.19.0"]
    assert "dependency-groups" not in data


def test_new_project_invalid_package_fails_without_creating_files(tmp_path: Path) -> None:
    """A malformed requirement stops the command before writing anything."""
    result = runner.invoke(
        app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive", "-p", "not valid!"]
    )
    assert result.exit_code == 1
    assert "Invalid package requirement" in result.output
    assert not (tmp_path / "demo").exists()


def test_line_editing_is_enabled_for_prompts() -> None:
    """``readline`` must be loaded so cursor keys work in ``input()`` based prompts (POSIX)."""
    import sys

    import pytest

    from pachifast.cli.helpers import enable_line_editing

    if sys.platform == "win32":
        pytest.skip("readline is not available on Windows (its console handles cursor keys natively)")
    assert enable_line_editing() is True
    assert "readline" in sys.modules


def _new_demo_project(tmp_path: Path) -> Path:
    """Create a project skeleton (no git) and return its root."""
    assert (
        runner.invoke(
            app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive", "--no-git"]
        ).exit_code
        == 0
    )
    return tmp_path / "demo"


def test_init_reports_stale_database_url_from_environment(tmp_path: Path, monkeypatch) -> None:
    """A leftover exported PACHIFAST_AUTH_DATABASE_URL is reported clearly and nothing is written."""
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if not k.startswith("PACHIFAST_")})
    root = _new_demo_project(tmp_path)
    os.environ["PACHIFAST_AUTH_DATABASE_URL"] = "sqlite+aiosqlite:////tmp/no-such-old-demo/db.sqlite3"

    result = runner.invoke(app, ["init", "--base-dir", str(root), "--password", "s3cret-pass-1"])
    assert result.exit_code == 1
    assert "PACHIFAST_AUTH_DATABASE_URL is already set in your environment" in result.output
    assert "unset PACHIFAST_AUTH_DATABASE_URL" in result.output
    assert not (root / ".env").exists()
    assert not (root / "migrations").exists()


def test_init_environment_matching_env_file_is_accepted(tmp_path: Path, monkeypatch) -> None:
    """The same value in the environment and in .env is not a conflict."""
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if not k.startswith("PACHIFAST_")})
    root = _new_demo_project(tmp_path)
    url = f"sqlite+aiosqlite:///{root / 'db.sqlite3'}"
    os.environ["PACHIFAST_AUTH_DATABASE_URL"] = url
    result = runner.invoke(app, ["init", "--base-dir", str(root), "--password", "s3cret-pass-1", "--database-url", url])
    assert result.exit_code == 0, result.output


def test_init_reports_missing_sqlite_directory(tmp_path: Path, monkeypatch) -> None:
    """A SQLite URL whose directory does not exist fails with the directory named, before anything is written."""
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if not k.startswith("PACHIFAST_")})
    root = _new_demo_project(tmp_path)
    missing = tmp_path / "missing-dir" / "db.sqlite3"
    result = runner.invoke(
        app,
        [
            "init",
            "--base-dir",
            str(root),
            "--password",
            "s3cret-pass-1",
            "--database-url",
            f"sqlite+aiosqlite:///{missing}",
        ],
    )
    assert result.exit_code == 1
    assert "SQLite database directory does not exist" in result.output
    assert str(missing.parent).replace("\n", "") in result.output.replace("\n", "")
    assert not (root / ".env").exists()


def test_new_project_writes_a_sample_config_file(tmp_path: Path) -> None:
    """The generated pachifast.yaml is valid, carries the port and does not break the settings."""
    import yaml

    root = _new_demo_project(tmp_path)
    data = yaml.safe_load((root / "pachifast.yaml").read_text(encoding="utf-8"))
    assert data == {"server": {"host": "127.0.0.1", "port": 8000}}


def test_init_does_not_copy_yaml_defined_values_into_env(tmp_path: Path, monkeypatch) -> None:
    """A database URL defined in the YAML file stays there: .env would override it."""
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if not k.startswith("PACHIFAST_")})
    root = _new_demo_project(tmp_path)
    yaml_url = f"sqlite+aiosqlite:///{root / 'from-yaml.sqlite3'}"
    (root / "pachifast.yaml").write_text(
        f"server:\n  host: 0.0.0.0\n  port: 8123\nauth:\n  auth_database_url: {yaml_url}\n", encoding="utf-8"
    )

    result = runner.invoke(app, ["init", "--base-dir", str(root), "--password", "s3cret-pass-1"])
    assert result.exit_code == 0, result.output
    env_text = (root / ".env").read_text(encoding="utf-8")
    assert "PACHIFAST_SECRET_KEY=" in env_text
    assert "PACHIFAST_AUTH_DATABASE_URL" not in env_text
    assert (root / "from-yaml.sqlite3").exists()  # the migrations used the YAML database
    assert "admin" in runner.invoke(app, ["users", "ls", "--base-dir", str(root)]).output


def test_cli_password_change_revokes_tokens_and_lifts_the_lockout() -> None:
    from types import SimpleNamespace

    from pachifast.cli.services.users import _set_password

    user = SimpleNamespace(password_hash="old", password_changed_at=None, failed_login_count=3, locked_until="later")
    _set_password(user, "new-hash")
    assert user.password_hash == "new-hash" and user.password_changed_at is not None
    assert user.failed_login_count == 0 and user.locked_until is None


def test_migrations_strip_sqlmodel_rewrites_old_revisions(tmp_path: Path) -> None:
    """Revisions generated by 0.1.x import sqlmodel; the command makes them independent of it."""
    versions = tmp_path / "migrations" / "auth" / "versions"
    versions.mkdir(parents=True)
    old = (
        "import sqlalchemy as sa\nimport sqlmodel\n\n\ndef upgrade():\n"
        "    sa.Column('a', sqlmodel.sql.sqltypes.AutoString(length=10))\n"
        "    sa.Column('b', sqlmodel.sql.sqltypes.AutoString())\n"
    )
    clean = "import sqlalchemy as sa\n\n\ndef upgrade():\n    pass\n"
    (versions / "a_old.py").write_text(old)
    (versions / "b_clean.py").write_text(clean)

    checked = runner.invoke(app, ["migrations", "strip-sqlmodel", "--base-dir", str(tmp_path), "--check"])
    assert checked.exit_code == 1
    assert "a_old.py" in checked.output and "b_clean.py" not in checked.output
    assert "import sqlmodel" in (versions / "a_old.py").read_text()  # --check writes nothing

    done = runner.invoke(app, ["migrations", "strip-sqlmodel", "--base-dir", str(tmp_path)])
    assert done.exit_code == 0
    text = (versions / "a_old.py").read_text()
    assert "sqlmodel" not in text
    assert "sa.String(length=10)" in text and "sa.String()" in text
    assert (versions / "b_clean.py").read_text() == clean
    assert runner.invoke(app, ["migrations", "strip-sqlmodel", "--base-dir", str(tmp_path), "--check"]).exit_code == 0


def test_init_environment_conflict_message_says_what_to_do(tmp_path: Path, monkeypatch) -> None:
    """A secret key already in the environment is refused with an actionable message, without echoing it."""
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if not k.startswith("PACHIFAST_")})
    root = _new_demo_project(tmp_path)
    os.environ["PACHIFAST_SECRET_KEY"] = "an-old-exported-secret-key-0123456789abcdef"

    result = runner.invoke(app, ["init", "--base-dir", str(root), "--password", "s3cret-pass-1"])
    output = " ".join(result.output.split())
    assert result.exit_code == 1
    assert "PACHIFAST_SECRET_KEY is already set in your environment" in output
    assert "unset PACHIFAST_SECRET_KEY" in output
    assert "copy it into .env" in output
    assert "an-old-exported-secret-key" not in output
    assert not (root / ".env").exists()


def test_locale_export_without_dest_path_names_the_option(tmp_path: Path) -> None:
    """The missing folder is reported with the option name and an example."""
    result = runner.invoke(app, ["locale", "export", "--base-dir", str(tmp_path)])
    output = " ".join(result.output.split())
    assert result.exit_code == 2
    assert "--dest-path" in output
