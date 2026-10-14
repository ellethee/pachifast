"""``pachifast new`` commands: scaffold projects, apps and common modules."""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer
from rich.markup import escape

from pachifast import scaffold
from pachifast.cli.helpers import console, get_git_identity, handle_errors, init_repository, set_local_identity
from pachifast.scaffold.metadata import (
    check_email,
    check_requirement,
    check_requires_python,
    check_url,
    check_version,
)

app = typer.Typer(help="Create a project, app, or common module.", no_args_is_help=True)

BaseDirOption = Annotated[Path, typer.Option("--base-dir", help="Project root containing project_modules.py.")]
WithDbOption = Annotated[
    bool, typer.Option("--with-db", help="Create settings and models scaffolding for a dedicated database target.")
]
NoRegisterOption = Annotated[
    bool, typer.Option("--no-register", help="Create files without updating project_modules.py.")
]


def _ask(label: str, default: str = "", *, check: Callable[[str], None] | None = None) -> str:
    """Prompt for a value, asking again until ``check`` accepts it (empty default means optional).

    When a default is suggested, ``-`` clears it (an empty answer would just accept the default).
    """
    while True:
        answer = typer.prompt(label, default=default, show_default=bool(default)).strip()
        if default and answer == "-":
            answer = ""
        try:
            if check is not None:
                check(answer)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]", highlight=False)
            continue
        return answer


def _check_requirements(value: str) -> None:
    """Validate a comma-separated list of package requirements typed at a prompt."""
    for requirement in scaffold.parse_requirements(value):
        check_requirement(requirement)


def _collect_metadata(
    *,
    interactive: bool,
    version: str | None,
    description: str | None,
    requires_python: str | None,
    author: str | None,
    author_email: str | None,
    license_id: str | None,
    keywords: list[str] | None,
    homepage: str | None,
    repository: str | None,
    packages: list[str] | None = None,
    dev_packages: list[str] | None = None,
    git_identity: tuple[str | None, str | None] = (None, None),
) -> scaffold.ProjectMetadata:
    """Merge CLI options, prompts (for the options left out, when interactive) and defaults.

    ``git_identity`` (name, email) is only offered as the suggested answer of the author prompts.
    """
    defaults = scaffold.ProjectMetadata()
    if interactive:
        version = version if version is not None else _ask("Version", defaults.version, check=check_version)
        description = description if description is not None else _ask("Description", defaults.description)
        requires_python = (
            requires_python
            if requires_python is not None
            else _ask("Requires Python", defaults.requires_python, check=check_requires_python)
        )
        git_name, git_email = git_identity
        author = (
            author
            if author is not None
            else _ask("Author name (- for none)" if git_name else "Author name", git_name or "")
        )
        author_email = (
            author_email
            if author_email is not None
            else _ask("Author email (- for none)" if git_email else "Author email", git_email or "", check=check_email)
        )
        license_id = license_id if license_id is not None else _ask("License (SPDX id, e.g. MIT, empty for none)")
        if keywords is None:
            keywords = scaffold.parse_keywords(_ask("Keywords (comma separated)"))
        homepage = homepage if homepage is not None else _ask("Homepage URL", check=lambda v: check_url("homepage", v))
        repository = (
            repository if repository is not None else _ask("Repository URL", check=lambda v: check_url("repository", v))
        )
        if packages is None:
            packages = scaffold.parse_requirements(
                _ask("Packages (comma separated, e.g. httpx>=0.27,<1, rich)", check=_check_requirements)
            )
        if dev_packages is None:
            dev_packages = scaffold.parse_requirements(
                _ask("Dev packages (comma separated, e.g. pytest, ruff)", check=_check_requirements)
            )
    return scaffold.ProjectMetadata(
        version=version if version is not None else defaults.version,
        description=description if description is not None else defaults.description,
        requires_python=requires_python if requires_python is not None else defaults.requires_python,
        author=author,
        author_email=author_email,
        license=license_id,
        keywords=list(keywords or []),
        homepage=homepage,
        repository=repository,
        packages=list(packages or []),
        dev_packages=list(dev_packages or []),
    )


@app.command("project")
@handle_errors
def new_project(
    name: Annotated[str, typer.Argument(help="Project name.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Directory where the project will be created.")
    ] = Path("."),
    project_version: Annotated[str | None, typer.Option("--project-version", help="Project version.")] = None,
    description: Annotated[str | None, typer.Option("--description", "-d", help="One-line description.")] = None,
    requires_python: Annotated[
        str | None, typer.Option("--requires-python", help="Python version specifier (default: >=3.11).")
    ] = None,
    author: Annotated[str | None, typer.Option("--author", help="Author name.")] = None,
    author_email: Annotated[str | None, typer.Option("--author-email", help="Author email.")] = None,
    license_id: Annotated[
        str | None, typer.Option("--license", help="SPDX license expression, e.g. MIT or Apache-2.0.")
    ] = None,
    keywords: Annotated[
        list[str] | None, typer.Option("--keyword", "-k", help="Keyword (repeat the option for more).")
    ] = None,
    homepage: Annotated[str | None, typer.Option("--homepage", help="Homepage URL.")] = None,
    repository: Annotated[str | None, typer.Option("--repository", help="Source repository URL.")] = None,
    packages: Annotated[
        list[str] | None,
        typer.Option("--package", "-p", help="Runtime dependency, e.g. 'httpx>=0.27' (repeat for more)."),
    ] = None,
    dev_packages: Annotated[
        list[str] | None,
        typer.Option("--dev-package", help="Development dependency, e.g. 'pytest' (repeat for more)."),
    ] = None,
    interactive: Annotated[
        bool | None,
        typer.Option(
            "--interactive/--no-interactive",
            help="Ask for the pyproject properties not given as options "
            "(default: only when running in a terminal). --no-interactive uses the options and defaults.",
        ),
    ] = None,
    git: Annotated[
        bool,
        typer.Option(
            "--git/--no-git",
            help="Run git init in the new project and set the author as its local git user.name/user.email "
            "(only values not already set locally).",
        ),
    ] = True,
) -> None:
    """Create a new project skeleton.

    Interactive mode asks for the pyproject.toml properties that were not passed as options.
    """
    ask = sys.stdin.isatty() if interactive is None else interactive
    metadata = _collect_metadata(
        interactive=ask,
        version=project_version,
        description=description,
        requires_python=requires_python,
        author=author,
        author_email=author_email,
        license_id=license_id,
        keywords=keywords,
        homepage=homepage,
        repository=repository,
        packages=packages,
        dev_packages=dev_packages,
        git_identity=get_git_identity(output_dir) if ask and (author is None or author_email is None) else (None, None),
    )
    path = scaffold.create_project(name, output_dir=str(output_dir), metadata=metadata)
    console.print(f"[green]Created project[/green] {path}", highlight=False)
    if git:
        _setup_git(path, metadata)


def _setup_git(path: Path, metadata: scaffold.ProjectMetadata) -> None:
    """``git init`` the project and store the author as local git identity (never fails the command)."""
    if not init_repository(path):
        console.print("[yellow]git init skipped[/yellow] (git not available or failed)", highlight=False)
        return
    console.print("Initialized git repository", highlight=False)
    written = set_local_identity(path, metadata.author, metadata.author_email)
    if written:
        console.print(f"Set local git config: {', '.join(written)}", highlight=False)


@app.command("app")
@handle_errors
def new_app(
    name: Annotated[str, typer.Argument(help="App module name.")],
    base_dir: BaseDirOption = Path("."),
    with_db: WithDbOption = False,
    no_register: NoRegisterOption = False,
) -> None:
    """Create a new app module in an existing project."""
    path = scaffold.create_app(name, base_dir=str(base_dir), register=not no_register, with_db=with_db)
    console.print(f"[green]Created app[/green] {path}", highlight=False)


@app.command("common")
@handle_errors
def new_common(
    name: Annotated[str, typer.Argument(help="Common module name.")],
    base_dir: BaseDirOption = Path("."),
    with_db: WithDbOption = False,
    no_register: NoRegisterOption = False,
) -> None:
    """Create a new common module in an existing project."""
    path = scaffold.create_common(name, base_dir=str(base_dir), register=not no_register, with_db=with_db)
    console.print(f"[green]Created common module[/green] {path}", highlight=False)


@app.command("pipeline")
@handle_errors
def new_pipeline(
    base_dir: BaseDirOption = Path("."),
    provider: Annotated[
        scaffold.Provider, typer.Option("--provider", help="CI provider to generate the pipeline for.")
    ] = scaffold.Provider.ALL,
    python_version: Annotated[
        str, typer.Option("--python-version", help="Python version (major.minor) of the base image.")
    ] = scaffold.DEFAULT_PYTHON_VERSION,
    force: Annotated[bool, typer.Option("--force", help="Overwrite files that already exist.")] = False,
    index_name: Annotated[
        str | None,
        typer.Option(
            "--index-name",
            help="Name of a private uv index declared in pyproject.toml ([[tool.uv.index]]), e.g. a Gitea registry: "
            "its credentials are passed to the build as secrets (UV_INDEX_USERNAME / UV_INDEX_PASSWORD).",
        ),
    ] = None,
    ca_cert: Annotated[
        str | None,
        typer.Option(
            "--ca-cert",
            help="Path inside the project of the certificate authority (.crt/.pem) used by the private index's "
            "server, e.g. certs/internal-ca.crt: the image build trusts it. Public certificate, not a secret.",
        ),
    ] = None,
) -> None:
    """Generate the Dockerfile, .dockerignore and the CI pipeline (GitHub Actions, GitLab CI and/or Gitea Actions)."""
    report = scaffold.create_pipeline(
        base_dir=str(base_dir),
        provider=provider,
        python_version=python_version,
        force=force,
        index_name=index_name,
        ca_cert=ca_cert,
    )
    for path in report.written:
        console.print(f"[green]Created[/green] {escape(str(path))}", highlight=False)
    for path in report.skipped:
        console.print(
            f"[yellow]Skipped[/yellow] {escape(str(path))} (already exists, use --force to overwrite)", highlight=False
        )
    for warning in report.warnings:
        console.print(f"[yellow]Warning:[/yellow] {escape(warning)}", highlight=False)
    console.print(
        "\nNext steps:\n"
        "  1. Run `uv lock` and commit uv.lock (the image is built with `uv sync --frozen`).\n"
        "  2. Pass the configuration as environment variables at runtime "
        "(PACHIFAST_SECRET_KEY, PACHIFAST_AUTH_DATABASE_URL, ...): the image contains no secrets.\n"
        "  3. Run the migrations as a release step, not inside the web container start-up: "
        "`pachifast migrations upgrade --target-name auth`.\n"
        "  Note: uv must be able to resolve the pachifast dependency during the build; if it is not on a package "
        "index reachable from the CI, configure one (or [tool.uv.sources]) in pyproject.toml."
        + (
            "\n  Private index: set the secrets/variables UV_INDEX_USERNAME and UV_INDEX_PASSWORD in the CI "
            "(for Gitea Actions also REGISTRY_TOKEN, to push the image)."
            if index_name
            else ""
        ),
        highlight=False,
        markup=False,
    )
