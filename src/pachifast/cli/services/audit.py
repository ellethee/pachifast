"""Activity log maintenance for the CLI: delete the rows older than a number of days."""

import asyncio
from datetime import timedelta
from typing import Any

from pachifast.cli.services.users import _with_session, in_project


@in_project
def purge_activity_log(*, base_dir: str = ".", days: int | None = None, dry_run: bool = False) -> tuple[int, int]:
    """Delete the activity log rows older than ``days`` (default: ``activity_log_retention_days``).

    Returns:
        ``(days, rows)``: the retention used and the number of rows deleted (or that would be, with ``dry_run``).

    Raises:
        ValueError: neither ``days`` nor ``activity_log_retention_days`` says how many days to keep.
    """

    async def action(session: Any) -> tuple[int, int]:
        from sqlalchemy import delete, func, select

        from pachifast.audit.models import ActivityLog
        from pachifast.config import get_settings
        from pachifast.database.utils import utcnow

        keep = days if days is not None else get_settings().activity_log_retention_days
        if keep <= 0:
            raise ValueError("Say how many days to keep: --days N, or set activity_log_retention_days.")
        cutoff = utcnow() - timedelta(days=keep)
        if dry_run:
            count = (
                await session.execute(
                    select(func.count()).select_from(ActivityLog).where(ActivityLog.timestamp < cutoff)
                )
            ).scalar_one()
            return keep, int(count)
        result = await session.execute(delete(ActivityLog).where(ActivityLog.timestamp < cutoff))
        await session.commit()
        return keep, int(result.rowcount or 0)

    return asyncio.run(_with_session(base_dir, action))
