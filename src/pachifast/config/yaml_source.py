"""Optional YAML configuration file, the lowest priority settings source.

The file is looked up in the project directory (the current directory) and the first one found is loaded::

    pachifast.yaml, pachifast.yml

Other names (``config.yaml``, ``<project>.yaml``) are not read: a file of another tool in the same directory would
otherwise be taken for settings. ``PACHIFAST_CONFIG_FILE`` points to a specific file instead.
A missing file is not an error; an unreadable or malformed one is.

Priority of the settings sources, highest first: explicit arguments, environment variables, ``.env``,
YAML file, defaults. Keys are the setting names (case-insensitive), e.g. ``port: 9000``.
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

log = logging.getLogger(__name__)

CONFIG_FILE_VAR = "PACHIFAST_CONFIG_FILE"
_FIXED_NAMES = ("pachifast",)
_SUFFIXES = (".yaml", ".yml")


def candidate_paths(base_dir: Path) -> list[Path]:
    """Return the config file names to try in ``base_dir``, in priority order."""
    return [base_dir / f"{name}{suffix}" for name in _FIXED_NAMES for suffix in _SUFFIXES]


def find_config_file(base_dir: Path | None = None) -> Path | None:
    """Locate the config file: ``PACHIFAST_CONFIG_FILE`` if set, else the first candidate in ``base_dir``.

    Raises:
        FileNotFoundError: If ``PACHIFAST_CONFIG_FILE`` points to a file that does not exist.
    """
    explicit = os.environ.get(CONFIG_FILE_VAR)
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"{CONFIG_FILE_VAR} points to a missing file: {path}")
        return path
    root = (base_dir or Path.cwd()).resolve()
    return next((path for path in candidate_paths(root) if path.is_file()), None)


def load_config_data(base_dir: Path | None = None) -> dict[str, Any]:
    """Load the YAML config file as a dict with lower-cased keys (``{}`` when there is no file).

    Raises:
        ValueError: If the file is not valid YAML or its top level is not a mapping.
    """
    path = find_config_file(base_dir)
    if path is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid configuration file {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid configuration file {path}: the top level must be a mapping (key: value).")
    log.debug("Loaded configuration file %s", path)
    return {str(key).lower(): value for key, value in data.items()}


class YamlConfigSource(PydanticBaseSettingsSource):
    """Settings source reading the YAML config file; keys match field names or string aliases, ignoring case."""

    def __init__(self, settings_cls: type[BaseSettings], base_dir: Path | None = None) -> None:
        super().__init__(settings_cls)
        self._data = load_config_data(base_dir)

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """Return the value for one field (not used by ``__call__``; required by the base class)."""
        key = field.validation_alias if isinstance(field.validation_alias, str) else field_name
        return self._data.get(key.lower()), key, False

    def __call__(self) -> dict[str, Any]:
        """Collect the values of the fields present in the file, keyed as the model expects them."""
        values: dict[str, Any] = {}
        for field_name, field in self.settings_cls.model_fields.items():
            value, key, _ = self.get_field_value(field, field_name)
            if value is not None:
                values[key] = value
        return values
