import re
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote_plus

from pachifast.database.engine import _engine_cache, _sessionmaker_cache

from .schemas import AdmInfoEngineInfo

is_postgresql = re.compile(
    r"^postgresql.+://(?P<user>\w+):(?P<password>\w+)@(?P<host>.+):(?P<port>\d+)/(?P<dbname>[-\w]+).*$",
    re.MULTILINE | re.UNICODE,
)


def determine_environment_type(db_types: dict[str, list[str] | Callable], host: str) -> str:
    for db_type, identifier in db_types.items():
        if callable(identifier):
            return identifier(host)
        elif isinstance(identifier, list):
            if any(item in host for item in identifier):
                return db_type
        elif identifier in host:
            return db_type
    return "development"


def get_items_info(items: dict[str, Any], db_types: dict[str, list[str] | Callable]) -> list[Any]:
    info_list: list[Any] = []
    for key in items:
        matched = is_postgresql.match(unquote_plus(key))
        if matched:
            user, password, host, port, dbname = matched.groups()
            info_list.append(
                AdmInfoEngineInfo(
                    server_type="PostgreSQL",
                    scope=determine_environment_type(db_types, host),
                    host=host,
                    dbname=dbname,
                    port=int(port),
                )
            )
        elif key.startswith("sqlite"):
            info_list.append(AdmInfoEngineInfo(server_type="SQLite", scope="local"))
        else:
            info_list.append(AdmInfoEngineInfo(server_type="Unknown", scope="local"))
    return info_list


async def list_engines(db_types: dict[str, list[str] | Callable]) -> list[AdmInfoEngineInfo]:
    engines: list[AdmInfoEngineInfo] = get_items_info(_engine_cache, db_types)
    return engines


async def list_sesions(db_types: dict[str, list[str] | Callable]) -> list[AdmInfoEngineInfo]:

    sessions: list[AdmInfoEngineInfo] = get_items_info(_sessionmaker_cache, db_types)
    return sessions
