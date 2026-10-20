"""MFA endpoints: setup, status, login step-2 verification."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from pachifast.auth.models import User
from pachifast.cache import get_cache
from pachifast.config import get_settings
from pachifast.database.utils import utcnow
from pachifast.dependencies import ActiveUserDep, CoreSessionDep, SettingsDep
from pachifast.i18n import api_error
from pachifast.ratelimit import enforce_auth_rate_limit
from pachifast.routing.router_factory import get_router

from .schemas import (
    BackupCodesRegenerateRequest,
    BackupCodesRegenerateResponse,
    EmailConfirmResponse,
    EmailSetupResponse,
    MfaDisableRequest,
    MfaDisableResponse,
    MfaStatusResponse,
    MfaVerifyRequest,
    OtpConfirmRequest,
    SmsConfirmResponse,
    SmsSetupRequest,
    SmsSetupResponse,
    TotpConfirmRequest,
    TotpConfirmResponse,
    TotpSetupResponse,
)

router = get_router(deps=["noauth"])


def _locked(user: User, loc: tuple[str, ...] = ("body", "code")) -> HTTPException:
    """429 for a locked account, with ``Retry-After`` (seconds left) so a client can show a countdown."""
    from pachifast.auth.security import retry_after_seconds

    return api_error(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "mfa.too_many_attempts",
        loc=loc,
        headers={"Retry-After": str(retry_after_seconds(user))},
    )


def _require_mfa_enabled(settings: SettingsDep) -> None:
    """Enrolling is pointless (and misleading) while the login does not ask for the second factor."""
    if not settings.MFA_ENABLED:
        raise api_error(status.HTTP_403_FORBIDDEN, "mfa.disabled", loc=("body",))


def _no_setup(method: str) -> HTTPException:
    """400 for a confirmation that has no setup (or no valid code) waiting."""
    if method == "email":
        return api_error(status.HTTP_400_BAD_REQUEST, "mfa.no_email_setup", loc=("body", "code"))
    return api_error(status.HTTP_400_BAD_REQUEST, "mfa.no_sms_setup", loc=("body", "code"))


def _code_invalid(method: str) -> HTTPException:
    """400 for a wrong or expired enrollment code."""
    if method == "email":
        return api_error(status.HTTP_400_BAD_REQUEST, "mfa.email_code_invalid", loc=("body", "code"))
    return api_error(status.HTTP_400_BAD_REQUEST, "mfa.sms_code_invalid", loc=("body", "code"))


async def _start_otp_channel(
    session: CoreSessionDep, user: User, settings: SettingsDep, *, method: str, phone_number: str | None = None
) -> list[str]:
    """Save the email or SMS method as *not yet active*, send a code to it and return the backup codes.

    The method counts only after ``/mfa/<method>/confirm`` receives that code: otherwise a stolen access token could
    switch the second factor to an address or a number of the thief.
    """
    from .cruds import create_or_update_mfa_config
    from .security import build_and_send_challenge, generate_backup_codes, hash_backup_codes

    # one code per user and cooldown: the route sends to an address or a number that the caller chooses
    cooldown = settings.mfa_send_cooldown_seconds
    if cooldown and not await get_cache().add(f"mfa-send:{user.id}", "1", cooldown):
        raise api_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "auth.rate_limited",
            loc=("body",),
            headers={"Retry-After": str(cooldown)},
        )
    backup_codes = generate_backup_codes()
    cfg = await create_or_update_mfa_config(
        session,
        user_id=user.id,
        method=method,
        backup_codes_hashed=await run_in_threadpool(hash_backup_codes, backup_codes),
        phone_number=phone_number,
        is_enabled=False,
    )
    await build_and_send_challenge(session, user, cfg, uuid.uuid4().hex, settings)
    return backup_codes


async def _confirm_otp_channel(
    session: CoreSessionDep, user: User, settings: SettingsDep, *, method: str, code: str
) -> None:
    """Second step of the email / SMS enrollment: the code that arrived on the channel turns the method on."""
    from .cruds import (
        claim_challenge,
        consume_challenge_attempt,
        enable_mfa_config,
        get_latest_challenge,
        get_mfa_config,
    )
    from .security import verify_otp

    cfg = await get_mfa_config(session, user.id)
    if not cfg or cfg.is_enabled or cfg.method != method:
        raise _no_setup(method)
    challenge = await get_latest_challenge(session, user.id, method)
    if not challenge or not challenge.otp_hash or challenge.expires_at < utcnow():
        raise _no_setup(method)
    max_attempts = getattr(settings, "MFA_MAX_ATTEMPTS", 5)
    if not await consume_challenge_attempt(session, challenge.id, max_attempts):
        raise api_error(status.HTTP_429_TOO_MANY_REQUESTS, "mfa.too_many_attempts", loc=("body", "code"))
    if not await run_in_threadpool(verify_otp, code, challenge.otp_hash) or not await claim_challenge(
        session, challenge.id
    ):
        raise _code_invalid(method)
    await enable_mfa_config(session, user.id)


async def _refuse_if_enabled(session: CoreSessionDep, user_id: uuid.UUID) -> None:
    """Enrolling again would replace (and, for TOTP, switch off) the active second factor with whatever the
    caller sends. With a stolen access token that is an account takeover, so it takes ``/mfa/disable`` first,
    which asks for the password."""
    from .cruds import get_mfa_config

    cfg = await get_mfa_config(session, user_id)
    if cfg and cfg.is_enabled:
        raise api_error(status.HTTP_409_CONFLICT, "mfa.already_enabled", loc=("body",))


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


@router.get("/status", response_model=MfaStatusResponse)
async def mfa_status(
    user: Annotated[User, ActiveUserDep],
    session: CoreSessionDep,
):
    from .cruds import get_mfa_config

    cfg = await get_mfa_config(session, user.id)
    if cfg and cfg.is_enabled:
        return MfaStatusResponse(enabled=True, method=cfg.method)
    return MfaStatusResponse(enabled=False, method=None)


# ---------------------------------------------------------------------------
# Setup TOTP
# ---------------------------------------------------------------------------


@router.post("/totp/setup", response_model=TotpSetupResponse)
async def totp_setup(
    user: Annotated[User, ActiveUserDep],
    session: CoreSessionDep,
    settings: SettingsDep,
):
    _require_mfa_enabled(settings)
    from .cruds import create_or_update_mfa_config
    from .security import (
        generate_backup_codes,
        generate_totp_secret,
        get_totp_uri,
        hash_backup_codes,
    )

    await _refuse_if_enabled(session, user.id)
    secret = generate_totp_secret()
    issuer = settings.MFA_TOTP_ISSUER or settings.app_name or "PachiFast"
    otpauth_uri = get_totp_uri(secret, user.username, issuer)
    backup_codes = generate_backup_codes()
    hashed = await run_in_threadpool(hash_backup_codes, backup_codes)

    await create_or_update_mfa_config(
        session,
        user_id=user.id,
        method="totp",
        totp_secret=secret,
        backup_codes_hashed=hashed,
        is_enabled=False,
    )

    return TotpSetupResponse(secret=secret, otpauth_uri=otpauth_uri, backup_codes=backup_codes)


@router.post("/totp/confirm", response_model=TotpConfirmResponse)
async def totp_confirm(
    payload: TotpConfirmRequest,
    user: Annotated[User, ActiveUserDep],
    session: CoreSessionDep,
):
    from .cruds import claim_totp_step, enable_mfa_config, get_mfa_config
    from .security import totp_step

    cfg = await get_mfa_config(session, user.id)
    if not cfg or cfg.method != "totp" or not cfg.totp_secret:
        raise api_error(status.HTTP_400_BAD_REQUEST, "mfa.no_totp_setup", loc=("body", "code"))
    step = totp_step(cfg.totp_secret, payload.code)
    if step is None or not await claim_totp_step(session, cfg.id, step):
        raise api_error(status.HTTP_400_BAD_REQUEST, "mfa.totp_invalid", loc=("body", "code"))
    await enable_mfa_config(session, user.id)
    return TotpConfirmResponse(ok=True)


# ---------------------------------------------------------------------------
# Setup Email OTP
# ---------------------------------------------------------------------------


@router.post("/email/setup", dependencies=[Depends(enforce_auth_rate_limit)], response_model=EmailSetupResponse)
async def email_setup(
    user: Annotated[User, ActiveUserDep],
    session: CoreSessionDep,
    settings: SettingsDep,
):
    _require_mfa_enabled(settings)
    await _refuse_if_enabled(session, user.id)
    if not user.email:
        raise api_error(status.HTTP_400_BAD_REQUEST, "mfa.no_email", loc=("body",))
    backup_codes = await _start_otp_channel(session, user, settings, method="email")
    return EmailSetupResponse(ok=True, backup_codes=backup_codes)


@router.post("/email/confirm", dependencies=[Depends(enforce_auth_rate_limit)], response_model=EmailConfirmResponse)
async def email_confirm(
    payload: OtpConfirmRequest,
    user: Annotated[User, ActiveUserDep],
    session: CoreSessionDep,
    settings: SettingsDep,
):
    await _confirm_otp_channel(session, user, settings, method="email", code=payload.code)
    return EmailConfirmResponse(ok=True)


# ---------------------------------------------------------------------------
# Setup SMS OTP
# ---------------------------------------------------------------------------


@router.post("/sms/setup", dependencies=[Depends(enforce_auth_rate_limit)], response_model=SmsSetupResponse)
async def sms_setup(
    payload: SmsSetupRequest,
    user: Annotated[User, ActiveUserDep],
    session: CoreSessionDep,
    settings: SettingsDep,
):
    _require_mfa_enabled(settings)
    await _refuse_if_enabled(session, user.id)
    backup_codes = await _start_otp_channel(session, user, settings, method="sms", phone_number=payload.phone_number)
    return SmsSetupResponse(ok=True, backup_codes=backup_codes)


@router.post("/sms/confirm", dependencies=[Depends(enforce_auth_rate_limit)], response_model=SmsConfirmResponse)
async def sms_confirm(
    payload: OtpConfirmRequest,
    user: Annotated[User, ActiveUserDep],
    session: CoreSessionDep,
    settings: SettingsDep,
):
    await _confirm_otp_channel(session, user, settings, method="sms", code=payload.code)
    return SmsConfirmResponse(ok=True)


# ---------------------------------------------------------------------------
# Disable
# ---------------------------------------------------------------------------


@router.post("/disable", response_model=MfaDisableResponse)
async def mfa_disable(
    payload: MfaDisableRequest,
    user: Annotated[User, ActiveUserDep],
    session: CoreSessionDep,
):
    from pachifast.auth.security import check_current_password, is_user_locked

    from .cruds import delete_mfa_config

    if is_user_locked(user):
        raise _locked(user, loc=("body", "password"))
    if not await check_current_password(session, user, payload.password):
        raise api_error(status.HTTP_400_BAD_REQUEST, "mfa.password_incorrect", loc=("body", "password"))
    await delete_mfa_config(session, user.id)
    return MfaDisableResponse(ok=True)


# ---------------------------------------------------------------------------
# Backup codes regenerate
# ---------------------------------------------------------------------------


@router.post("/backup-codes/regenerate", response_model=BackupCodesRegenerateResponse)
async def backup_codes_regenerate(
    payload: BackupCodesRegenerateRequest,
    user: Annotated[User, ActiveUserDep],
    session: CoreSessionDep,
):
    from pachifast.auth.security import check_current_password, is_user_locked

    from .cruds import get_mfa_config, update_backup_codes
    from .security import generate_backup_codes, hash_backup_codes

    if is_user_locked(user):
        raise _locked(user, loc=("body", "password"))
    if not await check_current_password(session, user, payload.password):
        raise api_error(status.HTTP_400_BAD_REQUEST, "mfa.password_incorrect", loc=("body", "password"))
    cfg = await get_mfa_config(session, user.id)
    if not cfg or not cfg.is_enabled:
        raise api_error(status.HTTP_400_BAD_REQUEST, "mfa.not_active", loc=("body",))
    backup_codes = generate_backup_codes()
    hashed = await run_in_threadpool(hash_backup_codes, backup_codes)
    await update_backup_codes(session, user.id, hashed)
    return BackupCodesRegenerateResponse(backup_codes=backup_codes)


# ---------------------------------------------------------------------------
# Verify step-2 (noauth, protected by the mfa_token)
# ---------------------------------------------------------------------------


@router.post(
    "/verify",
    skip_envelope=not get_settings().wrap_auth_token_response,
    dependencies=[Depends(enforce_auth_rate_limit)],
)
async def mfa_verify(
    payload: MfaVerifyRequest,
    session: CoreSessionDep,
    settings: SettingsDep,
    request: Request,
):
    from pachifast.auth.cruds import get_user_by_id
    from pachifast.auth.refresh_cookie import set_refresh_cookie, token_body
    from pachifast.auth.security import (
        clear_login_failures,
        create_access_token,
        is_user_locked,
        issue_refresh_token,
        lock_if_exhausted,
        record_login,
        reserve_login_attempt,
    )

    from .cruds import (
        claim_challenge,
        claim_totp_step,
        cleanup_expired_challenges,
        consume_challenge_attempt,
        delete_pending_challenge,
        get_mfa_config,
        get_pending_challenge_by_jti,
        remove_used_backup_code,
    )
    from .security import (
        totp_step,
        verify_backup_code,
        verify_mfa_pending_token,
        verify_otp,
    )

    token_payload = verify_mfa_pending_token(payload.mfa_token, settings)
    if not token_payload:
        raise api_error(status.HTTP_401_UNAUTHORIZED, "mfa.token_invalid", loc=("body", "mfa_token"))

    jti = token_payload.get("jti")
    try:
        user_id = uuid.UUID(str(token_payload["sub"]))
    except ValueError:
        raise api_error(status.HTTP_401_UNAUTHORIZED, "mfa.token_invalid", loc=("body", "mfa_token")) from None

    challenge = await get_pending_challenge_by_jti(session, jti)
    if not challenge:
        raise api_error(status.HTTP_401_UNAUTHORIZED, "mfa.challenge_not_found", loc=("body", "mfa_token"))

    user = await get_user_by_id(session, user_id)
    if not user or not user.is_active:
        raise api_error(status.HTTP_401_UNAUTHORIZED, "user.inactive")
    if is_user_locked(user):
        raise _locked(user)

    # Every guess is paid for before it is checked, both on the challenge and on the account (the same counter as
    # the password): many challenges, or many parallel requests, do not add up to more guesses than the limit.
    max_attempts = getattr(settings, "MFA_MAX_ATTEMPTS", 5)
    if not await consume_challenge_attempt(session, challenge.id, max_attempts):
        raise api_error(status.HTTP_429_TOO_MANY_REQUESTS, "mfa.too_many_attempts", loc=("body", "code"))
    if not await reserve_login_attempt(session, user_id):
        await session.refresh(user)  # the lock may have just been set
        raise _locked(user)

    cfg = await get_mfa_config(session, user_id)
    if not cfg or not cfg.is_enabled:
        raise api_error(status.HTTP_401_UNAUTHORIZED, "mfa.not_configured", loc=("body",))

    code_ok = False
    backup_idx: int | None = None

    if challenge.method == "totp" and cfg.totp_secret:
        step = totp_step(cfg.totp_secret, payload.code)
        code_ok = step is not None and await claim_totp_step(session, cfg.id, step)  # a used code cannot be replayed
    elif challenge.method in ("email", "sms") and challenge.otp_hash:
        # the delete is the claim: two parallel requests with the same correct code cannot both pass
        code_ok = await run_in_threadpool(verify_otp, payload.code, challenge.otp_hash) and await claim_challenge(
            session, challenge.id
        )

    if not code_ok:
        backup_idx = await run_in_threadpool(verify_backup_code, payload.code, cfg.backup_codes)
        code_ok = backup_idx is not None and await remove_used_backup_code(session, cfg, backup_idx)

    if not code_ok:
        await lock_if_exhausted(session, user_id)
        raise api_error(status.HTTP_401_UNAUTHORIZED, "mfa.code_invalid", loc=("body", "code"))

    await delete_pending_challenge(session, challenge.id)
    await cleanup_expired_challenges(session)
    await clear_login_failures(session, user_id)

    await record_login(session, user)
    access_token = create_access_token(str(user.id))
    refresh_token = await issue_refresh_token(session, user.id)
    response = JSONResponse(content=token_body(access_token, refresh_token))
    set_refresh_cookie(response, request, refresh_token)
    return response
