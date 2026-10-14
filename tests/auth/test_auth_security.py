"""Test di password (Argon2id), JWT, lockout e last_login."""

import time
import uuid
from datetime import timedelta

import jwt
import pytest
from fastapi import HTTPException
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher
from sqlalchemy.ext.asyncio import AsyncSession

from pachifast.auth import cruds, security
from pachifast.auth.schemas import RefreshToken, UserCreate
from pachifast.config import PachifastSettings, get_settings
from pachifast.database.utils import utcnow

PASSWORD = "correct-horse-battery"


async def _make_user(session: AsyncSession, username: str = "Alice", **kwargs):
    """Crea un utente di test."""
    return await cruds.create_user(session, UserCreate(username=username, password=PASSWORD, **kwargs))


# --- password ---------------------------------------------------------------


def test_hash_is_argon2id_phc() -> None:
    h = security.hash_password(PASSWORD)
    assert h.startswith("$argon2id$")
    assert "m=19456,t=2,p=1" in h
    assert security.verify_password(PASSWORD, h)
    assert not security.verify_password("wrong", h)
    assert security.hash_password(PASSWORD) != h  # salt casuale


def test_legacy_or_garbage_hashes_are_rejected() -> None:
    assert not security.verify_password(PASSWORD, "legacy_sha256$1$salt$digest")
    assert not security.verify_password(PASSWORD, "")
    assert not security.verify_password(PASSWORD, None)


def test_verify_and_update_requests_rehash_for_weak_params() -> None:
    weak = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),)).hash(PASSWORD)
    ok, new_hash = security.verify_and_update_password(PASSWORD, weak)
    assert ok and new_hash and "m=19456,t=2,p=1" in new_hash
    ok, new_hash = security.verify_and_update_password(PASSWORD, security.hash_password(PASSWORD))
    assert ok and new_hash is None


def test_rehash_on_login(run) -> None:
    async def scenario(session: AsyncSession) -> str:
        user = await _make_user(session)
        user.password_hash = PasswordHash((Argon2Hasher(time_cost=1, memory_cost=8, parallelism=1),)).hash(PASSWORD)
        await session.commit()
        logged = await security.authenticate_user(session, "alice", PASSWORD)
        assert logged is not None
        return logged.password_hash

    assert "m=19456,t=2,p=1" in run(scenario)


# --- utenti -----------------------------------------------------------------


def test_create_user_normalizes_and_rejects_duplicates(run) -> None:
    async def scenario(session: AsyncSession) -> None:
        user = await _make_user(session, "MixedCase", email="Foo@Example.com")
        assert user.username == "mixedcase"
        assert user.email == "foo@example.com"
        assert isinstance(user.id, uuid.UUID)
        assert user.created_at.tzinfo is not None
        assert user.password_hash and user.password_hash.startswith("$argon2id$")
        with pytest.raises(ValueError):
            await _make_user(session, "MIXEDCASE")
        with pytest.raises(ValueError):
            await cruds.create_user(session, UserCreate(username="short-pw", password="abc"))

    run(scenario)


# --- JWT --------------------------------------------------------------------


def test_access_and_refresh_tokens_are_not_interchangeable() -> None:
    uid = uuid.uuid4()
    access = security.create_access_token(uid)
    refresh = security.create_refresh_token(uid)
    assert security.decode_access_token(access) == uid
    assert security.decode_refresh_token(refresh) == uid
    with pytest.raises(HTTPException) as exc:
        security.decode_access_token(refresh)
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException):
        security.decode_refresh_token(access)


def test_invalid_tokens_are_rejected() -> None:
    settings = get_settings()
    now = int(time.time())
    expired = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "exp": now - 10}, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    forged = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "access", "exp": now + 60}, "x" * 40, algorithm=settings.ALGORITHM
    )
    bad_sub = jwt.encode(
        {"sub": "not-a-uuid", "type": "access", "exp": now + 60}, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    for token in (expired, forged, bad_sub, "garbage"):
        with pytest.raises(HTTPException) as exc:
            security.decode_access_token(token)
        assert exc.value.status_code == 401


def test_refresh_endpoint_rejects_access_token_and_inactive_user(run) -> None:
    from pachifast.auth.root import refresh_access_token

    async def scenario(session: AsyncSession) -> None:
        user = await _make_user(session)
        issued = await security.issue_refresh_token(session, user.id)
        good = await refresh_access_token(RefreshToken(refresh_token=issued), session)
        assert security.decode_access_token(good.access_token) == user.id

        with pytest.raises(HTTPException):
            await refresh_access_token(RefreshToken(refresh_token=security.create_access_token(user.id)), session)

        user.is_active = False
        await session.commit()
        with pytest.raises(HTTPException):
            await refresh_access_token(RefreshToken(refresh_token=good.refresh_token), session)

        with pytest.raises(HTTPException):
            await refresh_access_token(RefreshToken(refresh_token=security.create_refresh_token(uuid.uuid4())), session)

    run(scenario)


# --- login / lockout --------------------------------------------------------


def test_last_login_updated_only_on_record_login(run) -> None:
    async def scenario(session: AsyncSession) -> None:
        user = await _make_user(session)
        assert user.last_login_at is None
        logged = await security.authenticate_user(session, "ALICE", PASSWORD)
        assert logged is not None
        await security.record_login(session, logged)
        await session.refresh(logged)
        assert logged.last_login_at is not None and logged.last_login_at.tzinfo is not None

    run(scenario)


def test_lockout_after_max_attempts(run) -> None:
    max_attempts = get_settings().login_max_attempts
    assert max_attempts == 5

    async def scenario(session: AsyncSession) -> None:
        user = await _make_user(session)
        for _ in range(max_attempts):
            assert await security.authenticate_user(session, "alice", "wrong") is None
        await session.refresh(user)
        assert user.locked_until is not None and user.locked_until > utcnow()
        # Anche la password giusta e' rifiutata mentre l'account e' bloccato.
        assert await security.authenticate_user(session, "alice", PASSWORD) is None

        # Scaduto il blocco il login torna possibile e il contatore si azzera.
        user.locked_until = utcnow() - timedelta(seconds=1)
        await session.commit()
        assert await security.authenticate_user(session, "alice", PASSWORD) is not None
        await session.refresh(user)
        assert user.failed_login_count == 0 and user.locked_until is None

    run(scenario)


def test_inactive_user_cannot_login(run) -> None:
    async def scenario(session: AsyncSession) -> None:
        await _make_user(session, is_active=False)
        assert await security.authenticate_user(session, "alice", PASSWORD) is None

    run(scenario)


def test_unknown_user_returns_none(run) -> None:
    async def scenario(session: AsyncSession) -> None:
        assert await security.authenticate_user(session, "nobody", PASSWORD) is None

    run(scenario)


# --- settings ---------------------------------------------------------------


def test_secret_key_is_required() -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        PachifastSettings(_env_file=None, SECRET_KEY="")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="SECRET_KEY"):
        PachifastSettings(_env_file=None, SECRET_KEY="short")  # type: ignore[call-arg]


def test_table_prefix_and_overrides() -> None:
    s = PachifastSettings(_env_file=None, SECRET_KEY="k" * 40, table_prefix="app_", group_table="custom_group")  # type: ignore[call-arg]
    assert s.get_table_name("user") == "app_user"
    assert s.get_table_name("activity_log") == "app_activity_log"
    assert s.get_table_name("group") == "custom_group"
    assert get_settings().get_table_name("user") == "pachifast_user"


def test_tokens_issued_before_password_change_are_revoked(run) -> None:
    """Dopo un cambio password i vecchi token (iat precedente) vengono rifiutati."""

    async def scenario(session: AsyncSession) -> None:
        user = await _make_user(session)
        settings = get_settings()
        old_iat = int(time.time()) - 100
        old_token = jwt.encode(
            {"sub": str(user.id), "type": "access", "iat": old_iat, "exp": old_iat + 3600},
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )
        # Prima del cambio password il token e' valido (password_changed_at = creazione, dopo old_iat):
        # simulo l'utente storico senza cambio password.
        user.password_changed_at = None
        security.ensure_token_not_revoked(security.decode_access_payload(old_token), user)

        await cruds.update_user(session, user, password="another-long-password")
        with pytest.raises(HTTPException) as exc:
            security.ensure_token_not_revoked(security.decode_access_payload(old_token), user)
        assert exc.value.status_code == 401

        fresh = security.create_access_token(user.id)
        security.ensure_token_not_revoked(security.decode_access_payload(fresh), user)

    run(scenario)
