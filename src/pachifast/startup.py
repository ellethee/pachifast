"""Startup hooks of the core module."""

import logging

from pachifast.audit import models as audit_models
from pachifast.auth import models as auth_models
from pachifast.config import get_settings
from pachifast.database.base import Base
from pachifast.database.engine import get_auth_database_url, get_engine_from_url
from pachifast.events import models as events_models
from pachifast.mfa import models as mfa_models

log = logging.getLogger(__name__)

#: The modules whose models are the tables of the core (the ``auth`` migration target lists the same ones).
CORE_MODEL_MODULES = frozenset(module.__name__ for module in (auth_models, mfa_models, audit_models, events_models))


def core_tables() -> list:
    """Every table declared in the core model modules (a new core model is picked up without listing it)."""
    return [mapper.local_table for mapper in Base.registry.mappers if mapper.class_.__module__ in CORE_MODEL_MODULES]


async def init_db(*, extra_tables: list | None = None) -> None:
    """Create the auth/permission tables on the configured database, if available.

    extra_tables: tabelle SQLAlchemy aggiuntive da creare (es. da scan_models()).
    """
    settings = get_settings()
    if not settings.auto_create_auth_schema:
        log.info("Skipping auth schema auto-create: auto_create_auth_schema is disabled")
        return

    database_url = get_auth_database_url(required=False)
    if not database_url:
        log.warning("Skipping auth database initialization: AUTH_DATABASE_URL is not configured")
        return

    all_tables = core_tables() + (extra_tables or [])
    async with get_engine_from_url(database_url).begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=all_tables)
