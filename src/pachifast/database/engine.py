"""Dbmanager :mod:`database.engine`."""

import logging
from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Request, status
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool
from starlette.requests import HTTPConnection

from pachifast.config import get_settings
from pachifast.i18n import api_error

_engine_cache: dict[str, AsyncEngine] = {}
_sessionmaker_cache: dict[str, async_sessionmaker[AsyncSession]] = {}
log = logging.getLogger(__name__)


def pool_args() -> dict:
    """Connection pool arguments of the engines of a server database (SQLite has no pool), from the ``db_pool_*``
    settings."""
    settings = get_settings()
    return dict(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
    )


def _is_sqlite_url(database_url: str) -> bool:
    return database_url.startswith("sqlite")


def get_session_database_label(session: AsyncSession) -> str | None:
    """Identificativo (host/database) del DB a cui è legata la sessione, senza credenziali."""
    try:
        url = session.get_bind().url
        if url.host:
            return f"{url.host}/{url.database}" if url.database else url.host
        return url.database or None
    except Exception:
        log.debug("Impossibile determinare il database della sessione", exc_info=True)
        return None


def add_request_database(request: Request, session: AsyncSession) -> None:
    """Aggiunge il database della sessione a request.state.database (lista, senza duplicati)."""
    label = get_session_database_label(session)
    if not label:
        return
    current: list[str] = getattr(request.state, "database", [])
    if label not in current:
        request.state.database = [*current, label]


def set_core_request_database(request: HTTPConnection, session: AsyncSession) -> None:
    """Registra il database auth/core separatamente da request.state.database.

    get_core_session gira sull'auth-check di quasi ogni richiesta autenticata: se lo
    aggiungessimo alla lista "business" (add_request_database) comparirebbe come rumore
    accanto al database realmente usato dall'endpoint. EnvelopeRoute lo usa solo come
    fallback quando request.state.database è vuota (endpoint che leggono/scrivono solo sul
    database core).
    """
    label = get_session_database_label(session)
    if label:
        request.state.core_database = label


class _CoreSessionLocalProxy:
    """Lazy async_sessionmaker proxy to avoid import-time engine creation."""

    def __call__(self, *args, **kwargs):
        return get_core_sessionmaker()(*args, **kwargs)

    def __getattr__(self, item: str):
        return getattr(get_core_sessionmaker(), item)


CoreSessionLocal = _CoreSessionLocalProxy()


def get_auth_database_url(required: bool = True) -> str | None:
    """Return the configured auth database URL from settings."""
    settings = get_settings()
    database_url = settings.AUTH_DATABASE_URL or ""
    if database_url:
        return database_url
    if required:
        raise RuntimeError(
            "Authentication database is not configured. Set AUTH_DATABASE_URL in the project settings or .env file."
        )
    return None


async def get_core_session(request: HTTPConnection) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session bound to the auth database (for HTTP requests and WebSocket connections)."""
    try:
        sessionmaker = get_core_sessionmaker()
    except RuntimeError as exc:
        log.error("%s", exc)  # the reason stays in the log, the client gets a fixed message
        raise api_error(status.HTTP_503_SERVICE_UNAVAILABLE, "db.not_configured") from exc

    async with sessionmaker() as session:
        set_core_request_database(request, session)
        yield session


CoreSessionDep = Annotated[AsyncSession, Depends(get_core_session)]


def get_core_engine() -> AsyncEngine:
    """Return the cached async engine for the auth database."""
    database_url = get_auth_database_url(required=True)
    assert database_url is not None
    return get_engine_from_url(database_url, echo=False)


def get_core_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the cached async sessionmaker for the auth database."""
    database_url = get_auth_database_url(required=True)
    assert database_url is not None
    return get_sessionmaker_from_url(database_url, echo=False)


def _sqlite_pragmas(dbapi_connection, _record) -> None:
    """Per connection settings of SQLite: it enforces foreign keys (``ON DELETE CASCADE``) only when asked, waits
    for a lock instead of failing at once with "database is locked", and a file keeps a write ahead log so readers
    do not block the writer."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")  # stays MEMORY (no change) for an in-memory database
    finally:
        cursor.close()


def get_engine_from_url(database_url: str, echo: bool | None = None, connect_args: dict | None = None) -> AsyncEngine:
    """Create or reuse an async SQLAlchemy engine for an arbitrary URL."""
    settings = get_settings()
    if database_url not in _engine_cache:
        echo = echo if settings.dbdebug else False
        if _is_sqlite_url(database_url):
            # SQLite non supporta il connection pool di SQLAlchemy.
            # In-memory (:memory:) richiede StaticPool per condividere la connessione;
            # i file SQLite usano NullPool per evitare conflitti di scrittura concorrente.
            is_memory = ":memory:" in database_url
            kwargs: dict = dict(
                echo=echo or settings.dbdebug,
                future=True,
                poolclass=StaticPool if is_memory else NullPool,
                connect_args=connect_args or {},
            )
        else:
            kwargs = dict(echo=echo or settings.dbdebug, future=True, **pool_args())
            if connect_args is not None:
                kwargs["connect_args"] = connect_args
        engine = create_async_engine(database_url, **kwargs)
        if _is_sqlite_url(database_url):
            event.listen(engine.sync_engine, "connect", _sqlite_pragmas)
        _engine_cache[database_url] = engine
    return _engine_cache[database_url]


def get_sessionmaker_from_url(
    database_url: str, echo: bool = False, connect_args: dict | None = None
) -> async_sessionmaker[AsyncSession]:
    """Create or reuse an async sessionmaker for an arbitrary database URL."""
    if database_url not in _sessionmaker_cache:
        engine = get_engine_from_url(database_url, echo=echo, connect_args=connect_args)
        _sessionmaker_cache[database_url] = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _sessionmaker_cache[database_url]


def session_dependency_from_url(
    database_url: str, echo: bool = False, connect_args: dict | None = None
) -> Annotated[AsyncSession, Depends]:
    """Build a FastAPI dependency that yields sessions for a specific URL."""

    async def _get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
        SessionLocal = get_sessionmaker_from_url(database_url, echo, connect_args=connect_args)
        async with SessionLocal() as session:
            add_request_database(request, session)
            yield session

    return Depends(_get_session)


SessionDep = Annotated[AsyncSession, Depends]
