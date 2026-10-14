"""Utility condivise per i modelli: UUID, datetime UTC e colonne JSON portabili."""

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
    """Normalizza un datetime a UTC timezone-aware (i valori naive sono considerati UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def new_uuid() -> uuid.UUID:
    """Genera un nuovo UUID, v7 (ordinabile nel tempo) se disponibile, altrimenti v4."""
    generator = getattr(uuid, "uuid7", None)
    return generator() if generator is not None else uuid.uuid4()


class UtcDateTime(TypeDecorator):
    """DateTime che salva e restituisce sempre valori timezone-aware in UTC.

    SQLite (e alcuni driver) restituiscono datetime naive anche con ``timezone=True``:
    questo tipo li normalizza sempre a UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        """Converte il valore in UTC prima di scriverlo sul database."""
        return as_utc(value)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        """Restituisce il valore letto dal database come UTC timezone-aware."""
        return as_utc(value)
