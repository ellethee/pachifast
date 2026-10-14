"""The generic model admin: ``@register_admin`` gives CRUD routes, a schema for the frontend and permissions."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import datetime
from typing import Literal

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import DateTime, Float, ForeignKey, String, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

from pachifast import create_app
from pachifast.admin import AdminModule, ModelAdmin, admin_action, register_admin
from pachifast.audit import models as audit_models  # noqa: F401
from pachifast.auth import cruds, models  # noqa: F401
from pachifast.auth.models import Permission, UserPermission
from pachifast.auth.schemas import UserCreate
from pachifast.auth.security import create_access_token
from pachifast.config import get_settings
from pachifast.database import Base
from pachifast.presets import core_module

PASSWORD = "long-enough-pass-1"
API = "/api/admin/models/shop/invoice"


class ShopInvoice(Base):
    """A table of the tests, administered below."""

    __tablename__ = "test_shop_invoice"

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    number: Mapped[str] = mapped_column(String(20), unique=True)
    status: Mapped[Literal["open", "paid"]] = mapped_column(String(10), default="open")
    total: Mapped[float] = mapped_column(Float, default=0.0)
    paid: Mapped[bool] = mapped_column(default=False)
    due: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_by: Mapped[str] = mapped_column(String(20), default="system")
    internal_note: Mapped[str] = mapped_column(String(50), default="secret")


@register_admin(ShopInvoice)
class InvoiceAdmin(ModelAdmin):
    app = "shop"
    name = "invoice"
    title = "Invoice"
    list_display = ["number", "status", "total", "paid", "due"]
    search_fields = ["number"]
    list_filter = ["status", "paid", "total", "due"]
    ordering = ["-id"]
    readonly_fields = ["created_by"]
    exclude = ["internal_note"]
    fields_meta = {"number": {"label": "Invoice no."}}
    actions = ["mark_paid"]

    @admin_action(label="Mark as paid")
    async def mark_paid(self, session, rows) -> str:
        """Set the status of the selected invoices to paid."""
        for row in rows:
            row.status = "paid"
        return f"{len(rows)} paid"


class ShopCustomer(Base):
    """A table nobody administers: a foreign key to it stays a plain number."""

    __tablename__ = "test_shop_customer"

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    name: Mapped[str] = mapped_column(String(20), default="")


class ShopLine(Base):
    """A row of an invoice: its ``invoice_id`` points at an administered model."""

    __tablename__ = "test_shop_line"

    id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("test_shop_invoice.id"))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("test_shop_customer.id"), default=None)
    description: Mapped[str] = mapped_column(String(50), default="")


@register_admin(ShopLine)
class LineAdmin(ModelAdmin):
    app = "shop"
    name = "line"
    list_filter = ["invoice_id"]


class Env:
    """A running app on SQLite with a superuser, an editor with some permissions and a user with none."""

    def __init__(self, client: TestClient, ids: dict[str, object]) -> None:
        self.client = client
        self.ids = ids

    def headers(self, name: str) -> dict[str, str]:
        """Authorization header of ``name``."""
        return {"Authorization": f"Bearer {create_access_token(self.ids[name])}"}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[Env]:
    url = f"sqlite+aiosqlite:///{tmp_path / 'admin.sqlite3'}"
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    monkeypatch.setenv("PACHIFAST_DEBUG", "false")
    monkeypatch.setenv("PACHIFAST_ACTIVITY_LOG_ENABLED", "false")
    ids: dict[str, object] = {}

    async def prepare() -> None:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for name, superuser in (("boss", True), ("editor", False), ("nobody", False)):
                user = await cruds.create_user(
                    session, UserCreate(username=name, password=PASSWORD, is_superuser=superuser)
                )
                ids[name] = user.id
        await engine.dispose()

    asyncio.run(prepare())
    from pachifast.database import engine as engine_module

    get_settings.cache_clear()
    engine_module._engine_cache.clear()
    engine_module._sessionmaker_cache.clear()
    client = TestClient(create_app((core_module, AdminModule)))
    client.__enter__()  # runs the startup hooks: the permissions of the admin are created here

    async def grant() -> None:
        engine = create_async_engine(url)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            rows = (
                await session.execute(
                    select(Permission).where(Permission.codename.in_(["shop.invoice.view", "shop.invoice.change"]))
                )
            ).scalars()
            session.add_all(UserPermission(user_id=ids["editor"], permission_id=p.id) for p in rows)
            await session.commit()
        await engine.dispose()

    asyncio.run(grant())
    yield Env(client, ids)
    client.__exit__(None, None, None)
    get_settings.cache_clear()


def _data(response):
    """The payload of a successful (enveloped) response."""
    assert response.status_code < 300, response.text
    return response.json()["data"]


def _create(env: Env, number: str, **extra) -> dict:
    return _data(env.client.post(API, json={"number": number, **extra}, headers=env.headers("boss")))


def test_startup_creates_the_four_permissions_of_the_model(env: Env) -> None:
    async def codenames() -> set[str]:
        engine = create_async_engine(os.environ["PACHIFAST_AUTH_DATABASE_URL"])
        async with async_sessionmaker(engine)() as session:
            result = set((await session.execute(select(Permission.codename))).scalars())
        await engine.dispose()
        return result

    assert {f"shop.invoice.{a}" for a in ("view", "add", "change", "delete")} <= asyncio.run(codenames())


def test_create_read_update_delete(env: Env) -> None:
    boss = env.headers("boss")
    created = _create(env, "INV-1", status="paid", total=12.5, internalNote="hacked", createdBy="mallory")
    assert created["number"] == "INV-1" and created["status"] == "paid" and created["total"] == 12.5
    assert created["createdBy"] == "system"  # read-only: ignored
    assert "internalNote" not in created  # excluded: never leaves the server
    pk = created["id"]

    assert _data(env.client.get(f"{API}/{pk}", headers=boss))["number"] == "INV-1"
    updated = _data(env.client.patch(f"{API}/{pk}", json={"total": 99, "createdBy": "x", "id": 77}, headers=boss))
    assert updated["total"] == 99 and updated["createdBy"] == "system" and updated["id"] == pk
    assert env.client.delete(f"{API}/{pk}", headers=boss).status_code == 204
    gone = env.client.get(f"{API}/{pk}", headers=boss)
    assert gone.status_code == 404 and gone.json()["detail"][0]["code"] == "admin.object_not_found"


def test_list_search_filter_order_and_paging(env: Env) -> None:
    boss = env.headers("boss")
    for i in range(7):
        _create(env, f"INV-{i}", status="paid" if i % 2 else "open", total=i)
    page = _data(env.client.get(API, params={"page": 2, "pageSize": 3}, headers=boss))
    assert (len(page["items"]), page["total"], page["page"], page["pageSize"], page["pages"]) == (3, 7, 2, 3, 3)
    assert page["items"][0]["number"] == "INV-3"  # default order: newest first
    paid = _data(env.client.get(API, params={"status": "paid"}, headers=boss))
    assert paid["total"] == 3
    assert _data(env.client.get(API, params={"search": "INV-5"}, headers=boss))["total"] == 1
    asc = _data(env.client.get(API, params={"orderBy": "total", "pageSize": 2}, headers=boss))
    assert [i["number"] for i in asc["items"]] == ["INV-0", "INV-1"]
    assert _data(env.client.get(API, headers=boss))["pageSize"] == 25  # a default, the list is never unbounded
    assert _data(env.client.get(API, params={"pageSize": 100000}, headers=boss))["pageSize"] == 200
    refused = env.client.get(API, params={"orderBy": "internalNote"}, headers=boss)
    assert refused.status_code == 400 or refused.status_code == 422


def test_a_filter_on_a_column_that_is_not_listed_is_ignored(env: Env) -> None:
    boss = env.headers("boss")
    _create(env, "A-1")
    assert _data(env.client.get(API, params={"createdBy": "nobody"}, headers=boss))["total"] == 1


def test_invalid_bodies_answer_the_uniform_422(env: Env) -> None:
    boss = env.headers("boss")
    bad_status = env.client.post(API, json={"number": "X", "status": "weird"}, headers=boss)
    assert bad_status.status_code == 422 and bad_status.json()["detail"][0]["loc"][:2] == ["body", "status"]
    missing = env.client.post(API, json={"status": "open"}, headers=boss)
    assert missing.status_code == 422 and missing.json()["detail"][0]["loc"][:2] == ["body", "number"]
    too_long = env.client.post(API, json={"number": "N" * 30}, headers=boss)
    assert too_long.status_code == 422
    null_required = env.client.post(API, json={"number": None}, headers=boss)
    assert null_required.status_code == 422


def test_a_duplicate_answers_409_and_leaves_the_session_usable(env: Env) -> None:
    boss = env.headers("boss")
    _create(env, "DUP")
    clash = env.client.post(API, json={"number": "DUP"}, headers=boss)
    assert clash.status_code == 409 and clash.json()["detail"][0]["code"] == "admin.integrity_error"
    assert _create(env, "OK-2")["number"] == "OK-2"


def test_permissions_per_action(env: Env) -> None:
    _create(env, "P-1")
    editor, nobody = env.headers("editor"), env.headers("nobody")
    assert env.client.get(API, headers=editor).status_code == 200
    assert env.client.patch(f"{API}/1", json={"total": 5}, headers=editor).status_code == 200
    for response in (
        env.client.post(API, json={"number": "P-2"}, headers=editor),
        env.client.delete(f"{API}/1", headers=editor),
        env.client.get(API, headers=nobody),
        env.client.get(f"{API}/1", headers=nobody),
    ):
        assert response.status_code == 403
        assert response.json()["detail"][0]["code"] == "access.permissions_missing"
    assert env.client.get(API).status_code == 401


def test_the_menu_lists_only_what_the_user_can_see(env: Env) -> None:
    boss = _data(env.client.get("/api/admin/models", headers=env.headers("boss")))
    assert [m["model"] for m in boss if m["app"] == "shop"] == ["invoice", "line"]
    assert boss[0]["permissions"] == {"view": True, "add": True, "change": True, "delete": True}
    editor = _data(env.client.get("/api/admin/models", headers=env.headers("editor")))
    assert editor[0]["permissions"] == {"view": True, "add": False, "change": True, "delete": False}
    assert _data(env.client.get("/api/admin/models", headers=env.headers("nobody"))) == []


def test_unknown_model_and_bad_primary_key(env: Env) -> None:
    boss = env.headers("boss")
    unknown = env.client.get("/api/admin/models/shop/nope", headers=boss)
    assert unknown.status_code == 404 and unknown.json()["detail"][0]["code"] == "admin.model_not_found"
    assert env.client.get(f"{API}/not-a-number", headers=boss).status_code == 404


def test_schema_describes_the_table_and_the_form(env: Env) -> None:
    schema = _data(env.client.get(f"{API}/_schema", headers=env.headers("editor")))
    assert (schema["app"], schema["model"], schema["title"], schema["primaryKey"]) == (
        "shop",
        "invoice",
        "Invoice",
        "id",
    )
    assert schema["permissions"] == {"view": True, "add": False, "change": True, "delete": False}
    fields = schema["fields"]
    assert "internalNote" not in fields
    assert fields["number"]["label"] == "Invoice no." and fields["number"]["component"] == "input"
    assert fields["number"]["maxlength"] == 20 and fields["number"]["required"] is True
    assert fields["status"]["component"] == "select"
    assert [o["value"] for o in fields["status"]["properties"]["options"]] == ["open", "paid"]
    assert fields["status"]["optionKeys"]["open"] == "admin.shop.invoice.status.options.open"
    assert fields["status"]["required"] is False
    assert (fields["total"]["component"], fields["paid"]["component"], fields["due"]["component"]) == (
        "number",
        "toggle",
        "datetime",
    )
    assert fields["due"]["nullable"] is True
    assert (fields["number"]["type"], fields["total"]["type"], fields["paid"]["type"], fields["due"]["type"]) == (
        "string",
        "number",
        "boolean",
        "datetime",
    )
    assert (
        fields["status"]["default"] == "open"
        and fields["paid"]["default"] is False
        and fields["number"]["default"] is None
    )
    assert fields["total"]["properties"] == {"step": "any"} and fields["id"]["properties"] == {"step": 1}
    assert schema["primaryKeyType"] == "int"
    assert fields["createdBy"]["readonly"] is True and fields["id"]["readonly"] is True
    table = schema["list"]
    assert table["display"] == ["number", "status", "total", "paid", "due"]
    assert table["search"] is True and table["ordering"] == ["-id"]
    assert [f["field"] for f in table["filters"]] == ["status", "paid", "total", "due"]
    assert table["filters"][0]["component"] == "select"


def test_options_cannot_expose_an_excluded_column() -> None:
    """A column in ``exclude`` never leaves the server: it cannot be searched, shown, sorted or used as the label."""

    class Secret(Base):
        __tablename__ = "test_shop_secret"
        id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
        name: Mapped[str] = mapped_column(String(10))
        token: Mapped[str] = mapped_column(String(10))

    with pytest.raises(ValueError, match="in exclude"):

        @register_admin(Secret)
        class Searchable(ModelAdmin):
            app = "shop"
            exclude = ["token"]
            search_fields = ["name", "token"]

    with pytest.raises(ValueError, match="label_field"):

        @register_admin(Secret)
        class Labelled(ModelAdmin):
            app = "shop"
            exclude = ["token"]
            label_field = "token"


def test_registration_is_checked() -> None:
    class Other(Base):
        __tablename__ = "test_shop_other"
        id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)
        name: Mapped[str] = mapped_column(String(10))

    with pytest.raises(ValueError, match="unknown column"):

        @register_admin(Other)
        class Bad(ModelAdmin):
            app = "shop"
            list_display = ["nope"]

    with pytest.raises(ValueError, match="lowercase identifier"):

        @register_admin(Other)
        class BadName(ModelAdmin):
            app = "Shop Floor"

    with pytest.raises(ValueError, match="already registered"):

        @register_admin(Other)
        class Duplicate(ModelAdmin):
            app = "shop"
            name = "invoice"


def test_schema_and_options_need_view(env: Env) -> None:
    nobody = env.headers("nobody")
    for path in (f"{API}/_schema", f"{API}/_options"):
        response = env.client.get(path, headers=nobody)
        assert response.status_code == 403 and response.json()["detail"][0]["code"] == "access.permissions_missing"


def test_options_for_selects_pointing_at_the_model(env: Env) -> None:
    boss = env.headers("boss")
    for i in range(6):
        _create(env, f"OPT-{i}")
    page = _data(env.client.get(f"{API}/_options", params={"pageSize": 4}, headers=boss))
    assert (len(page["items"]), page["total"], page["pages"]) == (4, 6, 2)
    assert page["items"][0] == {"value": 1, "label": "OPT-0"}  # the label is the first text column of the table
    found = _data(env.client.get(f"{API}/_options", params={"search": "OPT-3"}, headers=boss))
    assert found["items"] == [{"value": 4, "label": "OPT-3"}]
    chosen = _data(env.client.get(f"{API}/_options", params={"ids": "2,5"}, headers=boss))
    assert [i["value"] for i in chosen["items"]] == [2, 5] or sorted(i["value"] for i in chosen["items"]) == [2, 5]
    assert env.client.get(f"{API}/_options", params={"ids": "x"}, headers=boss).status_code == 422
    assert "internalNote" not in str(page)


def test_history_records_add_change_delete_and_survives_the_row(env: Env) -> None:
    boss = env.headers("boss")
    created = _create(env, "H-1", total=5)
    pk = created["id"]
    env.client.patch(f"{API}/{pk}", json={"total": 7, "status": "paid"}, headers=boss)
    env.client.patch(f"{API}/{pk}", json={"total": 7}, headers=boss)  # nothing changes: nothing recorded
    env.client.delete(f"{API}/{pk}", headers=boss)
    history = _data(env.client.get(f"{API}/{pk}/history", headers=boss))
    assert [h["action"] for h in history["items"]] == ["delete", "change", "add"]  # newest first
    assert history["total"] == 3
    change = history["items"][1]
    assert change["changes"] == {"total": {"old": 5.0, "new": 7.0}, "status": {"old": "open", "new": "paid"}}
    assert change["username"] == "boss" and change["userId"] == str(env.ids["boss"])
    assert history["items"][2]["changes"]["number"] == {"old": None, "new": "H-1"}
    assert "internalNote" not in str(history)
    other = _data(env.client.get(f"{API}/9999/history", headers=boss))
    assert other["total"] == 0


def test_history_needs_view_and_is_per_row(env: Env) -> None:
    first, second = _create(env, "R-1")["id"], _create(env, "R-2")["id"]
    assert env.client.get(f"{API}/{first}/history", headers=env.headers("nobody")).status_code == 403
    only_second = _data(env.client.get(f"{API}/{second}/history", headers=env.headers("editor")))
    assert [h["changes"]["number"]["new"] for h in only_second["items"]] == ["R-2"]


def test_bulk_actions(env: Env) -> None:
    boss, editor = env.headers("boss"), env.headers("editor")
    ids = [_create(env, f"B-{i}")["id"] for i in range(3)]
    done = _data(env.client.post(f"{API}/_actions/mark_paid", json={"ids": ids[:2]}, headers=editor))
    assert done == {"affected": 2, "message": "2 paid"}
    statuses = {r["number"]: r["status"] for r in _data(env.client.get(API, headers=boss))["items"]}
    assert statuses == {"B-0": "paid", "B-1": "paid", "B-2": "open"}
    history = _data(env.client.get(f"{API}/{ids[0]}/history", headers=boss))["items"][0]
    assert history["action"] == "action:mark_paid" and history["changes"]["status"]["new"] == "paid"
    forbidden = env.client.post(f"{API}/_actions/delete_selected", json={"ids": ids}, headers=editor)
    assert (
        forbidden.status_code == 403 and "shop.invoice.delete" in forbidden.json()["detail"][0]["params"]["permissions"]
    )
    assert (
        _data(env.client.post(f"{API}/_actions/delete_selected", json={"ids": ids[:2]}, headers=boss))["affected"] == 2
    )
    assert _data(env.client.get(API, headers=boss))["total"] == 1


def test_a_bulk_action_is_all_or_nothing_and_checks_its_input(env: Env) -> None:
    boss = env.headers("boss")
    ids = [_create(env, f"C-{i}")["id"] for i in range(2)]
    missing = env.client.post(f"{API}/_actions/mark_paid", json={"ids": [*ids, 999]}, headers=boss)
    assert missing.status_code == 404 and missing.json()["detail"][0]["code"] == "admin.object_not_found"
    assert {r["status"] for r in _data(env.client.get(API, headers=boss))["items"]} == {"open"}
    unknown = env.client.post(f"{API}/_actions/explode", json={"ids": ids}, headers=boss)
    assert unknown.status_code == 404 and unknown.json()["detail"][0]["code"] == "admin.action_not_found"
    for body in ({}, {"ids": []}, {"ids": "1"}, {"ids": ["x"]}, {"ids": list(range(501))}):
        assert env.client.post(f"{API}/_actions/mark_paid", json=body, headers=boss).status_code == 422, body
    nobody = env.client.post(f"{API}/_actions/mark_paid", json={"ids": ids}, headers=env.headers("nobody"))
    assert nobody.status_code == 403


def test_actions_must_be_marked() -> None:
    class Marked(Base):
        __tablename__ = "test_shop_marked"
        id: Mapped[int | None] = mapped_column(primary_key=True, default=None, nullable=False)

    with pytest.raises(ValueError, match="admin_action"):

        @register_admin(Marked)
        class Bad(ModelAdmin):
            app = "shop"
            actions = ["not_there"]

    with pytest.raises(ValueError, match="admin_action"):

        @register_admin(Marked)
        class Builtin(ModelAdmin):
            app = "shop"
            actions = ["delete_selected"]  # always there: it is not declared


def test_schema_lists_the_actions_with_what_the_user_may_run(env: Env) -> None:
    schema = _data(env.client.get(f"{API}/_schema", headers=env.headers("editor")))
    actions = {a["name"]: a for a in schema["actions"]}
    assert actions["delete_selected"]["allowed"] is False and actions["delete_selected"]["permission"] == "delete"
    assert actions["mark_paid"] == {
        "name": "mark_paid",
        "label": "Mark as paid",
        "labelKey": "admin.shop.invoice.actions.mark_paid.label",
        "permission": "change",
        "confirm": True,
        "allowed": True,
    }
    assert schema["history"] is True


def test_a_foreign_key_to_an_administered_model_becomes_a_lookup(env: Env) -> None:
    schema = _data(env.client.get("/api/admin/models/shop/line/_schema", headers=env.headers("boss")))
    invoice = schema["fields"]["invoiceId"]
    assert invoice["component"] == "lookup" and invoice["lookup"] == {"app": "shop", "model": "invoice"}
    assert invoice["type"] == "integer" and invoice["required"] is True
    customer = schema["fields"]["customerId"]
    assert customer["component"] == "number" and "lookup" not in customer  # its table is not administered
    assert schema["list"]["filters"][0]["component"] == "lookup"
    assert schema["list"]["filters"][0]["lookup"] == {"app": "shop", "model": "invoice"}


def test_iter_text_keys_lists_what_a_catalog_can_translate() -> None:
    from pachifast.admin.schema import iter_text_keys

    keys = dict(iter_text_keys([InvoiceAdmin]))
    assert keys["admin.shop.invoice.title"] == "Invoice"
    assert keys["admin.shop.invoice.number.label"] == "Invoice no."
    assert keys["admin.shop.invoice.status.options.open"] == "Open"
    assert keys["admin.shop.invoice.actions.mark_paid.label"] == "Mark as paid"
    assert not any("internal_note" in key for key in keys)


def test_range_filters_on_numbers_and_dates(env: Env) -> None:
    boss = env.headers("boss")
    for i in range(5):
        _create(env, f"G-{i}", total=i * 10, due=f"2026-0{i + 1}-15T12:00:00Z")
    totals = _data(env.client.get(API, params={"total__gte": 10, "total__lte": 30}, headers=boss))
    assert sorted(r["number"] for r in totals["items"]) == ["G-1", "G-2", "G-3"]
    dated = _data(
        env.client.get(
            API, params={"due__gte": "2026-02-01T00:00:00Z", "due__lte": "2026-04-01T00:00:00+00:00"}, headers=boss
        )
    )
    assert sorted(r["number"] for r in dated["items"]) == ["G-1", "G-2"]
    assert env.client.get(API, params={"due__gte": "not a date"}, headers=boss).status_code == 422
    schema = _data(env.client.get(f"{API}/_schema", headers=boss))
    ranges = {f["field"]: f.get("range", False) for f in schema["list"]["filters"]}
    assert ranges == {
        "status": False,
        "paid": False,
        "total": True,
        "due": True,
    }  # only the filters on numbers, dates and times have a range
    assert "total__gte" not in str(schema)


def test_a_datetime_with_an_offset_is_stored_as_utc_in_a_naive_column(env: Env) -> None:
    boss = env.headers("boss")
    row = _create(env, "T-1", due="2026-03-01T10:00:00+02:00")
    assert row["due"].startswith("2026-03-01T08:00:00")
    patched = _data(env.client.patch(f"{API}/{row['id']}", json={"due": "2026-03-01T10:00:00Z"}, headers=boss))
    assert patched["due"].startswith("2026-03-01T10:00:00")


def test_delete_selected_answers_with_a_message(env: Env) -> None:
    ids = [_create(env, f"M-{i}")["id"] for i in range(3)]
    done = _data(env.client.post(f"{API}/_actions/delete_selected", json={"ids": ids}, headers=env.headers("boss")))
    assert done == {"affected": 3, "message": "3 deleted"}


def test_error_locations_use_the_names_of_the_schema(env: Env) -> None:
    boss = env.headers("boss")
    invoice = _create(env, "L-1")["id"]
    camel = env.client.post("/api/admin/models/shop/line", json={"invoiceId": "x"}, headers=boss)
    assert camel.json()["detail"][0]["loc"] == ["body", "invoiceId"]
    snake = env.client.post("/api/admin/models/shop/line", json={"invoice_id": "x"}, headers=boss)
    assert snake.json()["detail"][0]["loc"] == ["body", "invoiceId"]  # always the camelCase name of the schema
    assert invoice
    bad_filter = env.client.get("/api/admin/models/shop/line", params={"invoiceId": "x"}, headers=boss)
    assert bad_filter.json()["detail"][0]["loc"] == ["query", "invoiceId"]
