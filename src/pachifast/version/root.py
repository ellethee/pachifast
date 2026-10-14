from importlib.metadata import version as installed_version

from pachifast.routing.router_factory import get_router

router = get_router(deps=["noauth"])


@router.get("/", status_code=200)
async def version():
    """
    Version of the installed pachifast package.
    """
    return {"version": installed_version("pachifast")}
