"""The sections of ``pachifast.yaml``: single source of truth for the nested layout of the configuration file.

The file groups the settings in sections (``server``, ``auth``, ``cache``...). Each section lists its keys; a key is
either the name of a settings field (``port``, ``secret_key``) or, in the prefixed sections, the field name without
the section prefix (``cache.url`` is ``cache_url``, ``mfa.enabled`` is ``mfa_enabled``). The JSON Schema generator
(``schema.py``) and the YAML loader (``yaml_source.py``) both read this module, and a test checks that every settings
field belongs to exactly one section, so a new setting cannot be forgotten.
"""

from typing import Final

SectionKeys = list[tuple[str, str]]
"""A section: ``(yaml_key, field_name)`` pairs, in the order shown in the schema and in the docs."""

SECTION_PREFIXES: Final[dict[str, str]] = {
    "cache": "cache_",
    "mfa": "mfa_",
    "activity_log": "activity_log_",
    "events": "events_",
    "rate_limit": "rate_limit_",
}
"""Sections whose keys may omit the prefix of the field name (``cache.url`` -> ``cache_url``)."""

SECTION_DESCRIPTIONS: Final[dict[str, str]] = {
    "app": "Identity and language of the application.",
    "server": "Development server, API behaviour and response format.",
    "dev": "Switches for development only: never enable them in production.",
    "log": "Logging.",
    "routes": "Routers and routes of the core and of the project that are not mounted.",
    "database": "Main database, connection pool and table prefix.",
    "cache": "Cache of the application: in-process or shared Redis.",
    "auth": "Authentication: database, tokens, passwords, login protection.",
    "mfa": "Multi-factor authentication.",
    "tables": "Names of the tables of the core (empty: table_prefix + default name).",
    "activity_log": "Activity log of the write requests.",
    "events": "Event WebSocket.",
    "rate_limit": "Defaults of the `rate_limit()` dependency for the routes that use it.",
}
"""Short description of each section, used by the JSON Schema and the docs."""


def _pairs(section: str, keys: str) -> SectionKeys:
    """Build the ``(yaml_key, field_name)`` pairs of a section from a space-separated list of YAML keys."""
    return [(key, field_name(section, key)) for key in keys.split()]


def field_name(section: str, key: str) -> str:
    """Field name (lower case) of ``section.key``: the key itself, or the section prefix plus the key.

    Fields that are real names (``port``) are written as they are; the prefixed sections (``cache``, ``mfa``,
    ``activity_log``, ``events``) accept the short form (``url``) and the full one (``cache_url``).
    """
    prefix = SECTION_PREFIXES.get(section)
    if prefix and not key.startswith(prefix):
        return prefix + key
    return key


SECTIONS: Final[dict[str, SectionKeys]] = {
    "app": _pairs("app", "app_name project_name description version default_locale use_accept_language api_v1_prefix"),
    "server": _pairs(
        "server",
        "host port debug docs_enabled envelope_enabled request_id_enabled expose_response_database "
        "wrap_auth_token_response",
    ),
    "dev": _pairs("dev", "global_noauth swagger_noauth"),
    "log": _pairs("log", "log_format log_level"),
    "routes": _pairs("routes", "disable_core_routers disable_core_routes disable_routers disable_routes"),
    "database": _pairs(
        "database",
        "database_url dbdebug db_pool_size db_max_overflow db_pool_recycle db_pool_timeout table_prefix "
        "auto_create_auth_schema",
    ),
    "cache": _pairs("cache", "url prefix max_entries"),
    "auth": _pairs(
        "auth",
        "auth_database_url secret_key algorithm access_token_expire_minutes refresh_token_expire_days "
        "refresh_token_rotation refresh_reuse_grace_seconds refresh_mode refresh_cookie_name refresh_cookie_path "
        "refresh_cookie_samesite refresh_cookie_secure refresh_allowed_origins registration_enabled "
        "password_min_length login_max_attempts login_lockout_minutes auth_rate_limit auth_rate_window_seconds",
    ),
    "mfa": _pairs("mfa", "enabled encryption_key max_attempts otp_expire_minutes send_cooldown_seconds totp_issuer"),
    "tables": _pairs(
        "tables",
        "user_table group_table permission_table user_group_table user_permission_table group_permission_table "
        "user_settings_table mfa_config_table mfa_pending_table",
    ),
    "activity_log": _pairs("activity_log", "enabled exclude_paths retention_days table"),
    "events": _pairs("events", "broadcast_enabled allow_query_token public_types ticket_store"),
    "rate_limit": _pairs("rate_limit", "default window_seconds"),
}
"""Ordered mapping section -> ``(yaml_key, field_name)`` pairs. Field names are lower case."""


def field_locations() -> dict[str, str]:
    """Map every field name (lower case) to its canonical ``section.key`` location in the YAML file."""
    return {field: f"{section}.{key}" for section, pairs in SECTIONS.items() for key, field in pairs}
