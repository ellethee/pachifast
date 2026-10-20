"""Fixes from the independent security review (ASVS 4.0.3 level 1): one test per confirmed finding."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, timedelta

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pyotp
import pytest
from fastapi import Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pachifast import create_app
from pachifast.audit import models as audit_models  # noqa: F401
from pachifast.auth import cruds, models  # noqa: F401
from pachifast.auth.schemas import UserCreate
from pachifast.auth.security import create_access_token
from pachifast.config import get_settings
from pachifast.database import Base
from pachifast.mfa import MfaModule, MfaSenderAdapter
from pachifast.mfa import models as mfa_models  # noqa: F401
from pachifast.presets import core_module

PASSWORD = "long-enough-pass-1"


@dataclass
class Env:
    """A running app on SQLite with a superuser and two regular users."""

    build: Callable[..., TestClient]
    url: str
    ids: dict[str, object]

    def token(self, name: str) -> dict[str, str]:
        """Authorization header of ``name``."""
        return {"Authorization": f"Bearer {create_access_token(self.ids[name])}"}

    def run(self, work: Callable[[AsyncSession], object]) -> object:
        """Run ``work(session)`` against the test database."""

        async def main() -> object:
            engine = create_async_engine(self.url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    return await work(session)  # type: ignore[misc]
            finally:
                await engine.dispose()

        return asyncio.run(main())


POSTGRES_ADMIN_URL = os.environ.get("PACHIFAST_TEST_POSTGRES_URL", "")


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not POSTGRES_ADMIN_URL, reason="PACHIFAST_TEST_POSTGRES_URL is not set"),
        ),
    ]
)
def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[Env]:
    """The app on SQLite and, when ``PACHIFAST_TEST_POSTGRES_URL`` is set, on a throwaway PostgreSQL database."""
    database = None
    if request.param == "sqlite":
        url = f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}"
    else:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        admin = make_url(POSTGRES_ADMIN_URL).set(drivername="postgresql+psycopg")
        database = f"pf_sec_{uuid.uuid4().hex[:10]}"
        admin_engine = create_engine(admin, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database}"'))
        url = admin.set(drivername="postgresql+asyncpg", database=database).render_as_string(False)
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")
    monkeypatch.setenv("PACHIFAST_MFA_ENABLED", "true")
    monkeypatch.setenv("PACHIFAST_ACTIVITY_LOG_ENABLED", "false")
    ids: dict[str, object] = {}

    async def prepare() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for name, superuser in (("boss", True), ("ann", False), ("bob", False)):
                user = await cruds.create_user(
                    session, UserCreate(username=name, password=PASSWORD, is_superuser=superuser)
                )
                ids[name] = user.id
        await engine.dispose()

    asyncio.run(prepare())

    clients: list[TestClient] = []

    def build(client: tuple[str, int] | None = None, **settings: str) -> TestClient:
        """A client whose requests share one event loop (asyncpg connections cannot move between loops)."""
        from pachifast.database import engine as engine_module

        for key, value in settings.items():
            monkeypatch.setenv(f"PACHIFAST_{key.upper()}", value)
        get_settings.cache_clear()
        engine_module._engine_cache.clear()
        engine_module._sessionmaker_cache.clear()
        kwargs = {"client": client} if client else {}
        test_client = TestClient(create_app((core_module, MfaModule)), **kwargs)
        test_client.__enter__()
        clients.append(test_client)
        return test_client

    yield Env(build, url, ids)
    for test_client in clients:
        test_client.__exit__(None, None, None)
    get_settings.cache_clear()
    if database:
        with admin_engine.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)'))
        admin_engine.dispose()


# --- defaults -----------------------------------------------------------------


def test_safer_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """ASVS 2.1.1 asks for 12 characters; access tokens stay out of WebSocket URLs unless asked for."""
    for name in ("PACHIFAST_PASSWORD_MIN_LENGTH", "PACHIFAST_EVENTS_ALLOW_QUERY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.password_min_length == 12
    assert settings.events_allow_query_token is False
    get_settings.cache_clear()


# --- finding 6: confirming TOTP must not answer 500 ------------------------------


def test_totp_setup_and_confirm_enable_mfa_and_answer_ok(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    setup = client.post("/api/mfa/totp/setup", headers=headers)
    assert setup.status_code == 200, setup.text
    secret = (setup.json().get("data") or setup.json())["secret"]
    confirm = client.post("/api/mfa/totp/confirm", json={"code": pyotp.TOTP(secret).now()}, headers=headers)
    assert confirm.status_code == 200, confirm.text
    assert (confirm.json().get("data") or confirm.json())["ok"] is True


# --- finding 3: swagger_noauth must not trust a Referer header --------------------


@pytest.mark.parametrize(
    ("peer", "referer", "expected"),
    [
        (("127.0.0.1", 50000), "http://localhost:8000/docs", 200),
        (("127.0.0.1", 50000), "http://localhost:8000/docs/core", 200),
        (("203.0.113.9", 50000), "http://localhost:8000/docs", 401),  # a remote client cannot use it
        (("127.0.0.1", 50000), "http://evil.example/page?next=/docs", 401),  # "/docs" outside the path
        (("127.0.0.1", 50000), "", 401),
    ],
)
def test_swagger_noauth_needs_a_local_client_and_the_docs_page(env: Env, peer, referer, expected) -> None:
    client = env.build(client=peer, swagger_noauth="true")
    headers = {"Referer": referer} if referer else {}
    assert client.get("/api/auth/user", headers=headers).status_code == expected


def test_swagger_noauth_refuses_a_request_that_went_through_a_proxy(env: Env) -> None:
    client = env.build(client=("127.0.0.1", 50000), swagger_noauth="true")
    headers = {"Referer": "http://localhost:8000/docs", "X-Forwarded-For": "203.0.113.9"}
    assert client.get("/api/auth/user", headers=headers).status_code == 401


def test_swagger_noauth_is_off_by_default(env: Env) -> None:
    client = env.build(client=("127.0.0.1", 50000))
    assert client.get("/api/auth/user", headers={"Referer": "http://localhost:8000/docs"}).status_code == 401


# --- finding 8: the optional-user dependency applies the same checks ---------------


def test_optional_user_rejects_inactive_users_and_tokens_issued_before_a_password_change(env: Env) -> None:
    from pachifast.auth.dependencies import get_current_user_optional
    from pachifast.auth.security import create_access_token as make

    app = FastAPI()

    @app.get("/who")
    async def who(user=Depends(get_current_user_optional)) -> dict[str, str | None]:
        return {"user": user.username if user else None}

    from pachifast.database.engine import get_core_session

    async def session_override():
        engine = create_async_engine(env.url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                yield session
        finally:
            await engine.dispose()

    app.dependency_overrides[get_core_session] = session_override
    client = TestClient(app)
    ann, bob = make(env.ids["ann"]), make(env.ids["bob"])
    assert client.get("/who", headers={"Authorization": f"Bearer {ann}"}).json() == {"user": "ann"}

    async def deactivate(session: AsyncSession) -> None:
        user = await cruds.get_user_by_id(session, env.ids["ann"])
        user.is_active = False
        await session.commit()

    env.run(deactivate)
    assert client.get("/who", headers={"Authorization": f"Bearer {ann}"}).json() == {"user": None}

    async def change_password(session: AsyncSession) -> None:
        from pachifast.auth.security import utcnow

        user = await cruds.get_user_by_id(session, env.ids["bob"])
        user.password_changed_at = utcnow() + timedelta(seconds=5)
        await session.commit()

    env.run(change_password)
    assert client.get("/who", headers={"Authorization": f"Bearer {bob}"}).json() == {"user": None}
    assert client.get("/who").json() == {"user": None}


# --- finding 12: the WebSocket dependency honours events_allow_query_token --------


def _ws_app(env: Env) -> TestClient:
    from pachifast.auth.dependencies import get_current_active_ws_user

    client = env.build()

    @client.app.websocket("/ws-auth")  # type: ignore[attr-defined]
    async def ws_auth(ws: WebSocket, user=Depends(get_current_active_ws_user)) -> None:
        await ws.accept()
        await ws.send_json({"username": user.username})
        await ws.close()

    return client


def test_ws_dependency_ignores_the_query_token_unless_enabled(env: Env) -> None:
    token = env.token("ann")["Authorization"].split()[1]
    client = _ws_app(env)
    with pytest.raises(Exception):  # noqa: B017 - the handshake is refused
        with client.websocket_connect(f"/ws-auth?token={token}"):
            pass
    with client.websocket_connect("/ws-auth", headers={"Authorization": f"Bearer {token}"}) as ws:
        assert ws.receive_json() == {"username": "ann"}


def test_ws_dependency_accepts_the_query_token_when_enabled(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACHIFAST_EVENTS_ALLOW_QUERY_TOKEN", "true")
    token = env.token("ann")["Authorization"].split()[1]
    client = _ws_app(env)
    with client.websocket_connect(f"/ws-auth?token={token}") as ws:
        assert ws.receive_json() == {"username": "ann"}


# --- finding 11: orderBy only sorts by columns that may be shown ---------------------


@pytest.mark.parametrize(
    "field", ["password_hash", "passwordHash", "metadata", "registry", "__table__", "failed_login_count"]
)
def test_admin_user_list_refuses_to_sort_by_hidden_or_non_column_attributes(env: Env, field: str) -> None:
    client = env.build()
    response = client.get(f"/api/admin/user?orderBy=-{field}", headers=env.token("boss"))
    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["code"] == "api.invalid_order_by"


def test_admin_user_list_still_sorts_by_the_allowed_columns(env: Env) -> None:
    client = env.build()
    response = client.get("/api/admin/user?orderBy=-createdAt,username", headers=env.token("boss"))
    assert response.status_code == 200


# --- finding 10: a locked account costs the same as an unknown one -------------------


def test_a_locked_account_still_runs_a_password_hash(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    from pachifast.auth import security

    calls: list[str] = []
    real = security._password_hash.verify_and_update

    def spy(password: str, hashed: str):
        calls.append(hashed)
        return real(password, hashed)

    monkeypatch.setattr(security._password_hash, "verify_and_update", spy)

    async def lock(session: AsyncSession) -> None:
        user = await cruds.get_user_by_id(session, env.ids["ann"])
        user.locked_until = security.utcnow() + timedelta(minutes=5)
        await session.commit()

    env.run(lock)

    async def attempt(session: AsyncSession) -> object:
        return await security.authenticate_user(session, "ann", PASSWORD)

    assert env.run(attempt) is None
    assert len(calls) == 1


# --- the MFA routes (they were never mounted) and the findings on them ---------------


def _data(response):
    """The body without the envelope (the MFA routes are enveloped)."""
    body = response.json()
    return body["data"] if isinstance(body, dict) and "data" in body and "success" in body else body


def _login(client: TestClient, name: str, password: str = PASSWORD):
    return client.post("/api/auth/token", data={"username": name, "password": password})


def _enrol_totp(client: TestClient, headers: dict[str, str]) -> tuple[str, list[str]]:
    """Set TOTP up and confirm it; returns the secret and the backup codes."""
    setup = _data(client.post("/api/mfa/totp/setup", headers=headers))
    code = pyotp.TOTP(setup["secret"]).now()
    assert client.post("/api/mfa/totp/confirm", json={"code": code}, headers=headers).status_code == 200
    return setup["secret"], setup["backup_codes"]


def _next_code(secret: str, steps: int = 1) -> str:
    """A code from a later 30-second step: valid through the clock-drift window and not yet used."""
    import time

    return pyotp.TOTP(secret).at(time.time() + 30 * steps)


def test_the_mfa_routes_come_with_the_mfa_module(env: Env) -> None:
    """``MfaModule`` mounts ``/api/mfa``; the core module alone does not."""
    assert env.build().get("/api/mfa/status", headers=env.token("ann")).status_code == 200
    core_only = TestClient(create_app((core_module,)))
    assert core_only.get("/api/mfa/status", headers=env.token("ann")).status_code == 404


def test_login_with_totp_end_to_end(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    secret, _ = _enrol_totp(client, headers)

    first = _login(client, "ann")
    assert first.status_code == 200 and first.json()["mfa_required"] is True
    assert "access_token" not in first.json()
    mfa_token = first.json()["mfa_token"]

    wrong = client.post("/api/mfa/verify", json={"mfa_token": mfa_token, "code": "000000"})
    assert wrong.status_code == 401 and wrong.json()["detail"][0]["code"] == "mfa.code_invalid"

    code = _next_code(secret)
    done = client.post("/api/mfa/verify", json={"mfa_token": mfa_token, "code": code})
    assert done.status_code == 200, done.text
    assert {"access_token", "token_type", "refresh_token"} <= set(done.json())  # like /auth/token: no envelope

    # the same code cannot be replayed on a new login (finding 7)
    again = client.post("/api/mfa/verify", json={"mfa_token": _login(client, "ann").json()["mfa_token"], "code": code})
    assert again.status_code == 401


def test_a_backup_code_works_once(env: Env) -> None:
    client = env.build()
    secret, backups = _enrol_totp(client, env.token("ann"))
    del secret
    token = _login(client, "ann").json()["mfa_token"]
    assert client.post("/api/mfa/verify", json={"mfa_token": token, "code": backups[0]}).status_code == 200
    token = _login(client, "ann").json()["mfa_token"]
    reused = client.post("/api/mfa/verify", json={"mfa_token": token, "code": backups[0]})
    assert reused.status_code == 401
    other = client.post("/api/mfa/verify", json={"mfa_token": token, "code": backups[1]})
    assert other.status_code == 200


@pytest.mark.parametrize(
    ("route", "body"),
    [("totp/setup", None), ("email/setup", None), ("sms/setup", {"phone_number": "+390000000"})],
)
def test_enrolling_again_cannot_replace_an_active_second_factor(env: Env, route: str, body) -> None:
    """A stolen access token must not be able to switch MFA off or move it to another device (finding 4)."""
    client = env.build()
    headers = env.token("ann")
    secret, _ = _enrol_totp(client, headers)

    refused = (
        client.post(f"/api/mfa/{route}", json=body, headers=headers)
        if body
        else client.post(f"/api/mfa/{route}", headers=headers)
    )
    assert refused.status_code == 409 and refused.json()["detail"][0]["code"] == "mfa.already_enabled"
    assert _data(client.get("/api/mfa/status", headers=headers))["enabled"] is True
    assert _login(client, "ann").json()["mfa_required"] is True

    # the documented way: disable with the password, then enrol again
    assert client.post("/api/mfa/disable", json={"password": PASSWORD}, headers=headers).status_code == 200
    assert client.post("/api/mfa/totp/setup", headers=headers).status_code == 200
    del secret


def test_wrong_mfa_codes_across_logins_lock_the_account(env: Env) -> None:
    """Logging in again gives a fresh challenge, but the failures add up on the account (finding 1)."""
    client = env.build()
    _enrol_totp(client, env.token("ann"))
    limit = get_settings().login_max_attempts

    for _ in range(limit):
        token = _login(client, "ann").json()["mfa_token"]
        wrong = client.post("/api/mfa/verify", json={"mfa_token": token, "code": "000000"})
        assert wrong.status_code in (401, 429)

    assert _login(client, "ann").status_code == 401  # locked, even with the right password
    assert client.get("/api/auth/user", headers=env.token("ann")).status_code == 200  # tokens are another matter


def test_a_good_second_factor_gives_the_account_a_clean_slate(env: Env) -> None:
    client = env.build()
    secret, _ = _enrol_totp(client, env.token("ann"))
    for _ in range(2):
        token = _login(client, "ann").json()["mfa_token"]
        client.post("/api/mfa/verify", json={"mfa_token": token, "code": "000000"})
    token = _login(client, "ann").json()["mfa_token"]
    assert client.post("/api/mfa/verify", json={"mfa_token": token, "code": _next_code(secret)}).status_code == 200

    async def counter(session: AsyncSession) -> int:
        return (await cruds.get_user_by_id(session, env.ids["ann"])).failed_login_count

    assert env.run(counter) == 0


# --- finding 1 and 2: the limits hold under parallel requests ------------------------


def test_parallel_guesses_cannot_spend_more_attempts_than_the_limit(env: Env) -> None:
    from pachifast.mfa import cruds as mfa_cruds

    client = env.build()
    _enrol_totp(client, env.token("ann"))
    token = _login(client, "ann").json()["mfa_token"]
    from pachifast.mfa.security import verify_mfa_pending_token

    jti = verify_mfa_pending_token(token, get_settings())["jti"]

    async def spend_all() -> int:
        engine = create_async_engine(env.url, connect_args={"timeout": 30})
        try:
            challenge = await _challenge(engine, jti)

            async def one() -> bool:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    return await mfa_cruds.consume_challenge_attempt(session, challenge, 5)

            return sum(await asyncio.gather(*(one() for _ in range(20))))
        finally:
            await engine.dispose()

    assert asyncio.run(spend_all()) == 5


async def _challenge(engine, jti: str) -> int:
    from pachifast.mfa.cruds import get_pending_challenge_by_jti

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        challenge = await get_pending_challenge_by_jti(session, jti)
        assert challenge is not None
        return challenge.id


def test_parallel_password_guesses_are_limited_before_they_are_checked(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each guess reserves its place first, so a burst cannot run more password checks than the limit allows."""
    from pachifast.auth import security

    checks: list[int] = []
    real = security.verify_and_update_password

    def counting(password: str, hashed: str | None):
        checks.append(1)
        return real(password, hashed)

    monkeypatch.setattr(security, "verify_and_update_password", counting)
    limit = get_settings().login_max_attempts

    async def burst() -> list[object]:
        engine = create_async_engine(env.url, connect_args={"timeout": 30})
        try:

            async def attempt(password: str) -> object:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    return await security.authenticate_user(session, "ann", password)

            guesses = [f"wrong-{i}" for i in range(2 * limit + 8)]
            burst_results = list(await asyncio.gather(*(attempt(g) for g in guesses)))
            # a right password does clear the failures, so it must not take part in the race: it comes after
            return [*burst_results, await attempt(PASSWORD)]

        finally:
            await engine.dispose()

    results = asyncio.run(burst())
    assert len(checks) <= limit, f"{len(checks)} password checks ran for a limit of {limit}"
    assert all(result is None for result in results)  # the account is locked: not even the right password gets in


# --- finding 9 and 13: changing the password, and no uncounted password oracle -------


def _old_token(user_id: object, seconds_ago: int = 60) -> dict[str, str]:
    """An access token issued ``seconds_ago`` seconds ago (before a later password change)."""
    from datetime import datetime

    import jwt

    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "type": "access",
        "iat": now - timedelta(seconds=seconds_ago),
        "exp": now + timedelta(minutes=30),
    }
    return {"Authorization": f"Bearer {jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)}"}


def test_a_user_changes_their_own_password(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")

    async def age_the_account(session: AsyncSession) -> None:
        from pachifast.auth.security import utcnow

        user = await cruds.get_user_by_id(session, env.ids["ann"])
        user.password_changed_at = utcnow() - timedelta(minutes=10)
        await session.commit()

    env.run(age_the_account)
    old = _old_token(env.ids["ann"], seconds_ago=300)
    assert client.get("/api/auth/user", headers=old).status_code == 200

    new = "another-long-password-2"
    wrong = client.post(
        "/api/auth/user/password", json={"currentPassword": "not-the-password-x", "newPassword": new}, headers=headers
    )
    assert wrong.status_code == 400 and wrong.json()["detail"][0]["code"] == "auth.password_incorrect"

    short = client.post(
        "/api/auth/user/password", json={"currentPassword": PASSWORD, "newPassword": "short"}, headers=headers
    )
    assert short.status_code == 422 and short.json()["detail"][0]["code"] == "auth.password_too_short"

    done = client.post(
        "/api/auth/user/password", json={"currentPassword": PASSWORD, "newPassword": new}, headers=headers
    )
    assert done.status_code == 204 and done.content == b""

    assert _login(client, "ann", PASSWORD).status_code == 401
    assert _login(client, "ann", new).status_code == 200
    assert client.get("/api/auth/user", headers=old).status_code == 401  # tokens from before the change are revoked


def test_changing_the_password_needs_authentication(env: Env) -> None:
    client = env.build()
    response = client.post("/api/auth/user/password", json={"currentPassword": PASSWORD, "newPassword": "x" * 20})
    assert response.status_code == 401


def test_the_current_password_check_cannot_be_used_to_guess_without_limit(env: Env) -> None:
    """Change-password, MFA disable and backup-code regeneration share the login counter and its lockout."""
    client = env.build()
    headers = env.token("ann")
    _enrol_totp(client, headers)
    limit = get_settings().login_max_attempts

    for attempt in range(limit):
        route = ("/api/mfa/disable", "/api/mfa/backup-codes/regenerate", "/api/auth/user/password")[attempt % 3]
        body = (
            {"currentPassword": "wrong-password-xx", "newPassword": "z" * 20}
            if "password" in route
            else {"password": "wrong-password-xx"}
        )
        assert client.post(route, json=body, headers=headers).status_code == 400

    # locked: even the right password is refused now, with a countdown for the authenticated routes
    for route, body in (
        ("/api/mfa/disable", {"password": PASSWORD}),
        ("/api/mfa/backup-codes/regenerate", {"password": PASSWORD}),
        ("/api/auth/user/password", {"currentPassword": PASSWORD, "newPassword": "z" * 20}),
    ):
        response = client.post(route, json=body, headers=headers)
        assert response.status_code == 429, route
        assert response.json()["detail"][0]["code"].endswith(".too_many_attempts")
        assert int(response.headers["Retry-After"]) >= 1
    assert _login(client, "ann").status_code == 401


# --- finding 5: refresh tokens are single use, and logout revokes -----------------------


def _tokens(response) -> tuple[str, str]:
    body = response.json()
    return body["access_token"], body["refresh_token"]


def _refresh(client: TestClient, refresh_token: str):
    return client.post("/api/auth/refresh-token", json={"refresh_token": refresh_token})


def test_a_refresh_returns_a_new_single_use_refresh_token(env: Env) -> None:
    client = env.build()
    _, first = _tokens(_login(client, "ann"))

    second = _refresh(client, first)
    assert second.status_code == 200, second.text
    access, rotated = _tokens(second)
    assert rotated != first
    assert client.get("/api/auth/user", headers={"Authorization": f"Bearer {access}"}).status_code == 200

    # the new one works, the old one does not
    assert _refresh(client, rotated).status_code == 200


def test_a_second_use_inside_the_grace_window_does_not_revoke_the_login(env: Env) -> None:
    client = env.build()
    _, first = _tokens(_login(client, "ann"))
    _, second = _tokens(_refresh(client, first))

    reused = _refresh(client, first)
    assert reused.status_code == 401 and reused.json()["detail"][0]["code"] == "auth.refresh_token_revoked"
    # inside the grace window (two tabs refreshing together) the login survives
    assert _refresh(client, second).status_code == 200


def test_presenting_a_used_refresh_token_again_revokes_the_whole_login(env: Env) -> None:
    client = env.build(refresh_reuse_grace_seconds="0")
    _, first = _tokens(_login(client, "ann"))
    _, second = _tokens(_refresh(client, first))

    reused = _refresh(client, first)
    assert reused.status_code == 401 and reused.json()["detail"][0]["code"] == "auth.refresh_token_revoked"
    # whoever holds the newest token loses it too: one of the two parties is an attacker
    assert _refresh(client, second).status_code == 401
    # a login made later is a different family and is not touched
    _, other = _tokens(_login(client, "ann"))
    assert _refresh(client, other).status_code == 200


def test_logout_revokes_the_given_login_or_all_of_them(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    _, a = _tokens(_login(client, "ann"))
    _, b = _tokens(_login(client, "ann"))

    assert client.post("/api/auth/logout", json={"refresh_token": a}, headers=headers).status_code == 204
    assert _refresh(client, a).status_code == 401
    assert _refresh(client, b).status_code == 200  # another session of the same user is left alone

    _, c = _tokens(_login(client, "ann"))
    assert client.post("/api/auth/logout", headers=headers).status_code == 204  # no body: every session
    assert _refresh(client, c).status_code == 401
    assert client.post("/api/auth/logout").status_code == 401  # logging out needs the access token


def test_logout_with_somebody_elses_refresh_token_revokes_nothing_of_theirs(env: Env) -> None:
    """The token is only used to find the login to end, and only if it is the caller's own."""
    client = env.build()
    _, bobs = _tokens(_login(client, "bob"))
    _, anns = _tokens(_login(client, "ann"))
    assert client.post("/api/auth/logout", json={"refresh_token": bobs}, headers=env.token("ann")).status_code == 204
    assert _refresh(client, anns).status_code == 401  # fell back to ending all of ann's own logins
    assert _refresh(client, bobs).status_code == 200  # bob's login is untouched
    client.post("/api/auth/logout", json={"refresh_token": "garbage"}, headers=env.token("bob"))
    _, bobs2 = _tokens(_login(client, "bob"))
    assert _refresh(client, bobs2).status_code == 200


def test_changing_the_password_ends_every_login(env: Env) -> None:
    client = env.build()
    _, refresh = _tokens(_login(client, "ann"))
    done = client.post(
        "/api/auth/user/password",
        json={"currentPassword": PASSWORD, "newPassword": "brand-new-password-3"},
        headers=env.token("ann"),
    )
    assert done.status_code == 204
    assert _refresh(client, refresh).status_code == 401


def test_deactivating_a_user_ends_every_login(env: Env) -> None:
    client = env.build()
    _, refresh = _tokens(_login(client, "ann"))

    async def deactivate(session: AsyncSession) -> None:
        user = await cruds.get_user_by_id(session, env.ids["ann"])
        await cruds.update_user(session, user, is_active=False)

    env.run(deactivate)
    assert _refresh(client, refresh).status_code == 401


def test_a_refresh_token_without_a_record_is_refused(env: Env) -> None:
    """Tokens of 0.1.x (and anything not issued by this server) cannot be refreshed: log in again."""
    from pachifast.auth.security import create_refresh_token

    client = env.build()
    refused = _refresh(client, create_refresh_token(env.ids["ann"]))
    assert refused.status_code == 401 and refused.json()["detail"][0]["code"] == "auth.refresh_token_revoked"


def test_without_rotation_the_token_stays_valid_but_can_still_be_revoked(env: Env) -> None:
    client = env.build(refresh_token_rotation="false")
    _, first = _tokens(_login(client, "ann"))
    again = _refresh(client, first)
    assert again.status_code == 200 and again.json().get("refresh_token") is None
    assert _refresh(client, first).status_code == 200
    assert client.post("/api/auth/logout", json={"refresh_token": first}, headers=env.token("ann")).status_code == 204
    assert _refresh(client, first).status_code == 401


def test_parallel_refreshes_with_the_same_token_give_exactly_one_new_token(env: Env) -> None:
    from pachifast.auth import security

    client = env.build()
    _, first = _tokens(_login(client, "ann"))
    payload = security.decode_refresh_payload(first)

    async def race() -> list[object]:
        engine = create_async_engine(env.url, connect_args={"timeout": 30})
        try:

            async def one() -> object:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    try:
                        return await security.rotate_refresh_token(session, payload, env.ids["ann"])  # type: ignore[arg-type]
                    except Exception as exc:  # the losers are refused
                        return exc

            return list(await asyncio.gather(*(one() for _ in range(8))))
        finally:
            await engine.dispose()

    results = asyncio.run(race())
    winners = [r for r in results if isinstance(r, str)]
    assert len(winners) == 1, results


def test_the_mfa_second_step_issues_a_refresh_token_that_can_be_rotated(env: Env) -> None:
    client = env.build()
    secret, backups = _enrol_totp(client, env.token("ann"))
    token = _login(client, "ann").json()["mfa_token"]
    done = client.post("/api/mfa/verify", json={"mfa_token": token, "code": backups[0]})
    assert done.status_code == 200
    refresh = (done.json().get("data") or done.json())["refresh_token"]
    assert _refresh(client, refresh).status_code == 200
    del secret


# --- gaps found after the review: Retry-After, registration switch, TOTP across workers ---


def test_a_locked_account_answers_429_with_retry_after_at_the_second_step(env: Env) -> None:
    """The countdown a client needs: seconds until the account is unlocked."""
    client = env.build()
    _enrol_totp(client, env.token("ann"))
    limit = get_settings().login_max_attempts
    token = _login(client, "ann").json()["mfa_token"]

    async def lock(session: AsyncSession) -> None:
        from pachifast.auth.security import utcnow

        user = await cruds.get_user_by_id(session, env.ids["ann"])
        user.locked_until = utcnow() + timedelta(minutes=10)
        await session.commit()

    env.run(lock)
    response = client.post("/api/mfa/verify", json={"mfa_token": token, "code": "000000"})
    assert response.status_code == 429 and response.json()["detail"][0]["code"] == "mfa.too_many_attempts"
    assert 590 <= int(response.headers["Retry-After"]) <= 600
    assert limit == 5


def test_retry_after_is_readable_by_browsers_on_other_origins(env: Env) -> None:
    env.build()  # the settings of the test database
    client = TestClient(create_app((core_module,), cors_origins=["https://app.example"]))
    response = client.get("/api/auth/user", headers={"Origin": "https://app.example"})
    assert "retry-after" in response.headers.get("access-control-expose-headers", "").lower()


def test_registration_is_closed_by_default(env: Env) -> None:
    body = {"username": "newcomer", "password": "a-long-enough-password-1"}
    refused = env.build().post("/api/auth/register", json=body)
    assert refused.status_code == 403 and refused.json()["detail"][0]["code"] == "auth.registration_disabled"


def test_registration_can_be_switched_off(env: Env) -> None:
    body = {"username": "newcomer", "password": "a-long-enough-password-1"}
    assert env.build(registration_enabled="true").post("/api/auth/register", json=body).status_code == 201
    refused = env.build(registration_enabled="false").post("/api/auth/register", json={**body, "username": "another"})
    assert refused.status_code == 403 and refused.json()["detail"][0]["code"] == "auth.registration_disabled"


def test_a_totp_code_is_refused_again_by_the_database_not_the_cache(env: Env) -> None:
    """The last accepted step lives on the MFA row, so a second worker (or a restart) cannot be used to replay it."""
    from pachifast.mfa import cruds as mfa_cruds

    client = env.build()
    secret, _ = _enrol_totp(client, env.token("ann"))
    code = _next_code(secret)
    token = _login(client, "ann").json()["mfa_token"]
    assert client.post("/api/mfa/verify", json={"mfa_token": token, "code": code}).status_code == 200

    from pachifast.cache import reset_cache

    reset_cache()  # a fresh worker: nothing remembered in memory
    token = _login(client, "ann").json()["mfa_token"]
    assert client.post("/api/mfa/verify", json={"mfa_token": token, "code": code}).status_code == 401

    async def steps(session: AsyncSession) -> tuple[bool, bool]:
        cfg = await mfa_cruds.get_mfa_config(session, env.ids["ann"])
        return (
            await mfa_cruds.claim_totp_step(session, cfg.id, cfg.last_totp_step),  # the same step
            await mfa_cruds.claim_totp_step(session, cfg.id, cfg.last_totp_step - 1),  # an older one
        )

    assert env.run(steps) == (False, False)


# --- user settings: validated values, bulk update, appearance schema ------------------------

SETTINGS = "/api/auth/user/settings"


def _values(response) -> dict:
    """The payload of a successful (enveloped) response."""
    return response.json()["data"]


def test_a_setting_value_is_validated_against_its_field(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    assert (
        _values(client.put(f"{SETTINGS}/appearance/theme", json={"value": "dark"}, headers=headers))["appearance"][
            "theme"
        ]
        == "dark"
    )
    assert client.put(f"{SETTINGS}/preferences/tablePageSize", json={"value": 50}, headers=headers).status_code == 200
    for route, value in (
        ("appearance/theme", "purple"),  # not in the Literal
        ("appearance/reduceMotion", "maybe"),  # not a boolean
        ("preferences/tablePageSize", 5),  # below ge
        ("preferences/tablePageSize", 101),  # above le
        ("preferences/tablePageSize", 20.5),  # not an integer
        ("preferences/locale", "not a locale!"),
        ("preferences/timezone", "Mars/Olympus"),
    ):
        response = client.put(f"{SETTINGS}/{route}", json={"value": value}, headers=headers)
        assert response.status_code == 422, (route, value)
        detail = response.json()["detail"][0]
        assert detail["code"] == "user.settings_value_invalid" and detail["loc"] == ["body", "value"]
    saved = _values(client.get(SETTINGS, headers=headers))
    assert saved["appearance"]["theme"] == "dark" and saved["preferences"]["tablePageSize"] == 50


def test_nullable_and_float_values_are_accepted(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    assert client.put(f"{SETTINGS}/preferences/locale", json={"value": "it-IT"}, headers=headers).status_code == 200
    reset = client.put(f"{SETTINGS}/preferences/locale", json={"value": None}, headers=headers)
    assert reset.status_code == 200, reset.text
    assert _values(reset)["preferences"]["locale"] is None
    whole = client.put(f"{SETTINGS}/preferences/tablePageSize", json={"value": 30.0}, headers=headers)
    assert whole.status_code == 200 and _values(whole)["preferences"]["tablePageSize"] == 30


def test_bulk_update_is_all_or_nothing(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    ok = client.patch(
        SETTINGS,
        json={"appearance": {"theme": "light", "accent": "coral"}, "preferences": {"dateFormat": "ymd"}},
        headers=headers,
    )
    assert ok.status_code == 200
    assert (_values(ok)["appearance"]["accent"], _values(ok)["preferences"]["dateFormat"]) == ("coral", "ymd")
    bad = client.patch(SETTINGS, json={"appearance": {"theme": "dark", "density": "huge"}}, headers=headers)
    assert bad.status_code == 422 and bad.json()["detail"][0]["code"] == "user.settings_value_invalid"
    assert _values(client.get(SETTINGS, headers=headers))["appearance"]["theme"] == "light"  # nothing was saved
    unknown = client.patch(SETTINGS, json={"nope": {"x": 1}}, headers=headers)
    assert unknown.json()["detail"][0]["code"] == "user.settings_category_invalid"


def test_a_stored_value_that_no_longer_validates_falls_back_to_the_default(env: Env) -> None:
    from pachifast.auth.models import UserSettingsTable

    client = env.build()
    headers = env.token("ann")
    client.put(f"{SETTINGS}/appearance/accent", json={"value": "indigo"}, headers=headers)

    async def corrupt(session: AsyncSession) -> None:
        row = (await session.execute(UserSettingsTable.__table__.select())).first()
        data = dict(row.json_settings)
        data["appearance"] = {**data["appearance"], "theme": "removed-option"}
        data["preferences"] = {"tablePageSize": 1, "theme": "light"}  # old key and an out-of-range value
        await session.execute(UserSettingsTable.__table__.update().values(json_settings=data))
        await session.commit()

    env.run(corrupt)
    values = _values(client.get(SETTINGS, headers=headers))
    assert values["appearance"]["theme"] == "auto" and values["appearance"]["accent"] == "indigo"
    assert values["preferences"]["tablePageSize"] == 25


def test_appearance_schema_describes_selects_limits_and_appliers(env: Env) -> None:
    client = env.build()
    schema = _values(client.get(f"{SETTINGS}/schema", headers=env.token("ann")))
    theme = schema["appearance"]["fields"]["theme"]
    assert (theme["component"], theme["apply"]) == ("select", "theme")
    assert [o["value"] for o in theme["properties"]["options"]] == ["dark", "light", "auto"]
    assert theme["optionKeys"]["dark"] == "settings.appearance.theme.options.dark"
    assert schema["appearance"]["fields"]["reduceMotion"]["component"] == "toggle"
    page_size = schema["preferences"]["fields"]["tablePageSize"]
    assert (page_size["component"], page_size["properties"]["min"], page_size["properties"]["max"]) == (
        "number",
        10,
        100,
    )
    assert (
        "theme" not in schema["preferences"]["fields"]
        and schema["preferences"]["fields"]["locale"]["apply"] == "locale"
    )


# --- MFA: enrolment needs the switch on, and the email must be proven -------------------------------------


def _give_email(env: Env, username: str, email: str) -> None:
    """Set the email of a user straight in the database."""
    from sqlalchemy import update

    async def run() -> None:
        engine = create_async_engine(env.url)
        async with engine.begin() as conn:
            await conn.execute(update(models.User).where(models.User.username == username).values(email=email))
        await engine.dispose()

    asyncio.run(run())


def test_enrolling_needs_mfa_to_be_switched_on(env: Env) -> None:
    client = env.build(mfa_enabled="false")
    headers = env.token("ann")
    for route, body in (("totp/setup", None), ("email/setup", None), ("sms/setup", {"phone_number": "+390000000"})):
        refused = client.post(f"/api/mfa/{route}", json=body, headers=headers)
        assert refused.status_code == 403 and refused.json()["detail"][0]["code"] == "mfa.disabled"


def test_email_otp_is_active_only_after_the_code_is_confirmed(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pachifast.mfa.security.generate_otp_code", lambda length=6: "123456")
    _give_email(env, "ann", "ann@example.org")
    client = env.build()
    headers = env.token("ann")

    setup = client.post("/api/mfa/email/setup", headers=headers)
    assert setup.status_code == 200 and _data(setup)["confirm_required"] is True
    assert _data(client.get("/api/mfa/status", headers=headers))["enabled"] is False
    assert "mfa_required" not in _login(client, "ann").json()  # the login still asks for the password only

    wrong = client.post("/api/mfa/email/confirm", json={"code": "000000"}, headers=headers)
    assert wrong.status_code == 400 and wrong.json()["detail"][0]["code"] == "mfa.email_code_invalid"
    assert client.post("/api/mfa/email/confirm", json={"code": "123456"}, headers=headers).status_code == 200
    assert _data(client.get("/api/mfa/status", headers=headers))["method"] == "email"
    assert _login(client, "ann").json()["mfa_required"] is True
    # the code works once
    again = client.post("/api/mfa/email/confirm", json={"code": "123456"}, headers=headers)
    assert again.status_code in (400, 409)


def test_confirming_without_a_pending_email_setup_is_refused(env: Env) -> None:
    client = env.build()
    refused = client.post("/api/mfa/email/confirm", json={"code": "123456"}, headers=env.token("ann"))
    assert refused.status_code == 400 and refused.json()["detail"][0]["code"] == "mfa.no_email_setup"


def test_a_duplicate_permission_is_a_conflict_not_a_server_error(env: Env) -> None:
    client = env.build()
    headers = env.token("boss")
    body = {"codename": "reports.view", "description": "x"}
    assert client.post("/api/admin/permissions/permissions", json=body, headers=headers).status_code == 200
    duplicate = client.post("/api/admin/permissions/permissions", json=body, headers=headers)
    assert duplicate.status_code == 409 and duplicate.json()["detail"][0]["code"] == "db.conflict"
    too_long = client.post("/api/admin/permissions/permissions", json={"codename": "x" * 300}, headers=headers)
    assert too_long.status_code == 422


def test_sms_otp_is_active_only_after_the_code_is_confirmed(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str]] = []

    class RecordingSender(MfaSenderAdapter):
        async def send_sms_otp(self, *, user_id, phone_number, otp_code, expires_in_minutes) -> None:
            sent.append((phone_number, otp_code))

    monkeypatch.setattr("pachifast.mfa.sender._sender_class", RecordingSender)
    monkeypatch.setattr("pachifast.mfa.security.generate_otp_code", lambda length=6: "654321")
    client = env.build()
    headers = env.token("ann")

    setup = client.post("/api/mfa/sms/setup", json={"phone_number": "+390000000"}, headers=headers)
    assert setup.status_code == 200 and _data(setup)["confirm_required"] is True
    assert _data(client.get("/api/mfa/status", headers=headers))["enabled"] is False
    wrong = client.post("/api/mfa/sms/confirm", json={"code": "000000"}, headers=headers)
    assert wrong.status_code == 400 and wrong.json()["detail"][0]["code"] == "mfa.sms_code_invalid"
    assert client.post("/api/mfa/sms/confirm", json={"code": "654321"}, headers=headers).status_code == 200
    assert _data(client.get("/api/mfa/status", headers=headers))["method"] == "sms"
    assert _login(client, "ann").json()["mfa_required"] is True
    assert sent and sent[0] == ("+390000000", "654321")


# --- rate limit per client address --------------------------------------------------------------------


def test_the_rate_limit_is_off_by_default(env: Env) -> None:
    client = env.build()
    for _ in range(12):  # no 429 for the address (the account lockout of "ann" answers 429 with another code)
        response = _login(client, "bob", PASSWORD)
        assert response.status_code == 200


def test_the_rate_limit_stops_a_client_that_tries_many_accounts(env: Env) -> None:
    from pachifast.cache import reset_cache

    reset_cache()
    client = env.build(auth_rate_limit="3", auth_rate_window_seconds="600")
    for name in ("ann", "bob", "boss"):
        assert _login(client, name, "wrong-password-0").status_code == 401
    refused = _login(client, "ann", "wrong-password-0")
    assert refused.status_code == 429 and refused.json()["detail"][0]["code"] == "auth.rate_limited"
    assert int(refused.headers["Retry-After"]) >= 1
    # the same address cannot register or verify a second factor either: one budget for the three routes
    assert client.post("/api/auth/register", json={"username": "x", "password": "y"}).status_code == 429
    reset_cache()


def test_the_rate_limit_counts_an_ipv6_network_as_one_client() -> None:
    from pachifast.ratelimit import _client_key

    assert _client_key("2001:db8:1:2:aaaa:bbbb:cccc:dddd") == _client_key("2001:db8:1:2::1") == "2001:db8:1:2::/64"
    assert _client_key("2001:db8:1:3::1") != _client_key("2001:db8:1:2::1")
    assert _client_key("203.0.113.9") == "203.0.113.9"
    assert _client_key(None) == "unknown"
    assert _client_key("testclient") == "testclient"


async def test_the_counter_of_the_memory_cache_expires_with_its_first_increment() -> None:
    from pachifast.cache.memory import MemoryCache

    cache = MemoryCache()
    assert [await cache.incr("k", 0.2) for _ in range(3)] == [1, 2, 3]
    await asyncio.sleep(0.25)
    assert await cache.incr("k", 0.2) == 1


async def test_sqlite_enforces_foreign_keys_and_waits_for_locks(tmp_path) -> None:
    from sqlalchemy import text

    from pachifast.database.engine import get_engine_from_url

    engine = get_engine_from_url(f"sqlite+aiosqlite:///{tmp_path / 'pragmas.sqlite3'}")
    async with engine.connect() as conn:
        assert (await conn.execute(text("PRAGMA foreign_keys"))).scalar() == 1
        assert (await conn.execute(text("PRAGMA busy_timeout"))).scalar() == 5000
        assert (await conn.execute(text("PRAGMA journal_mode"))).scalar() == "wal"
    await engine.dispose()


# --- bad query parameters on the admin lists answer 4xx, never 500 --------------------

ADMIN_LISTS = ["/api/admin/user", "/api/admin/activity-log"]


@pytest.mark.parametrize("path", ADMIN_LISTS)
@pytest.mark.parametrize(
    "query",
    [
        "page=99999999999999999999",
        "page=9223372036854775807&pageSize=200",
        "page=999999999999&pageSize=200",
        "pageSize=99999999999999999999",
        "page=0",
        "page=-1",
        "pageSize=-1",
        "page=abc",
        "orderBy=",
        "search=%00",
    ],
)
def test_admin_lists_never_answer_500_on_odd_paging_parameters(env: Env, path: str, query: str) -> None:
    client = env.build()
    response = client.get(f"{path}?{query}", headers=env.token("boss"))
    assert response.status_code < 500, (response.status_code, response.text[:200])


def test_admin_applications_does_not_fail(env: Env) -> None:
    client = env.build()
    response = client.get("/api/admin/permissions/applications", headers=env.token("boss"))
    assert response.status_code < 500, response.text[:300]


ODD_VALUES = ["", "0", "-1", "abc", "99999999999999999999", "9223372036854775807", "%00", "a" * 3000, "1e400", "[]"]


def test_no_get_route_answers_500_on_odd_query_or_path_values(env: Env) -> None:
    """Fuzz every documented GET route with odd values for each parameter: the answer must be a 2xx/3xx/4xx."""
    client = env.build()
    headers = env.token("boss")
    schema = client.get("/api/v1/openapi.json").json()
    failures: list[str] = []
    for path, operations in schema["paths"].items():
        op = operations.get("get")
        if not op or not path.startswith("/api") or "/health" in path:
            continue
        params = [p for p in op.get("parameters", []) if p["in"] in {"query", "path"}]
        for value in ODD_VALUES:
            for target in params or [None]:
                url = path
                query: dict[str, str] = {}
                for p in params:
                    v = value if p is target else "1"
                    if p["in"] == "path":
                        url = url.replace("{" + p["name"] + "}", v or "x")
                    elif p is target:
                        query[p["name"]] = v
                response = client.get(url, params=query, headers=headers)
                if response.status_code >= 500:
                    failures.append(f"{response.status_code} GET {url} {query}")
    assert not failures, "\n".join(failures[:20])


def test_a_nul_character_in_a_list_search_is_a_422(env: Env) -> None:
    client = env.build()
    response = client.get("/api/admin/user?search=a%00b", headers=env.token("boss"))
    assert response.status_code == 422, response.text
