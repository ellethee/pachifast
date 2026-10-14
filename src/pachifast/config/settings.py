"""Settings :mod:`core.settings`."""

from collections.abc import Callable
from functools import lru_cache
from logging import getLogger
from typing import Annotated, Literal

from fastapi import Depends
from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from pachifast.modules import apply_module_settings

from .helpers import get_env_file
from .yaml_source import YamlConfigSource

log = getLogger(__name__)


class _PachifastMeta(type(BaseSettings)):
    def __new__(mcs, cls_name, bases, namespace, **kwargs):
        env_prefix = namespace.pop("_env_prefix", None)
        if env_prefix is not None:
            parent_config = {}
            for base in bases:
                if hasattr(base, "model_config"):
                    parent_config = dict(base.model_config)
                    break
            own_config = namespace.get("model_config")
            if own_config:
                parent_config.update(dict(own_config))
            parent_config["env_prefix"] = env_prefix
            namespace["model_config"] = SettingsConfigDict(**parent_config)
        return super().__new__(mcs, cls_name, bases, namespace, **kwargs)


class PachifastSettingsBase(BaseSettings, metaclass=_PachifastMeta):
    """Shared base for all Settings (project and app): the `_env_prefix` mechanism +
    common defaults (`env_file`, `extra`). No business fields: whoever needs them
    inherits from `PachifastSettings` below, per-app Settings derive directly from here."""

    model_config = SettingsConfigDict(
        env_prefix="PACHIFAST_",
        extra="allow",
        dotenv_filtering="match_prefix",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Priority: arguments, environment, ``.env``, YAML config file (see ``yaml_source``), secrets files.

        The ``.env`` file is looked up in the current directory each time the settings are built (not once, when
        this module is imported), so a command run with ``--base-dir`` from another directory finds the project's
        file. An explicit ``_env_file`` argument still wins.
        """
        if not getattr(dotenv_settings, "env_file", None) and (env_path := get_env_file()):
            dotenv_settings = DotEnvSettingsSource(settings_cls, env_file=env_path)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSource(settings_cls),
            file_secret_settings,
        )


class ServerSettings(PachifastSettingsBase):
    """Where the development server listens. Small on purpose: ``pachifast dev`` reads it without the full settings."""

    model_config = SettingsConfigDict(dotenv_filtering="match_prefix", extra="ignore")

    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)


class PachifastSettings(ServerSettings):
    # Restores the historical behaviour (no prefix filter) for the project Settings:
    # here reading extra variables without the own prefix is intended.
    model_config = SettingsConfigDict(dotenv_filtering=None, extra="allow")

    app_name: str = "PachiFast"

    # envs
    DATABASE_URL: str = ""
    SECRET_KEY: str = ""  # obbligatoria: nessun default (vedi _require_secret_key)
    ALGORITHM: Literal["HS256", "HS384", "HS512"] = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, ge=1)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, ge=1)
    refresh_token_rotation: bool = True
    # seconds after a rotation in which presenting the spent token again is refused but does not revoke the login
    # (two tabs refreshing together); 0 revokes at the first reuse
    refresh_reuse_grace_seconds: int = Field(default=10, ge=0)
    registration_enabled: bool = False
    AUTH_DATABASE_URL: str = ""
    auto_create_auth_schema: bool = False
    api_v1_prefix: str = ""
    # off by default: with debug=True Starlette shows the traceback (server paths included) to clients
    debug: bool = False
    # application log level (CRITICAL..DEBUG); empty: DEBUG with debug=true, otherwise INFO
    log_level: str = ""
    # "text" (coloured, for a terminal) or "json" (one object per line, for a log collector)
    log_format: Literal["text", "json"] = "text"
    # an X-Request-ID on every response and a request_id in every log line (the id of the client is kept if it is sane)
    request_id_enabled: bool = True
    dbdebug: bool = False
    # connection pool of a server database (PostgreSQL); SQLite has none
    db_pool_size: int = Field(default=5, ge=1, le=500)
    db_max_overflow: int = Field(default=10, ge=0, le=500)
    db_pool_timeout: int = Field(default=30, ge=1)
    db_pool_recycle: int = Field(default=1800, ge=-1)
    global_noauth: bool = False
    swagger_noauth: bool = False
    # serve the Swagger UI (/docs, /docs/core) and the OpenAPI schemas (/api/v1/openapi*.json); off for a public API
    docs_enabled: bool = True
    expose_response_database: bool = False
    # false = JSON responses stay without the {success,message,data} envelope (the frontend must be
    # configured accordingly)
    envelope_enabled: bool = True
    wrap_auth_token_response: bool = False
    activity_log_enabled: bool = False
    activity_log_exclude_paths: str = "refresh-token"
    # days `pachifast audit purge` keeps when it is run without --days; 0 = it needs --days
    activity_log_retention_days: int = Field(default=0, ge=0)
    events_broadcast_enabled: bool = False
    # still accept the access token in the query string (/ws?token=): it ends up in proxy and access logs; with False
    # only a single-use ticket gets in (POST /api/core/events/ticket, then /ws?ticket=)
    events_allow_query_token: bool = False
    # where the WebSocket tickets live: "database" (default, shared across workers and hosts) or "cache" (see cache_url)
    events_ticket_store: Literal["database", "cache"] = "database"
    # event types sent to ALL connected clients (default: none, only superusers and the user concerned);
    # e.g. "login" to show who has connected. Comma-separated list.
    events_public_types: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # cache with expiry (currently: per-user activity log state, tickets if events_ticket_store="cache"):
    # empty = in the process memory (limited to cache_max_entries items, not shared across workers);
    # redis://... / rediss://... = shared Redis (needs pachifast[redis]);
    # cache_prefix separates several apps on the same Redis
    cache_url: str = ""
    cache_prefix: str = "pachifast:"
    cache_max_entries: int = 10_000
    # language of the API error messages (see i18n/catalogs.py); use_accept_language lets the request choose the
    # language with the Accept-Language header (if supported), otherwise default_locale always applies
    default_locale: str = "en"
    use_accept_language: bool = False
    project_name: str = ""
    version: str = ""
    description: str = ""

    # routers/routes not to mount (comma-separated lists, see routing/core_filters.py):
    # disable_* applies to all modules (core, apps, common), disable_core_* only to the core
    disable_routers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    disable_routes: Annotated[list[str], NoDecode] = Field(default_factory=list)
    disable_core_routers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    disable_core_routes: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cache_url")
    @classmethod
    def _check_cache_url(cls, value: str) -> str:
        """Empty (in-process cache) or a Redis URL."""
        value = value.strip()
        if value and not value.startswith(("redis://", "rediss://", "unix://")):
            raise ValueError("cache_url must be empty or a redis://, rediss:// or unix:// URL")
        return value

    @field_validator(
        "disable_routers",
        "disable_routes",
        "disable_core_routers",
        "disable_core_routes",
        "events_public_types",
        mode="before",
    )
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Accept ``a,b`` from the environment/.env as well as a real list."""
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # auth: account security
    password_min_length: int = 12
    login_max_attempts: int = 5
    # requests per client address and window on login, registration and the MFA check; 0 = no limit
    auth_rate_limit: int = Field(default=0, ge=0, le=10_000)
    auth_rate_window_seconds: int = Field(default=60, ge=1)
    login_lockout_minutes: int = 15

    # table names: common prefix + single overrides (empty string = "prefix + base name")
    table_prefix: str = "pachifast_"
    user_table: str = ""
    user_settings_table: str = ""
    permission_table: str = ""
    group_table: str = ""
    group_permission_table: str = ""
    user_group_table: str = ""
    user_permission_table: str = ""
    activity_log_table: str = ""

    # mfa
    MFA_ENABLED: bool = False
    # key of the columns encrypted at rest (the TOTP secrets); empty: derived from SECRET_KEY, so a rotation of the
    # SECRET_KEY then makes them unreadable: set it once and keep it
    MFA_ENCRYPTION_KEY: str = ""
    MFA_TOTP_ISSUER: str = ""
    MFA_OTP_EXPIRE_MINUTES: int = 10
    MFA_MAX_ATTEMPTS: int = 5
    # seconds a user waits between two codes sent by /mfa/email/setup and /mfa/sms/setup; 0 = no wait
    mfa_send_cooldown_seconds: int = Field(default=30, ge=0, le=3600)
    mfa_config_table: str = ""
    mfa_pending_table: str = ""

    @field_validator("log_level")
    @classmethod
    def _known_log_level(cls, value: str) -> str:
        """Empty (automatic) or the name of a standard logging level."""
        level = value.strip().upper()
        if level and level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError(f"log_level {value!r} is not valid (CRITICAL, ERROR, WARNING, INFO or DEBUG).")
        return level

    @field_validator("default_locale")
    @classmethod
    def _known_locale(cls, value: str) -> str:
        """The default language must be a language tag; with no catalog for it the messages are in English."""
        from pachifast.i18n.loader import check_locale_tag

        locale = value.strip()
        try:
            check_locale_tag(locale)
        except ValueError:
            raise ValueError(f"default_locale {value!r} is not a language tag (examples: it, en, pt-BR).") from None
        return locale

    @model_validator(mode="after")
    def _require_secret_key(self) -> "PachifastSettings":
        """Enforce an explicit SECRET_KEY (no insecure default value)."""
        if not self.SECRET_KEY:
            raise ValueError(
                "SECRET_KEY is not set: set the PACHIFAST_SECRET_KEY environment variable "
                "(or put it in the .env file) to a random string of at least 32 characters, "
                'for example: python -c "import secrets; print(secrets.token_hex(32))"'
            )
        if len(self.SECRET_KEY) < 32:
            raise ValueError("SECRET_KEY is too short: at least 32 characters are required.")
        return self

    def get_table_name(self, base_name: str) -> str:
        """Effective name of a table: the ``<base>_table`` override or ``table_prefix + base``.

        Args:
            base_name: base name (e.g. ``user``, ``user_settings``, ``mfa_config``, ``activity_log``).
        """
        override = {
            "user": self.user_table,
            "user_settings": self.user_settings_table,
            "permission": self.permission_table,
            "group": self.group_table,
            "group_permission": self.group_permission_table,
            "user_group": self.user_group_table,
            "user_permission": self.user_permission_table,
            "activity_log": self.activity_log_table,
            "mfa_config": self.mfa_config_table,
            "mfa_pending": self.mfa_pending_table,
        }.get(base_name, "")
        return override or f"{self.table_prefix}{base_name}"


_settings_factory: Callable[[], PachifastSettings] | None = None


def configure_settings(factory: Callable[[], PachifastSettings]) -> None:
    """Register an external factory for get_settings().

    Lets host projects provide their own Settings implementation
    (with a different prefix, extra fields, etc.) without modifying pachifast.
    Call before creating the WSGI/ASGI app.
    """
    global _settings_factory
    _settings_factory = factory
    get_settings.cache_clear()


@lru_cache
def get_settings() -> PachifastSettings:
    if _settings_factory is not None:
        return _settings_factory()
    settings = PachifastSettings()
    apply_module_settings(settings)
    log.debug("Settings loaded")
    return settings


SettingsDep = Annotated[PachifastSettings, Depends(get_settings)]
