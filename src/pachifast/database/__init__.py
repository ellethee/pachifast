"""Database management modules."""

from .aliased_models import AliasedBaseModel
from .base import Base
from .engine import (
    CoreSessionDep,
    CoreSessionLocal,
    SessionDep,
    add_request_database,
    get_auth_database_url,
    get_core_engine,
    get_core_session,
    get_core_sessionmaker,
    get_engine_from_url,
    get_session_database_label,
    get_sessionmaker_from_url,
    session_dependency_from_url,
    set_core_request_database,
)
from .model_discovery import scan_models
from .sync import (
    get_sync_engine_from_url,
    get_sync_sessionmaker_from_url,
    sync_session_dependency_from_url,
    to_sync_url,
)
from .utils import JsonType, UtcDateTime, as_utc, new_uuid, utcnow

__all__ = [
    "AliasedBaseModel",
    "Base",
    "CoreSessionDep",
    "CoreSessionLocal",
    "JsonType",
    "SessionDep",
    "UtcDateTime",
    "add_request_database",
    "as_utc",
    "get_auth_database_url",
    "get_core_engine",
    "get_core_session",
    "get_core_sessionmaker",
    "get_engine_from_url",
    "get_session_database_label",
    "get_sessionmaker_from_url",
    "get_sync_engine_from_url",
    "get_sync_sessionmaker_from_url",
    "new_uuid",
    "scan_models",
    "session_dependency_from_url",
    "set_core_request_database",
    "sync_session_dependency_from_url",
    "to_sync_url",
    "utcnow",
]
