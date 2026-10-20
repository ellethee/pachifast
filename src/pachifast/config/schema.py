"""JSON Schema of the ``pachifast.yaml`` configuration file, generated from the settings model.

Editors with a YAML language server (VS Code with the Red Hat YAML extension, JetBrains, Neovim...) use it for
completion, validation and hover help. The settings are grouped in sections (see ``sections.py``); keys are
case-insensitive, so the schema lists them in lower case. Top-level keys that are not sections are allowed:
projects and apps add their own.
"""

from typing import Any

from .sections import SECTION_DESCRIPTIONS, SECTIONS
from .settings import PachifastSettings

SCHEMA_ID = "https://pachifast.local/schemas/pachifast.schema.json"

_ENV_ONLY = " Prefer the environment variable or the .env file: do not commit it."

DESCRIPTIONS: dict[str, str] = {
    "host": "Address the development server (`pachifast dev`) listens on.",
    "port": "Port of the development server.",
    "app_name": "Name of the application, shown in the OpenAPI documentation.",
    "database_url": "SQLAlchemy URL of the main database (async driver, e.g. postgresql+asyncpg://...)." + _ENV_ONLY,
    "secret_key": "Secret that signs the tokens: random string of at least 32 characters, required." + _ENV_ONLY,
    "algorithm": "JWT signing algorithm.",
    "access_token_expire_minutes": "Lifetime of an access token, in minutes.",
    "refresh_token_expire_days": "Lifetime of a refresh token, in days.",
    "auth_database_url": "SQLAlchemy URL of the authentication database (users, groups, permissions)." + _ENV_ONLY,
    "auto_create_auth_schema": "Create the auth tables at startup instead of using Alembic migrations.",
    "api_v1_prefix": "Prefix added to every API route.",
    "debug": "Show the traceback page to clients on errors: development only, off by default.",
    "log_level": "Log level of the application; empty means DEBUG with `debug`, INFO otherwise.",
    "dbdebug": "Log the SQL statements.",
    "registration_enabled": "Allow POST /auth/register (anyone can create an account); false answers 403.",
    "refresh_reuse_grace_seconds": "Grace seconds in which a reused refresh token is refused but the login is kept.",
    "refresh_token_rotation": "Every refresh issues a new, single-use refresh token; a reused one revokes the login.",
    "global_noauth": "Disable authentication on every router: development only.",
    "db_pool_size": "Connections a server database (PostgreSQL) keeps open, per process.",
    "db_max_overflow": "Extra connections allowed above db_pool_size under load, per process.",
    "db_pool_timeout": "Seconds a request waits for a free connection before it fails.",
    "db_pool_recycle": "Seconds after which a connection is replaced; -1 never.",
    "activity_log_retention_days": "Days of activity log that `pachifast audit purge` keeps; 0 needs --days.",
    "log_format": "Format of the log lines: text (coloured) or json (one object per line).",
    "request_id_enabled": "Put an X-Request-ID header on every response and the id in every log line.",
    "auth_rate_limit": "Requests per client address and window on login, registration and MFA check; 0 is no limit.",
    "mfa_send_cooldown_seconds": "Seconds between two codes sent by the email or SMS enrolment; 0 is no wait.",
    "refresh_mode": "Where the refresh token travels: `body` (JSON, default), `cookie` (HttpOnly cookie) or `both`.",
    "refresh_cookie_name": "Name of the refresh cookie.",
    "refresh_cookie_path": "Path of the refresh cookie; keep it on the auth routes.",
    "refresh_cookie_samesite": "SameSite attribute of the refresh cookie: `lax` or `strict`.",
    "refresh_cookie_secure": "Secure attribute of the refresh cookie; empty: on when the request is https.",
    "refresh_allowed_origins": "Comma separated origins, besides the request host, allowed to use the refresh cookie.",
    "auth_rate_window_seconds": "Length of the window of auth_rate_limit, in seconds.",
    "docs_enabled": "Serve the Swagger UI (/docs, /docs/core) and the OpenAPI schemas; false removes them (404).",
    "swagger_noauth": "Let the Swagger UI (/docs) act as the first superuser without a token: development only.",
    "expose_response_database": "Add the name of the database the data came from to the enveloped API responses.",
    "envelope_enabled": "Wrap 2xx JSON in {success, message, data}; false: plain bodies (configure the client too).",
    "wrap_auth_token_response": "Wrap the login and refresh-token responses in the standard envelope.",
    "activity_log_enabled": "Record POST/PUT/PATCH/DELETE requests in the activity log and expose it.",
    "activity_log_exclude_paths": "Comma-separated path fragments that are never logged.",
    "events_broadcast_enabled": "Open the event WebSocket (/api/core/events/ws).",
    "events_allow_query_token": "Accept the access token in the WebSocket URL (?token=); false: one-time tickets only.",
    "events_ticket_store": "Where the WebSocket tickets live: the auth database (shared by workers) or the cache.",
    "events_public_types": "Event types sent to every client (e.g. login); default: superusers and the user concerned.",
    "cache_url": "Empty: in-process cache. redis://, rediss:// or unix:// URL: shared Redis (needs pachifast[redis]).",
    "cache_prefix": "Prefix of the cache keys, to share one Redis between applications.",
    "cache_max_entries": "Maximum entries of the in-process cache.",
    "default_locale": "Language of the API error messages (language tag, e.g. en, it, pt-BR).",
    "use_accept_language": "Pick the message language from the Accept-Language header of each request.",
    "project_name": "Name of the project, reported by /api/health/ (empty: the application title, default PachiFast).",
    "version": "Version of the project, reported by /api/health/ (empty: the application version, default 0.1.0).",
    "description": "Description of the project, reported by /api/health/ (empty: no description).",
    "disable_routers": "Routers of the project not to mount.",
    "disable_routes": "Routes of the project not to expose: `[METHOD ]/path`, relative to /api.",
    "disable_core_routers": "Routers of the core not to mount (e.g. permissions).",
    "disable_core_routes": "Routes of the core not to expose: `[METHOD ]/path`, relative to /api.",
    "password_min_length": "Minimum length of a password.",
    "login_max_attempts": "Failed logins before the account is locked.",
    "login_lockout_minutes": "How long a locked account stays locked, in minutes.",
    "table_prefix": "Prefix of every table name of the core.",
    "user_table": "Name of the users table (empty: table_prefix + user).",
    "user_settings_table": "Name of the user settings table (empty: table_prefix + user_settings).",
    "permission_table": "Name of the permissions table (empty: table_prefix + permission).",
    "group_table": "Name of the groups table (empty: table_prefix + group).",
    "group_permission_table": "Name of the group-permission table (empty: table_prefix + group_permission).",
    "user_group_table": "Name of the user-group table (empty: table_prefix + user_group).",
    "user_permission_table": "Name of the user-permission table (empty: table_prefix + user_permission).",
    "activity_log_table": "Name of the activity log table (empty: table_prefix + activity_log).",
    "mfa_enabled": "Enable multi-factor authentication.",
    "mfa_encryption_key": "Key of the TOTP secrets stored encrypted; empty: derived from secret_key." + _ENV_ONLY,
    "mfa_totp_issuer": "Issuer shown by authenticator apps.",
    "mfa_otp_expire_minutes": "Minutes the user has to give the MFA code after the password.",
    "mfa_max_attempts": "Wrong MFA codes allowed before the login challenge is discarded.",
    "mfa_config_table": "Name of the MFA configuration table (empty: table_prefix + mfa_config).",
    "mfa_pending_table": "Name of the pending MFA challenges table (empty: table_prefix + mfa_pending).",
}

_LOG_LEVELS = ["", "CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]


def _as_yaml_property(prop: dict[str, Any], name: str) -> dict[str, Any]:
    """Adapt a property of the model schema to the YAML file: description, list-or-comma-string, extra checks."""
    prop = {key: value for key, value in prop.items() if key != "title"}
    prop["description"] = DESCRIPTIONS.get(name, prop.get("description", ""))
    if prop.get("type") == "array":
        prop = {
            "description": prop["description"],
            "default": prop.get("default", []),
            "anyOf": [prop, {"type": "string"}],
        }
        del prop["anyOf"][0]["description"]
    if name == "log_level":
        prop["enum"] = _LOG_LEVELS
    if name == "cache_url":
        prop["pattern"] = r"^$|^(redis|rediss|unix)://"
    if name == "default_locale":
        prop["pattern"] = r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{2,8})*$"
    return prop


def build_config_schema() -> dict[str, Any]:
    """The JSON Schema (draft 2020-12) of ``pachifast.yaml``: one object per section, keys as in ``sections.py``."""
    model_schema = PachifastSettings.model_json_schema()
    by_field = {name.lower(): prop for name, prop in model_schema["properties"].items()}
    properties: dict[str, Any] = {}
    for section, pairs in SECTIONS.items():
        properties[section] = {
            "description": SECTION_DESCRIPTIONS[section],
            "type": "object",
            "properties": {key: _as_yaml_property(by_field[field], field) for key, field in pairs},
            "additionalProperties": False,
        }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "pachifast.yaml",
        "description": "Configuration of a pachifast project. Environment variables and .env take precedence.",
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
