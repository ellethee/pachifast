"""The refresh token in an ``HttpOnly`` cookie (``refresh_mode`` ``cookie`` or ``both``).

In ``body`` mode (the default) nothing here is used: the refresh token travels in the JSON as it always did.
With the cookie, JavaScript never sees the refresh token; the browser sends it back to ``/refresh-token`` and
``/logout`` only. Because a cookie is sent without the page asking for it, a request that relies on it must prove
that it comes from a script of the application: it needs the header :data:`CSRF_HEADER` (a header the browser
refuses to send cross-site without a CORS preflight) and its ``Origin``, when present, must be an allowed one.
"""

from fastapi import Request, Response, status

from pachifast.config import get_settings
from pachifast.i18n import api_error

CSRF_HEADER = "X-Pachi-Auth"
CSRF_VALUE = "refresh"


def uses_cookie() -> bool:
    """True when the refresh token is also handed out as a cookie (``cookie`` and ``both`` modes)."""
    return get_settings().refresh_mode in {"cookie", "both"}


def json_has_refresh_token() -> bool:
    """True when the JSON of the login responses carries the refresh token (``body`` and ``both`` modes)."""
    return get_settings().refresh_mode != "cookie"


def token_body(access_token: str, refresh_token: str | None) -> dict[str, str | None]:
    """The JSON of a login or refresh response; the ``refresh_token`` key only when the mode puts it there
    (``None`` stays a null: rotation is off and the presented token is still the valid one)."""
    body: dict[str, str | None] = {"access_token": access_token, "token_type": "bearer"}
    if json_has_refresh_token():
        body["refresh_token"] = refresh_token
    return body


def _secure(request: Request) -> bool:
    configured = get_settings().refresh_cookie_secure
    return request.url.scheme == "https" if configured is None else configured


def set_refresh_cookie(response: Response, request: Request, refresh_token: str | None) -> None:
    """Hand out ``refresh_token`` as the cookie (no-op in ``body`` mode, or without a new token: rotation is off)."""
    if not uses_cookie() or refresh_token is None:
        return
    settings = get_settings()
    response.set_cookie(
        settings.refresh_cookie_name,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path=settings.refresh_cookie_path,
        secure=_secure(request),
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )


def clear_refresh_cookie(response: Response, request: Request) -> None:
    """Delete the cookie (logout, or a refresh the server refused)."""
    settings = get_settings()
    response.delete_cookie(
        settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        secure=_secure(request),
        httponly=True,
        samesite=settings.refresh_cookie_samesite,
    )


def _allowed_origins(request: Request) -> set[str]:
    extra = {o.strip().rstrip("/") for o in get_settings().refresh_allowed_origins.split(",") if o.strip()}
    return {f"{request.url.scheme}://{request.headers.get('host', '')}"} | extra


def read_cookie_token(request: Request) -> str | None:
    """The refresh token of the cookie, after the CSRF checks; ``None`` when there is no cookie.

    Raises:
        HTTPException: 403 ``auth.csrf_rejected`` when the cookie is there but the request does not prove it comes
            from the application (missing header, or a foreign ``Origin``).
    """
    token = request.cookies.get(get_settings().refresh_cookie_name)
    if not token:
        return None
    origin = request.headers.get("origin")
    if request.headers.get(CSRF_HEADER) != CSRF_VALUE or (
        origin is not None and origin.rstrip("/") not in _allowed_origins(request)
    ):
        raise api_error(status.HTTP_403_FORBIDDEN, "auth.csrf_rejected", loc=("header", CSRF_HEADER))
    return token


def resolve_refresh_token(request: Request, body_token: str | None) -> str | None:
    """The refresh token of a request: the one in the body when it is given, else the cookie (checked for CSRF).

    The body is always accepted, whatever the mode: it carries the secret itself, so it needs no CSRF defence and
    keeps CLIs, tests and the clients of 0.16 working while a project moves to the cookie.
    """
    return body_token or read_cookie_token(request)


__all__ = [
    "CSRF_HEADER",
    "CSRF_VALUE",
    "clear_refresh_cookie",
    "json_has_refresh_token",
    "read_cookie_token",
    "resolve_refresh_token",
    "set_refresh_cookie",
    "token_body",
    "uses_cookie",
]
