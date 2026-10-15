"""Disabling core routers/routes from configuration (PACHIFAST_DISABLE_CORE_ROUTERS / _ROUTES)."""

import os

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient

from pachifast import create_app
from pachifast.config import get_settings
from pachifast.presets import core_module
from pachifast.routing.core_filters import is_route_disabled, is_router_disabled, router_key


@pytest.fixture(autouse=True)
def _auth_database(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A database URL is needed for protected routes to reach the token check (401 instead of 503)."""
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}")


def _paths(
    monkeypatch: pytest.MonkeyPatch,
    *,
    routers: str = "",
    routes: str = "",
    all_routers: str = "",
    all_routes: str = "",
    modules: tuple = (core_module,),
) -> tuple[TestClient, set[str]]:
    """Build the app with the given settings; return the client and the documented paths.

    ``routers``/``routes`` set the ``disable_core_*`` settings, ``all_routers``/``all_routes`` the ``disable_*`` ones.
    """
    for name, value in (
        ("PACHIFAST_DISABLE_CORE_ROUTERS", routers),
        ("PACHIFAST_DISABLE_CORE_ROUTES", routes),
        ("PACHIFAST_DISABLE_ROUTERS", all_routers),
        ("PACHIFAST_DISABLE_ROUTES", all_routes),
    ):
        if value:
            monkeypatch.setenv(name, value)
        else:
            monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    client = TestClient(create_app(modules))
    paths = set(client.get("/api/v1/openapi.json").json()["paths"])
    get_settings.cache_clear()
    return client, paths


# --- pure matching ----------------------------------------------------------


def test_router_key() -> None:
    assert router_key("pachifast.auth.root") == "auth"
    assert router_key("pachifast.auth.user") == "auth.user"
    assert router_key("pachifast.admin.permissions") == "admin.permissions"
    assert router_key("pachifast.health.health") == "health"
    assert router_key("pachifast.infos.root") == "infos"
    assert router_key("apps.billing.root") == "apps.billing"
    assert router_key("common.tools.invoices") == "common.tools.invoices"


def test_router_names_match_key_group_or_last_segment() -> None:
    assert is_router_disabled("admin.permissions", ["permissions"])
    assert is_router_disabled("admin.permissions", ["admin"])
    assert is_router_disabled("admin.user", ["Admin.User"])
    assert is_router_disabled("apps.billing.invoices", ["billing"])  # a segment in the middle
    assert is_router_disabled("apps.billing.invoices", ["apps"]) and is_router_disabled(
        "apps.billing", ["apps.billing"]
    )
    assert not is_router_disabled("apps.billing", ["bill"])  # whole segments only
    assert is_router_disabled("admin.user", ["/admin/user"])
    assert is_router_disabled("auth.user", ["user"]) and is_router_disabled("admin.user", ["user"])
    assert not is_router_disabled("admin.user", ["permissions"])
    assert not is_router_disabled("health", ["admin", ""])


def test_route_entries_match_suffix_subtree_method_and_wildcard() -> None:
    settings = "/auth/user/settings"
    assert is_route_disabled(settings, ["GET"], ["/user/settings"])  # suffix
    assert is_route_disabled("/auth/user/settings/{category}/{key}", ["PUT"], ["/user/settings"])  # subtree
    assert is_route_disabled(settings, ["GET"], ["/auth/user/settings/"])  # full path, trailing slash
    assert is_route_disabled(settings, ["GET"], ["GET /user/settings"])
    assert not is_route_disabled(settings, ["GET"], ["PUT /user/settings"])
    assert not is_route_disabled(settings, ["GET"], ["/user/setting"])  # not a segment prefix
    assert is_route_disabled("/admin/permissions/groups", ["GET"], ["/admin/permissions/*"])
    assert not is_route_disabled("/health/", ["GET"], ["/version"])
    assert not is_route_disabled("/ws", None, ["GET /ws"])  # method entries never apply to websockets


def test_settings_accept_comma_separated_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACHIFAST_DISABLE_CORE_ROUTERS", "permissions, admin")
    monkeypatch.setenv("PACHIFAST_DISABLE_CORE_ROUTES", "/user/permission,/user/settings")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.disable_core_routers == ["permissions", "admin"]
        assert settings.disable_core_routes == ["/user/permission", "/user/settings"]
    finally:
        get_settings.cache_clear()


# --- integration ------------------------------------------------------------


def test_nothing_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client, paths = _paths(monkeypatch)
    assert client.get("/api/auth/user").status_code == 401  # baseline: protected without a token
    assert "/api/auth/token" in paths
    assert "/api/admin/permissions/groups" in paths
    assert "/api/admin/user" in paths
    assert "/api/auth/user/settings" in paths
    assert "/api/infos/engines" in paths
    assert not any("extrainfo" in path for path in paths)


def test_disable_core_routers(monkeypatch: pytest.MonkeyPatch) -> None:
    _, paths = _paths(monkeypatch, routers="permissions,admin")
    assert not any(path.startswith("/api/admin/") for path in paths)
    assert "/api/auth/token" in paths and "/api/auth/user" in paths and "/api/health/" in paths


def test_disable_single_router_by_last_segment(monkeypatch: pytest.MonkeyPatch) -> None:
    _, paths = _paths(monkeypatch, routers="infos")
    assert "/api/infos/engines" not in paths
    assert "/api/admin/user" in paths


def test_disable_core_routes_keeps_the_rest_protected(monkeypatch: pytest.MonkeyPatch) -> None:
    client, paths = _paths(monkeypatch, routes="/user/settings")
    assert "/api/auth/user/settings" not in paths
    assert "/api/auth/user/settings/{category}/{key}" not in paths
    assert "/api/auth/user" in paths and "/api/admin/user" in paths
    # Removed routes are gone; the remaining ones of a filtered router still require authentication.
    assert client.get("/api/auth/user/settings").status_code in (404, 405)
    assert client.get("/api/auth/user").status_code == 401
    assert client.get("/api/admin/user").status_code == 401


def test_disable_route_by_method(monkeypatch: pytest.MonkeyPatch) -> None:
    client, paths = _paths(monkeypatch, routes="PUT /auth/user/settings/{category}/{key}")
    assert "/api/auth/user/settings" in paths
    assert "/api/auth/user/settings/{category}/{key}" not in paths
    assert client.get("/api/auth/user/settings").status_code == 401


def test_module_level_routers_are_not_modified(monkeypatch: pytest.MonkeyPatch) -> None:
    """A filtered app must not leak into the next one built in the same process."""
    _paths(monkeypatch, routers="admin", routes="/user/settings")
    _, paths = _paths(monkeypatch)
    assert "/api/admin/user" in paths and "/api/auth/user/settings" in paths


def test_infos_engines_is_superuser_only(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Anonymous: 401, regular user: 403, superuser: 200 (it exposes database hosts and names)."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from pachifast.auth import cruds, models  # noqa: F401
    from pachifast.auth.schemas import UserCreate
    from pachifast.auth.security import create_access_token
    from pachifast.database import Base

    url = f"sqlite+aiosqlite:///{tmp_path / 'auth.sqlite3'}"

    async def prepare() -> tuple[str, str]:
        engine = create_async_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            regular = await cruds.create_user(session, UserCreate(username="regular", password="long-enough-pass"))
            admin = await cruds.create_user(
                session, UserCreate(username="boss", password="long-enough-pass", is_superuser=True)
            )
        await engine.dispose()
        return create_access_token(regular.id), create_access_token(admin.id)

    regular_token, admin_token = asyncio.run(prepare())
    client, _ = _paths(monkeypatch)
    url_path = "/api/infos/engines"
    assert client.get(url_path).status_code == 401
    assert client.get(url_path, headers={"Authorization": f"Bearer {regular_token}"}).status_code == 403
    assert client.get(url_path, headers={"Authorization": f"Bearer {admin_token}"}).status_code == 200


# --- app modules (disable_routers / disable_routes) -------------------------


@pytest.fixture
def billing_module(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """An importable app module ``demo_apps.billing`` with two routers (invoices, pay)."""
    import importlib
    import sys

    from pachifast import AppModule

    package = tmp_path / "demo_apps"
    (package / "billing").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "billing" / "__init__.py").write_text("")
    (package / "billing" / "root.py").write_text(
        "from pachifast.routing.router_factory import get_router\n"
        'router = get_router(name="billing", deps=["noauth"])\n\n\n'
        '@router.get("/invoices")\nasync def invoices():\n    return []\n\n\n'
        '@router.post("/pay")\nasync def pay():\n    return {}\n'
    )
    (package / "billing" / "reports.py").write_text(
        "from pachifast.routing.router_factory import get_router\n"
        'router = get_router(name="reports", deps=["noauth"])\n\n\n'
        '@router.get("/daily")\nasync def daily():\n    return []\n'
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    yield AppModule(
        name="billing",
        kind="app",
        router_modules=("demo_apps.billing.root", "demo_apps.billing.reports"),
    )
    for name in [n for n in sys.modules if n.startswith("demo_apps")]:
        del sys.modules[name]


def test_app_routers_are_mounted_by_default(monkeypatch: pytest.MonkeyPatch, billing_module) -> None:
    client, _ = _paths(monkeypatch, modules=(core_module, billing_module))
    paths = set(client.get("/api/v1/openapi-apps.json").json()["paths"])
    assert {"/api/billing/invoices", "/api/billing/pay", "/api/reports/daily"} <= paths


def test_disable_routers_applies_to_apps_by_name_or_group(monkeypatch: pytest.MonkeyPatch, billing_module) -> None:
    client, _ = _paths(monkeypatch, all_routers="reports", modules=(core_module, billing_module))
    paths = set(client.get("/api/v1/openapi-apps.json").json()["paths"])
    assert "/api/reports/daily" not in paths and "/api/billing/pay" in paths

    client, _ = _paths(monkeypatch, all_routers="demo_apps.billing", modules=(core_module, billing_module))
    assert client.get("/api/v1/openapi-apps.json").json()["paths"] == {}  # the whole group is off


def test_disable_routes_applies_to_apps_and_blocks_the_call(monkeypatch: pytest.MonkeyPatch, billing_module) -> None:
    client, _ = _paths(monkeypatch, all_routes="POST /billing/pay", modules=(core_module, billing_module))
    paths = set(client.get("/api/v1/openapi-apps.json").json()["paths"])
    assert "/api/billing/pay" not in paths and "/api/billing/invoices" in paths
    assert client.post("/api/billing/pay").status_code in (404, 405)  # emergency switch: really gone
    assert client.get("/api/billing/invoices").status_code == 200


def test_disable_core_settings_do_not_touch_apps(monkeypatch: pytest.MonkeyPatch, billing_module) -> None:
    client, _ = _paths(monkeypatch, routers="billing,reports", routes="/pay", modules=(core_module, billing_module))
    paths = set(client.get("/api/v1/openapi-apps.json").json()["paths"])
    assert {"/api/billing/pay", "/api/billing/invoices", "/api/reports/daily"} <= paths


def test_disable_settings_reach_the_core_too(monkeypatch: pytest.MonkeyPatch) -> None:
    _, paths = _paths(monkeypatch, all_routers="admin", all_routes="/infos")
    assert not any(path.startswith("/api/admin/") for path in paths)
    assert "/api/infos/engines" not in paths and "/api/auth/token" in paths


def test_disable_settings_accept_yaml_lists(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pachifast.yaml").write_text(
        "routes:\n  disable_routers: [permissions]\n  disable_routes:\n    - GET /health\n"
    )
    monkeypatch.delenv("PACHIFAST_DISABLE_ROUTERS", raising=False)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.disable_routers == ["permissions"] and settings.disable_routes == ["GET /health"]
    finally:
        get_settings.cache_clear()
