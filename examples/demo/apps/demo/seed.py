"""Startup hook: sample records and the demo permissions, only when they are missing."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from .data import sample_items
from .models import DemoItem

log = logging.getLogger(__name__)

DEMO_PERMISSIONS = {
    "demo.view": "See the demo records",
    "demo.edit": "Create and change the demo records",
    "demo.delete": "Delete the demo records",
    "demo.upload": "Upload and delete demo files",
}


async def seed() -> None:
    """Insert the sample records (if the table is empty) and the ``demo.*`` permissions (if missing).

    A missing table (migrations not applied yet) is not an error: the hook only logs it.
    """
    from pachifast.auth.models import Permission
    from pachifast.database.engine import get_core_sessionmaker

    try:
        async with get_core_sessionmaker()() as session:
            if (await session.execute(select(DemoItem.id).limit(1))).first() is None:
                session.add_all(DemoItem(**fields) for fields in sample_items())
            existing = set((await session.execute(select(Permission.codename))).scalars())
            for codename, description in DEMO_PERMISSIONS.items():
                if codename not in existing:
                    session.add(Permission(codename=codename, description=description))
            await session.commit()
    except SQLAlchemyError as exc:
        log.warning("demo seed skipped (%s): apply the migrations of the 'demo' target", str(exc).splitlines()[0])
