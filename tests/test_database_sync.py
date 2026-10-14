"""Synchronous engines and sessions, and the ORM base that serves both the sync and the async side."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import String, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Mapped, Session, mapped_column

from pachifast.database import (
    Base,
    get_sync_engine_from_url,
    get_sync_sessionmaker_from_url,
    sync_session_dependency_from_url,
    to_sync_url,
)


class SyncThing(Base):
    """Throwaway table used by both the sync and the async tests."""

    __tablename__ = "test_sync_thing"

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    name: Mapped[str] = mapped_column(String(40), default="x")
    active: Mapped[bool] = mapped_column(default=True)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("sqlite+aiosqlite:///./a.db", "sqlite:///./a.db"),
        ("postgresql+asyncpg://u:p%40ss@h:5432/db", "postgresql+psycopg://u:p%40ss@h:5432/db"),
        ("sqlite:///./a.db", "sqlite:///./a.db"),
        ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ],
)
def test_to_sync_url_swaps_only_the_async_driver(url: str, expected: str) -> None:
    assert to_sync_url(url) == expected


def test_python_defaults_apply_when_the_instance_is_created() -> None:
    thing = SyncThing()
    assert (thing.id, thing.name, thing.active) == (None, "x", True)


def test_sync_engine_and_sessionmaker_are_cached_and_usable(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'sync.db'}"
    engine = get_sync_engine_from_url(url)
    assert get_sync_engine_from_url(url) is engine
    Base.metadata.create_all(engine, tables=[SyncThing.__table__])  # type: ignore[list-item]
    with get_sync_sessionmaker_from_url(url)() as session:
        session.add_all([SyncThing(name="a"), SyncThing(name="b")])
        session.commit()
        assert [t.name for t in session.scalars(select(SyncThing).order_by(SyncThing.id))] == ["a", "b"]
    engine.dispose()


def test_sync_dependency_yields_a_session_in_a_def_endpoint(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'dep.db'}"
    Base.metadata.create_all(get_sync_engine_from_url(url), tables=[SyncThing.__table__])  # type: ignore[list-item]
    app = FastAPI()
    dependency = sync_session_dependency_from_url(url)

    @app.post("/things")
    def create(name: str, session: Session = dependency) -> dict[str, int | None]:
        thing = SyncThing(name=name)
        session.add(thing)
        session.commit()
        return {"id": thing.id}

    with TestClient(app) as client:
        assert client.post("/things?name=z").json() == {"id": 1}
        assert client.post("/things?name=y").json() == {"id": 2}


async def test_the_same_model_works_with_an_async_session(tmp_path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'async.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[SyncThing.__table__])  # type: ignore[list-item]
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add_all([SyncThing(name="a"), SyncThing(name="b")])
        await session.commit()
        assert (await session.execute(text("select count(*) from test_sync_thing"))).scalar_one() == 2
    await engine.dispose()


async def test_init_db_creates_every_core_table_and_picks_up_new_ones(tmp_path: Path, monkeypatch) -> None:
    """The core tables come from the core model modules, not from a list that has to be kept in sync."""
    from pachifast.config import get_settings
    from pachifast.startup import core_tables, init_db

    url = f"sqlite+aiosqlite:///{tmp_path / 'core.db'}"
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_AUTO_CREATE_AUTH_SCHEMA", "true")
    get_settings.cache_clear()
    expected = {table.name for table in core_tables()}
    assert {"pachifast_user", "pachifast_refresh_token", "pachifast_mfa_config", "pachifast_ws_ticket"} <= expected

    await init_db()
    assert expected <= {name for name in _table_names(url)}
    get_settings.cache_clear()


def _table_names(url: str) -> set[str]:
    from sqlalchemy import create_engine, inspect

    engine = create_engine(to_sync_url(url))
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
