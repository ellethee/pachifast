"""Funzioni crittografiche per il modulo MFA."""

import asyncio
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import jwt
from starlette.concurrency import run_in_threadpool

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from pachifast.auth.models import User
    from pachifast.config.settings import PachifastSettings

    from .models import UserMfaConfig

MFA_PENDING_TOKEN_TYPE = "mfa_pending"


# ---------------------------------------------------------------------------
# TOTP
# ---------------------------------------------------------------------------


def generate_totp_secret() -> str:
    import pyotp

    return pyotp.random_base32()


def get_totp_uri(secret: str, username: str, issuer: str) -> str:
    import pyotp

    return pyotp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


TOTP_PERIOD = 30
TOTP_WINDOW = 1


def totp_step(secret: str, code: str) -> int | None:
    """The 30-second step (``unix time // 30``) the code belongs to, or ``None`` if it is wrong.

    Steps one period either side of the current one are accepted, for clock drift.
    """
    import time

    import pyotp

    totp = pyotp.TOTP(secret, interval=TOTP_PERIOD)
    now = int(time.time())
    for offset in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        if totp.at(now + offset * TOTP_PERIOD) == code:
            return now // TOTP_PERIOD + offset
    return None


def verify_totp_code(secret: str, code: str) -> bool:
    """True if ``code`` is a valid TOTP for ``secret`` (does not stop a replay: see :func:`claim_totp_step`)."""
    return totp_step(secret, code) is not None


# ---------------------------------------------------------------------------
# Email / SMS OTP (hash Argon2id, come le password)
# ---------------------------------------------------------------------------


def generate_otp_code(length: int = 6) -> str:
    return "".join(str(secrets.randbelow(10)) for _ in range(length))


def hash_otp(code: str) -> str:
    from pachifast.auth.security import hash_password

    return hash_password(code)


def verify_otp(code: str, hashed: str | None) -> bool:
    from pachifast.auth.security import verify_password

    return verify_password(code, hashed)


# ---------------------------------------------------------------------------
# Backup codes
# ---------------------------------------------------------------------------


def generate_backup_codes(count: int = 8) -> list[str]:
    codes = []
    for _ in range(count):
        part1 = secrets.token_hex(3).upper()
        part2 = secrets.token_hex(3).upper()
        codes.append(f"{part1}-{part2}")
    return codes


def hash_backup_codes(codes: list[str]) -> list[str]:
    from pachifast.auth.security import hash_password

    return [hash_password(c) for c in codes]


def verify_backup_code(code: str, hashed_codes: list[str]) -> int | None:
    """Verifica il codice di backup; restituisce l'indice del codice usato o None."""
    from pachifast.auth.security import verify_password

    normalized = code.strip().upper()
    for i, hashed in enumerate(hashed_codes):
        if verify_password(normalized, hashed):
            return i
    return None


# ---------------------------------------------------------------------------
# MFA pending token (JWT short-lived)
# ---------------------------------------------------------------------------


def create_mfa_pending_token(user_id: uuid.UUID, settings: "PachifastSettings") -> tuple[str, str]:
    """Crea un JWT mfa_pending. Restituisce (token, jti)."""
    jti = secrets.token_hex(16)
    expire_minutes = getattr(settings, "MFA_OTP_EXPIRE_MINUTES", 10)
    payload = {
        "sub": str(user_id),
        "type": MFA_PENDING_TOKEN_TYPE,
        "jti": jti,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(minutes=expire_minutes),
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    return token, jti


def verify_mfa_pending_token(token: str, settings: "PachifastSettings") -> dict | None:
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=["HS256"], options={"require": ["exp", "sub", "jti"]}
        )
        if payload.get("type") != MFA_PENDING_TOKEN_TYPE:
            return None
        return payload
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------------------
# Challenge orchestration
# ---------------------------------------------------------------------------


async def build_and_send_challenge(
    session: "AsyncSession",
    user: "User",
    mfa_cfg: "UserMfaConfig",
    jti: str,
    settings: "PachifastSettings",
) -> None:
    """Crea la riga UserMfaPendingChallenge e invia l'OTP se necessario."""
    from .cruds import create_pending_challenge
    from .sender import get_sender

    expire_minutes = getattr(settings, "MFA_OTP_EXPIRE_MINUTES", 10)
    expires_at = datetime.now(UTC) + timedelta(minutes=expire_minutes)

    otp_code: str | None = None
    otp_hash_val: str | None = None

    if mfa_cfg.method in ("email", "sms"):
        otp_code = generate_otp_code()
        otp_hash_val = await run_in_threadpool(hash_otp, otp_code)

    await create_pending_challenge(
        session=session,
        user_id=user.id,
        jti=jti,
        method=mfa_cfg.method,
        otp_hash=otp_hash_val,
        expires_at=expires_at,
    )

    if otp_code is None:
        return

    # The row exists before the response, so the client can verify at once; the (possibly slow) delivery runs
    # on its own and needs no database session.
    task = asyncio.create_task(_deliver_otp(get_sender(), user.id, user.email, mfa_cfg, otp_code, expire_minutes))
    _delivery_tasks.add(task)
    task.add_done_callback(_delivery_tasks.discard)


_delivery_tasks: set["asyncio.Task[None]"] = set()  # strong references: a bare task can be garbage collected


async def _deliver_otp(
    sender: object, user_id: object, email: str | None, mfa_cfg: "UserMfaConfig", otp_code: str, expire_minutes: int
) -> None:
    """Send the OTP by email or SMS; a failure is logged, never raised (the user can ask for a new login)."""
    try:
        if mfa_cfg.method == "email":
            await sender.send_email_otp(  # type: ignore[attr-defined]
                user_id=user_id, email=email, otp_code=otp_code, expires_in_minutes=expire_minutes
            )
        elif mfa_cfg.method == "sms" and mfa_cfg.phone_number:
            await sender.send_sms_otp(  # type: ignore[attr-defined]
                user_id=user_id,
                phone_number=mfa_cfg.phone_number,
                otp_code=otp_code,
                expires_in_minutes=expire_minutes,
            )
    except Exception:
        log.exception("MFA: delivery of the one-time code failed")
