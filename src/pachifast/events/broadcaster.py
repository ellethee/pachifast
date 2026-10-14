import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import WebSocket

log = logging.getLogger(__name__)

# WebSocket close codes (application range 4000-4999).
UNAUTHORIZED_CLOSE_CODE = 4401  # bad/expired token, or user deleted, deactivated or with a revoked token: do not retry
PRIVILEGES_CHANGED_CLOSE_CODE = 4403  # superuser status changed: reconnect to get the right audience
INTERNAL_ERROR_CLOSE_CODE = 1011  # unexpected server error: retry later

SEND_TIMEOUT = 5.0  # seconds a client has to take an event before it is dropped


def _snake_to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


def _keys_to_camel(obj):
    if isinstance(obj, dict):
        return {_snake_to_camel(k): _keys_to_camel(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_keys_to_camel(i) for i in obj]
    return obj


@dataclass(frozen=True)
class Subscriber:
    """Who is behind a WebSocket connection (fixed when the connection is opened).

    Attributes:
        user_id: The authenticated user's id as a string, ``None`` if unknown.
        is_superuser: Whether the user was a superuser when the connection was opened.
    """

    user_id: str | None = None
    is_superuser: bool = False


class EventBroadcaster:
    """Sends events to the connected WebSocket clients, never to all of them.

    Audience of an event: the superusers, plus the users named in ``to_users`` (for events that concern one
    user, for example a permission change). A regular user therefore never sees other people's activity, unless
    the event type is listed in the ``events_public_types`` setting (then it goes to every connected client,
    for example ``login`` to show who is online).
    """

    def __init__(self):
        self._connections: dict[WebSocket, Subscriber] = {}

    @property
    def enabled(self) -> bool:
        from pachifast.config import get_settings

        return getattr(get_settings(), "events_broadcast_enabled", False)

    async def connect(self, ws: WebSocket, user_id: str | None = None, is_superuser: bool = False) -> None:
        """Accept the connection and register who is behind it."""
        await ws.accept()
        self._connections[ws] = Subscriber(user_id=user_id, is_superuser=is_superuser)

    def disconnect(self, ws: WebSocket) -> None:
        self._connections.pop(ws, None)

    async def close_user(self, user_id: object, code: int, reason: str = "") -> int:
        """Close every connection of a user (deleted, deactivated, demoted...). Returns how many were closed."""
        target = str(user_id)
        sockets = [ws for ws, sub in self._connections.items() if sub.user_id == target]
        for ws in sockets:
            self._connections.pop(ws, None)
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                log.debug("events ws: closing a connection of user %s failed", target, exc_info=True)
        return len(sockets)

    def recipients(self, to_users: Iterable[object] = (), public: bool = False) -> list[WebSocket]:
        """Connections that may receive an event: everyone if ``public``, else superusers and ``to_users``."""
        if public:
            return list(self._connections)
        targets = {str(user) for user in to_users}
        return [ws for ws, sub in self._connections.items() if sub.is_superuser or sub.user_id in targets]

    async def emit(self, event_type: str, data: dict, *, to_users: Iterable[object] = (), public: bool = False) -> None:
        """Send an event to its audience: everyone if ``public``, else superusers and the users in ``to_users``."""
        recipients = self.recipients(to_users, public)
        if not recipients:
            return
        payload = json.dumps(
            {
                "type": event_type,
                "timestamp": datetime.now(UTC).isoformat(),
                "data": _keys_to_camel(data),
            }
        )
        # in parallel and with a deadline: one slow client must not hold up the others
        results = await asyncio.gather(*(self._send(ws, payload) for ws in recipients))
        for ws, delivered in zip(recipients, results, strict=True):
            if not delivered:
                self._connections.pop(ws, None)

    @staticmethod
    async def _send(ws: WebSocket, payload: str) -> bool:
        """Send ``payload``; ``False`` if the client is gone or did not take it within ``SEND_TIMEOUT`` seconds."""
        try:
            await asyncio.wait_for(ws.send_text(payload), timeout=SEND_TIMEOUT)
        except Exception:
            return False
        return True


_broadcaster = EventBroadcaster()


def get_broadcaster() -> EventBroadcaster:
    return _broadcaster


async def broadcast_event(event_type: str, data: dict, *, to_users: Iterable[object] = ()) -> None:
    """Broadcast an event (when events are enabled).

    The audience is the superusers and the users in ``to_users``, or every connected client when ``event_type`` is
    listed in the ``events_public_types`` setting.
    """
    if not _broadcaster.enabled:
        return
    from pachifast.config import get_settings

    public = event_type in get_settings().events_public_types
    await _broadcaster.emit(event_type, data, to_users=to_users, public=public)
