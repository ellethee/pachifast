import logging
import re
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from pachifast.utils.tasks import spawn

log = logging.getLogger(__name__)

_LOGGED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# How long the "skip logging" choice of a superuser is remembered (bounded cache, Redis if cache_url is set)
_CACHE_TTL = 30.0

# Limits of the stored text (the ``path`` column is 500 characters; the description gets the same bound).
MAX_PATH_LENGTH = 500
MAX_DESCRIPTION_LENGTH = 500
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


def sanitize_text(value: str | None, max_length: int) -> str | None:
    """Make user-controlled text safe to store and show: control characters become spaces, the length is bounded.

    ``path`` and ``description`` carry what the user typed (path parameters), so they are untrusted: a client must
    still escape them when rendering. Control characters would allow log forging and terminal tricks.
    """
    if value is None:
        return None
    cleaned = _CONTROL_CHARS.sub(" ", value)
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 1] + "…"
    return cleaned


class _SafeFormatMap(dict):
    """format_map che lascia i placeholder sconosciuti inalterati invece di sollevare KeyError."""

    def __missing__(self, key: str) -> str:
        return f"{{{key}}}"


def _extract_user_from_request(request: Request) -> tuple[uuid.UUID | None, str | None]:
    """Extract user_id and username from the JWT without a DB query."""
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        return None, None
    token = auth.split(" ", 1)[1].strip()
    try:
        from pachifast.auth.security import decode_access_token

        user_id = decode_access_token(token)
        return user_id, None
    except Exception:
        return None, None


async def _get_username(user_id: uuid.UUID) -> str | None:
    """Fetch the username from the DB (used only on a cache miss)."""
    try:
        from pachifast.auth.cruds import get_user_by_id
        from pachifast.database.engine import get_core_sessionmaker

        async with get_core_sessionmaker()() as session:
            user = await get_user_by_id(session, user_id)
            return user.username if user else None
    except Exception:
        return None


async def _should_skip(user_id: uuid.UUID | None) -> bool:
    """True only if the superuser has disable_activity_log=True (cached for ``_CACHE_TTL`` seconds)."""
    if user_id is None:
        return False
    from pachifast.cache import get_cache

    key = f"activity-skip:{user_id}"
    try:
        cached = await get_cache().get(key)
    except Exception:
        log.warning("activity log: cache not available, reading the setting from the database", exc_info=True)
        cached = None
    if cached is not None:
        return cached == "1"

    skip = False
    try:
        from pachifast.auth.cruds import get_user_by_id, get_user_settings
        from pachifast.database.engine import get_core_sessionmaker

        async with get_core_sessionmaker()() as session:
            user = await get_user_by_id(session, user_id)
            if user and user.is_superuser:
                settings = await get_user_settings(session, user_id)
                audit_section = getattr(settings, "audit", None)
                if audit_section is not None:
                    skip = bool(getattr(audit_section, "disable_activity_log", False))
    except Exception:
        pass

    try:
        await get_cache().set(key, "1" if skip else "0", _CACHE_TTL)
    except Exception:
        log.warning("activity log: cache not available, the setting is not remembered", exc_info=True)
    return skip


def _interpolate_log_as(template: str, path_params: dict, username: str | None, extra: dict | None = None) -> str:
    """Interpolate the log_as template with path_params, username and extra context from the endpoint."""
    ctx: dict = {**path_params, **(extra or {})}
    if username:
        ctx["username"] = username
    return template.format_map(_SafeFormatMap(ctx))


async def _save_log(
    user_id: uuid.UUID | None,
    username: str | None,
    method: str,
    path: str,
    status_code: int,
    duration_ms: int,
    description: str | None = None,
) -> None:
    path = sanitize_text(path, MAX_PATH_LENGTH) or ""
    description = sanitize_text(description, MAX_DESCRIPTION_LENGTH)
    username = sanitize_text(username, 150)
    try:
        from pachifast.audit.models import ActivityLog
        from pachifast.database.engine import get_core_sessionmaker

        async with get_core_sessionmaker()() as session:
            entry = ActivityLog(
                user_id=user_id,
                username=username,
                method=method,
                path=path,
                status_code=status_code,
                duration_ms=duration_ms,
                description=description,
            )
            session.add(entry)
            await session.commit()
        from pachifast.events import broadcast_event

        spawn(
            broadcast_event(
                "activity",
                {
                    "user_id": str(user_id) if user_id else None,
                    "username": username,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "description": description,
                },
            )
        )
    except Exception:
        log.exception("Errore salvataggio activity log")


class ActivityLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method not in _LOGGED_METHODS or not request.url.path.startswith("/api"):
            return await call_next(request)

        start = time.monotonic()
        response = await call_next(request)

        if response.status_code >= 500:
            return response

        # route e path_params vengono popolati da Starlette dentro call_next()
        route = request.scope.get("route")
        if route is None:
            return response  # no route matched (404/405): a client choosing random paths must not fill the table
        path_params: dict = request.scope.get("path_params") or {}

        user_id, username = _extract_user_from_request(request)
        if not await _should_skip(user_id):
            if user_id and username is None:
                username = await _get_username(user_id)

            description: str | None = None
            log_as_tpl = (getattr(route, "openapi_extra", None) or {}).get("x-log-as") if route else None
            if log_as_tpl:
                extra_ctx = getattr(request.state, "log_context", None) or {}
                description = _interpolate_log_as(log_as_tpl, path_params, username, extra_ctx)

            duration = int((time.monotonic() - start) * 1000)
            spawn(
                _save_log(
                    user_id=user_id,
                    username=username,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    duration_ms=duration,
                    description=description,
                )
            )

        return response
