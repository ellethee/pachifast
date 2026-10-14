"""Reusable HTTP and WebSocket authentication dependencies."""

from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, WebSocket, WebSocketException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from pachifast.database.engine import get_core_session
from pachifast.i18n import api_error

from .cruds import get_first_superuser, get_user_by_id
from .models import User
from .permission_provider import resolve_user_permissions

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")
_oauth2_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _is_swagger_request(request: Request) -> bool:
    """True for a request that comes from the Swagger UI served by this very process, on the same machine.

    ``swagger_noauth`` is a development shortcut. The Referer is spoofable, so it is not enough on its own:
    the peer must also be the loopback, which a remote client cannot fake. A request that carries proxy headers
    went through a reverse proxy on this machine, so its peer is the proxy and not the client: it is refused.
    """
    if any(name in request.headers for name in _PROXY_HEADERS):
        return False
    client = request.client.host if request.client else ""
    path = urlsplit(request.headers.get("referer", "")).path
    return client in LOOPBACK_HOSTS and (path == "/docs" or path.startswith("/docs/"))


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_core_session)],
    token: str | None = Depends(_oauth2_optional),
) -> User:
    """Resolve the current authenticated user from the bearer access token.

    When swagger_noauth is enabled in settings, requests originating from the
    Swagger UI (/docs) without a token are resolved as the first active superuser.
    """
    from pachifast.config import get_settings

    from .security import decode_access_payload, ensure_token_not_revoked, subject_to_user_id

    if token is None:
        settings = get_settings()
        if settings.swagger_noauth and _is_swagger_request(request):
            user = await get_first_superuser(session)
            if user is not None:
                return user
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "auth.not_authenticated",
            loc=("header", "Authorization"),
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_payload(token)
    user = await get_user_by_id(session, subject_to_user_id(payload))
    if user is None:
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "user.not_found",
            loc=("header", "Authorization"),
            headers={"WWW-Authenticate": "Bearer"},
        )
    ensure_token_not_revoked(payload, user)
    return user


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Ensure the current HTTP user is active before continuing."""
    if not current_user.is_active:
        raise api_error(400, "user.disabled", loc=("header", "Authorization"))
    return current_user


async def get_current_ws_user(
    websocket: WebSocket,
    session: Annotated[AsyncSession, Depends(get_core_session)],
) -> User:
    """Resolve the current authenticated user for a WebSocket connection."""
    from pachifast.config import get_settings

    from .security import decode_access_payload, ensure_token_not_revoked, subject_to_user_id

    token = websocket.query_params.get("token") if get_settings().events_allow_query_token else None
    authorization = websocket.headers.get("authorization", "")
    if not token and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()

    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing authentication token",
        )

    try:
        payload = decode_access_payload(token)
        user = await get_user_by_id(session, subject_to_user_id(payload))
        if user is not None:
            ensure_token_not_revoked(payload, user)
    except HTTPException as exc:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason=str(exc.detail),
        ) from exc

    if user is None:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Utente non trovato",
        )
    return user


async def get_current_active_ws_user(
    current_user: Annotated[User, Depends(get_current_ws_user)],
) -> User:
    """Ensure the current WebSocket user is active before continuing."""
    if not current_user.is_active:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Utente disabilitato",
        )
    return current_user


async def get_current_user_optional(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_core_session)],
    token: str | None = Depends(_oauth2_optional),
) -> User | None:
    """Come get_current_user ma ritorna None invece di lanciare 401 se non autenticato."""
    if token is None:
        return None
    from .security import decode_access_payload, ensure_token_not_revoked, subject_to_user_id

    try:
        payload = decode_access_payload(token)
        user = await get_user_by_id(session, subject_to_user_id(payload))
        if user is None or not user.is_active:
            return None
        ensure_token_not_revoked(payload, user)
    except HTTPException:
        return None
    return user


# Tipi riutilizzabili per injection
CurrentUserDep = Depends(get_current_user)
CurrentUserOptionalDep = Depends(get_current_user_optional)
ActiveUserDep = Depends(get_current_active_user)
CurrentWsUserDep = Depends(get_current_ws_user)
ActiveWsUserDep = Depends(get_current_active_ws_user)
TokenDep = Depends(oauth2_scheme)


def make_permission_checker(
    permissions: list[str] | None = None,
    require_staff: bool = False,
    require_superuser: bool = False,
) -> Callable:
    """Factory che ritorna una dependency FastAPI per il controllo dei permessi.

    I superuser bypassano tutti i controlli.
    L'ordine di verifica è: superuser → staff → permissions.

    Args:
        permissions: lista di permission string che l'utente deve possedere.
        require_staff: se True, l'utente deve avere is_staff=True.
        require_superuser: se True, solo i superuser possono accedere.
    """

    async def check_permissions(
        user: Annotated[User, Depends(get_current_active_user)],
        session: Annotated[AsyncSession, Depends(get_core_session)],
    ) -> None:
        # i superuser bypassa tutti i controlli
        if user.is_superuser:
            return
        if require_superuser:
            raise api_error(403, "access.superuser_required", loc=("header", "Authorization"))
        if require_staff and not user.is_staff:
            raise api_error(403, "access.staff_required", loc=("header", "Authorization"))
        if permissions and user.id is not None:
            user_perms = await resolve_user_permissions(session, user.id)
            missing = [p for p in permissions if p not in user_perms]
            if missing:
                raise api_error(
                    403, "access.permissions_missing", loc=("header", "Authorization"), permissions=", ".join(missing)
                )

    return check_permissions


def make_ws_permission_checker(
    permissions: list[str] | None = None,
    require_staff: bool = False,
    require_superuser: bool = False,
) -> Callable:
    """WebSocket version of permission checks."""

    async def check_permissions(
        user: Annotated[User, Depends(get_current_active_ws_user)],
        session: Annotated[AsyncSession, Depends(get_core_session)],
    ) -> None:
        if user.is_superuser:
            return
        if require_superuser:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Accesso riservato ai superuser",
            )
        if require_staff and not user.is_staff:
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION,
                reason="Accesso riservato allo staff",
            )
        if permissions and user.id is not None:
            user_perms = await resolve_user_permissions(session, user.id)
            missing = [p for p in permissions if p not in user_perms]
            if missing:
                raise WebSocketException(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason=f"Permessi mancanti: {', '.join(missing)}",
                )

    return check_permissions
