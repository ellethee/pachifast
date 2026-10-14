"""Localized API errors: stable codes, catalogs, default locale and Accept-Language."""

import asyncio
import os
import re
from pathlib import Path

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pachifast import create_app
from pachifast.auth import cruds, models  # noqa: F401
from pachifast.auth.schemas import UserCreate
from pachifast.config import get_settings
from pachifast.database import Base
from pachifast.i18n import MessageError, api_error, negotiate_locale, translate
from pachifast.i18n.loader import load_core_catalogs
from pachifast.mfa import models as mfa_models  # noqa: F401
from pachifast.presets import core_module

SRC = Path(__file__).resolve().parents[1] / "src" / "pachifast"
EN = load_core_catalogs()["en"]
FIELDS = re.compile(r"{(\w+)}")


# --- catalogs ---------------------------------------------------------------


def test_every_locale_has_exactly_the_codes_of_english_with_the_same_fields() -> None:
    for locale, catalog in load_core_catalogs().items():
        assert set(catalog) == set(EN), (locale, set(catalog) ^ set(EN))
        for code, text in catalog.items():
            assert set(FIELDS.findall(text)) == set(FIELDS.findall(EN[code])), (locale, code)


def test_every_code_used_in_the_source_exists_in_the_catalog() -> None:
    """Codes are passed as literals to ``api_error``/``_unauthorized``/``MessageError``: none may be missing."""
    used: set[str] = set()
    call = re.compile(r"(?:api_error\(\s*[^,]+,|_unauthorized\(|MessageError\()\s*\"([a-z_]+\.[a-z_]+)\"")
    for path in SRC.rglob("*.py"):
        if "i18n" in path.parts:
            continue
        used |= set(call.findall(path.read_text(encoding="utf-8")))
    assert used, "no code found: the pattern is out of date"
    assert used <= set(EN), used - set(EN)
    unused = {
        code for code in EN if not code.startswith(("settings.", "admin.text."))
    } - used  # derived texts, see below
    assert unused == set(), f"codes in the catalog that nothing raises: {sorted(unused)}"


def test_no_inline_error_messages_left_in_the_sources() -> None:
    """The messages live in the catalogs: the sources raise codes, not sentences."""
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "errors.py" or {"i18n", "cli", "scaffold"} & set(path.parts):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r'"msg": "|detail="[A-Z]', line):
                offenders.append(f"{path.relative_to(SRC)}:{number}")
    assert offenders == []


# --- translation ------------------------------------------------------------


def test_translate_uses_the_locale_then_english_then_the_code() -> None:
    assert translate("user.not_found", locale="it") == "Utente non trovato"
    assert translate("user.not_found", locale="en") == "User not found"
    assert translate("user.not_found", locale="fr") == "User not found"  # unknown locale: English
    assert translate("nope.nothing") == "nope.nothing"
    assert (
        translate("auth.password_too_short", {"min_length": 12}, "it") == "Password troppo corta (minimo 12 caratteri)."
    )
    assert translate("auth.password_too_short", {}, "en").startswith("Password too short")  # missing field: no crash


def test_negotiate_locale_handles_quality_and_regions() -> None:
    assert negotiate_locale("it-IT,it;q=0.9,en;q=0.8") == "it"
    assert negotiate_locale("fr;q=1, en;q=0.5") == "en"
    assert negotiate_locale("en;q=0.4, it;q=0.9") == "it"
    assert negotiate_locale("fr, de") is None and negotiate_locale("") is None and negotiate_locale(None) is None
    assert negotiate_locale("it;q=0, en") == "en" and negotiate_locale("it;q=abc, en") == "en"


def test_api_error_and_message_error_carry_the_code() -> None:
    exc = api_error(404, "user.not_found", loc=("path", "user_id"))
    assert exc.status_code == 404
    assert exc.detail == [
        {"loc": ["path", "user_id"], "msg": "User not found", "type": "value_error", "code": "user.not_found"}
    ]
    # variable parts travel as params, for clients that translate from the code
    item = api_error(403, "access.permissions_missing", permissions="a, b").detail[0]
    assert item["params"] == {"permissions": "a, b"} and item["msg"] == "Missing permissions: a, b"
    assert "params" not in exc.detail[0]
    error = MessageError("auth.password_too_short", min_length=8)
    assert error.code == "auth.password_too_short" and "minimum 8" in str(error) and isinstance(error, ValueError)


def test_default_locale_must_be_a_language_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACHIFAST_DEFAULT_LOCALE", "not a locale!")
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError, match="language tag"):
            get_settings()
    finally:
        monkeypatch.delenv("PACHIFAST_DEFAULT_LOCALE")
        get_settings.cache_clear()


# --- end to end -------------------------------------------------------------


@pytest.fixture
def build(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Factory of apps on a SQLite auth database with one user; environment: ``build(**env)``."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}"
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")

    async def prepare() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await cruds.create_user(session, UserCreate(username="ann", password="long-enough-pass"))
        await engine.dispose()

    asyncio.run(prepare())

    def factory(**env: str) -> TestClient:
        for key, value in env.items():
            monkeypatch.setenv(f"PACHIFAST_{key.upper()}", value)
        get_settings.cache_clear()
        return TestClient(create_app((core_module,)))

    yield factory
    get_settings.cache_clear()


def _login_error(client: TestClient, **headers: str) -> dict:
    response = client.post("/api/auth/token", data={"username": "ann", "password": "wrong-password"}, headers=headers)
    assert response.status_code == 401
    return response.json()["detail"][0]


def test_default_is_english_and_the_code_is_always_present(build) -> None:
    item = _login_error(build())
    assert item == {
        "loc": ["body", "username"],
        "msg": "Invalid credentials",
        "type": "value_error",
        "code": "auth.invalid_credentials",
    }


def test_default_locale_setting_changes_the_message_not_the_code(build) -> None:
    item = _login_error(build(default_locale="it"))
    assert item["msg"] == "Credenziali non valide" and item["code"] == "auth.invalid_credentials"


def test_accept_language_is_ignored_unless_enabled(build) -> None:
    assert _login_error(build(), **{"Accept-Language": "it"})["msg"] == "Invalid credentials"


def test_accept_language_picks_the_locale_when_enabled(build) -> None:
    client = build(use_accept_language="true")
    assert _login_error(client, **{"Accept-Language": "it-IT,it;q=0.9,en;q=0.5"})["msg"] == "Credenziali non valide"
    assert _login_error(client, **{"Accept-Language": "fr"})["msg"] == "Invalid credentials"  # unsupported: default
    assert _login_error(client)["msg"] == "Invalid credentials"  # no header: default
    # the locale of a request does not leak into the next one
    assert _login_error(client, **{"Accept-Language": "it"})["msg"] == "Credenziali non valide"
    assert _login_error(client)["msg"] == "Invalid credentials"


def test_validation_message_codes_reach_the_response(build) -> None:
    client = build(use_accept_language="true", registration_enabled="true")
    short = client.post(
        "/api/auth/register", json={"username": "newuser", "password": "x"}, headers={"Accept-Language": "it"}
    )
    assert short.status_code in (400, 422)
    taken = client.post(
        "/api/auth/register",
        json={"username": "ann", "password": "long-enough-pass"},
        headers={"Accept-Language": "it"},
    )
    assert taken.status_code == 400
    assert taken.json()["detail"][0] == {
        "loc": ["body", "username"],
        "msg": "Username già in uso",
        "type": "value_error",
        "code": "auth.username_taken",
    }
