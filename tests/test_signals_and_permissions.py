"""Lifecycle signals and the pluggable source of permissions."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pachifast import create_app, signals
from pachifast.auth import cruds
from pachifast.auth.permission_provider import resolve_user_permissions, set_permission_provider
from pachifast.auth.schemas import UserCreate
from pachifast.config import get_settings
from pachifast.database import Base
from pachifast.presets import core_module

PASSWORD = "correct-horse-battery-0"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[tuple[TestClient, str]]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}"
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")
    monkeypatch.setenv("PACHIFAST_REGISTRATION_ENABLED", "true")
    ids: dict[str, uuid.UUID] = {}

    async def prepare() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            ids["ann"] = (await cruds.create_user(session, UserCreate(username="ann", password=PASSWORD))).id
        await engine.dispose()

    signals.clear()
    asyncio.run(prepare())
    signals.clear()
    get_settings.cache_clear()
    from pachifast.database import engine as engine_module

    engine_module._engine_cache.clear()
    engine_module._sessionmaker_cache.clear()
    with TestClient(create_app((core_module,))) as client:
        yield client, url
    signals.clear()
    set_permission_provider(None)
    get_settings.cache_clear()


def _login(client: TestClient, username: str, password: str):
    return client.post("/api/auth/token", data={"username": username, "password": password})


def test_login_logout_and_failure_signals(env) -> None:
    client, _ = env
    seen: list[tuple[str, str]] = []

    signals.connect(signals.USER_LOGGED_IN, lambda *, user, **_: seen.append(("in", user.username)))
    signals.connect(signals.LOGIN_FAILED, lambda *, username, **_: seen.append(("failed", username)))
    signals.connect(signals.USER_LOGGED_OUT, lambda *, user, **_: seen.append(("out", user.username)))

    assert _login(client, "ann", "wrong-password-0").status_code == 401
    token = _login(client, "ann", PASSWORD).json()["access_token"]
    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert seen == [("failed", "ann"), ("in", "ann"), ("out", "ann")]


def test_user_created_and_password_changed_signals(env) -> None:
    client, _ = env
    seen: list[tuple[str, str]] = []

    async def created(*, user, **_) -> None:  # a coroutine function works as well as a function
        seen.append(("created", user.username))

    signals.connect(signals.USER_CREATED, created)
    signals.connect(signals.PASSWORD_CHANGED, lambda *, user, **_: seen.append(("password", user.username)))
    body = {"username": "bob", "password": PASSWORD}
    assert client.post("/api/auth/register", json=body).status_code in (200, 201)
    token = _login(client, "bob", PASSWORD).json()["access_token"]
    change = {"currentPassword": PASSWORD, "newPassword": "another-good-password-1"}
    assert (
        client.post("/api/auth/user/password", json=change, headers={"Authorization": f"Bearer {token}"}).status_code
        == 204
    )
    assert seen == [("created", "bob"), ("password", "bob")]


def test_a_failing_handler_is_logged_and_does_not_stop_the_request_or_the_others(
    env, caplog: pytest.LogCaptureFixture
) -> None:
    client, _ = env
    seen: list[str] = []

    def broken(**_) -> None:
        raise RuntimeError("handler bug")

    signals.connect(signals.LOGIN_FAILED, broken)
    signals.connect(signals.LOGIN_FAILED, lambda *, username, **_: seen.append(username))
    assert _login(client, "ghost", "whatever-password-0").status_code == 401
    assert seen == ["ghost"]
    assert "handler bug" in caplog.text


def test_connect_validates_and_disconnect_forgets() -> None:
    signals.clear()
    with pytest.raises(ValueError):
        signals.connect("no_such_signal", lambda **_: None)

    calls: list[int] = []

    def handler(**_) -> None:
        calls.append(1)

    signals.connect(signals.USER_CREATED, handler)
    signals.connect(signals.USER_CREATED, handler)  # twice is one
    asyncio.run(signals.emit(signals.USER_CREATED, user=None))
    signals.disconnect(signals.USER_CREATED, handler)
    signals.disconnect(signals.USER_CREATED, handler)  # no error
    asyncio.run(signals.emit(signals.USER_CREATED, user=None))
    assert calls == [1]
    signals.clear()


def test_a_permission_provider_replaces_the_database_lookup(env) -> None:
    client, url = env
    user_id = asyncio.run(_user_id(url))

    async def resolve() -> list[str]:
        engine = create_async_engine(url)
        try:
            async with async_sessionmaker(engine)() as session:
                return await resolve_user_permissions(session, user_id)
        finally:
            await engine.dispose()

    assert asyncio.run(resolve()) == []  # nothing in the database

    async def provider(session, uid):
        assert uid == user_id
        return {"reports.view", "reports.edit"}

    set_permission_provider(provider)
    assert asyncio.run(resolve()) == ["reports.edit", "reports.view"]
    token = _login(client, "ann", PASSWORD).json()["access_token"]
    me = client.get("/api/auth/user", headers={"Authorization": f"Bearer {token}"}).json()
    me = me.get("data", me)
    assert sorted(me["permissions"]) == ["reports.edit", "reports.view"]
    set_permission_provider(None)
    assert asyncio.run(resolve()) == []


async def _user_id(url: str) -> uuid.UUID:
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as session:
            user = await cruds.get_user_by_username(session, "ann")
            assert user is not None
            return user.id
    finally:
        await engine.dispose()
