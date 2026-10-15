"""Optional YAML configuration file, the lowest priority settings source.

The file is looked up in the project directory (the current directory) and the first one found is loaded::

    pachifast.yaml, pachifast.yml

Other names (``config.yaml``, ``<project>.yaml``) are not read: a file of another tool in the same directory would
otherwise be taken for settings. ``PACHIFAST_CONFIG_FILE`` points to a specific file instead.
A missing file is not an error; an unreadable or malformed one is.

Priority of the settings sources, highest first: explicit arguments, environment variables, ``.env``,
YAML file, defaults. The file is organised in sections (see ``sections``), keys are case-insensitive::

    server:
      port: 9000

The flat format of 0.15 (``port: 9000`` at the top level) is still read but deprecated.
"""

import difflib
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from .sections import SECTIONS, field_locations

log = logging.getLogger(__name__)

CONFIG_FILE_VAR = "PACHIFAST_CONFIG_FILE"
_FIXED_NAMES = ("pachifast",)
_SUFFIXES = (".yaml", ".yml")
_WARNED: set[tuple[Path, tuple[str, ...]]] = set()  # deprecation warnings already logged: once per file


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


def _flatten(data: dict[str, Any], path: Path) -> dict[str, Any]:
    """Turn the parsed file into ``{field_name: value}``: sections are flattened, flat keys are still accepted.

    Raises:
        ValueError: A section that is not a mapping, an unknown key in a section, or a field given twice.
    """
    flat: dict[str, Any] = {}
    origin: dict[str, str] = {}  # field -> where it was given, to report duplicates
    deprecated: list[str] = []
    locations = field_locations()
    for raw_key, value in data.items():
        key = str(raw_key).lower()
        if key in SECTIONS:
            if value is None:
                continue
            if not isinstance(value, dict):
                raise ValueError(f"Invalid configuration file {path}: section '{key}' must be a mapping (key: value).")
            valid = {name.lower(): field for name, field in SECTIONS[key]}
            for raw_name, item in value.items():
                name = str(raw_name).lower()
                if name not in valid:
                    hint = difflib.get_close_matches(name, valid, n=1)
                    suggestion = f" Did you mean '{hint[0]}'?" if hint else ""
                    raise ValueError(
                        f"Invalid configuration file {path}: unknown key '{name}' in section '{key}'."
                        f"{suggestion} Valid keys: {', '.join(valid)}."
                    )
                _store(flat, origin, valid[name], item, f"{key}.{name}", path)
        else:
            if key in locations:
                deprecated.append(key)
            # other keys belong to the project or to an app (own settings class): kept for it
            _store(flat, origin, key, value, key, path)
    if deprecated and (path, tuple(deprecated)) not in _WARNED:
        _WARNED.add((path, tuple(deprecated)))
        moved = ", ".join(f"{key} -> {locations[key]}" for key in deprecated)
        log.warning("%s: flat keys are deprecated, move them into their section (%s).", path, moved)
    return flat


def _store(flat: dict[str, Any], origin: dict[str, str], field: str, value: Any, where: str, path: Path) -> None:
    """Record ``field`` unless the value is ``null``; the same field given twice is an error."""
    if value is None:
        return
    if field in flat:
        raise ValueError(
            f"Invalid configuration file {path}: '{field}' is given twice ('{origin[field]}' and '{where}')."
        )
    flat[field] = value
    origin[field] = where


def load_config_data(base_dir: Path | None = None) -> dict[str, Any]:
    """Load the YAML config file as a flat dict ``{field_name_lower: value}`` (``{}`` when there is no file).

    The file is organised in sections (``server: {port: 9000}``); top-level setting names of the old flat format are
    still read, with a deprecation warning. Keys with a ``null`` value are ignored.

    Raises:
        ValueError: If the file is not valid YAML, its top level is not a mapping, or a section is malformed.
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
    return _flatten(data, path)


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
