"""Shared model utilities: UUIDs, UTC datetimes and portable JSON columns."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

#: Colonna JSON portabile: JSONB su PostgreSQL, JSON generico altrove (es. SQLite).
JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    """Ritorna l'istante corrente come datetime timezone-aware in UTC."""
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """Normalize a datetime to timezone-aware UTC (naive values are treated as UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def new_uuid() -> uuid.UUID:
    """Generate a new UUID: v7 (time-sortable) if available, otherwise v4."""
    generator = getattr(uuid, "uuid7", None)
    return generator() if generator is not None else uuid.uuid4()


class UtcDateTime(TypeDecorator):
    """DateTime that always stores and returns timezone-aware UTC values.

    SQLite (and some drivers) return naive datetimes even with ``timezone=True``:
    this type always normalizes them to UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        """Convert the value to UTC before writing it to the database."""
        return as_utc(value)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        """Return the value read from the database as timezone-aware UTC."""
        return as_utc(value)
