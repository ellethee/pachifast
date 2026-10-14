"""Synchronous SQLAlchemy engines and sessions, for code that does not run in an event loop.

The core always uses async sessions. These helpers are for application code that prefers (or must
use) a plain ``Session``: scripts, background jobs, endpoints declared with ``def``. They take the same
URLs as the async helpers and swap the async driver for its synchronous counterpart.
"""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from pachifast.config import get_settings

from .engine import add_request_database, pool_args

#: Async driver -> the synchronous driver used for the same database.
SYNC_DRIVERS: dict[str, str] = {
    "sqlite+aiosqlite": "sqlite",
    "postgresql+asyncpg": "postgresql+psycopg",
}

_engine_cache: dict[str, Engine] = {}
_sessionmaker_cache: dict[str, "sessionmaker[Session]"] = {}


def to_sync_url(database_url: str) -> str:
    """Return the URL with the synchronous driver; URLs that are already synchronous are unchanged.

    ``postgresql+psycopg`` needs ``pip install "psycopg[binary]"`` (extra ``pachifast[postgres-sync]``).
    """
    url = make_url(database_url)
    sync_drivername = SYNC_DRIVERS.get(url.drivername)
    if sync_drivername is None:
        return database_url
    return url.set(drivername=sync_drivername).render_as_string(hide_password=False)


def get_sync_engine_from_url(database_url: str, echo: bool = False, connect_args: dict | None = None) -> Engine:
    """Create or reuse a synchronous engine for ``database_url`` (an async URL is converted)."""
    sync_url = to_sync_url(database_url)
    if sync_url not in _engine_cache:
        kwargs: dict = {"echo": echo or get_settings().dbdebug}
        if not sync_url.startswith("sqlite"):
            kwargs.update(pool_args())
        if connect_args is not None:
            kwargs["connect_args"] = connect_args
        _engine_cache[sync_url] = create_engine(sync_url, **kwargs)
    return _engine_cache[sync_url]


def get_sync_sessionmaker_from_url(
    database_url: str, echo: bool = False, connect_args: dict | None = None
) -> "sessionmaker[Session]":
    """Create or reuse a ``sessionmaker`` bound to :func:`get_sync_engine_from_url`."""
    sync_url = to_sync_url(database_url)
    if sync_url not in _sessionmaker_cache:
        engine = get_sync_engine_from_url(database_url, echo=echo, connect_args=connect_args)
        _sessionmaker_cache[sync_url] = sessionmaker(bind=engine, expire_on_commit=False)
    return _sessionmaker_cache[sync_url]


def sync_session_dependency_from_url(
    database_url: str, echo: bool = False, connect_args: dict | None = None
) -> Annotated[Session, Depends]:
    """Build a FastAPI dependency that yields a synchronous ``Session`` for ``database_url``."""

    def _get_session(request: Request) -> Generator[Session, None, None]:
        with get_sync_sessionmaker_from_url(database_url, echo, connect_args)() as session:
            add_request_database(request, session)  # type: ignore[arg-type]
            yield session

    return Depends(_get_session)
