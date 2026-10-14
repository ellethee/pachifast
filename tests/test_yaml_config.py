"""YAML configuration file: discovery, priority (env > .env > YAML > defaults), errors, dev address."""

import os
from pathlib import Path

import pytest

from pachifast.cli.services.dev import resolve_server_address
from pachifast.config import PachifastSettingsBase, ServerSettings
from pachifast.config.yaml_source import CONFIG_FILE_VAR, candidate_paths, find_config_file, load_config_data


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No PACHIFAST_* variables, and the project directory is the current directory."""
    for key in [k for k in os.environ if k.startswith("PACHIFAST_")]:
        monkeypatch.delenv(key)
    monkeypatch.chdir(tmp_path)


def _settings(root: Path) -> ServerSettings:
    """Load ServerSettings for ``root`` the way ``pachifast dev`` does."""
    return ServerSettings(_env_file=str(root / ".env"))  # type: ignore[call-arg]


# --- discovery --------------------------------------------------------------


def test_no_config_file_is_not_an_error(tmp_path: Path) -> None:
    assert find_config_file(tmp_path) is None
    assert load_config_data(tmp_path) == {}
    settings = _settings(tmp_path)
    assert (settings.host, settings.port) == ("127.0.0.1", 8000)


def test_candidates_priority_and_project_name(tmp_path: Path) -> None:
    project = tmp_path / "demo"
    project.mkdir()
    names = [path.name for path in candidate_paths(project)]
    assert names == ["pachifast.yaml", "pachifast.yml"]

    # files of other tools in the directory are not settings
    (project / "demo.yaml").write_text("port: 1111\n")
    (project / "config.yaml").write_text("debug: true\n")
    assert find_config_file(project) is None
    (project / "pachifast.yml").write_text("port: 2222\n")
    assert find_config_file(project) == project / "pachifast.yml"
    (project / "pachifast.yaml").write_text("port: 3333\n")
    assert find_config_file(project) == project / "pachifast.yaml"  # first one wins
    assert load_config_data(project) == {"port": 3333}


def test_explicit_config_file_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "elsewhere.yaml"
    other.write_text("port: 4444\n")
    monkeypatch.setenv(CONFIG_FILE_VAR, str(other))
    assert _settings(tmp_path).port == 4444
    monkeypatch.setenv(CONFIG_FILE_VAR, str(tmp_path / "missing.yaml"))
    with pytest.raises(FileNotFoundError, match="missing file"):
        find_config_file(tmp_path)


# --- priority ---------------------------------------------------------------


def test_yaml_values_are_used_and_keys_are_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("PORT: 9000\nHost: 127.0.0.1\n")
    settings = _settings(tmp_path)
    assert (settings.host, settings.port) == ("127.0.0.1", 9000)


def test_environment_beats_dotenv_beats_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pachifast.yaml").write_text("port: 9000\nhost: 10.0.0.1\n")
    assert _settings(tmp_path).port == 9000  # yaml alone

    (tmp_path / ".env").write_text("PACHIFAST_PORT=9100\n")
    settings = _settings(tmp_path)
    assert (settings.port, settings.host) == (9100, "10.0.0.1")  # .env over yaml, yaml fills the rest

    monkeypatch.setenv("PACHIFAST_PORT", "9200")
    assert _settings(tmp_path).port == 9200  # real environment over .env


def test_invalid_yaml_is_reported_with_the_file_name(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("port: [unclosed\n")
    with pytest.raises(ValueError, match="Invalid configuration file .*pachifast.yaml"):
        _settings(tmp_path)
    (tmp_path / "pachifast.yaml").write_text("- just\n- a list\n")
    with pytest.raises(ValueError, match="top level must be a mapping"):
        _settings(tmp_path)


def test_empty_yaml_is_fine_and_invalid_port_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("")
    assert _settings(tmp_path).port == 8000
    (tmp_path / "pachifast.yaml").write_text("port: 70000\n")
    with pytest.raises(ValueError):
        _settings(tmp_path)


def test_list_settings_and_aliases_come_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real lists work for the disable_core_* settings and alias-only fields are matched case-insensitively."""
    from pydantic import Field

    from pachifast.config import PachifastSettings

    (tmp_path / "pachifast.yaml").write_text(
        "secret_key: "
        + "k" * 40
        + "\ndisable_core_routers: [permissions, admin]\ndisable_core_routes: [/user/settings]\n"
    )
    settings = PachifastSettings(_env_file=None)  # type: ignore[call-arg]
    assert settings.disable_core_routers == ["permissions", "admin"]
    assert settings.disable_core_routes == ["/user/settings"]

    class BillingSettings(PachifastSettingsBase):
        database_url: str = Field(default="", validation_alias="BILLING_DATABASE_URL")

    (tmp_path / "pachifast.yaml").write_text("billing_database_url: sqlite:///billing.db\n")
    assert BillingSettings(_env_file=None).database_url == "sqlite:///billing.db"  # type: ignore[call-arg]
    monkeypatch.setenv("BILLING_DATABASE_URL", "sqlite:///env.db")
    assert BillingSettings(_env_file=None).database_url == "sqlite:///env.db"  # type: ignore[call-arg]


# --- pachifast dev ----------------------------------------------------------


def test_dev_address_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = str(tmp_path)
    assert resolve_server_address(root) == ("127.0.0.1", 8000)
    (tmp_path / "pachifast.yaml").write_text("port: 9000\nhost: 10.1.2.3\n")
    assert resolve_server_address(root) == ("10.1.2.3", 9000)
    monkeypatch.setenv("PACHIFAST_PORT", "9300")
    assert resolve_server_address(root) == ("10.1.2.3", 9300)
    assert resolve_server_address(root, host="0.0.0.0", port=7000) == ("0.0.0.0", 7000)  # CLI options win


def test_dev_address_reads_yaml_of_the_project_dir_not_the_cwd(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pachifast.yaml").write_text("port: 9001\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.chdir(elsewhere)
    assert resolve_server_address(str(project))[1] == 9001
    assert Path.cwd() == elsewhere  # the lookup did not leave the cwd changed
