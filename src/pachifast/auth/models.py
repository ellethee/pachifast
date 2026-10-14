"""Modelli SQLAlchemy per utenti, gruppi e permessi (RBAC)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pachifast.config import get_settings
from pachifast.database.base import Base
from pachifast.database.utils import JsonType, UtcDateTime, new_uuid, utcnow

settings = get_settings()

USER_TABLE = settings.get_table_name("user")
USER_SETTINGS_TABLE = settings.get_table_name("user_settings")
PERMISSION_TABLE = settings.get_table_name("permission")
GROUP_TABLE = settings.get_table_name("group")
GROUP_PERMISSION_TABLE = settings.get_table_name("group_permission")
USER_GROUP_TABLE = settings.get_table_name("user_group")
USER_PERMISSION_TABLE = settings.get_table_name("user_permission")
REFRESH_TOKEN_TABLE = settings.get_table_name("refresh_token")


def _user_fk(*, primary_key: bool = False, unique: bool = False) -> Mapped[uuid.UUID]:
    """Colonna FK UUID verso la tabella utenti (ON DELETE CASCADE)."""
    return mapped_column(
        Uuid,
        ForeignKey(f"{USER_TABLE}.id", ondelete="CASCADE"),
        primary_key=primary_key,
        unique=unique,
        nullable=False,
    )


class User(Base):
    """Account utente. La password e' salvata solo come hash PHC (Argon2id)."""

    __tablename__ = USER_TABLE

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default_factory=new_uuid)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(254), unique=True, default=None)
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    first_name: Mapped[str] = mapped_column(String(150), default="")
    last_name: Mapped[str] = mapped_column(String(150), default="")
    is_active: Mapped[bool] = mapped_column(default=True)
    is_superuser: Mapped[bool] = mapped_column(default=False)
    is_staff: Mapped[bool] = mapped_column(default=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    password_changed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    last_login_at: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    failed_login_count: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default_factory=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default_factory=utcnow)


class UserSettingsTable(Base):
    """Impostazioni utente serializzate come JSON."""

    __tablename__ = USER_SETTINGS_TABLE

    user_id: Mapped[uuid.UUID] = _user_fk(primary_key=True)
    json_settings: Mapped[dict | None] = mapped_column(JsonType, default_factory=dict)


class Permission(Base):
    """Permesso atomico identificato da un ``codename`` univoco."""

    __tablename__ = PERMISSION_TABLE

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    codename: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(String(500), default="")


class Group(Base):
    """Gruppo: insieme di permessi assegnabile a piu' utenti."""

    __tablename__ = GROUP_TABLE

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    description: Mapped[str] = mapped_column(String(500), default="")


class GroupPermission(Base):
    """Associazione gruppo-permesso."""

    __tablename__ = GROUP_PERMISSION_TABLE

    group_id: Mapped[int] = mapped_column(
        ForeignKey(f"{GROUP_TABLE}.id", ondelete="CASCADE", name=f"fk_{GROUP_PERMISSION_TABLE}_group_id"),
        primary_key=True,
    )
    permission_id: Mapped[int] = mapped_column(
        ForeignKey(f"{PERMISSION_TABLE}.id", ondelete="CASCADE", name=f"fk_{GROUP_PERMISSION_TABLE}_permission_id"),
        primary_key=True,
    )


class UserGroup(Base):
    """Associazione utente-gruppo."""

    __tablename__ = USER_GROUP_TABLE

    user_id: Mapped[uuid.UUID] = _user_fk(primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey(f"{GROUP_TABLE}.id", ondelete="CASCADE", name=f"fk_{USER_GROUP_TABLE}_group_id"), primary_key=True
    )


class UserPermission(Base):
    """Permesso assegnato direttamente all'utente (override)."""

    __tablename__ = USER_PERMISSION_TABLE

    user_id: Mapped[uuid.UUID] = _user_fk(primary_key=True)
    permission_id: Mapped[int] = mapped_column(
        ForeignKey(f"{PERMISSION_TABLE}.id", ondelete="CASCADE", name=f"fk_{USER_PERMISSION_TABLE}_permission_id"),
        primary_key=True,
    )


class RefreshTokenRecord(Base):
    """A refresh token that was issued: its ``jti`` is the token's identity, ``family`` the login it belongs to.

    Rotation marks a token ``used`` and issues the next one of the same family; presenting a used token again
    means it leaked, and the whole family is revoked. Deleting rows revokes tokens (logout, password change).
    """

    __tablename__ = REFRESH_TOKEN_TABLE

    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    family: Mapped[str] = mapped_column(String(36), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey(f"{USER_TABLE}.id", ondelete="CASCADE"), index=True, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default_factory=utcnow)
