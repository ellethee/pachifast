"""WebSocket events: audience is superusers plus the concerned user, unless the type is listed as public."""

import asyncio
import os
import time
import uuid
from datetime import timedelta

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pachifast import create_app
from pachifast.audit import models as audit_models  # noqa: F401
from pachifast.auth import cruds, models  # noqa: F401
from pachifast.auth.schemas import UserCreate
from pachifast.auth.security import create_access_token, create_refresh_token
from pachifast.config import get_settings
from pachifast.database import Base
from pachifast.events.broadcaster import EventBroadcaster
from pachifast.mfa import models as mfa_models  # noqa: F401
from pachifast.presets import core_module


class FakeSocket:
    """Minimal WebSocket double that records what it is sent."""

    def __init__(self, fail: bool = False) -> None:
        self.sent: list[str] = []
        self.fail = fail

    async def accept(self) -> None:
        return None

    async def send_text(self, payload: str) -> None:
        if self.fail:
            raise RuntimeError("closed")
        self.sent.append(payload)


def _run(coro):
    return asyncio.run(coro)


async def _room(broadcaster: EventBroadcaster):
    """One superuser, two regular users and an anonymous-id connection."""
    boss, ann, bob = FakeSocket(), FakeSocket(), FakeSocket()
    await broadcaster.connect(boss, user_id="boss", is_superuser=True)
    await broadcaster.connect(ann, user_id="ann")
    await broadcaster.connect(bob, user_id="bob")
    return boss, ann, bob


# --- audience logic ---------------------------------------------------------


def test_default_audience_is_superusers_only() -> None:
    async def scenario() -> None:
        broadcaster = EventBroadcaster()
        boss, ann, bob = await _room(broadcaster)
        await broadcaster.emit("login", {"user_id": "ann"})
        assert len(boss.sent) == 1 and ann.sent == [] and bob.sent == []

    _run(scenario())


def test_concerned_user_receives_their_own_event_only() -> None:
    async def scenario() -> None:
        broadcaster = EventBroadcaster()
        boss, ann, bob = await _room(broadcaster)
        await broadcaster.emit("user_permissions_changed", {"userId": "ann"}, to_users=["ann"])
        assert len(boss.sent) == 1 and len(ann.sent) == 1 and bob.sent == []
        await broadcaster.emit("user_permissions_changed", {"userId": "bob"}, to_users=[uuid.UUID(int=1), "bob"])
        assert len(boss.sent) == 2 and len(ann.sent) == 1 and len(bob.sent) == 1

    _run(scenario())


def test_public_events_reach_every_client() -> None:
    async def scenario() -> None:
        broadcaster = EventBroadcaster()
        boss, ann, bob = await _room(broadcaster)
        await broadcaster.emit("login", {"user_id": "ann"}, public=True)
        assert len(boss.sent) == len(ann.sent) == len(bob.sent) == 1

    _run(scenario())


def test_dead_sockets_are_dropped_and_no_recipients_is_fine() -> None:
    async def scenario() -> None:
        broadcaster = EventBroadcaster()
        await broadcaster.emit("login", {})  # nobody connected
        dead, alive = FakeSocket(fail=True), FakeSocket()
        await broadcaster.connect(dead, user_id="a", is_superuser=True)
        await broadcaster.connect(alive, user_id="b", is_superuser=True)
        await broadcaster.emit("login", {})
        assert len(alive.sent) == 1 and dead not in broadcaster._connections

    _run(scenario())


def test_public_types_setting_accepts_a_comma_separated_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACHIFAST_EVENTS_PUBLIC_TYPES", "login, activity")
    get_settings.cache_clear()
    try:
        assert get_settings().events_public_types == ["login", "activity"]
    finally:
        get_settings.cache_clear()
    monkeypatch.delenv("PACHIFAST_EVENTS_PUBLIC_TYPES")
    get_settings.cache_clear()
    assert get_settings().events_public_types == []  # default: nothing is public
    get_settings.cache_clear()


# --- end to end with real WebSockets ----------------------------------------


@pytest.fixture
def events_app(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """App with events enabled on SQLite; yields ``(client, tokens, ids)`` for boss, ann and bob."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}"
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_EVENTS_BROADCAST_ENABLED", "true")
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")
    monkeypatch.setenv("PACHIFAST_EVENTS_ALLOW_QUERY_TOKEN", "true")  # the legacy ?token= path; off by default

    async def prepare():
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        users = {}
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for name, superuser in (("boss", True), ("ann", False), ("bob", False), ("gone", False)):
                users[name] = await cruds.create_user(
                    session,
                    UserCreate(
                        username=name,
                        password="long-enough-pass",
                        is_superuser=superuser,
                        is_active=name != "gone",
                    ),
                )
        await engine.dispose()
        return users

    users = _run(prepare())
    tokens = {name: create_access_token(user.id) for name, user in users.items()}

    def build(public_types: str = ""):
        if public_types:
            monkeypatch.setenv("PACHIFAST_EVENTS_PUBLIC_TYPES", public_types)
        else:
            monkeypatch.delenv("PACHIFAST_EVENTS_PUBLIC_TYPES", raising=False)
        get_settings.cache_clear()
        return TestClient(create_app((core_module,)))

    yield build, tokens, {name: user.id for name, user in users.items()}
    get_settings.cache_clear()


def _ws(client: TestClient, token: str):
    return client.websocket_connect(f"/api/core/events/ws?token={token}")


def test_regular_user_no_longer_sees_other_users_login_events(events_app) -> None:
    build, tokens, ids = events_app
    client = build()
    with _ws(client, tokens["boss"]) as boss, _ws(client, tokens["ann"]) as ann:
        assert boss.receive_json()["type"] == "snapshot" and ann.receive_json() == {"type": "snapshot", "data": []}

        login = client.post("/api/auth/token", data={"username": "bob", "password": "long-enough-pass"})
        assert login.status_code == 200
        event = boss.receive_json()
        assert event["type"] == "login" and event["data"]["username"] == "bob"

        # Ann is concerned by nothing yet: the first thing she gets is the event addressed to her, not bob's login.
        headers = {"Authorization": f"Bearer {tokens['boss']}"}
        changed = client.put(
            f"/api/admin/permissions/users/{ids['ann']}/groups", json={"groupIds": []}, headers=headers
        )
        assert changed.status_code == 200, changed.text
        received = ann.receive_json()
        assert received["type"] == "user_permissions_changed"
        assert received["data"] == {"userId": str(ids["ann"])}
        assert boss.receive_json()["type"] == "user_permissions_changed"


def test_login_events_reach_everyone_when_listed_as_public(events_app) -> None:
    build, tokens, _ = events_app
    client = build(public_types="login")
    with _ws(client, tokens["boss"]) as boss, _ws(client, tokens["ann"]) as ann:
        boss.receive_json()
        ann.receive_json()
        assert (
            client.post("/api/auth/token", data={"username": "bob", "password": "long-enough-pass"}).status_code == 200
        )
        for socket in (boss, ann):
            event = socket.receive_json()
            assert event["type"] == "login" and event["data"]["username"] == "bob"


def test_logout_emits_an_event_to_superusers_only_unless_public(events_app) -> None:
    build, tokens, ids = events_app
    client = build()
    with _ws(client, tokens["boss"]) as boss, _ws(client, tokens["ann"]) as ann:
        boss.receive_json()
        ann.receive_json()
        response = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {tokens['bob']}"})
        assert response.status_code == 204 and response.content == b""
        event = boss.receive_json()
        assert event["type"] == "logout" and event["data"]["username"] == "bob" and "userId" in event["data"]
        # ann is not a superuser and the event is not public: her next message is the one addressed to her
        headers = {"Authorization": f"Bearer {tokens['boss']}"}
        changed = client.put(
            f"/api/admin/permissions/users/{ids['ann']}/groups", json={"groupIds": []}, headers=headers
        )
        assert changed.status_code == 200
        assert ann.receive_json()["type"] == "user_permissions_changed"


def test_logout_event_reaches_everyone_when_listed_as_public(events_app) -> None:
    build, tokens, _ = events_app
    client = build(public_types="logout")
    with _ws(client, tokens["boss"]) as boss, _ws(client, tokens["ann"]) as ann:
        boss.receive_json()
        ann.receive_json()
        assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {tokens['bob']}"}).status_code == 204
        for socket in (boss, ann):
            assert socket.receive_json()["type"] == "logout"


def test_logout_requires_a_valid_access_token(events_app) -> None:
    build, tokens, ids = events_app
    client = build()
    assert client.post("/api/auth/logout").status_code == 401
    assert client.post("/api/auth/logout", headers={"Authorization": "Bearer nope"}).status_code == 401
    refresh = create_refresh_token(str(ids["bob"]))
    assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {refresh}"}).status_code == 401


# --- connections of users that change while connected -------------------------


def _closed_code(ws) -> int:
    """Read from a socket until the server closes it and return the close code."""
    message = ws.receive()
    assert message["type"] == "websocket.close", message
    return message["code"]


def _patch(client: TestClient, token: str, user_id, body: dict):
    return client.patch(f"/api/admin/user/{user_id}", json=body, headers={"Authorization": f"Bearer {token}"})


def test_deactivating_a_user_closes_their_socket_with_4401(events_app) -> None:
    build, tokens, ids = events_app
    client = build()
    with _ws(client, tokens["ann"]) as ann, _ws(client, tokens["bob"]) as bob:
        ann.receive_json()
        bob.receive_json()
        assert _patch(client, tokens["boss"], ids["ann"], {"is_active": False}).status_code == 200
        assert _closed_code(ann) == 4401
        # the other user is untouched
        assert _patch(client, tokens["boss"], ids["bob"], {"first_name": "Bob"}).status_code == 200
        assert client.post("/api/auth/logout", headers={"Authorization": f"Bearer {tokens['bob']}"}).status_code == 204


def test_changing_a_password_closes_the_socket_with_4401(events_app) -> None:
    build, tokens, ids = events_app
    client = build()
    with _ws(client, tokens["ann"]) as ann:
        ann.receive_json()
        assert _patch(client, tokens["boss"], ids["ann"], {"password": "another-long-pass"}).status_code == 200
        assert _closed_code(ann) == 4401


def test_demoting_a_superuser_closes_the_socket_with_4403(events_app) -> None:
    """A demoted superuser must not keep receiving everybody's events; 4403 tells the client to reconnect."""
    build, tokens, ids = events_app
    client = build()
    with _ws(client, tokens["bob"]) as bob:  # bob is promoted first, then demoted
        bob.receive_json()
        assert _patch(client, tokens["boss"], ids["bob"], {"is_superuser": True}).status_code == 200
        assert _closed_code(bob) == 4403
    with _ws(client, tokens["bob"]) as bob:
        assert bob.receive_json() == {"type": "snapshot", "data": []}  # reconnects as a superuser
        assert _patch(client, tokens["boss"], ids["bob"], {"is_superuser": False}).status_code == 200
        assert _closed_code(bob) == 4403


def test_deleting_a_user_closes_their_socket_with_4401(events_app) -> None:
    build, tokens, ids = events_app
    client = build()
    with _ws(client, tokens["ann"]) as ann:
        ann.receive_json()
        deleted = client.delete(f"/api/admin/user/{ids['ann']}", headers={"Authorization": f"Bearer {tokens['boss']}"})
        assert deleted.status_code == 204 and deleted.content == b""
        assert _closed_code(ann) == 4401


def test_periodic_check_closes_a_socket_changed_behind_the_servers_back(
    events_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changes made outside the API (the CLI, another process) are caught by the periodic check."""
    from sqlalchemy import update as sql_update

    monkeypatch.setattr("pachifast.events.root.PING_INTERVAL_SECONDS", 0.2)
    build, tokens, ids = events_app
    client = build()
    url = os.environ["PACHIFAST_AUTH_DATABASE_URL"]

    async def deactivate() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(sql_update(models.User).where(models.User.id == ids["ann"]).values(is_active=False))
        await engine.dispose()

    with _ws(client, tokens["ann"]) as ann, _ws(client, tokens["bob"]) as bob:
        ann.receive_json()
        bob.receive_json()
        assert ann.receive_json() == {"type": "ping"}  # nothing changed: only a ping
        _run(deactivate())
        for _ in range(20):  # skip the pings sent before the check sees the change
            message = ann.receive()
            if message["type"] == "websocket.close":
                assert message["code"] == 4401
                break
        else:
            pytest.fail("the socket of the deactivated user was not closed")
        assert bob.receive_json() == {"type": "ping"}  # bob is still connected


def test_periodic_check_survives_a_database_failure(events_app, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pachifast.events.root.PING_INTERVAL_SECONDS", 0.2)
    build, tokens, _ = events_app
    client = build()
    with _ws(client, tokens["ann"]) as ann:
        ann.receive_json()

        async def broken(user_id, issued_at):
            raise RuntimeError("database is down")

        monkeypatch.setattr("pachifast.events.root._current_state", broken)
        assert ann.receive_json() == {"type": "ping"}  # still open


# --- handshake and close codes ----------------------------------------------


def _first_message(client: TestClient, query: str) -> dict:
    """Open the socket and return the first message: entering fails if the server closes before ``accept``."""
    with client.websocket_connect(f"/api/core/events/ws{query}") as ws:
        return ws.receive()


def _assert_closed_with(message: dict, code: int) -> None:
    assert message["type"] == "websocket.close" and message["code"] == code, message


def test_bad_credentials_are_accepted_then_closed_with_4401(events_app) -> None:
    """The handshake completes and the client gets 4401: closing before accept would be an HTTP 403 in a browser."""
    import jwt

    build, tokens, ids = events_app
    client = build()
    settings = get_settings()
    stale_iat = int(time.time()) - 3600
    revoked = jwt.encode(  # issued before the user's last password change
        {"sub": str(ids["ann"]), "type": "access", "iat": stale_iat, "exp": stale_iat + 7200},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    cases = {
        "no token": "",
        "empty token": "?token=",
        "garbage token": "?token=abc",
        "refresh token": f"?token={create_refresh_token(ids['ann'])}",
        "unknown user": f"?token={create_access_token(uuid.uuid4())}",
        "inactive user": f"?token={tokens['gone']}",
        "revoked token": f"?token={revoked}",
    }
    for label, query in cases.items():
        message = _first_message(client, query)
        assert message["type"] == "websocket.close" and message["code"] == 4401, (label, message)


def test_valid_token_still_connects(events_app) -> None:
    build, tokens, _ = events_app
    with _ws(build(), tokens["ann"]) as ws:
        assert ws.receive_json() == {"type": "snapshot", "data": []}


def test_unexpected_error_closes_with_1011_and_is_logged(
    events_app, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failure that is not about credentials (e.g. the database) must not look like a bad token."""
    build, tokens, _ = events_app
    client = build()

    async def broken(session, user_id):
        raise RuntimeError("database is down")

    monkeypatch.setattr("pachifast.auth.cruds.get_user_by_id", broken)
    with caplog.at_level("ERROR", logger="pachifast.events.root"):
        message = _first_message(client, f"?token={tokens['ann']}")
    _assert_closed_with(message, 1011)
    assert any("authentication failed unexpectedly" in record.getMessage() for record in caplog.records)
    assert any(record.exc_info for record in caplog.records)  # the traceback is in the log, not in the response


def test_superuser_snapshot_is_empty_when_the_activity_table_is_missing(
    events_app, caplog: pytest.LogCaptureFixture
) -> None:
    """The activity log table is not created by the auth migrations: the socket must survive without it."""
    from sqlalchemy import text

    build, tokens, _ = events_app
    url = os.environ["PACHIFAST_AUTH_DATABASE_URL"]

    async def drop_activity_table() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE pachifast_activity_log"))
        await engine.dispose()

    _run(drop_activity_table())
    with caplog.at_level("WARNING", logger="pachifast.events.root"):
        with _ws(build(), tokens["boss"]) as ws:
            assert ws.receive_json() == {"type": "snapshot", "data": []}
    assert any("snapshot not available" in record.getMessage() for record in caplog.records)


# --- one-time tickets -------------------------------------------------------


def _ticket(client: TestClient, token: str) -> str:
    response = client.post("/api/core/events/ticket", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["expires_in"] == 30
    return body["ticket"]


def test_ticket_requires_authentication(events_app) -> None:
    build, _, _ = events_app
    assert build().post("/api/core/events/ticket").status_code == 401


def test_ticket_opens_the_socket_once(events_app) -> None:
    build, tokens, _ = events_app
    client = build()
    ticket = _ticket(client, tokens["ann"])
    with client.websocket_connect(f"/api/core/events/ws?ticket={ticket}") as ws:
        assert ws.receive_json() == {"type": "snapshot", "data": []}
    _assert_closed_with(_first_message(client, f"?ticket={ticket}"), 4401)


def test_unknown_or_expired_ticket_is_refused(events_app, monkeypatch: pytest.MonkeyPatch) -> None:
    from pachifast.events import tickets

    build, tokens, _ = events_app
    client = build()
    _assert_closed_with(_first_message(client, "?ticket=nope"), 4401)
    ticket = _ticket(client, tokens["ann"])
    real = tickets.utcnow
    monkeypatch.setattr(tickets, "utcnow", lambda: real() + timedelta(seconds=tickets.TICKET_TTL_SECONDS + 1))
    _assert_closed_with(_first_message(client, f"?ticket={ticket}"), 4401)


def test_ticket_of_a_user_deactivated_meanwhile_is_refused(events_app) -> None:
    build, tokens, ids = events_app
    client = build()
    ticket = _ticket(client, tokens["ann"])

    async def deactivate() -> None:
        from pachifast.database.engine import get_core_sessionmaker

        async with get_core_sessionmaker()() as session:
            user = await cruds.get_user_by_id(session, ids["ann"])
            user.is_active = False
            session.add(user)
            await session.commit()

    _run(deactivate())
    _assert_closed_with(_first_message(client, f"?ticket={ticket}"), 4401)


def test_query_token_can_be_disabled(events_app, monkeypatch: pytest.MonkeyPatch) -> None:
    build, tokens, _ = events_app
    monkeypatch.setenv("PACHIFAST_EVENTS_ALLOW_QUERY_TOKEN", "false")
    client = build()
    _assert_closed_with(_first_message(client, f"?token={tokens['ann']}"), 4401)
    ticket = _ticket(client, tokens["ann"])
    with client.websocket_connect(f"/api/core/events/ws?ticket={ticket}") as ws:
        assert ws.receive_json()["type"] == "snapshot"


def test_cache_store_works_the_same(events_app, monkeypatch: pytest.MonkeyPatch) -> None:
    from pachifast.cache import reset_cache

    build, tokens, _ = events_app
    monkeypatch.setenv("PACHIFAST_EVENTS_TICKET_STORE", "cache")
    reset_cache()
    client = build()
    ticket = _ticket(client, tokens["ann"])
    with client.websocket_connect(f"/api/core/events/ws?ticket={ticket}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
    _assert_closed_with(_first_message(client, f"?ticket={ticket}"), 4401)


def test_only_the_hash_of_a_ticket_is_stored(events_app) -> None:
    build, tokens, _ = events_app
    client = build()
    ticket = _ticket(client, tokens["ann"])

    async def stored() -> list[str]:
        from sqlalchemy import select

        from pachifast.database.engine import get_core_sessionmaker
        from pachifast.events.models import WsTicket

        async with get_core_sessionmaker()() as session:
            return [row.ticket_hash for row in (await session.execute(select(WsTicket))).scalars()]

    hashes = _run(stored())
    assert len(hashes) == 1 and ticket not in hashes[0] and len(hashes[0]) == 64


def test_websocket_dependency_authenticates_without_a_request(events_app) -> None:
    """``get_current_active_ws_user`` used to fail with a TypeError: a WebSocket scope has no ``Request``."""
    from typing import Annotated

    from fastapi import Depends, WebSocket

    from pachifast.auth.dependencies import get_current_active_ws_user

    build, tokens, _ = events_app
    client = build()

    @client.app.websocket("/ws-auth")  # type: ignore[attr-defined]
    async def ws_auth(ws: WebSocket, user: Annotated[object, Depends(get_current_active_ws_user)]) -> None:
        await ws.accept()
        await ws.send_json({"username": user.username})  # type: ignore[attr-defined]
        await ws.close()

    with client.websocket_connect(f"/ws-auth?token={tokens['ann']}") as ws:
        assert ws.receive_json() == {"username": "ann"}


def test_user_setting_key_accepts_camel_case_and_rejects_unknown_keys(events_app) -> None:
    build, tokens, _ = events_app
    client = build()
    headers = {"Authorization": f"Bearer {tokens['boss']}"}
    base = "/api/auth/user/settings/audit"
    camel = client.put(f"{base}/disableActivityLog", json={"value": True}, headers=headers)
    snake = client.put(f"{base}/disable_activity_log", json={"value": False}, headers=headers)
    unknown = client.put(f"{base}/nope", json={"value": 1}, headers=headers)
    assert camel.status_code == 200 and snake.status_code == 200
    assert unknown.status_code == 422
    assert unknown.json()["detail"][0]["code"] == "user.settings_key_invalid"


def test_admin_deletes_answer_204_without_a_body(events_app) -> None:
    build, tokens, _ = events_app
    client = build()
    headers = {"Authorization": f"Bearer {tokens['boss']}"}
    perm = client.post(
        "/api/admin/permissions/permissions", json={"codename": "x.y", "description": ""}, headers=headers
    )
    group = client.post("/api/admin/permissions/groups", json={"name": "g1", "description": ""}, headers=headers)
    assert perm.status_code in (200, 201) and group.status_code in (200, 201)
    perm_id = perm.json()["data"]["id"]
    group_id = group.json()["data"]["id"]
    for path in (f"permissions/{perm_id}", f"groups/{group_id}"):
        response = client.delete(f"/api/admin/permissions/{path}", headers=headers)
        assert response.status_code == 204 and response.content == b""
    assert client.delete(f"/api/admin/permissions/groups/{group_id}", headers=headers).status_code == 404


def test_a_slow_client_does_not_hold_up_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    from pachifast.events import broadcaster as broadcaster_module

    monkeypatch.setattr(broadcaster_module, "SEND_TIMEOUT", 0.05)

    class StuckSocket(FakeSocket):
        async def send_text(self, payload: str) -> None:
            await asyncio.sleep(10)

    async def scenario() -> None:
        broadcaster = EventBroadcaster()
        stuck, alive = StuckSocket(), FakeSocket()
        await broadcaster.connect(stuck, user_id="a", is_superuser=True)
        await broadcaster.connect(alive, user_id="b", is_superuser=True)
        await asyncio.wait_for(broadcaster.emit("login", {}), timeout=2)
        assert len(alive.sent) == 1 and stuck not in broadcaster._connections

    _run(scenario())
