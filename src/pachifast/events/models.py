"""Table of the one-time WebSocket tickets (``events_ticket_store="database"``)."""

import uuid
from datetime import datetime

from sqlalchemy import Float, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pachifast.config import get_settings
from pachifast.database.base import Base
from pachifast.database.utils import UtcDateTime

WS_TICKET_TABLE = get_settings().get_table_name("ws_ticket")


class WsTicket(Base):
    """A pending ticket. Only its SHA-256 is stored: a copy of the table cannot be used to open sockets."""

    __tablename__ = WS_TICKET_TABLE
    __table_args__ = {"extend_existing": True}

    ticket_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    # No FK: a deleted user's tickets just fail the check of the user and expire.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    issued_at: Mapped[float | None] = mapped_column(Float, default=None)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
