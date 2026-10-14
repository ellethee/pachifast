"""Generators that write projects, apps and common modules to disk."""

import json
import re
from pathlib import Path

from pachifast.config.schema import build_config_schema
from pachifast.scaffold import pipelines, templates
from pachifast.scaffold.helpers import ensure_package, normalize_name, write_file
from pachifast.scaffold.metadata import ProjectMetadata
from pachifast.scaffold.pipelines import (
    DEFAULT_PYTHON_VERSION,
    PipelineReport,
    Provider,
    check_ca_cert,
    check_index_name,
    check_python_version,
)


def create_project(name: str, output_dir: str = ".", metadata: ProjectMetadata | None = None) -> Path:
    """Generate a new project skeleton on disk.

    Args:
        name: Project name (normalized to a valid identifier).
        output_dir: Directory in which the project folder is created.
        metadata: Values of the ``[project]`` table; defaults are used when omitted.

    Returns:
        The path of the created project.

    Raises:
        FileExistsError: If the target directory already exists.
        ValueError: If the name or a metadata value is invalid.
    """
    project_name = normalize_name(name)
    metadata = (metadata or ProjectMetadata()).normalized()
    metadata.validate()
    base_dir = Path(output_dir).resolve() / project_name
    if base_dir.exists():
        raise FileExistsError(f"Target directory already exists: {base_dir}")

    ensure_package(base_dir / "apps")
    ensure_package(base_dir / "common")

    write_file(base_dir / "pyproject.toml", templates.project_template_pyproject(project_name, metadata))
    write_file(base_dir / "main.py", templates.project_template_main())
    write_file(base_dir / "project_modules.py", templates.project_template_modules())
    write_file(base_dir / "README.md", templates.project_template_readme(project_name))
    write_file(base_dir / ".env.example", templates.project_template_env_example())
    write_file(base_dir / "pachifast.yaml", templates.project_template_config_yaml())
    write_file(
        base_dir / "pachifast.schema.json", json.dumps(build_config_schema(), indent=2, ensure_ascii=False) + "\n"
    )
    write_file(base_dir / ".gitignore", templates.project_template_gitignore())
    return base_dir


def create_app(name: str, base_dir: str = ".", register: bool = True, with_db: bool = False) -> Path:
    """Generate a new application module inside an existing project.

    Args:
        name: App name.
        base_dir: Project root containing ``apps/`` and ``project_modules.py``.
        register: Whether to register the module in ``project_modules.py``.
        with_db: Whether to add settings and models for a dedicated database.

    Returns:
        The path of the created app package.
    """
    app_name = normalize_name(name)
    root = Path(base_dir).resolve()
    app_dir = root / "apps" / app_name
    if app_dir.exists():
        raise FileExistsError(f"App already exists: {app_dir}")

    ensure_package(app_dir)
    write_file(app_dir / "root.py", templates.app_template_root(app_name))
    if with_db:
        write_file(app_dir / "settings.py", templates.db_settings_template(app_name, with_db=True))
        write_file(app_dir / "models.py", templates.db_models_template(app_name))
    write_file(app_dir / "module.py", templates.app_template_module(app_name, with_db=with_db))
    _create_locale_package(app_dir, app_name)

    if register:
        register_module(root / "project_modules.py", f"apps.{app_name}.module", f"{app_name}_module")
    return app_dir


def create_common(name: str, base_dir: str = ".", register: bool = True, with_db: bool = False) -> Path:
    """Generate a new shared ``common`` module inside an existing project.

    Args:
        name: Common module name.
        base_dir: Project root containing ``common/`` and ``project_modules.py``.
        register: Whether to register the module in ``project_modules.py``.
        with_db: Whether to add models for a dedicated database.

    Returns:
        The path of the created common package.
    """
    common_name = normalize_name(name)
    root = Path(base_dir).resolve()
    common_dir = root / "common" / common_name
    if common_dir.exists():
        raise FileExistsError(f"Common module already exists: {common_dir}")

    ensure_package(common_dir)
    write_file(common_dir / "settings.py", templates.db_settings_template(common_name, with_db=with_db))
    if with_db:
        write_file(common_dir / "models.py", templates.db_models_template(common_name))
    write_file(common_dir / "module.py", templates.common_template_module(common_name, with_db=with_db))
    _create_locale_package(common_dir, f"common.{common_name}")

    if register:
        register_module(root / "project_modules.py", f"common.{common_name}.module", f"{common_name}_common_module")
    return common_dir


def _create_locale_package(module_dir: Path, module_name: str) -> None:
    """Create ``locale/`` with an English catalog (the languages are the files of the folder)."""
    ensure_package(module_dir / "locale")
    write_file(module_dir / "locale" / "en.py", templates.locale_template(module_name, "en"))


def register_module(project_modules_path: Path, module_path: str, alias: str) -> None:
    """Insert a generated module into ``project_modules.py`` if missing.

    Raises:
        FileNotFoundError: If the registry file does not exist.
        ValueError: If the ``MODULES`` tuple cannot be found or updated.
    """
    if not project_modules_path.exists():
        raise FileNotFoundError(f"Missing project registry: {project_modules_path}")

    text = project_modules_path.read_text(encoding="utf-8")
    import_line = f"from {module_path} import module as {alias}"
    entry_line = f"    {alias},"

    if import_line not in text:
        marker = "MODULES = ("
        if marker not in text:
            raise ValueError(f"Cannot find MODULES tuple in {project_modules_path}")
        text = text.replace(marker, f"{import_line}\n\n{marker}", 1)

    if entry_line not in text:
        match = re.search(r"MODULES = \(\n(?P<body>.*)\n\)", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"Cannot update MODULES tuple in {project_modules_path}")
        body = match.group("body")
        new_body = f"{body}\n{entry_line}" if body else entry_line
        text = text[: match.start("body")] + new_body + text[match.end("body") :]

    project_modules_path.write_text(text, encoding="utf-8")


def create_pipeline(
    base_dir: str = ".",
    provider: Provider = Provider.ALL,
    python_version: str = DEFAULT_PYTHON_VERSION,
    force: bool = False,
    index_name: str | None = None,
    ca_cert: str | None = None,
) -> PipelineReport:
    """Generate the ``Dockerfile``, ``.dockerignore`` and CI pipeline files of a project.

    Existing files are left untouched (reported as skipped) unless ``force`` is true.

    Args:
        base_dir: Project root.
        provider: CI provider(s) to generate the pipeline for.
        python_version: ``major.minor`` of the base image (e.g. ``3.12``).
        force: Overwrite existing files.
        index_name: Name of a private uv index (``[[tool.uv.index]]`` in ``pyproject.toml``) whose credentials the
            image build needs; they are passed as build secrets, never written to the files.
        ca_cert: Path inside the project of a certificate authority (``.crt``/``.pem``) that the private index's
            server uses; the builder stage installs it so uv can verify the server.

    Returns:
        A report with written and skipped paths (relative to ``base_dir``) and warnings.

    Raises:
        FileNotFoundError: If ``base_dir`` is not a directory.
        ValueError: If ``python_version`` or ``index_name`` is malformed.
    """
    root = Path(base_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project directory not found: {root}")
    check_python_version(python_version)
    if index_name:
        check_index_name(index_name)
    if ca_cert:
        check_ca_cert(ca_cert)

    files: dict[str, str] = {
        "Dockerfile": pipelines.dockerfile_template(python_version, index_name, ca_cert),
        ".dockerignore": pipelines.dockerignore_template(),
    }
    if provider in (Provider.GITHUB, Provider.ALL):
        files[".github/workflows/docker-image.yml"] = pipelines.github_workflow_template(index_name)
    if provider in (Provider.GITLAB, Provider.ALL):
        files[".gitlab-ci.yml"] = pipelines.gitlab_ci_template(index_name)

    if provider in (Provider.GITEA, Provider.ALL):
        files[".gitea/workflows/docker-image.yml"] = pipelines.gitea_workflow_template(index_name)

    report = PipelineReport()
    for relative, content in files.items():
        target = root / relative
        if target.exists() and not force:
            report.skipped.append(relative)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        write_file(target, content)
        report.written.append(relative)

    if not (root / "pyproject.toml").exists():
        report.warnings.append("pyproject.toml not found: the image build installs the project from it.")
    if not (root / "uv.lock").exists():
        report.warnings.append(
            "uv.lock not found: the Dockerfile builds with `uv sync --frozen`, so run `uv lock` and commit the file."
        )
    if index_name:
        _warn_if_index_missing(root, index_name, report)
    if ca_cert and not (root / ca_cert).is_file():
        report.warnings.append(f"{ca_cert} not found: the image build copies it, so add the certificate there.")
    return report


def _warn_if_index_missing(root: Path, index_name: str, report: PipelineReport) -> None:
    """Warn when ``pyproject.toml`` does not declare the private index (``[[tool.uv.index]]`` with that name)."""
    import tomllib

    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return
    indexes = data.get("tool", {}).get("uv", {}).get("index", [])
    if not any(isinstance(item, dict) and item.get("name") == index_name for item in indexes):
        report.warnings.append(
            f"index {index_name!r} is not declared in pyproject.toml: add a [[tool.uv.index]] with name = "
            f'"{index_name}" and its url (without credentials), then run `uv lock`.'
        )
