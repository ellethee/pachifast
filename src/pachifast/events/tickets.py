"""One-time tickets to open the event WebSocket without putting the access token in the URL.

The URL of a WebSocket ends up in proxy and access logs, so it must not carry a long-lived credential. The client
asks for a ticket with an authenticated ``POST`` and opens the socket with ``?ticket=...``: the ticket is valid for a
few seconds and only once, so a leaked URL is worth nothing.

Where tickets live (``events_ticket_store``):

- ``database`` (default): the auth database, so every worker and host sees them. Only the SHA-256 of the ticket is
  stored, and redeeming is a ``DELETE`` that succeeds for exactly one caller.
- ``cache``: the configured cache (``cache_url``): Redis for several workers, otherwise the memory of the process.
"""

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import delete, select

from pachifast.cache import get_cache
from pachifast.config import get_settings
from pachifast.database.utils import utcnow

from .models import WsTicket

TICKET_TTL_SECONDS = 30.0


@dataclass(frozen=True)
class Ticket:
    """The identity a ticket stands for: the user and the ``iat`` of the access token it was issued from."""

    user_id: uuid.UUID
    issued_at: float | None


def _digest(value: str) -> str:
    """What is stored instead of the ticket itself."""
    return hashlib.sha256(value.encode()).hexdigest()


async def issue_ticket(user_id: uuid.UUID, issued_at: float | None) -> str:
    """Create a ticket for ``user_id`` valid for ``TICKET_TTL_SECONDS``."""
    value = secrets.token_urlsafe(32)
    key = _digest(value)
    if get_settings().events_ticket_store == "cache":
        payload = json.dumps({"user_id": str(user_id), "issued_at": issued_at})
        await get_cache().set(f"ws-ticket:{key}", payload, TICKET_TTL_SECONDS)
        return value

    from pachifast.database.engine import get_core_sessionmaker

    now = utcnow()
    async with get_core_sessionmaker()() as session:
        await session.execute(delete(WsTicket).where(WsTicket.expires_at <= now))  # type: ignore[arg-type]
        session.add(
            WsTicket(
                ticket_hash=key,
                user_id=user_id,
                issued_at=issued_at,
                expires_at=now + timedelta(seconds=TICKET_TTL_SECONDS),
            )
        )
        await session.commit()
    return value


async def redeem_ticket(value: str) -> Ticket | None:
    """Return the ticket and consume it, or ``None`` if it is unknown, already used or expired."""
    key = _digest(value)
    if get_settings().events_ticket_store == "cache":
        payload = await get_cache().pop(f"ws-ticket:{key}")
        if payload is None:
            return None
        data = json.loads(payload)
        return Ticket(user_id=uuid.UUID(data["user_id"]), issued_at=data["issued_at"])

    from pachifast.database.engine import get_core_sessionmaker

    async with get_core_sessionmaker()() as session:
        row = (await session.execute(select(WsTicket).where(WsTicket.ticket_hash == key))).scalars().first()
        if row is None:
            return None
        ticket = Ticket(user_id=row.user_id, issued_at=row.issued_at)
        expires_at = row.expires_at
        # The delete decides who wins when two sockets present the same ticket at once: only one row goes away.
        result = await session.execute(delete(WsTicket).where(WsTicket.ticket_hash == key))  # type: ignore[arg-type]
        await session.commit()
    if result.rowcount != 1 or expires_at <= utcnow():  # type: ignore[attr-defined]
        return None
    return ticket
