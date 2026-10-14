"""Routes :mod:`admin.user`: user management (superusers only)."""

import uuid
from typing import Annotated

from fastapi import HTTPException, Query, Response, status

from pachifast.admin.schemas import UserPaginted
from pachifast.auth import cruds
from pachifast.auth.dependencies import CurrentUserDep
from pachifast.auth.models import User
from pachifast.auth.schemas import UserRead, UserUpdate
from pachifast.auth.security import refusal_status
from pachifast.database.pagination import bound_page_params, paginate
from pachifast.dependencies import CoreSessionDep
from pachifast.events.broadcaster import PRIVILEGES_CHANGED_CLOSE_CODE, UNAUTHORIZED_CLOSE_CODE, get_broadcaster
from pachifast.i18n import api_error, detail_from_error
from pachifast.routing.router_factory import get_router
from pachifast.schemas.responses import PaginatedResponse

# disable_router = True
router = get_router(require_superuser=True)


def _not_found() -> HTTPException:
    """Uniform 404 error for a nonexistent user."""
    return api_error(status.HTTP_404_NOT_FOUND, "user.not_found", loc=("path", "user_id"))


@router.get("", response_model=PaginatedResponse[UserRead], summary="List users")
async def list_users(
    session: CoreSessionDep,
    user: Annotated[UserRead, CurrentUserDep],
    paginateParams: Annotated[UserPaginted, Query()],
):
    """Paginated list of users (default: most recent first)."""
    if not paginateParams.orderBy:
        paginateParams.orderBy = "-created_at"
    return await paginate(
        session,
        User,
        bound_page_params(paginateParams),
    )  # type: ignore


@router.get("/{user_id}", response_model=UserRead, summary="Get user details")
async def get_user(
    user_id: uuid.UUID,
    session: CoreSessionDep,
    user: Annotated[UserRead, CurrentUserDep],
):
    """User detail."""
    found = await cruds.get_user_by_id(session, user_id)
    if found is None:
        raise _not_found()
    return found


@router.delete("/{user_id}", status_code=204, skip_envelope=True, summary="Delete a user")
async def delete_user(
    user_id: uuid.UUID,
    session: CoreSessionDep,
    user: Annotated[UserRead, CurrentUserDep],
):
    """Delete a user. A superuser cannot delete themselves."""
    if user_id == user.id:
        raise api_error(status.HTTP_400_BAD_REQUEST, "user.cannot_delete_self", loc=("path", "user_id"))
    if not await cruds.delete_user(session, user_id):
        raise _not_found()
    await get_broadcaster().close_user(user_id, UNAUTHORIZED_CLOSE_CODE, "User deleted")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{user_id}", response_model=UserRead, summary="Update a user")
async def update_user(
    user_id: uuid.UUID,
    data: UserUpdate,
    session: CoreSessionDep,
    user: Annotated[UserRead, CurrentUserDep],
):
    """Update the given fields of a user (name, email, password, flags)."""
    target = await cruds.get_user_by_id(session, user_id)
    if target is None:
        raise _not_found()
    if user_id == user.id and (data.is_active is False or data.is_superuser is False):
        raise api_error(status.HTTP_400_BAD_REQUEST, "user.cannot_demote_self", loc=("body",))
    was_superuser = bool(target.is_superuser)
    try:
        updated = await cruds.update_user(
            session,
            target,
            email=data.email,
            first_name=data.first_name,
            last_name=data.last_name,
            is_active=data.is_active,
            is_staff=data.is_staff,
            is_superuser=data.is_superuser,
            password=data.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=refusal_status(exc), detail=detail_from_error(exc, loc=("body",))) from None
    await _close_stale_sockets(updated, was_superuser, password_changed=data.password is not None)
    return updated


async def _close_stale_sockets(user: User, was_superuser: bool, *, password_changed: bool) -> None:
    """Close the event WebSockets of a user that was deactivated, changed password or changed superuser status.

    The first two cases invalidate the connection (4401: the client must log in again); a change of superuser
    status closes it with 4403 so that the client reconnects and gets the events of its new role.
    """
    broadcaster = get_broadcaster()
    if not user.is_active or password_changed:
        await broadcaster.close_user(user.id, UNAUTHORIZED_CLOSE_CODE, "User disabled or password changed")
    elif bool(user.is_superuser) != was_superuser:
        await broadcaster.close_user(user.id, PRIVILEGES_CHANGED_CLOSE_CODE, "Privileges changed")
