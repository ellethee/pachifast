import asyncio
import logging

from fastapi import status
from sqlalchemy import text

from pachifast.database.engine import get_auth_database_url, get_sessionmaker_from_url
from pachifast.dependencies import SettingsDep
from pachifast.i18n import api_error
from pachifast.routing.router_factory import get_router

from .schemas import HealthCheck, LiveStatus

log = logging.getLogger(__name__)

router = get_router(1, deps=["noauth"])

#: seconds the readiness probe waits for the database before it answers "not ready"
DATABASE_TIMEOUT = 3.0


async def _database_reachable() -> bool:
    """``True`` when the auth database answers ``SELECT 1`` within :data:`DATABASE_TIMEOUT`."""
    database_url = get_auth_database_url(required=False)
    if not database_url:
        return False
    try:
        sessionmaker = get_sessionmaker_from_url(database_url)
        async with asyncio.timeout(DATABASE_TIMEOUT):
            async with sessionmaker() as session:
                await session.execute(text("SELECT 1"))
        return True
    except Exception:
        log.warning("Health check: the database cannot be reached", exc_info=True)
        return False


def _health(settings: SettingsDep, database: bool) -> HealthCheck:
    """The health payload for a database state."""
    return HealthCheck(
        name=settings.project_name,
        version=settings.version,
        description=settings.description,
        database=database,
    )


@router.get("/", response_model=HealthCheck)
async def health_check(settings: SettingsDep):
    """Name, version and whether the database answers; always ``200``, the flag says the rest."""
    return _health(settings, await _database_reachable())


@router.get("/live", response_model=LiveStatus)
async def liveness() -> LiveStatus:
    """Liveness probe: ``200`` while the process serves requests; it does not touch the database."""
    return LiveStatus(status="ok")


@router.get("/ready", response_model=HealthCheck)
async def readiness(settings: SettingsDep):
    """Readiness probe: ``200`` when the database answers, ``503 health.not_ready`` otherwise."""
    if not await _database_reachable():
        raise api_error(status.HTTP_503_SERVICE_UNAVAILABLE, "health.not_ready", loc=("server",))
    return _health(settings, True)
