"""Hashing password (Argon2id), token JWT e autenticazione utente."""

import math
import uuid
from datetime import UTC, datetime, timedelta
from logging import getLogger
from typing import Any

import jwt
from fastapi import HTTPException, status
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy import case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from pachifast import signals
from pachifast.config import get_settings
from pachifast.database.utils import as_utc, utcnow
from pachifast.i18n import MessageError, api_error

from .cruds import get_user_by_username
from .models import RefreshTokenRecord, User

log = getLogger(__name__)

# Parametri Argon2id raccomandati da OWASP: m=19 MiB, t=2, p=1.
ARGON2_MEMORY_COST_KIB = 19456
ARGON2_TIME_COST = 2
ARGON2_PARALLELISM = 1

TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"

_password_hash = PasswordHash(
    (
        Argon2Hasher(
            time_cost=ARGON2_TIME_COST,
            memory_cost=ARGON2_MEMORY_COST_KIB,
            parallelism=ARGON2_PARALLELISM,
        ),
    )
)

# Hash fittizio usato per uniformare i tempi di risposta quando l'utente non esiste.
_DUMMY_HASH = _password_hash.hash("pachifast-dummy-password")


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Restituisce l'hash Argon2id (formato PHC) della password."""
    return _password_hash.hash(password)


def get_password_hash(password: str) -> str:
    """Alias di :func:`hash_password`."""
    return hash_password(password)


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    """Verifica una password contro un hash PHC. Hash assente o non riconosciuto -> False."""
    return verify_and_update_password(plain_password, hashed_password)[0]


def verify_and_update_password(plain_password: str, hashed_password: str | None) -> tuple[bool, str | None]:
    """Verifica la password e, se i parametri dell'hash sono obsoleti, ne calcola uno nuovo.

    Returns:
        Tupla ``(valida, nuovo_hash)``: ``nuovo_hash`` e' ``None`` se non serve il rehash.
    """
    if not hashed_password:
        return False, None
    try:
        return _password_hash.verify_and_update(plain_password, hashed_password)
    except UnknownHashError:
        return False, None


def validate_password_policy(password: str) -> None:
    """Applica la policy password (lunghezza minima da settings). Solleva ValueError se non valida."""
    min_length = get_settings().password_min_length
    if len(password) < min_length:
        raise MessageError("auth.password_too_short", min_length=min_length)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def _create_token(subject: str | uuid.UUID, token_type: str, expires_delta: timedelta) -> str:
    """Crea un JWT firmato con claim ``sub``, ``type``, ``iat`` ed ``exp`` (UTC)."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {"sub": str(subject), "type": token_type, "iat": now, "exp": now + expires_delta}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(subject: str | uuid.UUID) -> str:
    """Crea un access token per l'utente indicato."""
    return _create_token(subject, TOKEN_TYPE_ACCESS, timedelta(minutes=get_settings().ACCESS_TOKEN_EXPIRE_MINUTES))


def create_refresh_token(subject: str | uuid.UUID) -> str:
    """Crea un JWT di tipo refresh **senza** registrarlo: ``/refresh-token`` lo rifiuta.

    Serve a chi deve solo costruire un token del tipo giusto (test, strumenti). Per un login vero usare
    :func:`issue_refresh_token`, che lo registra e permette rotazione e revoca.
    """
    return _create_token(subject, TOKEN_TYPE_REFRESH, timedelta(days=get_settings().REFRESH_TOKEN_EXPIRE_DAYS))


def _refresh_jwt(subject: str | uuid.UUID, jti: str, family: str) -> str:
    """A refresh JWT that carries its own identity (``jti``) and the login it belongs to (``fam``)."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(subject),
        "type": TOKEN_TYPE_REFRESH,
        "jti": jti,
        "fam": family,
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def issue_refresh_token(session: AsyncSession, user_id: uuid.UUID, *, family: str | None = None) -> str:
    """Issue a refresh token and remember it; ``family`` continues a login (rotation), ``None`` starts a new one.

    Expired records are purged on the way, so the table only holds tokens that could still be presented.
    """
    jti, family = str(uuid.uuid4()), family or str(uuid.uuid4())
    expires_at = utcnow() + timedelta(days=get_settings().REFRESH_TOKEN_EXPIRE_DAYS)
    await session.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.expires_at < utcnow()))
    session.add(RefreshTokenRecord(jti=jti, family=family, user_id=user_id, expires_at=expires_at))
    await session.commit()
    return _refresh_jwt(user_id, jti, family)


async def rotate_refresh_token(session: AsyncSession, payload: dict[str, Any], user_id: uuid.UUID) -> str | None:
    """Spend a refresh token. Returns the next one of the same login (or ``None`` with ``refresh_token_rotation``
    off, where the token stays valid).

    The token is marked used with one conditional ``UPDATE``: of several parallel requests with the same token
    exactly one wins. A token that was already used was presented twice, so it leaked: the whole login is revoked.

    Raises:
        HTTPException: 401 ``auth.refresh_token_revoked`` for an unknown, expired, revoked or reused token.
    """
    revoked = api_error(status.HTTP_401_UNAUTHORIZED, "auth.refresh_token_revoked", loc=("body", "refresh_token"))
    jti, family = payload.get("jti"), payload.get("fam")
    if not isinstance(jti, str) or not isinstance(family, str):  # issued before 0.2: log in again
        raise revoked

    condition = (
        RefreshTokenRecord.jti == jti,
        RefreshTokenRecord.user_id == user_id,
        RefreshTokenRecord.used.is_(False),
        RefreshTokenRecord.expires_at > utcnow(),
    )
    if not get_settings().refresh_token_rotation:
        found = await session.scalar(select(RefreshTokenRecord.jti).where(*condition))
        if found is None:
            raise revoked
        return None

    result = await session.execute(
        update(RefreshTokenRecord).where(*condition).values(used=True).execution_options(synchronize_session=False)
    )
    await session.commit()
    if result.rowcount == 1:
        return await issue_refresh_token(session, user_id, family=family)

    reused = await session.scalar(select(RefreshTokenRecord.jti).where(RefreshTokenRecord.jti == jti))
    if reused is not None:
        # two requests with the same token at the same moment (two tabs): the loser is refused, but the login
        # survives because the winner has just been given the next token of the family
        newest = await session.scalar(
            select(func.max(RefreshTokenRecord.created_at)).where(RefreshTokenRecord.family == family)
        )
        grace = timedelta(seconds=get_settings().refresh_reuse_grace_seconds)
        if newest is not None and utcnow() - as_utc(newest) <= grace:
            raise revoked
        log.warning("Refresh token riusato: login %s revocato (utente %s)", family, user_id)
        await revoke_refresh_family(session, family)
    raise revoked


async def revoke_refresh_family(session: AsyncSession, family: str) -> None:
    """Revoke every refresh token of one login."""
    await session.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.family == family))
    await session.commit()


async def revoke_user_refresh_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Revoke every refresh token of a user (all their logins)."""
    await session.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.user_id == user_id))
    await session.commit()


def _unauthorized(code: str) -> HTTPException:
    """Costruisce una HTTPException 401 (formato uniforme, messaggio localizzato) con header WWW-Authenticate."""
    return api_error(
        status.HTTP_401_UNAUTHORIZED,
        code,
        loc=("header", "Authorization"),
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    """Decodifica e valida un JWT verificando firma, scadenza e claim ``type``.

    Raises:
        HTTPException: 401 se il token non e' valido, scaduto o del tipo sbagliato.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        raise _unauthorized("auth.token_invalid") from None
    if payload.get("type") != expected_type:
        raise _unauthorized("auth.token_wrong_type")
    return payload


def subject_to_user_id(payload: dict[str, Any]) -> uuid.UUID:
    """Estrae l'UUID utente dal claim ``sub``. Solleva 401 se assente o malformato."""
    subject = payload.get("sub")
    if not subject:
        raise _unauthorized("auth.token_no_subject")
    try:
        return uuid.UUID(str(subject))
    except ValueError:
        raise _unauthorized("auth.token_invalid") from None


def decode_access_token(token: str) -> uuid.UUID:
    """Decodifica un access token (``type == "access"``) e ritorna l'id utente."""
    return subject_to_user_id(decode_token(token, TOKEN_TYPE_ACCESS))


def decode_refresh_token(token: str) -> uuid.UUID:
    """Decodifica un refresh token (``type == "refresh"``) e ritorna l'id utente."""
    return subject_to_user_id(decode_token(token, TOKEN_TYPE_REFRESH))


def ensure_token_not_revoked(payload: dict[str, Any], user: User) -> None:
    """Rifiuta i token emessi prima dell'ultimo cambio password dell'utente.

    Il claim ``iat`` ha risoluzione al secondo: un token emesso nello stesso secondo
    del cambio password resta valido.

    Raises:
        HTTPException: 401 se il token e' stato emesso prima di ``password_changed_at``.
    """
    changed_at = as_utc(user.password_changed_at)
    if changed_at is None:
        return
    issued_at = payload.get("iat")
    if not isinstance(issued_at, (int, float)) or issued_at < int(changed_at.timestamp()):
        raise _unauthorized("auth.token_revoked")


def decode_access_payload(token: str) -> dict[str, Any]:
    """Decodifica un access token e ritorna il payload completo."""
    payload = decode_token(token, TOKEN_TYPE_ACCESS)
    subject_to_user_id(payload)
    return payload


def decode_refresh_payload(token: str) -> dict[str, Any]:
    """Decodifica un refresh token e ritorna il payload completo."""
    payload = decode_token(token, TOKEN_TYPE_REFRESH)
    subject_to_user_id(payload)
    return payload


# ---------------------------------------------------------------------------
# Autenticazione
# ---------------------------------------------------------------------------


def is_user_locked(user: User, now: datetime | None = None) -> bool:
    """True se l'account e' temporaneamente bloccato (``locked_until`` nel futuro)."""
    locked_until = as_utc(user.locked_until)
    return locked_until is not None and locked_until > (now or utcnow())


async def reserve_login_attempt(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Count one attempt against the account *before* the secret is checked; ``False`` if none is left.

    One atomic ``UPDATE ... RETURNING``: parallel guesses cannot all slip in before the lockout takes effect,
    because each of them has to reserve its own place first. Beyond ``login_max_attempts`` the account is locked.
    """
    result = await session.execute(
        update(User)
        .where(User.id == user_id, or_(User.locked_until.is_(None), User.locked_until <= utcnow()))
        .values(failed_login_count=User.failed_login_count + 1)
        .returning(User.failed_login_count)
        .execution_options(synchronize_session=False)
    )
    count = result.scalar_one_or_none()
    await session.commit()
    if count is None:  # the account is locked: refused in the same statement that would have counted the attempt
        return False
    if count > get_settings().login_max_attempts:
        await lock_if_exhausted(session, user_id)
        return False
    return True


async def lock_if_exhausted(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """Lock the account for ``login_lockout_minutes`` once its attempts are used up; resets the counter."""
    settings = get_settings()
    locked_until = utcnow() + timedelta(minutes=settings.login_lockout_minutes)
    result = await session.execute(
        update(User)
        .where(User.id == user_id, User.failed_login_count >= settings.login_max_attempts)
        .values(locked_until=locked_until, failed_login_count=0)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    if result.rowcount:
        log.warning("Account %s bloccato fino a %s", user_id, locked_until)
    return bool(result.rowcount)


def retry_after_seconds(user: User, now: datetime | None = None) -> int:
    """Whole seconds until the account is unlocked (at least 1), for a ``Retry-After`` header."""
    locked_until = as_utc(user.locked_until)
    if locked_until is None:
        return 1
    return max(1, math.ceil((locked_until - (now or utcnow())).total_seconds()))


def locked_error(user: User, loc: tuple[str, ...] = ("body",)) -> HTTPException:
    """429 for a locked account with ``Retry-After`` (seconds left), for callers that are already authenticated.

    The login itself never uses it: it answers a uniform ``401`` so that a lock does not reveal the account.
    """
    return api_error(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "auth.too_many_attempts",
        loc=loc,
        headers={"Retry-After": str(retry_after_seconds(user))},
    )


async def release_login_attempt(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Give back a reserved attempt (the password was right but the login is not finished: MFA pending)."""
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(failed_login_count=case((User.failed_login_count > 0, User.failed_login_count - 1), else_=0))
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def clear_login_failures(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Reset the counter and the lock after a login that is completely done."""
    await session.execute(
        update(User)
        .where(User.id == user_id)
        .values(failed_login_count=0, locked_until=None)
        .execution_options(synchronize_session=False)
    )
    await session.commit()


async def authenticate_user(
    session: AsyncSession, username: str, password: str, *, clear_failures: bool = True
) -> User | None:
    """Autentica un utente con username e password.

    - Restituisce ``None`` per credenziali errate, utente inattivo o account bloccato
      (risposta uniforme, per non rivelare l'esistenza dell'account).
    - Ogni tentativo viene contato prima della verifica (:func:`reserve_login_attempt`): dopo
      ``login_max_attempts`` tentativi senza successo l'account e' bloccato per ``login_lockout_minutes``
      minuti, anche con richieste parallele.
    - Se l'hash usa parametri obsoleti lo aggiorna in modo trasparente (rehash).
    - Non aggiorna ``last_login_at``: vedi :func:`record_login`.
    - Con ``clear_failures=False`` il tentativo resta contato e il chiamante deve chiudere il login con
      :func:`clear_login_failures` oppure, se manca un secondo fattore, :func:`release_login_attempt`.
    """
    user = await get_user_by_username(session, username)
    if user is None:
        await _hash_cost(password, _DUMMY_HASH)
        return None

    if is_user_locked(user):
        # same cost as a real check: a locked account must not answer faster than an unknown one
        await _hash_cost(password, _DUMMY_HASH)
        return None

    if not await reserve_login_attempt(session, user.id):
        await _hash_cost(password, _DUMMY_HASH)
        return None

    valid, new_hash = await run_in_threadpool(verify_and_update_password, password, user.password_hash)
    if not valid:
        await lock_if_exhausted(session, user.id)
        return None

    # the hash took a while: parallel failures may have locked the account meanwhile
    await session.refresh(user)
    if is_user_locked(user):
        return None

    if new_hash:
        user.password_hash = new_hash
        await session.commit()
    if clear_failures:
        await clear_login_failures(session, user.id)
        await session.refresh(user)

    if not user.is_active:
        return None
    return user


async def check_current_password(session: AsyncSession, user: User, password: str) -> bool:
    """Check the password of an already authenticated user for a sensitive action (change it, switch MFA off).

    Counted like a login: a stolen access token cannot be used to guess the password without limit. A wrong
    password uses up an attempt (and can lock the account); a right one clears them.
    """
    if not await reserve_login_attempt(session, user.id):
        await _hash_cost(password, _DUMMY_HASH)
        return False
    valid, _ = await run_in_threadpool(verify_and_update_password, password, user.password_hash)
    if not valid:
        await lock_if_exhausted(session, user.id)
        return False
    await clear_login_failures(session, user.id)
    return True


def refusal_status(exc: ValueError) -> int:
    """HTTP status for a refused write: ``422`` when the password does not meet the policy, ``400`` otherwise.

    Registration, the admin routes and the change of password answer the same for a weak password.
    """
    code = getattr(exc, "code", "")
    return 422 if isinstance(code, str) and code.startswith("auth.password_") else 400


async def burn_hash_time(password: str) -> None:
    """Spend the time of one password hash, so that a branch that skips the hash answers as slowly as the others."""
    await _hash_cost(password, _DUMMY_HASH)


async def _hash_cost(password: str, hashed: str) -> None:
    """Spend the time of one password check without a result (keeps the answers equally slow)."""
    await run_in_threadpool(_password_hash.verify_and_update, password, hashed)


async def record_login(session: AsyncSession, user: User) -> None:
    """Aggiorna ``last_login_at`` (UTC) dopo un login completato con successo."""
    user.last_login_at = utcnow()
    await session.commit()
    await signals.emit(signals.USER_LOGGED_IN, user=user)
