"""Tests for ``pachifast new pipeline`` and ``pachifast serve``."""

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from pachifast.cli import app
from pachifast.cli.services import serve as serve_service

runner = CliRunner()


def _project(tmp_path: Path, *, lock: bool = True) -> Path:
    """Create a generated project (no git); optionally with a uv.lock."""
    assert (
        runner.invoke(
            app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive", "--no-git"]
        ).exit_code
        == 0
    )
    root = tmp_path / "demo"
    if lock:
        (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    return root


def _pipeline(root: Path, *extra: str):
    return runner.invoke(app, ["new", "pipeline", "--base-dir", str(root), *extra])


# --- new pipeline -----------------------------------------------------------


def test_pipeline_all_creates_every_file(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = _pipeline(root)
    assert result.exit_code == 0, result.output
    for name in (
        "Dockerfile",
        ".dockerignore",
        ".github/workflows/docker-image.yml",
        ".gitlab-ci.yml",
        ".gitea/workflows/docker-image.yml",
    ):
        assert (root / name).is_file(), name
    assert "uv lock" in result.output and "migrations upgrade --target-name auth" in result.output
    assert "resolve the pachifast dependency" in result.output


def test_pipeline_provider_selection(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _pipeline(root, "--provider", "github").exit_code == 0
    assert (root / ".github/workflows/docker-image.yml").exists()
    assert not (root / ".gitlab-ci.yml").exists()

    other = _project(tmp_path / "second")
    assert _pipeline(other, "--provider", "gitlab").exit_code == 0
    assert (other / ".gitlab-ci.yml").exists()
    assert not (other / ".github").exists()
    assert (other / "Dockerfile").exists()  # the image files are always generated


def test_dockerfile_content(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _pipeline(root, "--python-version", "3.13").exit_code == 0
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG PYTHON_VERSION=3.13" in dockerfile
    assert "ghcr.io/astral-sh/uv" in dockerfile
    assert "uv sync --frozen --no-dev" in dockerfile
    assert "EXPOSE 8000" in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/api/health/" in dockerfile and "curl" not in dockerfile
    assert 'CMD ["pachifast", "serve"]' in dockerfile
    assert "PACHIFAST_HOST=0.0.0.0" in dockerfile and "PACHIFAST_PORT=8000" in dockerfile
    # debug off in the image: no tracebacks in the responses (the uniform JSON 500 is used instead)
    assert "PACHIFAST_DEBUG=false" in dockerfile
    # non-root user, and no secret baked in
    user_line = next(line for line in dockerfile.splitlines() if line.startswith("USER "))
    assert user_line.split()[1] not in ("root", "0")
    assert "SECRET_KEY" not in dockerfile.replace("PACHIFAST_SECRET_KEY=...", "")


def test_dockerignore_keeps_secrets_out_but_migrations_in(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _pipeline(root).exit_code == 0
    lines = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in lines and "*.env" in lines and ".git" in lines and ".venv" in lines
    assert "migrations" not in lines  # the migrations are needed in the image


def test_github_workflow_is_valid_and_uses_ghcr(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _pipeline(root, "--provider", "github").exit_code == 0
    workflow = yaml.safe_load((root / ".github/workflows/docker-image.yml").read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["build"]["permissions"] == {"contents": "read", "packages": "write"}
    triggers = workflow.get("on") or workflow[True]  # YAML 1.1 reads a bare ``on`` as True
    assert triggers["push"]["branches"] == ["main"] and triggers["push"]["tags"] == ["v*"]
    text = (root / ".github/workflows/docker-image.yml").read_text(encoding="utf-8")
    assert "ghcr.io" in text and "secrets.GITHUB_TOKEN" in text
    assert "secrets." not in text.replace("secrets.GITHUB_TOKEN", "")  # no custom secrets
    uses = [step["uses"] for step in workflow["jobs"]["build"]["steps"] if "uses" in step]
    assert any(item.startswith("docker/build-push-action@") for item in uses)


def test_gitlab_ci_is_valid_and_pushes_to_the_project_registry(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _pipeline(root, "--provider", "gitlab").exit_code == 0
    text = (root / ".gitlab-ci.yml").read_text(encoding="utf-8")
    ci = yaml.safe_load(text)
    job = ci["build-image"]
    assert ci["stages"] == ["build"] and job["stage"] == "build"
    assert "docker:dind" in job["services"]
    assert "CI_REGISTRY_IMAGE" in text and "CI_REGISTRY_USER" in text and "CI_COMMIT_SHORT_SHA" in text
    assert {"if": "$CI_COMMIT_TAG"} in job["rules"]
    assert {"if": "$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH"} in job["rules"]


def test_pipeline_does_not_overwrite_without_force(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "Dockerfile").write_text("# mine\n", encoding="utf-8")
    result = _pipeline(root)
    assert result.exit_code == 0, result.output
    assert "Skipped" in result.output and "Dockerfile" in result.output
    assert (root / "Dockerfile").read_text(encoding="utf-8") == "# mine\n"
    assert (root / ".gitlab-ci.yml").exists()  # the missing ones are still created

    assert _pipeline(root, "--force").exit_code == 0
    assert "ARG PYTHON_VERSION" in (root / "Dockerfile").read_text(encoding="utf-8")


def test_pipeline_warns_when_uv_lock_is_missing(tmp_path: Path) -> None:
    root = _project(tmp_path, lock=False)
    result = _pipeline(root)
    assert result.exit_code == 0
    assert "uv.lock not found" in result.output
    assert "uv.lock not found" not in _pipeline(_project(tmp_path / "locked")).output


def test_pipeline_rejects_bad_input(tmp_path: Path) -> None:
    root = _project(tmp_path)
    bad_version = _pipeline(root, "--python-version", "three")
    assert bad_version.exit_code == 1 and "Invalid Python version" in bad_version.output
    assert not (root / "Dockerfile").exists()
    missing = runner.invoke(app, ["new", "pipeline", "--base-dir", str(tmp_path / "nowhere")])
    assert missing.exit_code == 1 and "not found" in missing.output


def test_gitea_workflow_uses_the_instance_registry_and_no_hardcoded_host(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _pipeline(root, "--provider", "gitea").exit_code == 0
    assert not (root / ".github").exists() and not (root / ".gitlab-ci.yml").exists()
    path = root / ".gitea/workflows/docker-image.yml"
    text = path.read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    triggers = workflow.get("on") or workflow[True]
    assert triggers["push"]["branches"] == ["main"] and triggers["push"]["tags"] == ["v*"]
    assert "GITHUB_SERVER_URL" in text and "secrets.REGISTRY_TOKEN" in text
    assert "ghcr.io" not in text and "type=gha" not in text and "git.internal" not in text
    steps = workflow["jobs"]["build"]["steps"]
    login = next(step for step in steps if step.get("uses", "").startswith("docker/login-action@"))
    assert login["with"]["registry"] == "${{ env.REGISTRY }}"
    assert "secrets:" not in text  # no private index requested
    assert "vars.BUILD_ADD_HOSTS" in text and "vars.BUILDX_DRIVER" in text


def test_private_index_passes_credentials_as_build_secrets(tmp_path: Path) -> None:
    root = _project(tmp_path)
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8") + '\n[[tool.uv.index]]\nname = "my-gitea"\nurl = "https://x/simple"\n',
        encoding="utf-8",
    )
    result = _pipeline(root, "--index-name", "my-gitea")
    assert result.exit_code == 0, result.output
    assert "not declared" not in result.output and "UV_INDEX_USERNAME" in result.output

    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.count("type=secret,id=uv_index_username,env=UV_INDEX_MY_GITEA_USERNAME") == 2
    assert dockerfile.count("type=secret,id=uv_index_password,env=UV_INDEX_MY_GITEA_PASSWORD") == 2
    assert "ARG UV_INDEX" not in dockerfile and "ENV UV_INDEX" not in dockerfile  # nothing persisted in a layer

    for name in (".github/workflows/docker-image.yml", ".gitea/workflows/docker-image.yml"):
        text = (root / name).read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)
        build = next(step for step in workflow["jobs"]["build"]["steps"] if "build-push" in step.get("uses", ""))
        assert "uv_index_username=${{ secrets.UV_INDEX_USERNAME" in build["with"]["secrets"]
        if "gitea" in name:  # same Gitea hosts the index: the registry secrets are the fallback
            assert "|| secrets.REGISTRY_USERNAME" in build["with"]["secrets"]
            assert "|| secrets.REGISTRY_TOKEN" in build["with"]["secrets"]
        assert "uv_index_password=${{ secrets.UV_INDEX_PASSWORD" in build["with"]["secrets"]
    gitlab = (root / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert yaml.safe_load(gitlab)["build-image"]["script"]
    assert "--secret id=uv_index_username,env=UV_INDEX_USERNAME" in gitlab


def test_private_index_warns_when_not_declared_and_rejects_bad_names(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = _pipeline(root, "--index-name", "gitea")
    assert result.exit_code == 0
    assert "not declared in pyproject.toml" in " ".join(result.output.split())
    bad = _pipeline(_project(tmp_path / "bad"), "--index-name", "bad name!")
    assert bad.exit_code == 1 and "Invalid index name" in bad.output


# --- serve ------------------------------------------------------------------


def _serve_project(tmp_path: Path) -> Path:
    root = _project(tmp_path)
    (root / "pachifast.yaml").write_text("server:\n  host: 127.0.0.1\n  port: 9050\n", encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _no_pachifast_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in [k for k in os.environ if k.startswith("PACHIFAST_")]:
        monkeypatch.delenv(key)


def test_serve_options_come_from_yaml_and_disable_reload(tmp_path: Path) -> None:
    root = _serve_project(tmp_path)
    options = serve_service.build_uvicorn_options(base_dir=str(root))
    assert options == {
        "host": "127.0.0.1",
        "port": 9050,
        "workers": 1,
        "proxy_headers": True,
        "log_level": "info",
        "reload": False,
        "app_dir": str(root),
    }
    assert "forwarded_allow_ips" not in options  # uvicorn's own (prudent) default


def test_serve_cli_options_win_and_are_forwarded_to_uvicorn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    import uvicorn

    root = _serve_project(tmp_path)
    calls: list[tuple[tuple, dict]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))
    previous = Path.cwd()
    try:
        result = runner.invoke(
            app,
            ["serve", "--base-dir", str(root), "--port", "7000", "--workers", "3", "--no-proxy-headers"]
            + ["--forwarded-allow-ips", "10.0.0.1", "--log-level", "WARNING"],
        )
        assert Path.cwd() == root.resolve()  # the server runs from the project directory
    finally:
        os.chdir(previous)
    assert result.exit_code == 0, result.output
    ((args, kwargs),) = calls
    assert args == ("main:app",)
    assert kwargs["host"] == "127.0.0.1" and kwargs["port"] == 7000
    assert kwargs["workers"] == 3 and kwargs["proxy_headers"] is False and kwargs["reload"] is False
    assert kwargs["forwarded_allow_ips"] == "10.0.0.1" and kwargs["log_level"] == "warning"


def test_serve_environment_beats_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _serve_project(tmp_path)
    monkeypatch.setenv("PACHIFAST_PORT", "9999")
    assert serve_service.build_uvicorn_options(base_dir=str(root))["port"] == 9999


def test_serve_reports_a_missing_module_and_bad_values(tmp_path: Path) -> None:
    missing = runner.invoke(app, ["serve", "--base-dir", str(tmp_path)])
    assert missing.exit_code == 1
    message = " ".join(missing.output.split())  # the console wraps long lines
    assert "Module 'main' not found" in message and "Run `pachifast serve`" in message

    root = _serve_project(tmp_path / "ok")
    with pytest.raises(ValueError, match="log-level"):
        serve_service.build_uvicorn_options(base_dir=str(root), log_level="loud")
    with pytest.raises(ValueError, match="workers"):
        serve_service.build_uvicorn_options(base_dir=str(root), workers=0)


def test_new_pipeline_next_steps_keep_square_brackets(tmp_path: Path) -> None:
    """The next-steps note must print ``[tool.uv.sources]`` literally (rich would treat it as markup)."""
    assert (
        runner.invoke(
            app, ["new", "project", "demo", "--output-dir", str(tmp_path), "--no-interactive", "--no-git"]
        ).exit_code
        == 0
    )
    result = runner.invoke(app, ["new", "pipeline", "--base-dir", str(tmp_path / "demo")])
    assert result.exit_code == 0, result.output
    assert "[tool.uv.sources]" in result.output


def test_ca_cert_makes_the_builder_trust_an_internal_authority(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "certs").mkdir()
    (root / "certs" / "internal-ca.crt").write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    result = _pipeline(root, "--ca-cert", "certs/internal-ca.crt", "--index-name", "gitea")
    assert result.exit_code == 0, result.output
    assert "not found" not in result.output.replace("project_modules.py not found", "")
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY certs/internal-ca.crt /usr/local/share/ca-certificates/internal-ca.crt" in dockerfile
    assert "RUN update-ca-certificates" in dockerfile and "ENV UV_SYSTEM_CERTS=1" in dockerfile
    builder, runtime = dockerfile.split("AS runtime")
    assert "update-ca-certificates" in builder and "update-ca-certificates" not in runtime  # not in the final image


def test_ca_cert_is_optional_validated_and_must_exist(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _pipeline(root).exit_code == 0
    assert "ca-certificates" not in (root / "Dockerfile").read_text(encoding="utf-8")

    missing = _pipeline(_project(tmp_path / "second"), "--ca-cert", "certs/nope.crt")
    assert missing.exit_code == 0 and "certs/nope.crt not found" in " ".join(missing.output.split())
    for bad in ("/etc/ssl/ca.crt", "../ca.crt", "certs/ca.txt", "a b.crt", "certs/ca.crt; rm -rf /"):
        result = _pipeline(_project(tmp_path / f"bad{abs(hash(bad))}"), "--ca-cert", bad)
        assert result.exit_code == 1 and "Invalid --ca-cert" in result.output, bad


def test_dockerfile_checks_the_message_catalogs_in_the_builder_stage(tmp_path: Path) -> None:
    root = _project(tmp_path)
    assert _pipeline(root).exit_code == 0
    builder, runtime = (root / "Dockerfile").read_text(encoding="utf-8").split("AS runtime")
    assert ".venv/bin/pachifast locale check; fi" in builder and "locale check" not in runtime
    assert "locale --help" in builder  # older pachifast versions without the command do not break the build
    # after the project is copied and installed, so the catalogs of the apps are there
    assert builder.index("COPY . .") < builder.index("locale check")
