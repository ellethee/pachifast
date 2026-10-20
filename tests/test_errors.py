"""Uniform error format: ``{"detail": [{"loc", "msg", "type"}]}`` for every error response."""

import asyncio
import os

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pachifast import create_app
from pachifast.auth import cruds, models  # noqa: F401
from pachifast.auth.schemas import UserCreate
from pachifast.auth.security import create_access_token
from pachifast.config import get_settings
from pachifast.database import Base
from pachifast.database.helpers import get_order_by_clauses
from pachifast.errors import InvalidOrderByError, error_detail
from pachifast.presets import core_module


def _assert_uniform(body: dict) -> list[dict]:
    """Assert the body has the uniform shape and return the detail items."""
    assert set(body) == {"detail"}
    detail = body["detail"]
    assert isinstance(detail, list) and detail
    for item in detail:
        assert {"loc", "msg", "type"} <= set(item)
        assert isinstance(item["loc"], list) and isinstance(item["msg"], str)
    return detail


@pytest.fixture
def app_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A fresh app on a SQLite auth database, debug off; yields ``(app, database_url)``."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}"
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")
    get_settings.cache_clear()
    yield create_app((core_module,)), url
    get_settings.cache_clear()


def test_route_not_found_and_method_not_allowed_use_the_uniform_list(app_env) -> None:
    client = TestClient(app_env[0])
    missing = client.get("/api/nope")
    assert missing.status_code == 404
    assert _assert_uniform(missing.json()) == error_detail("Not Found")
    wrong_method = client.post("/api/health/")
    assert wrong_method.status_code == 405
    assert _assert_uniform(wrong_method.json())[0]["msg"] == "Method Not Allowed"


def test_unauthorized_errors_are_lists_and_keep_the_www_authenticate_header(app_env) -> None:
    client = TestClient(app_env[0])
    anonymous = client.get("/api/auth/user")
    assert anonymous.status_code == 401
    assert _assert_uniform(anonymous.json())[0]["loc"] == ["header", "Authorization"]

    bad_token = client.get("/api/auth/user", headers={"Authorization": "Bearer abc"})
    assert bad_token.status_code == 401
    assert _assert_uniform(bad_token.json()) == [
        {
            "loc": ["header", "Authorization"],
            "msg": "Invalid token.",
            "type": "value_error",
            "code": "auth.token_invalid",
        }
    ]
    assert bad_token.headers["www-authenticate"] == "Bearer"


def test_string_and_list_details_of_custom_http_exceptions(app_env) -> None:
    app = app_env[0]

    @app.get("/teapot")
    async def teapot():
        raise HTTPException(418, detail="I am a teapot", headers={"X-Test": "1"})

    @app.get("/custom")
    async def custom():
        raise HTTPException(400, detail=error_detail("bad field", loc=("body", "name"), type="value_error"))

    client = TestClient(app)
    teapot_response = client.get("/teapot")
    assert teapot_response.status_code == 418 and teapot_response.headers["x-test"] == "1"
    assert _assert_uniform(teapot_response.json()) == error_detail("I am a teapot")
    custom_response = client.get("/custom")
    assert custom_response.status_code == 400
    assert _assert_uniform(custom_response.json()) == error_detail(
        "bad field", loc=("body", "name"), type="value_error"
    )


def test_validation_errors_keep_their_uniform_list(app_env) -> None:
    response = TestClient(app_env[0]).post("/api/auth/refresh-token", json={"refresh_token": 5})
    assert response.status_code == 422
    assert _assert_uniform(response.json())[0]["loc"] == ["body", "refresh_token"]


def test_unexpected_errors_return_a_json_500_without_leaking_the_exception(app_env) -> None:
    app = app_env[0]

    @app.get("/boom")
    async def boom():
        raise RuntimeError("secret internal detail")

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    assert response.status_code == 500
    assert _assert_uniform(response.json()) == error_detail("Internal Server Error", type="server_error")
    assert "secret internal detail" not in response.text


def test_order_by_helper_raises_the_dedicated_error() -> None:
    assert get_order_by_clauses(models.User, "-createdAt,username")  # camelCase and snake_case fields are fine
    with pytest.raises(InvalidOrderByError, match="nonexistent"):
        get_order_by_clauses(models.User, "nonexistent")
    assert issubclass(InvalidOrderByError, ValueError)  # existing callers catching ValueError keep working


def test_invalid_order_by_on_a_list_is_a_422_not_a_500(app_env) -> None:
    app, url = app_env

    async def prepare() -> str:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            admin = await cruds.create_user(
                session, UserCreate(username="boss", password="long-enough-pass", is_superuser=True)
            )
        await engine.dispose()
        return create_access_token(admin.id)

    headers = {"Authorization": f"Bearer {asyncio.run(prepare())}"}
    client = TestClient(app, raise_server_exceptions=False)

    ok = client.get("/api/admin/user?orderBy=-createdAt,username", headers=headers)
    assert ok.status_code == 200

    bad = client.get("/api/admin/user?orderBy=nonexistent", headers=headers)
    assert bad.status_code == 422
    detail = _assert_uniform(bad.json())
    assert detail[0]["loc"] == ["query", "orderBy"] and "nonexistent" in detail[0]["msg"]
    assert detail[0]["code"] == "api.invalid_order_by" and detail[0]["params"] == {"field": "nonexistent"}


def test_debug_is_off_by_default_so_a_500_never_shows_a_traceback(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Without PACHIFAST_DEBUG an unexpected error answers with the uniform JSON, not Starlette's debug page."""
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}")
    monkeypatch.delenv("PACHIFAST_DEBUG", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().debug is False
        app = create_app((core_module,))

        @app.get("/boom")
        async def boom():
            raise RuntimeError("secret internal detail")

        response = TestClient(app, raise_server_exceptions=False).get("/boom")
    finally:
        get_settings.cache_clear()
    assert response.status_code == 500
    assert _assert_uniform(response.json())[0]["type"] == "server_error"
    assert "secret internal detail" not in response.text and "Traceback" not in response.text


def test_log_level_follows_debug_and_the_log_level_setting() -> None:
    """Production (debug off) must not log at DEBUG: SQL drivers and libraries are very chatty and may log data."""
    import logging
    from types import SimpleNamespace

    from pachifast.app_factory import _log_level

    assert _log_level(SimpleNamespace(debug=False, log_level="")) == logging.INFO
    assert _log_level(SimpleNamespace(debug=True, log_level="")) == logging.DEBUG
    assert _log_level(SimpleNamespace(debug=False, log_level="WARNING")) == logging.WARNING
    assert _log_level(SimpleNamespace(debug=True, log_level="ERROR")) == logging.ERROR  # explicit wins


def test_log_level_setting_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACHIFAST_LOG_LEVEL", "warning")
    get_settings.cache_clear()
    try:
        assert get_settings().log_level == "WARNING"
        monkeypatch.setenv("PACHIFAST_LOG_LEVEL", "loud")
        get_settings.cache_clear()
        with pytest.raises(ValueError, match="log_level"):
            get_settings()
    finally:
        monkeypatch.delenv("PACHIFAST_LOG_LEVEL", raising=False)
        get_settings.cache_clear()


# --- fixes found while writing the documentation -----------------------------


def test_as_paginated_response_without_page_returns_everything() -> None:
    from pachifast.database.pagination import as_paginated_response
    from pachifast.schemas.responses import PageParams

    result = as_paginated_response([1, 2, 3], PageParams())
    assert result.items == [1, 2, 3] and result.total == 3


def test_websocket_core_session_dependency_accepts_a_websocket() -> None:
    import inspect

    from starlette.requests import HTTPConnection

    from pachifast.database.engine import get_core_session

    assert inspect.signature(get_core_session).parameters["request"].annotation is HTTPConnection
