"""Fixture per i test di autenticazione: SQLite async in memoria e SECRET_KEY di test."""

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

# Va impostata prima di importare pachifast (le Settings sono lette all'import dei modelli).
os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from pachifast.database import Base

T = TypeVar("T")


def run_with_session(fn: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Esegue ``fn(session)`` su un database SQLite in memoria con tutte le tabelle auth/MFA."""
    from pachifast.auth import models  # noqa: F401
    from pachifast.mfa import models as mfa_models  # noqa: F401

    async def _main() -> T:
        engine = create_async_engine(
            "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await fn(session)
        finally:
            await engine.dispose()

    return asyncio.run(_main())


@pytest.fixture
def run() -> Callable[[Callable[[AsyncSession], Awaitable[Any]]], Any]:
    """Fixture che espone :func:`run_with_session`."""
    return run_with_session
