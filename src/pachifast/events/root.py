import asyncio
import json
import logging
import time
import uuid
from typing import Annotated

from fastapi import HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from pachifast.auth.dependencies import CurrentUserDep
from pachifast.auth.schemas import UserRead
from pachifast.config import get_settings
from pachifast.routing.router_factory import get_router

from .broadcaster import (
    INTERNAL_ERROR_CLOSE_CODE,
    PRIVILEGES_CHANGED_CLOSE_CODE,
    UNAUTHORIZED_CLOSE_CODE,
    _broadcaster,
)
from .tickets import TICKET_TTL_SECONDS, issue_ticket, redeem_ticket

log = logging.getLogger(__name__)
router = get_router(name="core/events", deps=["noauth"])
# Same prefix, but authenticated: the WebSocket route cannot carry the bearer header, the ticket route does.
ticket_router = get_router(name="core/events")

# How often an open connection gets a ``ping`` and its user is checked again (deactivated, deleted, demoted...).
PING_INTERVAL_SECONDS = 30.0

_snapshot_warned = False


async def _get_snapshot() -> list[dict]:
    from pachifast.audit.models import ActivityLog
    from pachifast.database.engine import get_core_sessionmaker

    try:
        async with get_core_sessionmaker()() as session:
            stmt = select(ActivityLog).order_by(ActivityLog.timestamp.desc()).limit(20)
            result = await session.execute(stmt)
            entries = list(result.scalars().all())
    except SQLAlchemyError as exc:
        # e.g. the activity log table does not exist (it is not part of the auth migrations): no snapshot
        global _snapshot_warned
        if not _snapshot_warned:  # once per process: every superuser connection would repeat it
            _snapshot_warned = True
            log.warning(
                "events ws: snapshot not available (%s); apply the auth migrations "
                "(pachifast migrations upgrade --target-name auth)",
                str(exc).splitlines()[0],
            )
        return []
    return [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "user_id": e.user_id,
            "username": e.username,
            "method": e.method,
            "path": e.path,
            "status_code": e.status_code,
            "duration_ms": e.duration_ms,
            "description": e.description,
        }
        for e in reversed(entries)
    ]


async def _close_after_accept(ws: WebSocket, code: int, reason: str = "") -> None:
    """Accept the connection and then close it with ``code``.

    Closing *before* accepting makes the server answer the handshake with HTTP 403, so a browser never sees the
    close code (it only sees an abnormal closure) and cannot tell an expired token from any other failure.
    """
    await ws.accept()
    await ws.close(code=code, reason=reason)


async def _current_state(user_id: uuid.UUID, issued_at: object) -> bool | None:
    """Return whether the user is a superuser now, or ``None`` if the user may no longer use the token.

    ``None`` means: unknown or inactive user, or a token issued before the last password change.

    Raises:
        Exception: Only for unexpected failures (for example the database), never for bad credentials.
    """
    from pachifast.auth.cruds import get_user_by_id
    from pachifast.auth.security import ensure_token_not_revoked
    from pachifast.database.engine import get_core_sessionmaker

    async with get_core_sessionmaker()() as session:
        user = await get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        return None
    try:
        ensure_token_not_revoked({"iat": issued_at}, user)
    except HTTPException:
        return None
    return bool(user.is_superuser)


async def _authenticate(token: str) -> tuple[uuid.UUID, bool, object] | None:
    """Return ``(user_id, is_superuser, issued_at)`` for a valid access token of an active user, else ``None``.

    Raises:
        Exception: Only for unexpected failures (for example the database), never for bad credentials.
    """
    from pachifast.auth.security import decode_access_payload, subject_to_user_id

    try:
        payload = decode_access_payload(token)
        user_id = subject_to_user_id(payload)
    except HTTPException:
        return None
    issued_at = payload.get("iat")
    is_superuser = await _current_state(user_id, issued_at)
    if is_superuser is None:
        return None
    return user_id, is_superuser, issued_at


async def _close_code_if_changed(user_id: uuid.UUID, issued_at: object, was_superuser: bool) -> int | None:
    """Close code for a connection whose user changed since it was opened, ``None`` if it can go on.

    The token expiry is deliberately not checked here: the client refreshes its access token without reopening
    the socket, so an expired ``exp`` on an open connection is normal. A failure of the check itself (for example
    the database being down) leaves the connection open and is logged.
    """
    try:
        is_superuser = await _current_state(user_id, issued_at)
    except Exception:
        log.exception("events ws: re-checking user %s failed", user_id)
        return None
    if is_superuser is None:
        return UNAUTHORIZED_CLOSE_CODE
    if is_superuser != was_superuser:
        return PRIVILEGES_CHANGED_CLOSE_CODE
    return None


@ticket_router.post("/ticket", skip_envelope=True)
async def create_ticket(request: Request, user: Annotated[UserRead, CurrentUserDep]) -> dict:
    """One-time ticket to open ``/ws?ticket=...`` instead of putting the access token in the URL.

    The ticket is valid for ``expires_in`` seconds and works once; ask for a new one at every (re)connection.
    """
    from pachifast.auth.security import decode_access_payload

    token = request.headers.get("Authorization", "").split(" ", 1)[-1].strip()
    issued_at = decode_access_payload(token).get("iat") if token else None
    return {"ticket": await issue_ticket(user.id, issued_at), "expires_in": int(TICKET_TTL_SECONDS)}


async def _authenticate_ticket(value: str) -> tuple[uuid.UUID, bool, object] | None:
    """Like ``_authenticate`` for a ticket: ``None`` if it is invalid or its user may no longer connect."""
    ticket = await redeem_ticket(value)
    if ticket is None:
        return None
    is_superuser = await _current_state(ticket.user_id, ticket.issued_at)
    if is_superuser is None:
        return None
    return ticket.user_id, is_superuser, ticket.issued_at


@router.websocket("/ws")
async def events_ws(ws: WebSocket):
    """Event stream. Close codes: ``4401`` missing/invalid/expired token, unknown/inactive user, or a user deleted,
    deactivated or whose password changed while connected (do not retry with the same token); ``4403`` the user's
    superuser status changed (reconnect to get the right events); ``1011`` unexpected server error (retry later)."""
    ticket = ws.query_params.get("ticket")
    token = ws.query_params.get("token")
    if not ticket and not token:
        await _close_after_accept(ws, UNAUTHORIZED_CLOSE_CODE, "Missing token")
        return
    if not ticket and not get_settings().events_allow_query_token:
        await _close_after_accept(ws, UNAUTHORIZED_CLOSE_CODE, "Ticket required")
        return

    try:
        identity = await (_authenticate_ticket(ticket) if ticket else _authenticate(token or ""))
    except Exception:
        log.exception("events ws: authentication failed unexpectedly")
        await _close_after_accept(ws, INTERNAL_ERROR_CLOSE_CODE, "Internal error")
        return
    if identity is None:
        await _close_after_accept(ws, UNAUTHORIZED_CLOSE_CODE, "Invalid token")
        return
    user_id, is_superuser, issued_at = identity

    await _broadcaster.connect(ws, user_id=str(user_id), is_superuser=is_superuser)
    log.debug(
        "events ws: user %s connected (superuser=%s, active=%d)", user_id, is_superuser, len(_broadcaster._connections)
    )
    try:
        snapshot = await _get_snapshot() if is_superuser else []
        await ws.send_text(json.dumps({"type": "snapshot", "data": snapshot}))
        # The deadline is absolute: a client that keeps sending messages cannot postpone the check of its user.
        next_check = time.monotonic() + PING_INTERVAL_SECONDS
        while True:
            remaining = next_check - time.monotonic()
            if remaining > 0:
                try:
                    await asyncio.wait_for(ws.receive_text(), timeout=remaining)
                except TimeoutError:
                    pass
                continue
            code = await _close_code_if_changed(user_id, issued_at, is_superuser)
            if code is not None:
                log.debug("events ws: closing the connection of user %s with %d", user_id, code)
                await ws.close(code=code, reason="User changed")
                break
            await ws.send_text(json.dumps({"type": "ping"}))
            next_check = time.monotonic() + PING_INTERVAL_SECONDS
    except WebSocketDisconnect:
        pass
    except Exception:
        # a client that vanished mid-send ends up here: normal, so only a debug trace
        log.debug("events ws: connection of user %s ended with an error", user_id, exc_info=True)
    finally:
        _broadcaster.disconnect(ws)
        log.debug("events ws: user %s disconnected", user_id)
