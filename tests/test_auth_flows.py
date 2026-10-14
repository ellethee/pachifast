"""Auth flows: token validation, revocation, permissions, profile, password change, register, refresh, lockout."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import jwt
import pytest
from fastapi import Depends, WebSocket, WebSocketDisconnect
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pachifast import create_app
from pachifast.audit import models as audit_models  # noqa: F401
from pachifast.auth import cruds, models  # noqa: F401
from pachifast.auth.dependencies import (
    get_current_active_ws_user,
    get_current_user_optional,
    make_permission_checker,
    make_ws_permission_checker,
)
from pachifast.auth.schemas import UserCreate
from pachifast.auth.security import create_access_token, create_refresh_token
from pachifast.config import get_settings
from pachifast.database import Base
from pachifast.mfa import MfaModule
from pachifast.mfa import models as mfa_models  # noqa: F401
from pachifast.presets import core_module

PASSWORD = "long-enough-pass-1"
NEW_PASSWORD = "another-long-pass-22"
POSTGRES_ADMIN_URL = os.environ.get("PACHIFAST_TEST_POSTGRES_URL", "")


@dataclass
class Env:
    """A running app on SQLite (or PostgreSQL) with a superuser and two regular users."""

    build: Callable[..., TestClient]
    url: str
    ids: dict[str, Any]

    def token(self, name: str) -> dict[str, str]:
        """Authorization header of ``name``."""
        return {"Authorization": f"Bearer {create_access_token(self.ids[name])}"}

    def run(self, work: Callable[[AsyncSession], Any]) -> Any:
        """Run ``work(session)`` against the test database."""

        async def main() -> Any:
            engine = create_async_engine(self.url)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    return await work(session)
            finally:
                await engine.dispose()

        return asyncio.run(main())


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
        url = f"sqlite+aiosqlite:///{tmp_path / 'flows.sqlite3'}"
    else:
        from sqlalchemy import create_engine, text
        from sqlalchemy.engine import make_url

        admin = make_url(POSTGRES_ADMIN_URL).set(drivername="postgresql+psycopg")
        database = f"pf_flow_{uuid.uuid4().hex[:10]}"
        admin_engine = create_engine(admin, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{database}"'))
        url = admin.set(drivername="postgresql+asyncpg", database=database).render_as_string(False)
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")
    monkeypatch.setenv("PACHIFAST_MFA_ENABLED", "true")
    monkeypatch.setenv("PACHIFAST_ACTIVITY_LOG_ENABLED", "false")
    ids: dict[str, Any] = {}

    async def prepare() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for name, superuser in (("boss", True), ("ann", False), ("bob", False)):
                user = await cruds.create_user(
                    session,
                    UserCreate(username=name, password=PASSWORD, is_superuser=superuser, email=f"{name}@example.com"),
                )
                ids[name] = user.id
        await engine.dispose()

    asyncio.run(prepare())
    clients: list[TestClient] = []

    def build(**settings: str) -> TestClient:
        """A client whose requests share one event loop (asyncpg connections cannot move between loops)."""
        from pachifast.database import engine as engine_module

        for key, value in settings.items():
            monkeypatch.setenv(f"PACHIFAST_{key.upper()}", value)
        get_settings.cache_clear()
        engine_module._engine_cache.clear()
        engine_module._sessionmaker_cache.clear()
        test_client = TestClient(create_app((core_module, MfaModule)))
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


# --- helpers ------------------------------------------------------------------


def code_of(response) -> str:
    """The error code of an error response."""
    return response.json()["detail"][0]["code"]


def bearer(token: str) -> dict[str, str]:
    """Authorization header for a raw token."""
    return {"Authorization": f"Bearer {token}"}


def forge(
    subject: Any,
    *,
    token_type: str = "access",
    expires_in: timedelta = timedelta(minutes=10),
    issued_ago: timedelta = timedelta(0),
    key: str | None = None,
    extra: dict[str, Any] | None = None,
    drop: tuple[str, ...] = (),
) -> str:
    """A JWT with full control over type, expiry, issue time, key and claims."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "type": token_type,
        "iat": now - issued_ago,
        "exp": now + expires_in,
        **(extra or {}),
    }
    for claim in drop:
        payload.pop(claim, None)
    return jwt.encode(payload, key or settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def login(client: TestClient, name: str, password: str = PASSWORD):
    """POST /api/auth/token."""
    return client.post("/api/auth/token", data={"username": name, "password": password})


def refresh(client: TestClient, token: str):
    """POST /api/auth/refresh-token."""
    return client.post("/api/auth/refresh-token", json={"refresh_token": token})


def change_user(env: Env, name: str, **fields: Any) -> None:
    """Set columns on a user straight in the database."""

    async def work(session: AsyncSession) -> None:
        user = await cruds.get_user_by_id(session, env.ids[name])
        for key, value in fields.items():
            setattr(user, key, value)
        await session.commit()

    env.run(work)


def grant(env: Env, name: str, codename: str, *, via_group: bool) -> None:
    """Give ``name`` a permission directly or through a group."""

    async def work(session: AsyncSession) -> None:
        perm = await cruds.create_permission(session, codename, "")
        if via_group:
            group = await cruds.create_group(session, f"g-{codename}", "", [perm.id])
            await cruds.set_user_groups(session, env.ids[name], [group["id"]])
        else:
            await cruds.set_user_direct_permissions(session, env.ids[name], [perm.id])

    env.run(work)


# --- access token validation --------------------------------------------------


def test_missing_and_malformed_tokens_are_refused(env: Env) -> None:
    client = env.build()
    missing = client.get("/api/auth/user")
    assert missing.status_code == 401 and code_of(missing) == "auth.not_authenticated"
    assert missing.headers["www-authenticate"] == "Bearer"
    for garbage in ("not-a-jwt", "a.b.c", ""):
        refused = client.get("/api/auth/user", headers=bearer(garbage))
        assert refused.status_code == 401, garbage


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"expires_in": timedelta(seconds=-60)}, "auth.token_invalid"),
        ({"key": "a-different-secret-key-0123456789-abcdefgh"}, "auth.token_invalid"),
        ({"token_type": "refresh"}, "auth.token_wrong_type"),
        ({"token_type": "mfa"}, "auth.token_wrong_type"),
        ({"drop": ("exp",)}, "auth.token_invalid"),
        ({"drop": ("sub",)}, "auth.token_invalid"),
    ],
)
def test_bad_access_tokens_answer_401(env: Env, kwargs: dict[str, Any], code: str) -> None:
    client = env.build()
    response = client.get("/api/auth/user", headers=bearer(forge(env.ids["ann"], **kwargs)))
    assert response.status_code == 401 and code_of(response) == code


def test_a_token_with_a_non_uuid_subject_is_refused(env: Env) -> None:
    refused = env.build().get("/api/auth/user", headers=bearer(forge("not-a-uuid")))
    assert refused.status_code == 401 and code_of(refused) == "auth.token_invalid"


def test_a_token_with_an_empty_subject_is_refused(env: Env) -> None:
    refused = env.build().get("/api/auth/user", headers=bearer(forge("")))
    assert refused.status_code == 401 and code_of(refused) == "auth.token_no_subject"


def test_a_token_signed_with_none_algorithm_is_refused(env: Env) -> None:
    unsigned = jwt.encode({"sub": str(env.ids["ann"]), "type": "access", "exp": 4102444800}, None, algorithm="none")
    assert env.build().get("/api/auth/user", headers=bearer(unsigned)).status_code == 401


def test_a_valid_token_of_a_deleted_user_is_refused(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    assert client.get("/api/auth/user", headers=headers).status_code == 200

    async def delete(session: AsyncSession) -> None:
        assert await cruds.delete_user(session, env.ids["ann"])

    env.run(delete)
    refused = client.get("/api/auth/user", headers=headers)
    assert refused.status_code == 401 and code_of(refused) == "user.not_found"


def test_a_valid_token_of_a_disabled_user_is_refused(env: Env) -> None:
    client = env.build()
    change_user(env, "ann", is_active=False)
    refused = client.get("/api/auth/user", headers=env.token("ann"))
    assert refused.status_code == 400 and code_of(refused) == "user.disabled"


def test_tokens_issued_before_a_password_change_are_revoked(env: Env) -> None:
    client = env.build()
    change_user(env, "ann", password_changed_at=datetime.now(UTC) - timedelta(minutes=10))
    old = forge(env.ids["ann"], issued_ago=timedelta(minutes=5))
    assert client.get("/api/auth/user", headers=bearer(old)).status_code == 200
    change_user(env, "ann", password_changed_at=datetime.now(UTC) - timedelta(minutes=1))
    revoked = client.get("/api/auth/user", headers=bearer(old))
    assert revoked.status_code == 401 and code_of(revoked) == "auth.token_revoked"
    assert client.get("/api/auth/user", headers=env.token("ann")).status_code == 200


def test_a_token_without_iat_is_revoked_once_the_password_changed(env: Env) -> None:
    client = env.build()
    no_iat = forge(env.ids["ann"], drop=("iat",))
    change_user(env, "ann", password_changed_at=None)
    assert client.get("/api/auth/user", headers=bearer(no_iat)).status_code == 200
    change_user(env, "ann", password_changed_at=datetime.now(UTC) - timedelta(minutes=1))
    assert code_of(client.get("/api/auth/user", headers=bearer(no_iat))) == "auth.token_revoked"


# --- permissions --------------------------------------------------------------


def _probe_routes(client: TestClient) -> None:
    """Add protected probe routes to the running app."""
    app = client.app
    app.add_api_route(  # type: ignore[attr-defined]
        "/probe/perm", lambda: {"ok": True}, dependencies=[Depends(make_permission_checker(["probe.read"]))]
    )
    app.add_api_route(  # type: ignore[attr-defined]
        "/probe/two",
        lambda: {"ok": True},
        dependencies=[Depends(make_permission_checker(["probe.a", "probe.b"]))],
    )
    app.add_api_route(  # type: ignore[attr-defined]
        "/probe/staff", lambda: {"ok": True}, dependencies=[Depends(make_permission_checker(require_staff=True))]
    )
    app.add_api_route(  # type: ignore[attr-defined]
        "/probe/super", lambda: {"ok": True}, dependencies=[Depends(make_permission_checker(require_superuser=True))]
    )


def test_permission_denied_then_granted_directly(env: Env) -> None:
    client = env.build()
    _probe_routes(client)
    denied = client.get("/probe/perm", headers=env.token("ann"))
    assert denied.status_code == 403 and code_of(denied) == "access.permissions_missing"
    grant(env, "ann", "probe.read", via_group=False)
    assert client.get("/probe/perm", headers=env.token("ann")).status_code == 200
    assert client.get("/probe/perm", headers=env.token("bob")).status_code == 403


def test_permission_granted_through_a_group(env: Env) -> None:
    client = env.build()
    _probe_routes(client)
    assert client.get("/probe/perm", headers=env.token("bob")).status_code == 403
    grant(env, "bob", "probe.read", via_group=True)
    assert client.get("/probe/perm", headers=env.token("bob")).status_code == 200
    me = client.get("/api/auth/user", headers=env.token("bob")).json()
    assert "probe.read" in (me.get("data", me)["permissions"])


def test_every_listed_permission_is_required(env: Env) -> None:
    client = env.build()
    _probe_routes(client)

    async def work(session: AsyncSession) -> None:
        a = await cruds.create_permission(session, "probe.a")
        await cruds.create_permission(session, "probe.b")
        await cruds.set_user_direct_permissions(session, env.ids["ann"], [a.id])

    env.run(work)
    missing = client.get("/probe/two", headers=env.token("ann"))
    assert missing.status_code == 403 and code_of(missing) == "access.permissions_missing"
    assert client.get("/probe/two", headers=env.token("boss")).status_code == 200


def test_superuser_bypasses_every_check(env: Env) -> None:
    client = env.build()
    _probe_routes(client)
    for path in ("/probe/perm", "/probe/two", "/probe/staff", "/probe/super"):
        assert client.get(path, headers=env.token("boss")).status_code == 200, path


def test_staff_and_superuser_requirements(env: Env) -> None:
    client = env.build()
    _probe_routes(client)
    assert code_of(client.get("/probe/staff", headers=env.token("ann"))) == "access.staff_required"
    assert code_of(client.get("/probe/super", headers=env.token("ann"))) == "access.superuser_required"
    change_user(env, "ann", is_staff=True)
    assert client.get("/probe/staff", headers=env.token("ann")).status_code == 200
    assert client.get("/probe/super", headers=env.token("ann")).status_code == 403


def test_a_disabled_user_does_not_reach_the_permission_check(env: Env) -> None:
    client = env.build()
    _probe_routes(client)
    change_user(env, "boss", is_active=False)
    refused = client.get("/probe/perm", headers=env.token("boss"))
    assert refused.status_code == 400 and code_of(refused) == "user.disabled"


def test_admin_routes_need_the_superuser(env: Env) -> None:
    client = env.build()
    assert client.get("/api/admin/user", headers=env.token("ann")).status_code == 403
    assert client.get("/api/admin/user", headers=env.token("boss")).status_code == 200


# --- get_current_user_optional ---------------------------------------------------


def test_optional_user_with_bad_tokens_is_anonymous(env: Env) -> None:
    client = env.build()

    async def who(user=Depends(get_current_user_optional)) -> dict[str, Any]:
        return {"user": user.username if user else None}

    client.app.add_api_route("/probe/who", who)  # type: ignore[attr-defined]
    assert client.get("/probe/who", headers=env.token("ann")).json() == {"user": "ann"}
    assert client.get("/probe/who").json() == {"user": None}
    assert client.get("/probe/who", headers=bearer("garbage")).json() == {"user": None}
    assert client.get("/probe/who", headers=bearer(forge(env.ids["ann"], token_type="refresh"))).json() == {
        "user": None
    }
    assert client.get("/probe/who", headers=bearer(forge(uuid.uuid4()))).json() == {"user": None}


# --- WebSocket dependencies ---------------------------------------------------------


def _ws_probe(client: TestClient) -> None:
    """WebSocket routes that use the auth dependencies."""

    async def plain(websocket: WebSocket, user=Depends(get_current_active_ws_user)) -> None:
        await websocket.accept()
        await websocket.send_json({"user": user.username})
        await websocket.close()

    async def guarded(
        websocket: WebSocket,
        user=Depends(get_current_active_ws_user),
        _=Depends(make_ws_permission_checker(["probe.ws"])),
    ) -> None:
        await websocket.accept()
        await websocket.send_json({"user": user.username})
        await websocket.close()

    async def staff(websocket: WebSocket, _=Depends(make_ws_permission_checker(require_staff=True))) -> None:
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    async def superonly(websocket: WebSocket, _=Depends(make_ws_permission_checker(require_superuser=True))) -> None:
        await websocket.accept()
        await websocket.send_json({"ok": True})
        await websocket.close()

    app = client.app
    app.add_api_websocket_route("/probe/ws", plain)  # type: ignore[attr-defined]
    app.add_api_websocket_route("/probe/ws-perm", guarded)  # type: ignore[attr-defined]
    app.add_api_websocket_route("/probe/ws-staff", staff)  # type: ignore[attr-defined]
    app.add_api_websocket_route("/probe/ws-super", superonly)  # type: ignore[attr-defined]


def _ws_refused(client: TestClient, path: str, headers: dict[str, str] | None = None) -> bool:
    """True when the handshake is refused."""
    try:
        with client.websocket_connect(path, headers=headers or {}) as ws:
            ws.receive_json()
    except WebSocketDisconnect:
        return True
    return False


def test_websocket_authentication(env: Env) -> None:
    client = env.build()
    _ws_probe(client)
    assert _ws_refused(client, "/probe/ws")
    assert _ws_refused(client, "/probe/ws", bearer("garbage"))
    assert _ws_refused(client, "/probe/ws", bearer(forge(uuid.uuid4())))
    # the query token is off by default
    assert _ws_refused(client, f"/probe/ws?token={create_access_token(env.ids['ann'])}")
    with client.websocket_connect("/probe/ws", headers=env.token("ann")) as ws:
        assert ws.receive_json() == {"user": "ann"}


def test_websocket_refuses_revoked_and_disabled_users(env: Env) -> None:
    client = env.build()
    _ws_probe(client)
    old = forge(env.ids["bob"], issued_ago=timedelta(minutes=5))
    change_user(env, "bob", password_changed_at=datetime.now(UTC) - timedelta(minutes=10))
    change_user(env, "bob", password_changed_at=datetime.now(UTC) - timedelta(minutes=1))
    assert _ws_refused(client, "/probe/ws", bearer(old))
    change_user(env, "ann", is_active=False)
    assert _ws_refused(client, "/probe/ws", env.token("ann"))


def test_websocket_query_token_when_allowed(env: Env) -> None:
    client = env.build(events_allow_query_token="true")
    _ws_probe(client)
    with client.websocket_connect(f"/probe/ws?token={create_access_token(env.ids['ann'])}") as ws:
        assert ws.receive_json() == {"user": "ann"}


def test_websocket_permissions(env: Env) -> None:
    client = env.build()
    _ws_probe(client)
    assert _ws_refused(client, "/probe/ws-perm", env.token("ann"))
    grant(env, "ann", "probe.ws", via_group=False)
    with client.websocket_connect("/probe/ws-perm", headers=env.token("ann")) as ws:
        assert ws.receive_json() == {"user": "ann"}
    with client.websocket_connect("/probe/ws-perm", headers=env.token("boss")) as ws:
        assert ws.receive_json() == {"user": "boss"}
    assert _ws_refused(client, "/probe/ws-staff", env.token("bob"))
    assert _ws_refused(client, "/probe/ws-super", env.token("bob"))
    change_user(env, "bob", is_staff=True)
    assert not _ws_refused(client, "/probe/ws-staff", env.token("bob"))
    assert not _ws_refused(client, "/probe/ws-super", env.token("boss"))


# --- GET /api/auth/user and profile updates -----------------------------------------


def test_user_info_returns_the_profile_with_permissions(env: Env) -> None:
    client = env.build()
    grant(env, "ann", "probe.read", via_group=False)
    body = client.get("/api/auth/user", headers=env.token("ann")).json()
    body = body.get("data", body)
    assert body["username"] == "ann" and body["email"] == "ann@example.com"
    assert body["permissions"] == ["probe.read"]
    assert "password" not in body and "passwordHash" not in body and "password_hash" not in body


def _patch_profile(client: TestClient, headers: dict[str, str], **body):
    return client.patch("/api/auth/user", json=body, headers=headers)


def test_a_user_edits_their_own_name_without_a_password(env: Env) -> None:
    client = env.build()
    response = _patch_profile(client, env.token("ann"), firstName="Anna", lastName="Rossi")
    assert response.status_code == 200
    body = response.json().get("data", response.json())
    assert (body["firstName"], body["lastName"], body["username"]) == ("Anna", "Rossi", "ann")
    again = client.get("/api/auth/user", headers=env.token("ann")).json()
    assert again.get("data", again)["firstName"] == "Anna"


def test_the_profile_edit_ignores_privileges_and_the_username(env: Env) -> None:
    client = env.build()
    response = _patch_profile(
        client, env.token("ann"), firstName="A", isSuperuser=True, isStaff=True, username="root", isActive=False
    )
    assert response.status_code == 200
    body = response.json().get("data", response.json())
    assert body["username"] == "ann" and not body["isSuperuser"] and not body["isStaff"] and body["isActive"]


def test_changing_the_email_needs_the_current_password(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    for extra in ({}, {"currentPassword": "wrong-password-123"}):
        refused = _patch_profile(client, headers, email="new@example.org", **extra)
        assert refused.status_code == 400 and code_of(refused) == "auth.password_incorrect"
    ok = _patch_profile(client, headers, email="New@Example.org", currentPassword=PASSWORD)
    assert ok.status_code == 200 and ok.json().get("data", ok.json())["email"] == "new@example.org"
    # sending the email that the user already has is not a change: no password needed
    assert _patch_profile(client, headers, email="new@example.org").status_code == 200


def test_an_email_of_another_user_is_refused(env: Env) -> None:
    client = env.build()
    assert (
        _patch_profile(client, env.token("bob"), email="taken@example.org", currentPassword=PASSWORD).status_code == 200
    )
    refused = _patch_profile(client, env.token("ann"), email="taken@example.org", currentPassword=PASSWORD)
    assert refused.status_code == 400 and code_of(refused) == "user.email_exists"


def test_the_profile_edit_validates_and_needs_a_token(env: Env) -> None:
    client = env.build()
    assert _patch_profile(client, env.token("ann"), firstName="x" * 151).status_code == 422
    assert _patch_profile(client, env.token("ann"), email="not-an-email", currentPassword=PASSWORD).status_code == 422
    assert client.patch("/api/auth/user", json={"firstName": "A"}).status_code == 401


def test_the_profile_is_edited_through_the_admin_route(env: Env) -> None:
    client = env.build()
    url = f"/api/admin/user/{env.ids['ann']}"
    ok = client.patch(url, json={"first_name": "Annie", "email": "Annie@Example.com"}, headers=env.token("boss"))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    body = body.get("data", body)
    assert body["email"] == "annie@example.com"
    cleared = client.patch(url, json={"email": ""}, headers=env.token("boss"))
    assert cleared.status_code == 200


def test_profile_validation_errors(env: Env) -> None:
    client = env.build()
    url = f"/api/admin/user/{env.ids['ann']}"
    headers = env.token("boss")
    assert client.patch(url, json={"email": "not-an-email"}, headers=headers).status_code == 422
    assert client.patch(url, json={"first_name": "x" * 151}, headers=headers).status_code == 422
    assert client.patch(url, json={"last_name": "x" * 151}, headers=headers).status_code == 422
    assert client.patch(f"/api/admin/user/{uuid.uuid4()}", json={}, headers=headers).status_code == 404


def test_a_duplicate_email_is_refused_with_400(env: Env) -> None:
    client = env.build()
    refused = client.patch(
        f"/api/admin/user/{env.ids['ann']}", json={"email": "bob@example.com"}, headers=env.token("boss")
    )
    assert refused.status_code == 400 and code_of(refused) == "user.email_exists"


def test_an_admin_cannot_demote_or_disable_themselves(env: Env) -> None:
    client = env.build()
    url = f"/api/admin/user/{env.ids['boss']}"
    for body in ({"is_active": False}, {"is_superuser": False}):
        refused = client.patch(url, json=body, headers=env.token("boss"))
        assert refused.status_code == 400 and code_of(refused) == "user.cannot_demote_self"
    assert client.delete(url, headers=env.token("boss")).status_code == 400


def test_an_admin_can_delete_another_user_and_the_token_dies(env: Env) -> None:
    client = env.build()
    assert client.delete(f"/api/admin/user/{env.ids['bob']}", headers=env.token("boss")).status_code == 204
    assert client.get("/api/auth/user", headers=env.token("bob")).status_code == 401
    assert client.delete(f"/api/admin/user/{uuid.uuid4()}", headers=env.token("boss")).status_code == 404


# --- change password ----------------------------------------------------------------


def _password(client: TestClient, headers: dict[str, str], current: str, new: str):
    return client.post(
        "/api/auth/user/password", json={"currentPassword": current, "newPassword": new}, headers=headers
    )


def test_change_password_needs_the_right_current_password(env: Env) -> None:
    client = env.build()
    refused = _password(client, env.token("ann"), "wrong-password-123", NEW_PASSWORD)
    assert refused.status_code == 400 and code_of(refused) == "auth.password_incorrect"
    assert login(client, "ann").status_code == 200


def test_change_password_enforces_the_minimum_length(env: Env) -> None:
    client = env.build()
    short = _password(client, env.token("ann"), PASSWORD, "short-1")
    assert short.status_code == 422 and code_of(short) == "auth.password_too_short"
    assert login(client, "ann").status_code == 200  # unchanged


def test_the_minimum_length_follows_the_setting(env: Env) -> None:
    client = env.build(password_min_length="20")
    refused = _password(client, env.token("ann"), PASSWORD, "exactly-19-chars-ok")
    assert refused.status_code == 422
    assert _password(client, env.token("ann"), PASSWORD, "this-one-is-definitely-long").status_code == 204


def test_change_password_then_old_one_stops_working(env: Env) -> None:
    client = env.build()
    _, refresh_token = (lambda b: (b["access_token"], b["refresh_token"]))(login(client, "ann").json())
    assert _password(client, env.token("ann"), PASSWORD, NEW_PASSWORD).status_code == 204
    assert login(client, "ann", PASSWORD).status_code == 401
    assert login(client, "ann", NEW_PASSWORD).status_code == 200
    # the change ended every login
    revoked = refresh(client, refresh_token)
    assert revoked.status_code == 401 and code_of(revoked) == "auth.refresh_token_revoked"


def test_change_password_to_the_same_password_is_refused(env: Env) -> None:
    client = env.build()
    refused = _password(client, env.token("ann"), PASSWORD, PASSWORD)
    assert refused.status_code == 422 and code_of(refused) == "auth.password_unchanged"
    assert login(client, "ann").status_code == 200  # nothing changed, the old tokens still work


def test_change_password_unlocks_nothing_while_locked_and_answers_429(env: Env) -> None:
    client = env.build(login_max_attempts="2")
    for _ in range(3):
        _password(client, env.token("ann"), "wrong-password-123", NEW_PASSWORD)
    locked = _password(client, env.token("ann"), PASSWORD, NEW_PASSWORD)
    assert locked.status_code == 429 and code_of(locked) == "auth.too_many_attempts"
    assert int(locked.headers["retry-after"]) >= 1


def test_change_password_requires_a_token(env: Env) -> None:
    assert (
        env.build().post("/api/auth/user/password", json={"currentPassword": "a", "newPassword": "b"}).status_code
        == 401
    )


# --- register -----------------------------------------------------------------------


REGISTER = {"username": "newcomer", "password": "a-long-enough-password-1"}


def test_register_creates_a_user_who_can_log_in(env: Env) -> None:
    client = env.build(registration_enabled="true")
    created = client.post("/api/auth/register", json={**REGISTER, "email": "new@example.com"})
    assert created.status_code == 201, created.text
    body = created.json()
    body = body.get("data", body)
    assert body["username"] == "newcomer" and "password" not in body
    assert login(client, "newcomer", REGISTER["password"]).status_code == 200


def test_register_normalises_the_username_and_refuses_a_taken_one(env: Env) -> None:
    client = env.build(registration_enabled="true")
    assert client.post("/api/auth/register", json={**REGISTER, "username": "  NewComer "}).status_code == 201
    taken = client.post("/api/auth/register", json=REGISTER)
    assert taken.status_code == 400 and code_of(taken) == "auth.username_taken"
    existing = client.post("/api/auth/register", json={**REGISTER, "username": "ANN"})
    assert existing.status_code == 400 and code_of(existing) == "auth.username_taken"


def test_register_with_a_short_password_or_bad_fields(env: Env) -> None:
    client = env.build(registration_enabled="true")
    short = client.post("/api/auth/register", json={**REGISTER, "password": "short"})
    assert short.status_code == 422 and code_of(short) == "auth.password_too_short"
    assert client.post("/api/auth/register", json={**REGISTER, "username": "ab"}).status_code == 422
    assert client.post("/api/auth/register", json={**REGISTER, "email": "nope"}).status_code == 422
    assert client.post("/api/auth/register", json={"username": "newcomer"}).status_code == 422


def test_register_with_a_duplicate_email_is_a_400(env: Env) -> None:
    client = env.build(registration_enabled="true")
    refused = client.post("/api/auth/register", json={**REGISTER, "email": "ann@example.com"})
    assert refused.status_code == 400 and code_of(refused) == "user.username_or_email_exists"


def test_register_ignores_privileges_in_the_body(env: Env) -> None:
    client = env.build(registration_enabled="true")
    body = {**REGISTER, "is_superuser": True, "is_staff": True, "is_active": False}
    assert client.post("/api/auth/register", json=body).status_code == 201

    async def work(session: AsyncSession) -> tuple[bool, bool, bool]:
        user = await cruds.get_user_by_username(session, "newcomer")
        return user.is_superuser, user.is_staff, user.is_active

    assert env.run(work) == (False, False, True)


# --- logout -------------------------------------------------------------------------


def _records(env: Env, name: str) -> int:
    async def work(session: AsyncSession) -> int:
        rows = await session.scalars(
            select(models.RefreshTokenRecord).where(models.RefreshTokenRecord.user_id == env.ids[name])
        )
        return len(rows.all())

    return env.run(work)


def test_logout_requires_authentication(env: Env) -> None:
    assert env.build().post("/api/auth/logout").status_code == 401


def test_logout_without_a_body_revokes_every_login(env: Env) -> None:
    client = env.build()
    first = login(client, "ann").json()
    second = login(client, "ann").json()
    assert _records(env, "ann") == 2
    assert client.post("/api/auth/logout", headers=bearer(first["access_token"])).status_code == 204
    assert _records(env, "ann") == 0
    for token in (first, second):
        assert refresh(client, token["refresh_token"]).status_code == 401


def test_logout_with_a_refresh_token_revokes_only_that_login(env: Env) -> None:
    client = env.build()
    first = login(client, "ann").json()
    second = login(client, "ann").json()
    done = client.post(
        "/api/auth/logout", json={"refresh_token": first["refresh_token"]}, headers=bearer(first["access_token"])
    )
    assert done.status_code == 204
    assert _records(env, "ann") == 1
    assert refresh(client, first["refresh_token"]).status_code == 401
    assert refresh(client, second["refresh_token"]).status_code == 200


def test_logout_with_an_invalid_refresh_token_falls_back_to_revoking_everything(env: Env) -> None:
    client = env.build()
    first = login(client, "ann").json()
    login(client, "ann")
    done = client.post("/api/auth/logout", json={"refresh_token": "garbage"}, headers=bearer(first["access_token"]))
    assert done.status_code == 204
    assert _records(env, "ann") == 0


# --- refresh-token ------------------------------------------------------------------


def test_refresh_rotates_the_token_and_returns_a_working_access_token(env: Env) -> None:
    client = env.build()
    first = login(client, "ann").json()
    renewed = refresh(client, first["refresh_token"])
    assert renewed.status_code == 200, renewed.text
    body = renewed.json()
    assert body["refresh_token"] != first["refresh_token"]
    assert client.get("/api/auth/user", headers=bearer(body["access_token"])).status_code == 200
    assert _records(env, "ann") == 2  # the spent one stays marked as used inside the grace window


def test_refresh_without_rotation_keeps_the_token_valid(env: Env) -> None:
    client = env.build(refresh_token_rotation="false")
    first = login(client, "ann").json()
    renewed = refresh(client, first["refresh_token"])
    assert renewed.status_code == 200 and renewed.json()["refresh_token"] is None
    assert refresh(client, first["refresh_token"]).status_code == 200


def test_refresh_refuses_a_token_of_the_wrong_type_or_bad_signature(env: Env) -> None:
    client = env.build()
    access = create_access_token(env.ids["ann"])
    for bad in (
        access,
        "garbage",
        forge(env.ids["ann"], token_type="refresh", expires_in=timedelta(seconds=-5)),
        forge(env.ids["ann"], token_type="refresh", key="a-different-secret-key-0123456789-abcdefgh"),
        forge("not-a-uuid", token_type="refresh"),
    ):
        refused = refresh(client, bad)
        assert refused.status_code == 401 and code_of(refused) == "auth.refresh_token_invalid"


def test_refresh_refuses_an_unregistered_refresh_token(env: Env) -> None:
    refused = refresh(env.build(), create_refresh_token(env.ids["ann"]))
    assert refused.status_code == 401 and code_of(refused) == "auth.refresh_token_revoked"


def test_refresh_refuses_a_disabled_or_deleted_user(env: Env) -> None:
    client = env.build()
    ann = login(client, "ann").json()["refresh_token"]
    bob = login(client, "bob").json()["refresh_token"]
    change_user(env, "ann", is_active=False)

    async def delete(session: AsyncSession) -> None:
        user = await cruds.get_user_by_id(session, env.ids["bob"])
        await session.delete(user)
        await session.commit()

    env.run(delete)
    for token in (ann, bob, forge(uuid.uuid4(), token_type="refresh")):
        refused = refresh(client, token)
        assert refused.status_code == 401 and code_of(refused) == "auth.refresh_user_invalid"


def test_refresh_refuses_a_token_issued_before_a_password_change(env: Env) -> None:
    client = env.build()
    token = login(client, "ann").json()["refresh_token"]
    change_user(env, "ann", password_changed_at=datetime.now(UTC) + timedelta(seconds=30))
    refused = refresh(client, token)
    assert refused.status_code == 401 and code_of(refused) == "auth.refresh_token_revoked"


def test_refresh_refuses_a_legacy_token_without_jti(env: Env) -> None:
    refused = refresh(env.build(), forge(env.ids["ann"], token_type="refresh"))
    assert refused.status_code == 401 and code_of(refused) == "auth.refresh_token_revoked"


# --- lockout ------------------------------------------------------------------------


def test_the_account_locks_after_the_maximum_attempts_and_unlocks_when_the_lock_expires(env: Env) -> None:
    client = env.build(login_max_attempts="3", login_lockout_minutes="10")
    for _ in range(3):
        assert login(client, "ann", "wrong-password-123").status_code == 401
    # locked: even the right password is refused, with the same uniform answer
    refused = login(client, "ann")
    assert refused.status_code == 401 and code_of(refused) == "auth.invalid_credentials"

    async def read(session: AsyncSession) -> datetime:
        user = await cruds.get_user_by_id(session, env.ids["ann"])
        assert user.locked_until is not None
        return user.locked_until

    locked_until = env.run(read)
    assert locked_until is not None
    # a locked account answers 429 with Retry-After where the caller is authenticated
    blocked = _password(client, env.token("ann"), PASSWORD, NEW_PASSWORD)
    assert blocked.status_code == 429
    assert 1 <= int(blocked.headers["retry-after"]) <= 600

    change_user(env, "ann", locked_until=datetime.now(UTC) - timedelta(seconds=1))
    assert login(client, "ann").status_code == 200
    assert env.run(lambda s: _failed_count(s, env.ids["ann"])) == 0


async def _failed_count(session: AsyncSession, user_id: Any) -> int:
    user = await cruds.get_user_by_id(session, user_id)
    return user.failed_login_count


def test_a_right_password_resets_the_failed_counter(env: Env) -> None:
    client = env.build(login_max_attempts="3")
    for _ in range(2):
        assert login(client, "ann", "wrong-password-123").status_code == 401
    assert login(client, "ann").status_code == 200
    for _ in range(2):
        assert login(client, "ann", "wrong-password-123").status_code == 401
    assert login(client, "ann").status_code == 200  # never reached 3 in a row


def test_an_unknown_user_and_a_disabled_user_cannot_log_in(env: Env) -> None:
    client = env.build()
    assert login(client, "nobody").status_code == 401
    change_user(env, "ann", is_active=False)
    assert login(client, "ann").status_code == 401
