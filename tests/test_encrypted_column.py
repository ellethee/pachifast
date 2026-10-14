"""Columns encrypted at rest: the TOTP secret is never in the database as plain text."""

from __future__ import annotations

import os

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, select, text

from pachifast.config import get_settings
from pachifast.database.encrypted import PREFIX, EncryptedStr, decrypt_text, encrypt_text

table = Table("secret_demo", MetaData(), Column("id", Integer, primary_key=True), Column("value", EncryptedStr(255)))


@pytest.fixture
def engine(tmp_path):
    """A SQLite database with the demo table."""
    db = create_engine(f"sqlite:///{tmp_path / 'enc.sqlite3'}")
    table.metadata.create_all(db)
    yield db
    db.dispose()


@pytest.fixture(autouse=True)
def _settings(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_value_is_encrypted_in_the_table_and_plain_in_python(engine) -> None:
    with engine.begin() as conn:
        conn.execute(table.insert().values(id=1, value="JBSWY3DPEHPK3PXP"))
    with engine.connect() as conn:
        stored = conn.execute(text("SELECT value FROM secret_demo")).scalar_one()
        assert stored.startswith(PREFIX) and "JBSWY3DPEHPK3PXP" not in stored
        assert conn.execute(select(table.c.value)).scalar_one() == "JBSWY3DPEHPK3PXP"


def test_none_and_plain_legacy_values_are_read(engine) -> None:
    with engine.begin() as conn:
        conn.execute(table.insert().values(id=1, value=None))
        conn.execute(text("INSERT INTO secret_demo (id, value) VALUES (2, 'LEGACYPLAINSECRET')"))
    with engine.connect() as conn:
        rows = dict(conn.execute(select(table.c.id, table.c.value)).all())
    assert rows == {1: None, 2: "LEGACYPLAINSECRET"}


def test_an_encrypted_value_is_not_encrypted_twice() -> None:
    once = encrypt_text("abc")
    assert EncryptedStr().process_bind_param(once, None) == once
    assert decrypt_text(once) == "abc"


def test_the_key_comes_from_the_secret_key_or_from_mfa_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    stored = encrypt_text("abc")
    monkeypatch.setenv("PACHIFAST_SECRET_KEY", "another-secret-key-0123456789-abcdefghijkl")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="another key"):
        decrypt_text(stored)
    # a dedicated key survives a rotation of the secret key
    monkeypatch.setenv("PACHIFAST_MFA_ENCRYPTION_KEY", "dedicated-key-0123456789")
    get_settings.cache_clear()
    kept = encrypt_text("abc")
    monkeypatch.setenv("PACHIFAST_SECRET_KEY", "yet-another-secret-key-0123456789-abcdef")
    get_settings.cache_clear()
    assert decrypt_text(kept) == "abc"


def test_a_migration_writes_the_column_as_a_plain_string() -> None:
    """A revision says ``sa.String`` and does not import the Python type."""
    from sqlalchemy import Integer

    from pachifast.migrations.runtime import _render_item

    assert _render_item("type", EncryptedStr(255), None) == "sa.String(length=255)"
    assert _render_item("type", Integer(), None) is False
    assert _render_item("column", object(), None) is False
