"""Routes :mod:`admin.permissions`."""

import uuid

from fastapi import Request, Response, status
from sqlalchemy import select

from pachifast.auth import cruds
from pachifast.auth.labels import group_view, permission_view
from pachifast.auth.models import UserGroup
from pachifast.auth.schemas import (
    GroupWrite,
    PermissionWrite,
    UserGroupsWrite,
    UserPermissionsWrite,
)
from pachifast.dependencies import CoreSessionDep
from pachifast.events.broadcaster import broadcast_event
from pachifast.i18n import api_error
from pachifast.routing.router_factory import get_router

# disable_router = True
router = get_router(require_superuser=True)

EXCLUDE_ROUTES = [
    "/api/v1/openapi.json",
    "/api/auth",
    "/api/admin",
    "/api/version",
    "/api/health",
]


# --- Applications discovery (esistente) ---


@router.get("/applications", summary="Get all application URLs")
def get_all_urls_from_request(request: Request):
    """List the documented ``/api`` operations (from the OpenAPI schema, so lazily included routers work too)."""
    url_list = [
        {
            "path": path,
            "name": op.get("operationId"),
            "methods": [method.upper()],
            "summary": op.get("summary") or op.get("description") or op.get("operationId"),
            "tags": op.get("tags"),
        }
        for path, operations in request.app.openapi().get("paths", {}).items()
        if path.startswith("/api") and not any(path.startswith(exc) for exc in EXCLUDE_ROUTES)
        for method, op in operations.items()
        if method in {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
    ]
    url_list.sort(key=lambda x: (x["path"], x["methods"]))
    return url_list


# --- Permission CRUD ---


@router.get("/permissions", summary="List all permissions")
async def list_permissions(session: CoreSessionDep):
    return [permission_view(p) for p in await cruds.list_permissions(session)]


@router.post("/permissions", summary="Create permission")
async def create_permission(data: PermissionWrite, session: CoreSessionDep):
    return permission_view(await cruds.create_permission(session, data.codename, data.description))


@router.put("/permissions/{perm_id}", summary="Update permission")
async def update_permission(perm_id: int, data: PermissionWrite, session: CoreSessionDep):
    result = await cruds.update_permission(session, perm_id, data.codename, data.description)
    if not result:
        raise api_error(404, "permission.not_found", loc=("path", "perm_id"))
    return permission_view(result)


@router.delete("/permissions/{perm_id}", status_code=204, skip_envelope=True, summary="Delete permission")
async def delete_permission(perm_id: int, session: CoreSessionDep):
    ok = await cruds.delete_permission(session, perm_id)
    if not ok:
        raise api_error(404, "permission.not_found", loc=("path", "perm_id"))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Group CRUD ---


@router.get("/groups", summary="List all groups with permissions")
async def list_groups(session: CoreSessionDep):
    return [group_view(g) for g in await cruds.list_groups(session)]


@router.post("/groups", summary="Create group")
async def create_group(data: GroupWrite, session: CoreSessionDep):
    return group_view(await cruds.create_group(session, data.name, data.description, data.permission_ids))


@router.put("/groups/{group_id}", summary="Update group")
async def update_group(group_id: int, data: GroupWrite, session: CoreSessionDep):
    affected = (await session.execute(select(UserGroup.user_id).where(UserGroup.group_id == group_id))).scalars().all()
    result = await cruds.update_group(session, group_id, data.name, data.description, data.permission_ids)
    if not result:
        raise api_error(404, "group.not_found", loc=("path", "group_id"))
    for uid in affected:
        await broadcast_event("user_permissions_changed", {"userId": str(uid)}, to_users=[uid])
    return group_view(result)


@router.delete("/groups/{group_id}", status_code=204, skip_envelope=True, summary="Delete group")
async def delete_group(group_id: int, session: CoreSessionDep):
    affected = (await session.execute(select(UserGroup.user_id).where(UserGroup.group_id == group_id))).scalars().all()
    ok = await cruds.delete_group(session, group_id)
    if not ok:
        raise api_error(404, "group.not_found", loc=("path", "group_id"))
    for uid in affected:
        await broadcast_event("user_permissions_changed", {"userId": str(uid)}, to_users=[uid])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- User↔Group ---


@router.get("/users/{user_id}/groups", summary="Get user groups")
async def get_user_groups(user_id: uuid.UUID, session: CoreSessionDep):
    return [group_view(g) for g in await cruds.get_user_groups(session, user_id)]


@router.put("/users/{user_id}/groups", summary="Set user groups")
async def set_user_groups(user_id: uuid.UUID, data: UserGroupsWrite, session: CoreSessionDep):
    result = await cruds.set_user_groups(session, user_id, data.group_ids)
    await broadcast_event("user_permissions_changed", {"userId": str(user_id)}, to_users=[user_id])
    return [group_view(g) for g in result]


# --- User↔Permission (diretti) ---


@router.get("/users/{user_id}/permissions", summary="Get user direct permissions")
async def get_user_direct_permissions(user_id: uuid.UUID, session: CoreSessionDep):
    return [permission_view(p) for p in await cruds.get_user_direct_permissions(session, user_id)]


@router.put("/users/{user_id}/permissions", summary="Set user direct permissions")
async def set_user_direct_permissions(user_id: uuid.UUID, data: UserPermissionsWrite, session: CoreSessionDep):
    result = await cruds.set_user_direct_permissions(session, user_id, data.permission_ids)
    await broadcast_event("user_permissions_changed", {"userId": str(user_id)}, to_users=[user_id])
    return [permission_view(p) for p in result]


# --- Effective permissions ---


@router.get("/users/{user_id}/effective", summary="Get user effective permissions (groups + direct)")
async def get_user_effective_permissions(user_id: uuid.UUID, session: CoreSessionDep):
    perms = await cruds.get_user_permissions(session, user_id)
    return {"permissions": perms}
