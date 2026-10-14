"""Where the permissions of a user come from: the database tables of the core, unless a project says otherwise.

A project that keeps permissions elsewhere (an identity provider, a claim of its own, a rule engine) registers a
provider once at startup::

    from pachifast.auth.permission_provider import set_permission_provider

    async def from_claims(session, user_id):
        return {"reports.view"}

    set_permission_provider(from_claims)

The provider receives the session and the id of the user and returns the codenames the user holds. It replaces the
lookup that the permission checks of the routes (``permissions=[...]``), the model admin and ``GET /auth/user`` make.
Superusers still bypass the checks, and the admin screens that edit the groups and permissions of the database keep
showing the database.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

PermissionProvider = Callable[[AsyncSession, uuid.UUID], Awaitable[Iterable[str]]]

_provider: PermissionProvider | None = None


def set_permission_provider(provider: PermissionProvider | None) -> None:
    """Use ``provider`` to look up the permissions of a user; ``None`` goes back to the database."""
    global _provider
    _provider = provider


async def resolve_user_permissions(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """The codenames the user holds, from the provider if one is set, else from the groups and permissions in the DB."""
    if _provider is None:
        from .cruds import get_user_permissions

        return await get_user_permissions(session, user_id)
    return sorted(set(await _provider(session, user_id)))
