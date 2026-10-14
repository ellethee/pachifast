"""Modelli SQLAlchemy per la configurazione MFA e le challenge pendenti."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pachifast.auth.models import USER_TABLE
from pachifast.config import get_settings
from pachifast.database.base import Base
from pachifast.database.encrypted import EncryptedStr
from pachifast.database.utils import JsonType, UtcDateTime, utcnow

settings = get_settings()

MFA_CONFIG_TABLE = settings.get_table_name("mfa_config")
MFA_PENDING_TABLE = settings.get_table_name("mfa_pending")


class UserMfaConfig(Base):
    """Configurazione MFA di un utente (un solo metodo attivo per utente)."""

    __tablename__ = MFA_CONFIG_TABLE

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey(f"{USER_TABLE}.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    method: Mapped[str] = mapped_column(String(10))
    is_enabled: Mapped[bool] = mapped_column(default=False)
    #: encrypted at rest (see pachifast.database.encrypted); 255 characters hold the Fernet token of a 32-char secret
    totp_secret: Mapped[str | None] = mapped_column(EncryptedStr(255), default=None)
    phone_number: Mapped[str | None] = mapped_column(String(32), default=None)
    #: the 30-second step of the last TOTP code accepted: a code is never accepted twice (and never an older one)
    last_totp_step: Mapped[int | None] = mapped_column(default=None)
    backup_codes: Mapped[list[str]] = mapped_column(JsonType, nullable=False, server_default="[]", default_factory=list)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default_factory=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default_factory=utcnow)


class UserMfaPendingChallenge(Base):
    """Challenge MFA in attesa di verifica (secondo step del login)."""

    __tablename__ = MFA_PENDING_TABLE

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey(f"{USER_TABLE}.id", ondelete="CASCADE"), nullable=False)
    method: Mapped[str] = mapped_column(String(10))
    otp_hash: Mapped[str | None] = mapped_column(default=None)
    attempts: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default_factory=utcnow)
