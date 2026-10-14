"""Flows of the activity log: the middleware that records write requests and the routes that read the log."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from pachifast import create_app
from pachifast.audit import models as audit_models  # noqa: F401
from pachifast.audit.models import ActivityLog
from pachifast.auth import cruds, models  # noqa: F401
from pachifast.auth.schemas import UserCreate
from pachifast.auth.security import create_access_token
from pachifast.config import get_settings
from pachifast.database import Base
from pachifast.mfa import models as mfa_models  # noqa: F401
from pachifast.presets import core_module

PASSWORD = "long-enough-pass-1"
WRONG_PASSWORD = "definitely-wrong-pass-9"


@dataclass
class Env:
    """A running app on SQLite with a superuser and two regular users."""

    build: Callable[..., TestClient]
    url: str
    ids: dict[str, uuid.UUID]

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

    def rows(self) -> list[ActivityLog]:
        """All activity log rows, oldest first."""

        async def work(session: AsyncSession) -> list[ActivityLog]:
            return list((await session.execute(select(ActivityLog).order_by(ActivityLog.id))).scalars().all())

        return self.run(work)  # type: ignore[return-value]

    def wait_rows(self, count: int, attempts: int = 100) -> list[ActivityLog]:
        """Poll the database until at least ``count`` rows exist (the rows are written by background tasks)."""
        rows: list[ActivityLog] = []
        for _ in range(attempts):
            rows = self.rows()
            if len(rows) >= count:
                return rows
            time.sleep(0.02)
        pytest.fail(f"expected {count} activity log rows, found {len(rows)}")

    def seed(self, *entries: dict) -> None:
        """Insert activity log rows directly, for the tests of the read routes."""

        async def work(session: AsyncSession) -> None:
            for entry in entries:
                session.add(ActivityLog(**entry))
            await session.commit()

        self.run(work)


def _entry(path: str, **fields) -> dict:
    """Keyword arguments of a log row with sensible defaults."""
    return {"method": "POST", "path": path, "status_code": 200, "username": "seed", **fields}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[Env]:
    """The app on SQLite with the activity log switched on (``build(activity_log_enabled="false")`` turns it off)."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}"
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")
    monkeypatch.setenv("PACHIFAST_ACTIVITY_LOG_ENABLED", "true")
    ids: dict[str, uuid.UUID] = {}

    async def prepare() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for name, superuser in (("boss", True), ("ann", False)):
                user = await cruds.create_user(
                    session, UserCreate(username=name, password=PASSWORD, is_superuser=superuser)
                )
                ids[name] = user.id
        await engine.dispose()

    asyncio.run(prepare())
    clients: list[TestClient] = []

    def build(**settings: str) -> TestClient:
        from pachifast.database import engine as engine_module

        for key, value in settings.items():
            monkeypatch.setenv(f"PACHIFAST_{key.upper()}", value)
        get_settings.cache_clear()
        engine_module._engine_cache.clear()
        engine_module._sessionmaker_cache.clear()
        client = TestClient(create_app((core_module,)))
        client.__enter__()
        clients.append(client)
        return client

    yield Env(build, url, ids)
    for client in clients:
        client.__exit__(None, None, None)
    get_settings.cache_clear()


def _change_password(client: TestClient, env: Env, name: str = "ann", current: str = WRONG_PASSWORD):
    """A write request that is answered 4xx (wrong current password): it must be logged all the same."""
    return client.post(
        "/api/auth/user/password",
        json={"currentPassword": current, "newPassword": "another-long-pass-2"},
        headers=env.token(name),
    )


def _items(response) -> list[dict]:
    """The ``items`` of a paginated answer, with or without the envelope."""
    body = response.json()
    body = body.get("data", body) if isinstance(body, dict) and "items" not in body else body
    return body["items"] if isinstance(body, dict) else body


# --- the middleware ----------------------------------------------------------------------


def test_authenticated_write_request_produces_a_row(env: Env) -> None:
    client = env.build()
    response = _change_password(client, env)
    assert response.status_code == 400
    (row,) = env.wait_rows(1)
    assert row.user_id == env.ids["ann"]
    assert row.username == "ann"
    assert row.method == "POST"
    assert row.path == "/api/auth/user/password"
    assert row.status_code == 400
    assert row.duration_ms is not None and row.duration_ms >= 0
    assert row.timestamp is not None


def test_get_requests_and_non_api_paths_are_not_logged(env: Env) -> None:
    client = env.build()
    assert client.get("/api/user/me", headers=env.token("ann")).status_code in (200, 404)
    client.post("/not-api/anything", headers=env.token("ann"))
    _change_password(client, env)  # the only one that counts
    rows = env.wait_rows(1)
    assert [row.path for row in rows] == ["/api/auth/user/password"]


def test_request_without_token_is_logged_without_user(env: Env) -> None:
    client = env.build()
    response = client.post("/api/auth/token", data={"username": "ann", "password": WRONG_PASSWORD})
    assert response.status_code == 401
    (row,) = env.wait_rows(1)
    assert row.user_id is None and row.username is None
    assert row.path.endswith("/token") and row.status_code == 401


def test_invalid_token_is_logged_without_user(env: Env) -> None:
    client = env.build()
    client.post("/api/auth/user/password", json={}, headers={"Authorization": "Bearer not-a-jwt"})
    (row,) = env.wait_rows(1)
    assert row.user_id is None and row.username is None


def test_passwords_and_tokens_are_never_stored(env: Env) -> None:
    """The log keeps method and path, never the body: no column of any row carries a secret."""
    client = env.build()
    _change_password(client, env)
    client.post("/api/auth/token", data={"username": "ann", "password": WRONG_PASSWORD})
    rows = env.wait_rows(2)
    access = env.token("ann")["Authorization"].split(" ", 1)[1]
    for row in rows:
        stored = " ".join(str(getattr(row, c.name)) for c in ActivityLog.__table__.columns)
        for secret in (WRONG_PASSWORD, "another-long-pass-2", PASSWORD, access):
            assert secret not in stored


def test_path_description_and_username_are_sanitised_when_stored(env: Env) -> None:
    """Control characters become spaces and over-long values are cut before the row is written."""
    from pachifast.audit.middleware import MAX_DESCRIPTION_LENGTH, MAX_PATH_LENGTH, _save_log

    env.build()
    asyncio.run(_save_log(None, "an\nn\x1b", "POST", "/api/x\ny\x00" + "p" * 600, 200, 3, "d\r\n" + "e" * 600))
    (row,) = env.wait_rows(1)
    assert row.username == "an n "
    assert row.path.startswith("/api/x y ") and len(row.path) == MAX_PATH_LENGTH and row.path.endswith("…")
    assert row.description is not None and row.description.startswith("d  ")
    assert len(row.description) == MAX_DESCRIPTION_LENGTH


def test_server_errors_are_not_logged(env: Env) -> None:
    client = env.build()

    async def boom() -> None:
        raise RuntimeError("boom")

    client.app.add_api_route("/api/_t/boom", boom, methods=["POST"])  # type: ignore[attr-defined]
    quiet = TestClient(client.app, raise_server_exceptions=False)
    assert quiet.post("/api/_t/boom", headers=env.token("ann")).status_code == 500
    _change_password(client, env)
    rows = env.wait_rows(1)
    assert [row.path for row in rows] == ["/api/auth/user/password"]


def test_log_as_template_is_interpolated(env: Env) -> None:
    client = env.build()

    async def handler(item: str) -> dict:
        return {"ok": item}

    client.app.add_api_route(  # type: ignore[attr-defined]
        "/api/_t/{item}/touch",
        handler,
        methods=["POST"],
        openapi_extra={"x-log-as": "{username} touched {item} ({unknown})"},
    )
    assert client.post("/api/_t/widget/touch", headers=env.token("ann")).status_code == 200
    (row,) = env.wait_rows(1)
    assert row.description == "ann touched widget ({unknown})"
    assert row.path == "/api/_t/widget/touch"


def test_log_as_uses_the_context_of_the_request(env: Env) -> None:
    client = env.build()

    async def handler(request: Request) -> dict:
        request.state.log_context = {"what": "gadget"}
        return {}

    client.app.add_api_route(  # type: ignore[attr-defined]
        "/api/_t/ctx", handler, methods=["PUT"], openapi_extra={"x-log-as": "changed {what}"}
    )
    client.put("/api/_t/ctx", headers=env.token("ann"))
    (row,) = env.wait_rows(1)
    assert row.description == "changed gadget"


def test_disabled_log_writes_nothing_and_mounts_no_routes(env: Env) -> None:
    client = env.build(activity_log_enabled="false")
    _change_password(client, env)
    time.sleep(0.2)
    assert env.rows() == []
    assert client.get("/api/admin/audit/logs", headers=env.token("boss")).status_code == 404


def test_superuser_can_opt_out_of_the_log(env: Env) -> None:
    """``audit.disable_activity_log`` silences a superuser; a regular user is still recorded."""

    async def opt_out(session: AsyncSession) -> None:
        await cruds.update_user_settings(session, env.ids["boss"], "audit", "disable_activity_log", True)

    env.run(opt_out)
    client = env.build()
    assert _change_password(client, env, "boss", WRONG_PASSWORD).status_code == 400
    _change_password(client, env, "ann")
    rows = env.wait_rows(1)
    time.sleep(0.1)
    assert [row.username for row in env.rows()] == ["ann"] == [row.username for row in rows]


# --- helpers of the middleware ---------------------------------------------------------


def test_interpolate_log_as_keeps_unknown_placeholders() -> None:
    from pachifast.audit.middleware import _interpolate_log_as

    text = _interpolate_log_as("{username}:{id}:{x}:{nope}", {"id": 3}, "ann", {"x": "y"})
    assert text == "ann:3:y:{nope}"
    assert _interpolate_log_as("{username}", {}, None) == "{username}"


def test_extract_user_from_request_variants() -> None:
    from starlette.requests import Request

    from pachifast.audit.middleware import _extract_user_from_request

    def request(auth: str | None) -> Request:
        headers = [(b"authorization", auth.encode())] if auth else []
        return Request({"type": "http", "headers": headers})

    user_id = uuid.uuid4()
    assert _extract_user_from_request(request(None)) == (None, None)
    assert _extract_user_from_request(request("Basic abc")) == (None, None)
    assert _extract_user_from_request(request("Bearer junk")) == (None, None)
    assert _extract_user_from_request(request(f"Bearer {create_access_token(user_id)}")) == (user_id, None)


def test_save_log_swallows_database_errors(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    """A failing write is logged as an error and never raised (the request is already answered)."""
    from pachifast.audit.middleware import _save_log

    env.build()
    from pachifast.database import engine as engine_module

    def broken(*args, **kwargs):
        raise RuntimeError("no database")

    original = engine_module.get_core_sessionmaker
    engine_module.get_core_sessionmaker = broken  # type: ignore[assignment]
    try:
        asyncio.run(_save_log(None, None, "POST", "/api/x", 200, 1))
    finally:
        engine_module.get_core_sessionmaker = original
    assert "activity log" in caplog.text.lower()


# --- the read routes ---------------------------------------------------------------------


def test_non_superuser_gets_403_and_anonymous_401(env: Env) -> None:
    client = env.build()
    for path in ("/api/admin/audit/logs", "/api/admin/audit/activity"):
        assert client.get(path, headers=env.token("ann")).status_code == 403
        assert client.get(path).status_code == 401


def test_list_returns_rows_newest_first_with_total(env: Env) -> None:
    client = env.build()
    now = datetime.now(UTC)
    env.seed(*[_entry(f"/api/p{i}", timestamp=now - timedelta(minutes=10 - i)) for i in range(3)])
    response = client.get("/api/admin/audit/logs", headers=env.token("boss"))
    assert response.status_code == 200, response.text
    body = response.json()
    body = body.get("data", body)
    assert body["total"] == 3
    assert [item["path"] for item in body["items"]] == ["/api/p2", "/api/p1", "/api/p0"]
    assert {"id", "timestamp", "user_id", "username", "method", "path", "status_code", "duration_ms"} <= set(
        body["items"][0]
    )


def test_pagination(env: Env) -> None:
    client = env.build()
    now = datetime.now(UTC)
    env.seed(*[_entry(f"/api/p{i:02d}", timestamp=now - timedelta(minutes=i)) for i in range(5)])
    first = client.get("/api/admin/audit/logs", params={"page": 1, "pageSize": 2}, headers=env.token("boss"))
    last = client.get("/api/admin/audit/logs", params={"page": 3, "pageSize": 2}, headers=env.token("boss"))
    first_body, last_body = (r.json().get("data", r.json()) for r in (first, last))
    assert first_body["total"] == 5 and first_body["pageSize"] == 2
    assert [i["path"] for i in first_body["items"]] == ["/api/p00", "/api/p01"]
    assert [i["path"] for i in last_body["items"]] == ["/api/p04"]


def test_filters_user_method_status_and_search(env: Env) -> None:
    client = env.build()
    headers = env.token("boss")
    env.seed(
        _entry("/api/a", user_id=env.ids["ann"], username="ann", method="DELETE", status_code=404),
        _entry("/api/b", user_id=env.ids["boss"], username="boss", method="POST", status_code=200),
        _entry("/api/c", username="Zed", method="PUT", status_code=200),
    )

    def paths(**params) -> list[str]:
        response = client.get("/api/admin/audit/logs", params=params, headers=headers)
        assert response.status_code == 200, response.text
        return sorted(i["path"] for i in _items(response))

    assert paths(userId=str(env.ids["ann"])) == ["/api/a"]
    assert paths(method="PUT") == ["/api/c"]
    assert paths(statusCode=200) == ["/api/b", "/api/c"]
    assert paths(search="zed") == ["/api/c"]


def test_date_range_is_inclusive(env: Env) -> None:
    client = env.build()
    base = datetime(2025, 1, 10, 12, 0, tzinfo=UTC)
    env.seed(*[_entry(f"/api/d{i}", timestamp=base + timedelta(days=i)) for i in range(4)])
    headers = env.token("boss")

    def paths(**params) -> list[str]:
        response = client.get("/api/admin/audit/logs", params=params, headers=headers)
        assert response.status_code == 200, response.text
        return sorted(i["path"] for i in _items(response))

    both = paths(dateFrom=(base + timedelta(days=1)).isoformat(), dateTo=(base + timedelta(days=2)).isoformat())
    assert both == ["/api/d1", "/api/d2"]
    assert paths(dateFrom=(base + timedelta(days=3)).isoformat()) == ["/api/d3"]
    assert paths(dateTo=base.isoformat()) == ["/api/d0"]


def test_path_contains_treats_wildcards_as_plain_characters(env: Env) -> None:
    client = env.build()
    env.seed(
        _entry("/api/100%/done"),
        _entry("/api/1000/done"),
        _entry("/api/a_b"),
        _entry("/api/axb"),
        _entry("/api/back\\slash"),
    )
    headers = env.token("boss")

    def paths(value: str) -> list[str]:
        response = client.get("/api/admin/audit/logs", params={"pathContains": value}, headers=headers)
        assert response.status_code == 200, response.text
        return sorted(i["path"] for i in _items(response))

    assert paths("100%") == ["/api/100%/done"]
    assert paths("a_b") == ["/api/a_b"]
    assert paths("%") == ["/api/100%/done"]
    assert paths("_") == ["/api/a_b"]
    assert paths("\\") == ["/api/back\\slash"]
    assert paths("done") == ["/api/100%/done", "/api/1000/done"]


def _paths(client: TestClient, env: Env, route: str, **params) -> list[str]:
    response = client.get(route, params=params, headers=env.token("boss"))
    assert response.status_code == 200, response.text
    return sorted(i["path"] for i in _items(response))


@pytest.mark.parametrize("route", ["/api/admin/audit/logs", "/api/admin/audit/activity"])
def test_exclude_paths_explicit_and_empty(env: Env, route: str) -> None:
    client = env.build()
    env.seed(_entry("/api/auth/refresh-token"), _entry("/api/auth/token"), _entry("/api/items"))
    assert _paths(client, env, route, excludePaths="") == ["/api/auth/refresh-token", "/api/auth/token", "/api/items"]
    assert _paths(client, env, route, excludePaths="auth/token, items ,") == ["/api/auth/refresh-token"]


def test_activity_route_hides_refresh_token_by_default(env: Env) -> None:
    client = env.build()
    env.seed(_entry("/api/auth/refresh-token"), _entry("/api/items"))
    assert _paths(client, env, "/api/admin/audit/activity") == ["/api/items"]


def test_logs_route_hides_refresh_token_by_default(env: Env) -> None:
    client = env.build()
    env.seed(_entry("/api/auth/refresh-token"), _entry("/api/items"))
    assert _paths(client, env, "/api/admin/audit/logs") == ["/api/items"]


def test_activity_route_limit_filters_and_order(env: Env) -> None:
    client = env.build()
    now = datetime.now(UTC)
    env.seed(
        *[
            _entry(f"/api/n{i}", method="DELETE" if i % 2 else "POST", timestamp=now - timedelta(minutes=10 - i))
            for i in range(6)
        ],
        _entry("/api/auth/refresh-token", timestamp=now),
    )
    headers = env.token("boss")
    recent = client.get("/api/admin/audit/activity", params={"limit": 2}, headers=headers)
    assert recent.status_code == 200, recent.text
    assert [i["path"] for i in _items(recent)] == ["/api/n5", "/api/n4"]  # refresh-token hidden by default
    deletes = client.get("/api/admin/audit/activity", params={"method": "DELETE"}, headers=headers)
    assert sorted(i["path"] for i in _items(deletes)) == ["/api/n1", "/api/n3", "/api/n5"]
    searched = client.get("/api/admin/audit/activity", params={"search": "n2", "pathContains": "n"}, headers=headers)
    assert [i["path"] for i in _items(searched)] == ["/api/n2"]
    ranged = client.get(
        "/api/admin/audit/activity",
        params={"dateFrom": (now - timedelta(minutes=6)).isoformat(), "statusCode": 200, "userId": str(uuid.uuid4())},
        headers=headers,
    )
    assert _items(ranged) == []
    assert client.get("/api/admin/audit/activity", params={"limit": 0}, headers=headers).status_code == 422


def test_end_to_end_request_then_read_through_the_api(env: Env) -> None:
    client = env.build()
    _change_password(client, env)
    env.wait_rows(1)
    response = client.get(
        "/api/admin/audit/logs", params={"userId": str(env.ids["ann"]), "method": "POST"}, headers=env.token("boss")
    )
    (item,) = _items(response)
    assert item["username"] == "ann" and item["path"] == "/api/auth/user/password" and item["status_code"] == 400


def test_the_list_has_a_default_window_and_a_largest_page(env: Env) -> None:
    client = env.build()
    now = datetime.now(UTC)
    env.seed(*[_entry(f"/api/p{i:03d}", timestamp=now - timedelta(seconds=i)) for i in range(230)])
    bare = client.get("/api/admin/audit/logs", headers=env.token("boss")).json()
    bare = bare.get("data", bare)
    assert (bare["total"], len(bare["items"]), bare["page"], bare["pageSize"]) == (230, 25, 1, 25)
    huge = client.get("/api/admin/audit/logs", params={"pageSize": 100000}, headers=env.token("boss")).json()
    huge = huge.get("data", huge)
    assert (len(huge["items"]), huge["pageSize"]) == (200, 200)


def test_requests_to_paths_without_a_route_are_not_logged(env: Env) -> None:
    client = env.build()
    assert client.post("/api/does-not-exist", json={}).status_code == 404
    assert client.post("/api/auth/token", data={"username": "ann", "password": "wrong-password-0"}).status_code == 401
    rows = env.wait_rows(1)
    assert [row.path for row in rows] == ["/api/auth/token"]  # the failed login stays in the log
