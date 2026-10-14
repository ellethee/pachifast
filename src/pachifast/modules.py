"""Explicit module registry for reusable app composition."""

from __future__ import annotations

import importlib
import inspect
import logging
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager, AbstractContextManager, AsyncExitStack
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import FastAPI

log = logging.getLogger(__name__)


Hook = Callable[[], Any] | Callable[[FastAPI], Any] | str
LifespanHook = (
    Callable[[], AbstractAsyncContextManager[Any] | AbstractContextManager[Any]]
    | Callable[[FastAPI], AbstractAsyncContextManager[Any] | AbstractContextManager[Any]]
    | str
)
ModuleKind = Literal["core", "app", "common"]


@dataclass(frozen=True, slots=True)
class MigrationTarget:
    """Declarative description of an Alembic migration target."""

    name: str
    model_modules: tuple[str, ...]
    database_url_field: str | None = None
    database_url_resolver: str | None = None
    version_table: str | None = None


@dataclass(frozen=True, slots=True)
class AppModule:
    """Declarative description of a project module."""

    name: str
    kind: ModuleKind | None = None
    router_modules: tuple[str, ...] = ()
    settings_modules: tuple[str, ...] = ()
    migration_targets: tuple[MigrationTarget, ...] = ()
    #: run synchronously while the app is created, before it serves anything: the place to add a middleware or an
    #: exception handler (``add_middleware`` is refused once the app has started, so a startup hook is too late)
    setup_hooks: tuple[Hook, ...] = ()
    startup_hooks: tuple[Hook, ...] = ()
    shutdown_hooks: tuple[Hook, ...] = ()
    lifespan_hooks: tuple[LifespanHook, ...] = ()
    scan_dirs: tuple[str, ...] = ()  # dirs to scan with file-scan (alternative to router_modules)
    locale_package: str | None = (
        None  # package holding the messages (<language>.py with MESSAGES), e.g. "apps.billing.locale"
    )
    #: versions of pachifast the module works with, a PEP 440 specifier such as ">=0.15,<0.16"; checked at startup
    requires_pachifast: str | None = None


_configured_modules: tuple[AppModule, ...] = ()


def configure_modules(modules: Iterable[AppModule]) -> tuple[AppModule, ...]:
    """Persist the active module list for settings/bootstrap discovery."""
    configured = tuple(modules)
    check_requirements(configured)
    global _configured_modules
    _configured_modules = configured

    from pachifast.config.settings import get_settings

    get_settings.cache_clear()
    return configured


def check_requirements(modules: Iterable[AppModule]) -> None:
    """Refuse a module that needs another version of pachifast than the installed one.

    Raises:
        RuntimeError: a ``requires_pachifast`` is not met, or is not a valid specifier; the message names the module.
    """
    from importlib.metadata import PackageNotFoundError, version

    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import Version

    wanting = [module for module in modules if module.requires_pachifast]
    if not wanting:
        return
    try:
        installed = Version(version("pachifast"))
    except PackageNotFoundError:  # a source tree that is not installed: nothing to compare
        return
    for module in wanting:
        try:
            spec = SpecifierSet(module.requires_pachifast or "")
        except InvalidSpecifier as exc:
            raise RuntimeError(
                f"Module {module.name!r}: requires_pachifast {module.requires_pachifast!r} is not valid"
            ) from exc
        if not spec.contains(installed, prereleases=True):
            raise RuntimeError(
                f"Module {module.name!r} needs pachifast {module.requires_pachifast}, but {installed} is installed: "
                "upgrade or downgrade one of them"
            )


def get_configured_modules() -> tuple[AppModule, ...]:
    return _configured_modules


def resolve_modules(modules: Sequence[AppModule] | None = None) -> tuple[AppModule, ...]:
    if modules is None:
        return get_configured_modules()
    return tuple(modules)


def get_module_kind(module: AppModule) -> ModuleKind:
    """Return the logical module kind, inferring it when omitted."""
    if module.kind is not None:
        return module.kind

    if module.name == "core":
        return "core"

    if module.name.startswith("common."):
        return "common"

    for module_path in (*module.router_modules, *module.settings_modules):
        if module_path.startswith("pachifast."):
            return "core"
        if module_path.startswith("common."):
            return "common"

    return "app"


def apply_module_settings(settings: object, modules: Sequence[AppModule] | None = None) -> None:
    """Merge settings declared by the configured modules into the global settings object."""
    for module in resolve_modules(modules):
        for settings_module in module.settings_modules:
            _apply_settings_module(settings, settings_module)


def run_setup_hooks(app: FastAPI, modules: Sequence[AppModule] | None = None) -> None:
    """Run the module setup hooks (plain functions, not coroutines) in registration order, while the app is built."""
    for module in resolve_modules(modules):
        for hook in module.setup_hooks:
            callback = _import_hook(hook)
            result = callback(app) if _accepts_app(callback) else callback()
            if inspect.isawaitable(result):
                raise TypeError(f"Setup hook {hook!r} of module {module.name!r} must not be a coroutine function")


async def run_startup_hooks(app: FastAPI, modules: Sequence[AppModule] | None = None) -> None:
    """Run module startup hooks in registration order."""
    for module in resolve_modules(modules):
        for hook in module.startup_hooks:
            await _run_hook(hook, app)


async def run_shutdown_hooks(app: FastAPI, modules: Sequence[AppModule] | None = None) -> None:
    """Run module shutdown hooks in reverse registration order."""
    for module in reversed(resolve_modules(modules)):
        for hook in reversed(module.shutdown_hooks):
            await _run_hook(hook, app)


async def enter_lifespan_hooks(
    stack: AsyncExitStack,
    app: FastAPI,
    modules: Sequence[AppModule] | None = None,
) -> None:
    """Enter module lifespan context managers in registration order."""
    for module in resolve_modules(modules):
        for hook in module.lifespan_hooks:
            await _enter_lifespan_hook(stack, hook, app)


def resolve_migration_targets(modules: Sequence[AppModule] | None = None) -> tuple[MigrationTarget, ...]:
    """Return all migration targets declared by the configured modules."""
    targets: list[MigrationTarget] = []
    seen: set[str] = set()

    for module in resolve_modules(modules):
        for target in module.migration_targets:
            if target.name in seen:
                raise ValueError(f"Duplicate migration target name: {target.name}")
            if not target.database_url_field and not target.database_url_resolver:
                raise ValueError(
                    f"Migration target {target.name!r} must declare database_url_field or database_url_resolver"
                )
            targets.append(target)
            seen.add(target.name)

    return tuple(targets)


def _apply_settings_module(settings: object, module_path: str) -> None:
    mod = importlib.import_module(module_path)

    if hasattr(mod, "register_settings"):
        mod.register_settings(settings)
        log.info("Loaded settings via register_settings from %s", module_path)
        return

    merged = False

    for key in dir(mod):
        if key.endswith("_settings") or key == "settings":
            value = getattr(mod, key)
            if hasattr(value, "__dict__"):
                for attr, attr_value in vars(value).items():
                    if attr.startswith("_"):
                        continue
                    setattr(settings, attr, attr_value)
                merged = True
                log.info("Merged settings object %s from %s", key, module_path)

    for key in dir(mod):
        if key.isupper():
            setattr(settings, key.lower(), getattr(mod, key))
            merged = True

    if not merged:
        log.debug("No settings exported by %s", module_path)


async def _run_hook(hook: Hook, app: FastAPI) -> None:
    callback = _import_hook(hook)
    result = callback(app) if _accepts_app(callback) else callback()
    if inspect.isawaitable(result):
        await result


async def _enter_lifespan_hook(stack: AsyncExitStack, hook: LifespanHook, app: FastAPI) -> None:
    callback = _import_hook(hook)
    context_manager = callback(app) if _accepts_app(callback) else callback()
    if hasattr(context_manager, "__aenter__") and hasattr(context_manager, "__aexit__"):
        await stack.enter_async_context(context_manager)  # type: ignore[arg-type]
        return
    if hasattr(context_manager, "__enter__") and hasattr(context_manager, "__exit__"):
        stack.enter_context(context_manager)  # type: ignore[arg-type]
        return
    raise TypeError(f"Lifespan hook {hook!r} did not return a context manager")


def _import_hook(hook: Hook) -> Callable[..., Any]:
    if callable(hook):
        return hook

    module_path, separator, attr_name = hook.partition(":")
    if not separator:
        raise ValueError(f"Invalid startup hook reference: {hook!r}")

    module = importlib.import_module(module_path)
    callback = getattr(module, attr_name)
    if not callable(callback):
        raise TypeError(f"Startup hook {hook!r} is not callable")
    return callback


def _accepts_app(callback: Callable[..., Any]) -> bool:
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return False

    params = list(signature.parameters.values())
    if not params:
        return False
    return params[0].kind in (
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
    )
