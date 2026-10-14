"""A profile per user: the columns a project wants on its users, kept in a table of its own.

The user table of the core is fixed, so that an upgrade of pachifast never changes a table that holds your data.
Put your own columns in a one-to-one table that points at the user, and register it once at startup::

    class Profile(Base):                       # your model, your migration
        __tablename__ = "profiles"
        user_id: Mapped[uuid.UUID] = mapped_column(
            ForeignKey("pachifast_user.id", ondelete="CASCADE"), primary_key=True
        )
        phone: Mapped[str | None] = mapped_column(default=None)

    class ProfileSchema(AliasedBaseModel):     # what the API accepts and shows
        phone: str | None = Field(default=None, max_length=30)

    register_profile(Profile, ProfileSchema)

``GET /auth/user`` then answers a ``profile`` object and ``PATCH /auth/user`` accepts ``{"profile": {...}}``: the
fields that are sent change, validated by the schema (``422`` like any other body), and the row is created on the
first save. A user without a row sees the defaults of the schema.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    """The registered profile: the table, the schema of its API and the name of the column that points at the user."""

    model: type
    schema: type[BaseModel]
    user_column: str = "user_id"


_profile: ProfileConfig | None = None


def register_profile(model: type, schema: type[BaseModel], *, user_column: str = "user_id") -> None:
    """Use ``model`` (one row per user) as the profile of the users, shown and edited through ``schema``.

    Raises:
        ValueError: ``model`` has no column ``user_column``, or ``schema`` has a field that is no column of ``model``.
    """
    columns = set(model.__table__.columns.keys())  # type: ignore[attr-defined]
    if user_column not in columns:
        raise ValueError(f"{model.__name__} has no column {user_column!r} to point at the user")
    unknown = sorted(set(schema.model_fields) - columns)
    if unknown:
        raise ValueError(f"{schema.__name__} has fields that are not columns of {model.__name__}: {unknown}")
    global _profile
    _profile = ProfileConfig(model, schema, user_column)


def clear_profile() -> None:
    """Forget the registered profile (for tests)."""
    global _profile
    _profile = None


def get_profile_config() -> ProfileConfig | None:
    """The registered profile, or ``None``."""
    return _profile


def _dump(config: ProfileConfig, values: dict[str, Any]) -> dict[str, Any]:
    """The profile as the API shows it: validated by the schema, with the keys the schema serializes."""
    return config.schema.model_validate(values).model_dump(mode="json", by_alias=True)


async def _row(session: AsyncSession, config: ProfileConfig, user_id: uuid.UUID) -> Any | None:
    """The profile row of a user, or ``None``."""
    column = getattr(config.model, config.user_column)
    return (await session.execute(select(config.model).where(column == user_id))).scalar_one_or_none()


async def load_profile(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any] | None:
    """The profile of a user as a dict (the schema defaults without a row); ``None`` if none is registered."""
    config = _profile
    if config is None:
        return None
    row = await _row(session, config, user_id)
    values = {name: getattr(row, name) for name in config.schema.model_fields} if row is not None else {}
    return _dump(config, values)


async def save_profile(session: AsyncSession, user_id: uuid.UUID, changes: dict[str, Any]) -> dict[str, Any]:
    """Apply ``changes`` to the profile of a user (creating the row) and return the profile.

    Raises:
        pydantic.ValidationError: ``changes`` do not fit the schema; nothing is written.
        RuntimeError: no profile is registered.
    """
    config = _profile
    if config is None:
        raise RuntimeError("No profile is registered: call register_profile() first")
    row = await _row(session, config, user_id)
    current = {name: getattr(row, name) for name in config.schema.model_fields} if row is not None else {}
    # validated as a whole, so a field with no default that the user never sent is reported
    merged = config.schema.model_validate({**current, **changes})
    values = merged.model_dump(by_alias=False)
    if row is None:
        row = config.model(**{config.user_column: user_id}, **values)
        session.add(row)
    else:
        for name, value in values.items():
            setattr(row, name, value)
    await session.commit()
    return _dump(config, values)
