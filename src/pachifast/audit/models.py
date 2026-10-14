"""Modello del log delle attivita'."""

import uuid
from datetime import datetime

from sqlalchemy import Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pachifast.config import get_settings
from pachifast.database.base import Base
from pachifast.database.utils import UtcDateTime, utcnow

ACTIVITY_LOG_TABLE = get_settings().get_table_name("activity_log")


class ActivityLog(Base):
    __tablename__ = ACTIVITY_LOG_TABLE
    __table_args__ = (
        # the list of a user's activity, newest first; the single-column indexes below serve the other filters
        Index(f"ix_{ACTIVITY_LOG_TABLE}_user_id_timestamp", "user_id", "timestamp"),
        {"extend_existing": True},
    )

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime, index=True, default_factory=utcnow)
    # Nessuna FK: il log sopravvive alla cancellazione dell'utente (username denormalizzato).
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True, default=None)
    username: Mapped[str | None] = mapped_column(String(150), default=None)
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(500))
    status_code: Mapped[int]
    duration_ms: Mapped[int | None] = mapped_column(default=None)
    description: Mapped[str | None] = mapped_column(default=None)
