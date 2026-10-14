"""A text column that is encrypted at rest: the database never holds the plain value.

The value is encrypted with Fernet (AES-128-CBC and HMAC-SHA256) under a key derived from the configuration with
HKDF: ``MFA_ENCRYPTION_KEY`` when it is set, otherwise ``SECRET_KEY``. A stored value looks like ``enc1:<token>``;
a value without the prefix is read as plain text (a row written before the column was encrypted) and is encrypted
the next time it is written.

Changing the key makes the stored values unreadable. Set ``MFA_ENCRYPTION_KEY`` once and keep it when
``SECRET_KEY`` is rotated.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from pachifast.config import get_settings

PREFIX = "enc1:"
_INFO = b"pachifast:encrypted-column:v1"


@lru_cache(maxsize=8)
def _fernet(material: str) -> Fernet:
    """The Fernet of a key material (derived once per distinct key)."""
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_INFO).derive(material.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _current() -> Fernet:
    """The Fernet of the key in the configuration."""
    settings = get_settings()
    return _fernet(getattr(settings, "MFA_ENCRYPTION_KEY", "") or settings.SECRET_KEY)


def encrypt_text(value: str) -> str:
    """``value`` as it is stored: ``enc1:`` and the Fernet token."""
    return PREFIX + _current().encrypt(value.encode()).decode()


def decrypt_text(stored: str) -> str:
    """The plain text of a stored value; a value without the prefix is already plain.

    Raises:
        RuntimeError: the value was encrypted with another key.
    """
    if not stored.startswith(PREFIX):
        return stored
    try:
        return _current().decrypt(stored[len(PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "An encrypted value cannot be read: it was written with another key. "
            "Set MFA_ENCRYPTION_KEY to the key that was in use (SECRET_KEY by default)."
        ) from exc


class EncryptedStr(TypeDecorator):
    """``String`` column whose value is encrypted on write and decrypted on read (see the module docstring)."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Any) -> str | None:
        """Encrypt before writing (a value already encrypted is kept)."""
        if value is None or value.startswith(PREFIX):
            return value
        return encrypt_text(value)

    def process_result_value(self, value: str | None, dialect: Any) -> str | None:
        """Decrypt after reading."""
        return None if value is None else decrypt_text(value)
