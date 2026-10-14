"""Token :mod:`auth.token`."""

from typing import Annotated

from fastapi import Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from pachifast import signals
from pachifast.config import get_settings
from pachifast.database.engine import get_core_session
from pachifast.dependencies import CoreSessionDep, SettingsDep
from pachifast.i18n import api_error, detail_from_error
from pachifast.ratelimit import enforce_auth_rate_limit
from pachifast.routing.router_factory import get_router
from pachifast.utils.tasks import spawn

from .cruds import create_user, get_user_by_id, get_user_by_username, normalize_username
from .dependencies import CurrentUserDep
from .schemas import MfaRequiredResponse, RefreshToken, TokenResponse, UserCreate, UserRead
from .security import (
    burn_hash_time,
    create_access_token,
    decode_refresh_payload,
    ensure_token_not_revoked,
    issue_refresh_token,
    record_login,
    refusal_status,
    revoke_refresh_family,
    revoke_user_refresh_tokens,
    rotate_refresh_token,
    subject_to_user_id,
)

core_settings = get_settings()
router = get_router(deps=["noauth"])


@router.post(
    "/token",
    response_model=None,
    skip_envelope=not core_settings.wrap_auth_token_response,
    dependencies=[Depends(enforce_auth_rate_limit)],
)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: CoreSessionDep,
    settings: SettingsDep,
):
    from .security import authenticate_user, clear_login_failures, release_login_attempt

    user = await authenticate_user(session, form_data.username, form_data.password, clear_failures=False)
    if not user:
        await signals.emit(signals.LOGIN_FAILED, username=form_data.username)
        raise api_error(
            status.HTTP_401_UNAUTHORIZED,
            "auth.invalid_credentials",
            loc=("body", "username"),
            headers={"WWW-Authenticate": "Bearer"},
        )

    if settings.MFA_ENABLED:
        from pachifast.mfa.cruds import get_mfa_config
        from pachifast.mfa.security import build_and_send_challenge, create_mfa_pending_token

        mfa_cfg = await get_mfa_config(session, user.id)
        if mfa_cfg and mfa_cfg.is_enabled:
            # the password was right, so the attempt is given back; the second factor has its own counter
            await release_login_attempt(session, user.id)
            mfa_token, jti = create_mfa_pending_token(user.id, settings)
            await build_and_send_challenge(session, user, mfa_cfg, jti, settings)
            return JSONResponse(MfaRequiredResponse(mfa_token=mfa_token).model_dump())

    await clear_login_failures(session, user.id)
    await record_login(session, user)
    access_token = create_access_token(str(user.id))
    refresh_token = await issue_refresh_token(session, user.id)
    from pachifast.events import broadcast_event

    spawn(broadcast_event("login", {"user_id": str(user.id), "username": user.username}))
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, skip_envelope=True)
async def logout(
    user: Annotated[UserRead, CurrentUserDep],
    session: CoreSessionDep,
    payload: Annotated[RefreshToken | None, Body()] = None,
) -> Response:
    """Esce: revoca i refresh token ed emette l'evento ``logout`` (stesso schema di ``login``).

    Con ``{"refresh_token": ...}`` revoca il login di quel token; senza corpo revoca tutti i login dell'utente.
    L'access token non e' revocabile (e' senza stato): resta valido fino alla scadenza, che e' breve. Il client
    deve scartare entrambi i token.
    """
    from pachifast.events import broadcast_event

    family = None
    if payload is not None:
        try:
            token_payload = decode_refresh_payload(payload.refresh_token)
            if str(subject_to_user_id(token_payload)) == str(user.id):
                family = token_payload.get("fam")
        except HTTPException:
            pass  # an invalid token has nothing to revoke
    if isinstance(family, str):
        await revoke_refresh_family(session, family)
    else:
        await revoke_user_refresh_tokens(session, user.id)

    # the sockets of the user close with the logout (a client that is still logged in on another device reconnects)
    from pachifast.events.broadcaster import UNAUTHORIZED_CLOSE_CODE, get_broadcaster

    await get_broadcaster().close_user(user.id, UNAUTHORIZED_CLOSE_CODE, "Logged out")
    await signals.emit(signals.USER_LOGGED_OUT, user=user)
    await broadcast_event("logout", {"user_id": str(user.id), "username": user.username})
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/refresh-token", response_model=TokenResponse, skip_envelope=not core_settings.wrap_auth_token_response)
async def refresh_access_token(payload: RefreshToken, session: CoreSessionDep):
    """Emette un nuovo access token da un refresh token valido (utente esistente e attivo).

    Con ``refresh_token_rotation`` (default) risponde anche con un nuovo refresh token e quello presentato non
    vale piu'; ripresentarlo revoca l'intero login.
    """
    try:
        token_payload = decode_refresh_payload(payload.refresh_token)
        user_id = subject_to_user_id(token_payload)
    except HTTPException:
        raise api_error(
            status.HTTP_401_UNAUTHORIZED, "auth.refresh_token_invalid", loc=("body", "refresh_token")
        ) from None

    user = await get_user_by_id(session, user_id)
    if user is None or not user.is_active:
        raise api_error(status.HTTP_401_UNAUTHORIZED, "auth.refresh_user_invalid", loc=("body", "refresh_token"))
    try:
        ensure_token_not_revoked(token_payload, user)
    except HTTPException:
        raise api_error(
            status.HTTP_401_UNAUTHORIZED, "auth.refresh_token_revoked", loc=("body", "refresh_token")
        ) from None
    new_refresh = await rotate_refresh_token(session, token_payload, user.id)
    return TokenResponse(access_token=create_access_token(str(user.id)), refresh_token=new_refresh)


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(enforce_auth_rate_limit)],
)
async def register(
    user_in: UserCreate,
    session: AsyncSession = Depends(get_core_session),
):
    if not get_settings().registration_enabled:
        raise api_error(status.HTTP_403_FORBIDDEN, "auth.registration_disabled", loc=("body",))
    # Endpoint pubblico: mai concedere privilegi da input non autenticato.
    user_in = user_in.model_copy(update={"is_superuser": False, "is_staff": False, "is_active": True})
    existing_user = await get_user_by_username(session, normalize_username(user_in.username))
    if existing_user:
        await burn_hash_time(user_in.password)  # same cost as a real registration: the time does not tell
        raise api_error(400, "auth.username_taken", loc=("body", "username"))
    try:
        user = await create_user(session, user_in)
    except ValueError as e:
        status_code = refusal_status(e)
        loc = ("body", "password") if status_code == 422 else ("body", "username")
        raise HTTPException(status_code=status_code, detail=detail_from_error(e, loc=loc)) from None
    return user
