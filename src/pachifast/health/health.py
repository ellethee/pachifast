import asyncio
import logging

from fastapi import Request, status
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


def _health(request: Request, settings: SettingsDep, database: bool) -> HealthCheck:
    """The health payload for a database state.

    Name, version and description come from the settings; when they are empty, the application's own title and
    version (the ones shown in the OpenAPI document) are reported instead.
    """
    app = request.app
    return HealthCheck(
        name=settings.project_name or app.title,
        version=settings.version or app.version,
        description=settings.description or app.description,
        database=database,
    )


@router.get("/", response_model=HealthCheck)
async def health_check(request: Request, settings: SettingsDep):
    """Name, version and whether the database answers; always ``200``, the flag says the rest."""
    return _health(request, settings, await _database_reachable())


@router.get("/live", response_model=LiveStatus)
async def liveness() -> LiveStatus:
    """Liveness probe: ``200`` while the process serves requests; it does not touch the database."""
    return LiveStatus(status="ok")


@router.get("/ready", response_model=HealthCheck)
async def readiness(request: Request, settings: SettingsDep):
    """Readiness probe: ``200`` when the database answers, ``503 health.not_ready`` otherwise."""
    if not await _database_reachable():
        raise api_error(status.HTTP_503_SERVICE_UNAVAILABLE, "health.not_ready", loc=("server",))
    return _health(request, settings, True)
