"""History of the changes made through the model admin."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .models import AdminChange


def diff(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """``{column: {"old", "new"}}`` for the columns that differ (``None`` stands for a row that does not exist)."""
    before, after = before or {}, after or {}
    return {
        key: {"old": before.get(key), "new": after.get(key)}
        for key in before.keys() | after.keys()
        if before.get(key) != after.get(key)
    }


def record(
    session: AsyncSession,
    *,
    app: str,
    model: str,
    pk: Any,
    action: str,
    user: Any,
    changes: dict[str, Any] | None = None,
) -> None:
    """Add a history row to the session; it is saved by the commit of the change itself (all or nothing)."""
    session.add(
        AdminChange(
            app=app,
            model=model,
            pk=str(pk),
            action=action,
            user_id=getattr(user, "id", None),
            username=getattr(user, "username", None),
            changes=changes or None,
        )
    )
