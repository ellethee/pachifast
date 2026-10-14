"""Table of the model admin: who changed which row, and how."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pachifast.config import get_settings
from pachifast.database.base import Base
from pachifast.database.utils import JsonType, UtcDateTime, utcnow

ADMIN_CHANGE_TABLE = get_settings().get_table_name("admin_change")


class AdminChange(Base):
    """One write made through the model admin: ``add``, ``change``, ``delete`` or ``action:<name>``.

    ``changes`` maps each column to ``{"old": ..., "new": ...}`` (JSON safe, camelCase keys like the API). There is no
    foreign key to the user (the history outlives the account, the username is copied) and none to the row.
    """

    __tablename__ = ADMIN_CHANGE_TABLE
    __table_args__ = (Index(f"ix_{ADMIN_CHANGE_TABLE}_row", "app", "model", "pk"),)

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    app: Mapped[str] = mapped_column(String(63))
    model: Mapped[str] = mapped_column(String(63))
    pk: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, default=None)
    username: Mapped[str | None] = mapped_column(String(150), default=None)
    changes: Mapped[dict[str, Any] | None] = mapped_column(JsonType, default=None)
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime, index=True, default_factory=utcnow)
