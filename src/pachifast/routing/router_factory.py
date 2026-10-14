import inspect
import logging

from fastapi import Depends

from pachifast import dependencies
from pachifast.auth.dependencies import get_current_active_user, make_permission_checker
from pachifast.schemas.responses import StandardResponse

from .auto_response_router import AutoResponseRouter
from .permissioned_router import PermissionedRouter

log = logging.getLogger(__name__)


def get_router(
    parts: int = 2,
    deps: list[str] | None = None,
    name: str | None = None,
    response_model=StandardResponse,
    api_route=PermissionedRouter,
    permissions: list[str] | None = None,
    require_staff: bool = False,
    require_superuser: bool = False,
    **kwargs,
) -> PermissionedRouter:
    """Get router."""
    frm = inspect.stack()[1]
    mod = inspect.getmodule(frm[0])
    if mod is None:
        raise ValueError("Module not found")

    # Ricava il nome base del modulo e rimuovi "main" o "__init__" se presenti
    full_path = mod.__name__.split(".")
    clean_parts = [p for p in full_path[-parts:] if p not in ("root", "main", "__init__")]

    name = name or "/".join(clean_parts)
    deps = list(deps or [])

    from pachifast.config import get_settings

    if get_settings().global_noauth and "noauth" not in deps:
        log.warning("Router %s: global_noauth enabled, skipping authentication", name)
        deps.append("noauth")

    # Costruisce le dipendenze
    local_deps = []
    auth_deps: list = []
    for dep in deps:
        if isinstance(dep, str):
            if hasattr(dependencies, dep):
                local_deps.append(getattr(dependencies, dep))
        else:
            local_deps.append(dep)

    if "nouser" not in deps and "noauth" not in deps:
        auth_dep = Depends(get_current_active_user)
        local_deps.append(auth_dep)
        auth_deps.append(auth_dep)

    if permissions or require_staff or require_superuser:
        checker = make_permission_checker(permissions, require_staff, require_superuser)
        local_deps.append(Depends(checker))

    params = {
        "prefix": f"/{name}",
        "tags": name.split("/"),
        "dependencies": local_deps,
        "responses": {404: {"description": "Not found"}},
        # "default_response_model": response_model,
        # "default_response_class": response_class,
    }

    if api_route is AutoResponseRouter:
        params.update({"default_response_model": response_model})

    params.update(kwargs)
    router = api_route(**params)
    if isinstance(router, PermissionedRouter):
        router._auth_deps = auth_deps
    return router
