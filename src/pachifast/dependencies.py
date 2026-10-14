"""Dependencies :mod:`core.dependencies`."""

from pachifast.auth.dependencies import (
    ActiveUserDep,
    ActiveWsUserDep,
    CurrentUserDep,
    CurrentWsUserDep,
    TokenDep,
    get_current_active_user,
    get_current_active_ws_user,
    get_current_user,
    get_current_ws_user,
    oauth2_scheme,
)
from pachifast.config import SettingsDep
from pachifast.database.engine import CoreSessionDep

__all__ = [
    "ActiveUserDep",
    "ActiveWsUserDep",
    "CoreSessionDep",
    "CurrentUserDep",
    "CurrentWsUserDep",
    "SettingsDep",
    "TokenDep",
    "get_current_active_user",
    "get_current_active_ws_user",
    "get_current_user",
    "get_current_ws_user",
    "oauth2_scheme",
]
