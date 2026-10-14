"""AppModule registry: kind inference, settings merge, hooks, lifespan and migration targets."""

from __future__ import annotations

import os
import sys
import types
from contextlib import AsyncExitStack, contextmanager
from pathlib import Path

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi import FastAPI

from pachifast import modules as mods
from pachifast.modules import AppModule, MigrationTarget


@pytest.fixture
def fake_module(monkeypatch: pytest.MonkeyPatch):
    """Factory registering a throwaway module in ``sys.modules``."""

    def make(name: str, **attrs: object) -> types.ModuleType:
        module = types.ModuleType(name)
        for key, value in attrs.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    return make


@pytest.fixture(autouse=True)
def _restore_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mods, "_configured_modules", ())


def test_kind_is_explicit_or_inferred() -> None:
    assert mods.get_module_kind(AppModule("x", kind="common")) == "common"
    assert mods.get_module_kind(AppModule("core")) == "core"
    assert mods.get_module_kind(AppModule("common.mail")) == "common"
    assert mods.get_module_kind(AppModule("a", router_modules=("pachifast.x",))) == "core"
    assert mods.get_module_kind(AppModule("a", settings_modules=("common.s",))) == "common"
    assert mods.get_module_kind(AppModule("billing", router_modules=("apps.billing.router",))) == "app"


def test_configure_and_resolve_modules() -> None:
    first = AppModule("a")
    assert mods.get_configured_modules() == ()
    assert mods.configure_modules([first]) == (first,)
    assert mods.resolve_modules() == (first,)
    other = AppModule("b")
    assert mods.resolve_modules([other]) == (other,)


class _Obj:
    def __init__(self) -> None:
        self.public = 1
        self._private = 2


def test_settings_module_with_register_settings(fake_module) -> None:
    fake_module("t_reg", register_settings=lambda s: setattr(s, "registered", True))
    target = types.SimpleNamespace()
    mods.apply_module_settings(target, [AppModule("m", settings_modules=("t_reg",))])
    assert target.registered is True


def test_settings_module_merges_objects_and_constants(fake_module) -> None:
    fake_module("t_plain", billing_settings=_Obj(), LIMIT=5, other=3)
    target = types.SimpleNamespace()
    mods.apply_module_settings(target, [AppModule("m", settings_modules=("t_plain",))])
    assert target.public == 1 and target.limit == 5
    assert not hasattr(target, "_private") and not hasattr(target, "other")


def test_settings_module_without_exports_is_a_noop(fake_module) -> None:
    fake_module("t_empty", value=1)
    target = types.SimpleNamespace()
    mods.apply_module_settings(target, [AppModule("m", settings_modules=("t_empty",))])
    assert vars(target) == {}


async def test_startup_and_shutdown_hook_order(fake_module) -> None:
    calls: list[str] = []

    def sync_no_app() -> None:
        calls.append("sync")

    async def async_with_app(app: FastAPI) -> None:
        calls.append(f"async:{type(app).__name__}")

    fake_module("t_hooks", ref=lambda: calls.append("ref"))
    first = AppModule("a", startup_hooks=(sync_no_app, "t_hooks:ref"), shutdown_hooks=(sync_no_app, async_with_app))
    second = AppModule("b", startup_hooks=(async_with_app,), shutdown_hooks=(lambda: calls.append("b"),))
    app = FastAPI()
    await mods.run_startup_hooks(app, [first, second])
    assert calls == ["sync", "ref", "async:FastAPI"]
    calls.clear()
    await mods.run_shutdown_hooks(app, [first, second])
    assert calls == ["b", "async:FastAPI", "sync"]


async def test_hooks_use_configured_modules_by_default() -> None:
    calls: list[int] = []
    mods.configure_modules([AppModule("a", startup_hooks=(lambda: calls.append(1),))])
    await mods.run_startup_hooks(FastAPI())
    assert calls == [1]


async def test_invalid_hook_references(fake_module) -> None:
    fake_module("t_bad", notcallable=1)
    with pytest.raises(ValueError, match="Invalid startup hook"):
        await mods.run_startup_hooks(FastAPI(), [AppModule("a", startup_hooks=("no_separator",))])
    with pytest.raises(TypeError, match="not callable"):
        await mods.run_startup_hooks(FastAPI(), [AppModule("a", startup_hooks=("t_bad:notcallable",))])


async def test_lifespan_hooks_enter_and_exit_in_order(fake_module) -> None:
    events: list[str] = []

    @contextmanager
    def sync_cm():
        events.append("sync in")
        yield
        events.append("sync out")

    class AsyncCM:
        def __init__(self, app: FastAPI) -> None:
            self.app = app

        async def __aenter__(self) -> None:
            events.append("async in")

        async def __aexit__(self, *exc: object) -> None:
            events.append("async out")

    fake_module("t_life", cm=sync_cm)
    module = AppModule("a", lifespan_hooks=("t_life:cm", AsyncCM))
    async with AsyncExitStack() as stack:
        await mods.enter_lifespan_hooks(stack, FastAPI(), [module])
        assert events == ["sync in", "async in"]
    assert events == ["sync in", "async in", "async out", "sync out"]


async def test_lifespan_hook_must_return_a_context_manager() -> None:
    module = AppModule("a", lifespan_hooks=(lambda: 42,))
    async with AsyncExitStack() as stack:
        with pytest.raises(TypeError, match="context manager"):
            await mods.enter_lifespan_hooks(stack, FastAPI(), [module])


def test_accepts_app_detection() -> None:
    assert mods._accepts_app(lambda app: None)
    assert not mods._accepts_app(lambda: None)
    assert not mods._accepts_app(lambda *, app: None)
    assert not mods._accepts_app(print.__init__)  # signature not introspectable or without positional parameters


def test_migration_targets_are_collected() -> None:
    a = MigrationTarget("a", ("m.a",), database_url_field="AUTH_DATABASE_URL")
    b = MigrationTarget("b", ("m.b",), database_url_resolver="m:url", version_table="vt")
    result = mods.resolve_migration_targets(
        [AppModule("x", migration_targets=(a,)), AppModule("y", migration_targets=(b,))]
    )
    assert result == (a, b)
    assert b.version_table == "vt" and a.version_table is None


def test_duplicate_migration_target_is_rejected() -> None:
    t = MigrationTarget("dup", (), database_url_field="F")
    with pytest.raises(ValueError, match="Duplicate"):
        mods.resolve_migration_targets([AppModule("x", migration_targets=(t, t))])


def test_migration_target_without_database_url_is_rejected() -> None:
    with pytest.raises(ValueError, match="database_url_field or database_url_resolver"):
        mods.resolve_migration_targets([AppModule("x", migration_targets=(MigrationTarget("n", ()),))])


def test_app_module_is_frozen() -> None:
    with pytest.raises(AttributeError):
        AppModule("a").name = "b"  # type: ignore[misc]
    assert isinstance(Path("."), Path)
