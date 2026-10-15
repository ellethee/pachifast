"""YAML configuration file: discovery, priority (env > .env > YAML > defaults), errors, dev address."""

import os
from pathlib import Path

import pytest

from pachifast.cli.services.dev import resolve_server_address
from pachifast.config import PachifastSettingsBase, ServerSettings
from pachifast.config.sections import SECTIONS, field_locations
from pachifast.config.settings import PachifastSettings
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
    (project / "demo.yaml").write_text("server:\n  port: 1111\n")
    (project / "config.yaml").write_text("debug: true\n")
    assert find_config_file(project) is None
    (project / "pachifast.yml").write_text("server:\n  port: 2222\n")
    assert find_config_file(project) == project / "pachifast.yml"
    (project / "pachifast.yaml").write_text("server:\n  port: 3333\n")
    assert find_config_file(project) == project / "pachifast.yaml"  # first one wins
    assert load_config_data(project) == {"port": 3333}  # flat {field: value}


def test_explicit_config_file_variable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = tmp_path / "elsewhere.yaml"
    other.write_text("server:\n  port: 4444\n")
    monkeypatch.setenv(CONFIG_FILE_VAR, str(other))
    assert _settings(tmp_path).port == 4444
    monkeypatch.setenv(CONFIG_FILE_VAR, str(tmp_path / "missing.yaml"))
    with pytest.raises(FileNotFoundError, match="missing file"):
        find_config_file(tmp_path)


# --- priority ---------------------------------------------------------------


def test_yaml_values_are_used_and_keys_are_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("SERVER:\n  PORT: 9000\n  Host: 127.0.0.1\n")
    settings = _settings(tmp_path)
    assert (settings.host, settings.port) == ("127.0.0.1", 9000)


def test_environment_beats_dotenv_beats_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pachifast.yaml").write_text("server:\n  port: 9000\n  host: 10.0.0.1\n")
    assert _settings(tmp_path).port == 9000  # yaml alone

    (tmp_path / ".env").write_text("PACHIFAST_PORT=9100\n")
    settings = _settings(tmp_path)
    assert (settings.port, settings.host) == (9100, "10.0.0.1")  # .env over yaml, yaml fills the rest

    monkeypatch.setenv("PACHIFAST_PORT", "9200")
    assert _settings(tmp_path).port == 9200  # real environment over .env


def test_invalid_yaml_is_reported_with_the_file_name(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("server:\n  port: [unclosed\n")
    with pytest.raises(ValueError, match="Invalid configuration file .*pachifast.yaml"):
        _settings(tmp_path)
    (tmp_path / "pachifast.yaml").write_text("- just\n- a list\n")
    with pytest.raises(ValueError, match="top level must be a mapping"):
        _settings(tmp_path)


def test_empty_yaml_is_fine_and_invalid_port_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("")
    assert _settings(tmp_path).port == 8000
    (tmp_path / "pachifast.yaml").write_text("server:\n  port: 70000\n")
    with pytest.raises(ValueError):
        _settings(tmp_path)


def test_list_settings_and_aliases_come_from_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Real lists work for the disable_core_* settings and alias-only fields are matched case-insensitively."""
    from pydantic import Field

    from pachifast.config import PachifastSettings

    (tmp_path / "pachifast.yaml").write_text(
        "auth:\n  secret_key: "
        + "k" * 40
        + "\nroutes:\n  disable_core_routers: [permissions, admin]\n  disable_core_routes: [/user/settings]\n"
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


# --- nested sections --------------------------------------------------------


def test_every_setting_belongs_to_exactly_one_section() -> None:
    """A new setting must be added to config/sections.py (and described in schema.py)."""
    located = [field for pairs in SECTIONS.values() for _, field in pairs]
    assert len(located) == len(set(located)), "a field is listed in two sections"
    fields = {name.lower() for name in PachifastSettings.model_fields}
    assert sorted(fields - set(located)) == [], "settings missing from config/sections.py"
    assert sorted(set(located) - fields) == [], "config/sections.py lists unknown settings"
    assert set(field_locations()) == fields


def test_a_nested_file_loads_into_the_settings(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text(
        "server:\n  port: 9100\nauth:\n  secret_key: " + "k" * 40 + "\n  login_max_attempts: 3\n"
        "my_project:\n  anything: 1\n"
    )
    data = load_config_data(tmp_path)
    assert data["port"] == 9100 and data["login_max_attempts"] == 3
    assert data["my_project"] == {"anything": 1}  # other top-level keys are left to the project


def test_prefixed_sections_map_to_prefixed_fields(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text(
        "cache:\n  url: redis://localhost/0\nmfa:\n  enabled: true\n"
        "activity_log:\n  table: audit\nevents:\n  broadcast_enabled: true\ntables:\n  mfa_config_table: m\n"
    )
    assert load_config_data(tmp_path) == {
        "cache_url": "redis://localhost/0",
        "mfa_enabled": True,
        "activity_log_table": "audit",
        "events_broadcast_enabled": True,
        "mfa_config_table": "m",
    }


def test_null_values_are_ignored(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("server:\n  port:\ncache:\nlog:\n  log_level: DEBUG\n")
    assert load_config_data(tmp_path) == {"log_level": "DEBUG"}


def test_a_flat_core_key_is_an_error_with_its_new_location(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("port: 9400\n")
    with pytest.raises(
        ValueError,
        match=r"pachifast.yaml: 'port' is no longer a top-level key: put it in the 'server' section \(server.port\)\.",
    ):
        load_config_data(tmp_path)


def test_all_the_flat_keys_are_listed_in_one_message(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("port: 1\nCACHE_URL: redis://x/0\nactivity_log_retention_days: 5\n")
    with pytest.raises(ValueError) as info:
        load_config_data(tmp_path)
    message = str(info.value)
    assert "'activity_log_retention_days'" in message and "(activity_log.retention_days)" in message
    assert "'cache_url'" in message and "(cache.url)" in message
    assert "'port'" in message and "(server.port)" in message
    assert message.index("activity_log_retention_days") < message.index("cache_url") < message.index("'port'")


def test_unknown_top_level_keys_are_still_passed_through(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("billing_database_url: sqlite://x\nserver:\n  port: 9400\n")
    assert load_config_data(tmp_path) == {"billing_database_url": "sqlite://x", "port": 9400}
    assert _settings(tmp_path).port == 9400


def test_unknown_key_in_a_section_is_an_error_with_a_hint(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("server:\n  prot: 9000\n")
    with pytest.raises(
        ValueError, match=r"pachifast.yaml.*unknown key 'prot' in section 'server'.*Did you mean 'port'"
    ) as info:
        load_config_data(tmp_path)
    assert "host" in str(info.value)  # lists the valid keys


def test_a_section_that_is_not_a_mapping_is_an_error(tmp_path: Path) -> None:
    (tmp_path / "pachifast.yaml").write_text("server: 8000\n")
    with pytest.raises(ValueError, match="pachifast.yaml.*section 'server' must be a mapping"):
        load_config_data(tmp_path)


def test_environment_beats_a_nested_yaml_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "pachifast.yaml").write_text("server:\n  port: 9000\n")
    monkeypatch.setenv("PACHIFAST_PORT", "9500")
    assert _settings(tmp_path).port == 9500


# --- pachifast dev ----------------------------------------------------------


def test_dev_address_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = str(tmp_path)
    assert resolve_server_address(root) == ("127.0.0.1", 8000)
    (tmp_path / "pachifast.yaml").write_text("server:\n  port: 9000\n  host: 10.1.2.3\n")
    assert resolve_server_address(root) == ("10.1.2.3", 9000)
    monkeypatch.setenv("PACHIFAST_PORT", "9300")
    assert resolve_server_address(root) == ("10.1.2.3", 9300)
    assert resolve_server_address(root, host="0.0.0.0", port=7000) == ("0.0.0.0", 7000)  # CLI options win


def test_dev_address_reads_yaml_of_the_project_dir_not_the_cwd(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "pachifast.yaml").write_text("server:\n  port: 9001\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.chdir(elsewhere)
    assert resolve_server_address(str(project))[1] == 9001
    assert Path.cwd() == elsewhere  # the lookup did not leave the cwd changed
