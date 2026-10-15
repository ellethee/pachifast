import json
from pathlib import Path

import jsonschema
import pytest
import yaml
from typer.testing import CliRunner

from pachifast.cli.app import app
from pachifast.config.schema import DESCRIPTIONS, build_config_schema
from pachifast.config.sections import SECTIONS
from pachifast.config.settings import PachifastSettings
from pachifast.scaffold import templates

ROOT = Path(__file__).resolve().parent.parent


def _validate(data: object) -> None:
    jsonschema.Draft202012Validator(build_config_schema()).validate(data)


def test_every_setting_has_a_description() -> None:
    names = {name.lower() for name in PachifastSettings.model_fields}
    assert names == set(DESCRIPTIONS), "describe new settings in config/schema.py"


def test_the_schema_is_valid_and_lists_lower_case_keys_per_section() -> None:
    schema = build_config_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    assert list(schema["properties"]) == list(SECTIONS)
    assert schema["additionalProperties"] is True
    for name, section in schema["properties"].items():
        assert section["additionalProperties"] is False and section["description"]
        assert list(section["properties"]) == [key for key, _ in SECTIONS[name]]
    assert "secret_key" in schema["properties"]["auth"]["properties"]
    assert "SECRET_KEY" not in schema["properties"]["auth"]["properties"]
    assert schema["properties"]["cache"]["properties"]["url"]["pattern"]
    assert schema["properties"]["server"]["properties"]["port"]["maximum"] == 65535


def test_the_demo_configuration_validates() -> None:
    _validate(yaml.safe_load((ROOT / "examples" / "demo" / "pachifast.yaml").read_text(encoding="utf-8")))


def test_the_sample_configuration_and_a_full_one_validate() -> None:
    _validate(yaml.safe_load(templates.project_template_config_yaml()))
    _validate(
        {
            "server": {"port": 9000, "debug": True},
            "log": {"log_level": "info".upper()},
            "routes": {"disable_core_routers": ["permissions"]},
            "events": {"public_types": "login,logout", "ticket_store": "cache"},
            "cache": {"url": "redis://localhost/0"},
            "app": {"default_locale": "pt-BR"},
            "my_own_setting": {"anything": 1},
        }
    )


@pytest.mark.parametrize(
    "data",
    [
        {"server": {"port": 0}},
        {"server": {"port": "http"}},
        {"server": {"debug": "maybe"}},
        {"log": {"log_level": "LOUD"}},
        {"events": {"ticket_store": "disk"}},
        {"cache": {"url": "http://nope"}},
        {"app": {"default_locale": "not a tag"}},
        {"routes": {"disable_routes": [1]}},
        {"server": {"prot": 1}},
        {"server": 8000},
    ],
)
def test_wrong_values_are_refused(data: dict) -> None:
    with pytest.raises(jsonschema.ValidationError):
        _validate(data)


def test_the_committed_schema_is_up_to_date() -> None:
    committed = json.loads((ROOT / "docs" / "reference" / "pachifast.schema.json").read_text(encoding="utf-8"))
    assert committed == build_config_schema(), "run: uv run python scripts/gen_reference.py"


def test_the_demo_schema_is_up_to_date() -> None:
    committed = json.loads((ROOT / "examples" / "demo" / "pachifast.schema.json").read_text(encoding="utf-8"))
    assert committed == build_config_schema(), (
        "run: uv run pachifast config schema -o examples/demo/pachifast.schema.json"
    )


def test_cli_prints_and_writes_the_schema(tmp_path: Path) -> None:
    runner = CliRunner()
    printed = runner.invoke(app, ["config", "schema"])
    assert printed.exit_code == 0 and json.loads(printed.output) == build_config_schema()
    target = tmp_path / "s.json"
    written = runner.invoke(app, ["config", "schema", "-o", str(target)])
    assert written.exit_code == 0 and json.loads(target.read_text()) == build_config_schema()


def test_generated_reference_pages_are_up_to_date() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("gen_reference", ROOT / "scripts" / "gen_reference.py")
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    for path, text in module.pages().items():
        assert path.read_text(encoding="utf-8") == text, f"{path.name} is stale: uv run python scripts/gen_reference.py"
