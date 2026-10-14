"""Startup hook of the model admin: make sure its permissions exist so they can be given to groups."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pachifast.auth.models import Permission
from pachifast.database.engine import get_auth_database_url, get_engine_from_url

from .registry import ACTIONS, all_admins, discover_admin_modules, permission_codename

log = logging.getLogger(__name__)


async def sync_admin_permissions() -> None:
    """Create the missing ``<app>.<model>.<view|add|change|delete>`` permissions of every registered model.

    It only adds: a permission that exists is left as it is (description included) and none is ever removed.
    Without an authentication database, or before its tables exist, it logs and returns.
    """
    discover_admin_modules()
    wanted = {
        permission_codename(a, action): f"{action} {a.title or a.name}" for a in all_admins() for action in ACTIONS
    }
    if not wanted:
        return
    url = get_auth_database_url(required=False)
    if not url:
        log.warning("Model admin: AUTH_DATABASE_URL is not configured, permissions not created")
        return
    try:
        async with async_sessionmaker(
            get_engine_from_url(url), class_=AsyncSession, expire_on_commit=False
        )() as session:
            existing = set((await session.execute(select(Permission.codename))).scalars())
            missing = sorted(set(wanted) - existing)
            session.add_all(Permission(codename=code, description=wanted[code]) for code in missing)
            await session.commit()
    except Exception:
        log.exception("Model admin: could not create the permissions (are the auth tables migrated?)")
        return
    if missing:
        log.info("Model admin: created %d permission(s)", len(missing))


async def create_admin_tables() -> None:
    """Create the history table if it does not exist (a migration of the ``admin`` target does the same)."""
    url = get_auth_database_url(required=False)
    if not url:
        log.warning("Model admin: AUTH_DATABASE_URL is not configured, history table not created")
        return
    from .models import AdminChange

    async with get_engine_from_url(url).begin() as conn:
        await conn.run_sync(lambda sync_conn: AdminChange.__table__.create(sync_conn, checkfirst=True))  # type: ignore[attr-defined]
