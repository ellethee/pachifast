"""Router ``/infos``: read-only information about the running service (superusers only)."""

from pachifast.infos.engine.engine_helper import list_engines
from pachifast.infos.engine.schemas import AdmInfoEngineInfo
from pachifast.routing.router_factory import get_router

router = get_router(name="infos", require_superuser=True)

# Hosts whose engines are reported with scope "local"; any other host is "development".
LOCAL_HOSTS: dict[str, list[str]] = {"local": ["localhost", "127.0.0.1", "::1", "internal"]}


@router.get("/engines", response_model=list[AdmInfoEngineInfo], summary="List database engines")
async def get_engines() -> list[AdmInfoEngineInfo]:
    """List the database engines currently in use (type, host, database, scope)."""
    return await list_engines(LOCAL_HOSTS)
