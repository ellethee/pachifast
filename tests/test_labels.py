"""Translatable names of permissions and groups: stable keys from codename/name, texts from the catalogs."""

import asyncio
import os

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from pachifast import create_app
from pachifast.auth import cruds, models  # noqa: F401
from pachifast.auth.labels import group_key, permission_key, permission_view
from pachifast.auth.schemas import UserCreate
from pachifast.auth.security import create_access_token
from pachifast.config import get_settings
from pachifast.database import Base
from pachifast.i18n import configure_catalogs
from pachifast.i18n.catalogs import CATALOGS
from pachifast.mfa import models as mfa_models  # noqa: F401
from pachifast.presets import core_module


def test_keys_exist_only_for_identifiers_that_can_be_message_codes() -> None:
    assert permission_key("billing.invoice_view") == "permissions.billing.invoice_view"
    assert permission_key("view") == "permissions.view"
    assert permission_key("billing:view") is None and permission_key("a b") is None and permission_key("a..b") is None
    assert permission_key("1abc") is None
    assert group_key("billing_editors") == "groups.billing_editors"
    assert group_key("Billing admins") is None and group_key("a.b") is None  # a group name is one segment


def test_view_keeps_the_stored_fields_and_adds_the_texts() -> None:
    plain = permission_view({"id": 1, "codename": "billing:view", "description": "See invoices"})
    assert plain == {
        "id": 1,
        "codename": "billing:view",
        "description": "See invoices",
        "label": "billing:view",
        "localizedDescription": "See invoices",
    }
    keyed = permission_view({"id": 2, "codename": "billing.view", "description": "See invoices"})
    assert keyed["labelKey"] == "permissions.billing.view.label"
    assert keyed["descriptionKey"] == "permissions.billing.view.description"
    assert keyed["label"] == "billing.view"  # no catalog text: the identifier


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """``(client, headers, user id)`` of a superuser on a SQLite database; the catalogs are restored afterwards."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}"
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")
    monkeypatch.setenv("PACHIFAST_DEFAULT_LOCALE", "it")

    async def prepare():
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            boss = await cruds.create_user(
                session, UserCreate(username="boss", password="long-enough-pass", is_superuser=True)
            )
        await engine.dispose()
        return boss

    boss = asyncio.run(prepare())
    get_settings.cache_clear()
    client = TestClient(create_app((core_module,)))
    CATALOGS["it"].update(
        {
            "permissions.billing.invoice_view.label": "Vedere le fatture",
            "permissions.billing.invoice_view.description": "Consente di consultare le fatture",
            "groups.billing_editors.label": "Redattori fatture",
        }
    )
    CATALOGS["en"]["permissions.billing.invoice_view.label"] = "View invoices"
    yield client, {"Authorization": f"Bearer {create_access_token(boss.id)}"}, str(boss.id)
    configure_catalogs()
    get_settings.cache_clear()


def _data(response):
    """The payload of a successful response (the API wraps it in an envelope with ``data``)."""
    assert response.status_code == 200, response.text
    body = response.json()
    return body["data"] if isinstance(body, dict) and "data" in body else body


PERMS = "/api/admin/permissions/permissions"
GROUPS = "/api/admin/permissions/groups"


def test_permissions_are_listed_with_localized_texts_and_untouched_stored_fields(api) -> None:
    client, headers, _ = api
    _data(client.post(PERMS, json={"codename": "billing.invoice_view", "description": "See invoices"}, headers=headers))
    plain = _data(client.post(PERMS, json={"codename": "billing:edit", "description": "Edit"}, headers=headers))
    assert "labelKey" not in plain and plain["label"] == "billing:edit"

    by_code = {item["codename"]: item for item in _data(client.get(PERMS, headers=headers))}
    view = by_code["billing.invoice_view"]
    assert view["description"] == "See invoices"  # what an edit form sends back is not translated
    assert view["label"] == "Vedere le fatture" and view["localizedDescription"] == "Consente di consultare le fatture"
    assert view["labelKey"] == "permissions.billing.invoice_view.label"
    assert view["descriptionKey"] == "permissions.billing.invoice_view.description"
    assert by_code["billing:edit"]["label"] == "billing:edit" and "labelKey" not in by_code["billing:edit"]

    updated = _data(
        client.put(
            f"{PERMS}/{view['id']}",
            json={"codename": "billing.invoice_view", "description": "See all invoices"},
            headers=headers,
        )
    )
    assert updated["description"] == "See all invoices" and updated["label"] == "Vedere le fatture"


def test_groups_and_the_user_assignments_carry_the_same_texts(api) -> None:
    client, headers, boss_id = api
    perm = _data(client.post(PERMS, json={"codename": "billing.invoice_view", "description": "See"}, headers=headers))
    group = _data(
        client.post(
            GROUPS,
            json={"name": "billing_editors", "description": "Editors", "permissionIds": [perm["id"]]},
            headers=headers,
        )
    )
    assert group["name"] == "billing_editors" and group["description"] == "Editors"
    assert group["label"] == "Redattori fatture" and group["labelKey"] == "groups.billing_editors.label"
    assert group["permissions"][0]["label"] == "Vedere le fatture"  # nested permissions too
    spaced = _data(
        client.post(GROUPS, json={"name": "Billing admins", "description": "", "permissionIds": []}, headers=headers)
    )
    assert spaced["label"] == "Billing admins" and "labelKey" not in spaced

    listed = _data(client.get(GROUPS, headers=headers))
    assert {g["name"]: g["label"] for g in listed} == {
        "Billing admins": "Billing admins",
        "billing_editors": "Redattori fatture",
    }
    updated = _data(
        client.put(
            f"{GROUPS}/{group['id']}",
            json={"name": "billing_editors", "description": "New", "permissionIds": [perm["id"]]},
            headers=headers,
        )
    )
    assert updated["label"] == "Redattori fatture" and updated["description"] == "New"

    assigned = _data(
        client.put(f"/api/admin/permissions/users/{boss_id}/groups", json={"groupIds": [group["id"]]}, headers=headers)
    )
    assert assigned[0]["label"] == "Redattori fatture"
    mine = _data(client.get(f"/api/admin/permissions/users/{boss_id}/groups", headers=headers))
    assert mine[0]["permissions"][0]["labelKey"] == "permissions.billing.invoice_view.label"
    direct_url = f"/api/admin/permissions/users/{boss_id}/permissions"
    direct = _data(client.put(direct_url, json={"permissionIds": [perm["id"]]}, headers=headers))
    assert direct[0]["label"] == "Vedere le fatture"
    assert _data(client.get(direct_url, headers=headers))[0]["id"] == perm["id"]


def test_the_language_follows_the_request_when_accept_language_is_enabled(api, monkeypatch: pytest.MonkeyPatch) -> None:
    client, headers, _ = api
    _data(client.post(PERMS, json={"codename": "billing.invoice_view", "description": "See"}, headers=headers))
    monkeypatch.setenv("PACHIFAST_USE_ACCEPT_LANGUAGE", "true")
    get_settings.cache_clear()
    negotiating = TestClient(create_app((core_module,)))
    CATALOGS["en"]["permissions.billing.invoice_view.label"] = "View invoices"  # create_app reloads the catalogs
    CATALOGS["it"]["permissions.billing.invoice_view.label"] = "Vedere le fatture"
    english = _data(negotiating.get(PERMS, headers={**headers, "Accept-Language": "en"}))
    assert english[0]["label"] == "View invoices"
    assert _data(negotiating.get(PERMS, headers=headers))[0]["label"] == "Vedere le fatture"
