"""The 0.15 extension points: profile, ``requires_pachifast``, pool settings and the activity log retention."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient
from pydantic import Field
from sqlalchemy import ForeignKey, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from pachifast import create_app
from pachifast.audit.models import ActivityLog
from pachifast.auth import cruds
from pachifast.auth.models import User
from pachifast.auth.profile import clear_profile, register_profile
from pachifast.auth.schemas import UserCreate
from pachifast.config import get_settings
from pachifast.database import AliasedBaseModel, Base
from pachifast.database.utils import utcnow
from pachifast.modules import AppModule, check_requirements
from pachifast.presets import core_module

PASSWORD = "correct-horse-battery-0"


class Profile(Base):
    __tablename__ = "test_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(User.id, ondelete="CASCADE"), primary_key=True)
    phone: Mapped[str | None] = mapped_column(default=None)
    city: Mapped[str] = mapped_column(default="")


class ProfileSchema(AliasedBaseModel):
    phone: str | None = Field(default=None, max_length=12)
    city: str = Field(default="", max_length=30)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}"
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")

    async def prepare() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await cruds.create_user(session, UserCreate(username="ann", password=PASSWORD))
        await engine.dispose()

    asyncio.run(prepare())
    get_settings.cache_clear()
    from pachifast.database import engine as engine_module

    engine_module._engine_cache.clear()
    engine_module._sessionmaker_cache.clear()
    with TestClient(create_app((core_module,))) as test_client:
        yield test_client
    clear_profile()
    get_settings.cache_clear()


def _headers(client: TestClient) -> dict[str, str]:
    token = client.post("/api/auth/token", data={"username": "ann", "password": PASSWORD}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _user(response) -> dict:
    body = response.json()
    return body.get("data", body)


def test_without_a_profile_the_user_has_none_and_a_patch_with_one_is_refused(client: TestClient) -> None:
    headers = _headers(client)
    assert _user(client.get("/api/auth/user", headers=headers))["profile"] is None
    refused = client.patch("/api/auth/user", json={"profile": {"phone": "1"}}, headers=headers)
    assert refused.status_code == 422 and refused.json()["detail"][0]["code"] == "user.profile_not_configured"


def test_the_profile_is_shown_created_on_first_save_and_updated_by_field(client: TestClient) -> None:
    register_profile(Profile, ProfileSchema)
    headers = _headers(client)
    assert _user(client.get("/api/auth/user", headers=headers))["profile"] == {"phone": None, "city": ""}

    first = client.patch("/api/auth/user", json={"profile": {"phone": "0123"}}, headers=headers)
    assert first.status_code == 200, first.text
    assert _user(first)["profile"] == {"phone": "0123", "city": ""}
    second = client.patch("/api/auth/user", json={"profile": {"city": "Roma"}, "firstName": "Ann"}, headers=headers)
    me = _user(second)
    assert me["profile"] == {"phone": "0123", "city": "Roma"} and me["firstName"] == "Ann"
    assert _user(client.get("/api/auth/user", headers=headers))["profile"]["city"] == "Roma"


def test_a_profile_value_the_schema_refuses_is_a_422_and_nothing_is_written(client: TestClient) -> None:
    register_profile(Profile, ProfileSchema)
    headers = _headers(client)
    bad = client.patch("/api/auth/user", json={"profile": {"phone": "x" * 40}}, headers=headers)
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["loc"][:3] == ["body", "profile", "phone"]
    assert _user(client.get("/api/auth/user", headers=headers))["profile"]["phone"] is None


def test_register_profile_checks_the_columns() -> None:
    class Wrong(AliasedBaseModel):
        nope: str = ""

    with pytest.raises(ValueError, match="not columns"):
        register_profile(Profile, Wrong)
    with pytest.raises(ValueError, match="no column"):
        register_profile(Profile, ProfileSchema, user_column="owner")
    clear_profile()


def test_a_module_that_needs_another_pachifast_is_refused() -> None:
    check_requirements([AppModule(name="ok", requires_pachifast=">=0.1"), AppModule(name="none")])
    with pytest.raises(RuntimeError, match="needs pachifast <0.1"):
        check_requirements([AppModule(name="old", requires_pachifast="<0.1")])
    with pytest.raises(RuntimeError, match="not valid"):
        check_requirements([AppModule(name="typo", requires_pachifast="fast>>1")])


def test_the_pool_settings_reach_the_engine_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    from pachifast.database.engine import pool_args

    monkeypatch.setenv("PACHIFAST_DB_POOL_SIZE", "20")
    monkeypatch.setenv("PACHIFAST_DB_MAX_OVERFLOW", "0")
    get_settings.cache_clear()
    try:
        args = pool_args()
        assert (args["pool_size"], args["max_overflow"], args["pool_timeout"], args["pool_recycle"]) == (
            20,
            0,
            30,
            1800,
        )
    finally:
        get_settings.cache_clear()


def test_the_activity_log_has_an_index_on_user_and_time() -> None:
    names = {index.name for index in ActivityLog.__table__.indexes}
    assert any(name.endswith("_user_id_timestamp") for name in names)


def test_purge_activity_log_deletes_what_is_older_than_the_retention(tmp_path: Path) -> None:
    from pachifast.cli.services.audit import purge_activity_log

    url = f"sqlite+aiosqlite:///{tmp_path / 'purge.sqlite3'}"
    (tmp_path / ".env").write_text(
        f"PACHIFAST_SECRET_KEY=test-secret-key-0123456789-abcdefghijklmnop\nPACHIFAST_AUTH_DATABASE_URL={url}\n"
    )

    async def seed() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine)() as session:
            now = utcnow()
            for days in (1, 40, 100):
                session.add(
                    ActivityLog(
                        method="POST", path=f"/api/{days}", status_code=200, timestamp=now - timedelta(days=days)
                    )
                )
            await session.commit()
        await engine.dispose()

    async def paths() -> list[str]:
        engine = create_async_engine(url)
        async with async_sessionmaker(engine)() as session:
            rows = (await session.execute(select(ActivityLog.path).order_by(ActivityLog.id))).scalars().all()
        await engine.dispose()
        return list(rows)

    asyncio.run(seed())
    with pytest.raises(ValueError, match="No retention is configured.*--days"):
        purge_activity_log(base_dir=str(tmp_path))
    with (tmp_path / ".env").open("a") as env_file:
        env_file.write("PACHIFAST_ACTIVITY_LOG_RETENTION_DAYS=30\n")
    assert purge_activity_log(base_dir=str(tmp_path), dry_run=True) == (30, 2)
    (tmp_path / ".env").write_text((tmp_path / ".env").read_text().replace("RETENTION_DAYS=30", "RETENTION_DAYS=0"))
    assert purge_activity_log(base_dir=str(tmp_path), days=30, dry_run=True) == (30, 2)
    assert asyncio.run(paths()) == ["/api/1", "/api/40", "/api/100"]
    assert purge_activity_log(base_dir=str(tmp_path), days=30) == (30, 2)
    assert asyncio.run(paths()) == ["/api/1"]


def test_a_setup_hook_can_add_a_middleware_while_the_app_is_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}")
    get_settings.cache_clear()

    def add_header(app) -> None:
        @app.middleware("http")
        async def stamp(request, call_next):
            response = await call_next(request)
            response.headers["X-Extension"] = "yes"
            return response

    try:
        module = AppModule(name="ext", setup_hooks=(add_header,))
        with TestClient(create_app((core_module, module))) as test_client:
            response = test_client.get("/api/health/live")
        assert response.headers["X-Extension"] == "yes" and response.headers["X-Request-ID"]
    finally:
        get_settings.cache_clear()


def test_a_setup_hook_must_not_be_a_coroutine_function() -> None:
    from fastapi import FastAPI

    from pachifast.modules import run_setup_hooks

    async def late(app) -> None: ...

    with pytest.raises(TypeError, match="must not be a coroutine"):
        run_setup_hooks(FastAPI(), [AppModule(name="bad", setup_hooks=(late,))])
