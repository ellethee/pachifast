"""FastAPI application factory built on explicit modules."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware

from pachifast.modules import (
    AppModule,
    configure_modules,
    enter_lifespan_hooks,
    run_setup_hooks,
    run_shutdown_hooks,
    run_startup_hooks,
)
from pachifast.udp import udp_registry


def create_app(
    modules: Iterable[AppModule],
    *,
    cors_origins: Sequence[str] | None = None,
) -> FastAPI:
    """Build a FastAPI application from the declared module registry.

    The factory performs the full bootstrap sequence used by generated projects:

    - merges module settings into the global settings object
    - configures logging
    - wires startup, shutdown and lifespan hooks
    - mounts routers under `/api`
    - installs the split OpenAPI documentation endpoints
    - publishes shared registries on `app.state`
    """
    configured_modules = configure_modules(modules)

    from pachifast.config import get_settings
    from pachifast.errors import install_error_handlers
    from pachifast.i18n import configure_catalogs
    from pachifast.logging.customformatter import CustomFormatter
    from pachifast.openapi.openapi_setup import install_openapi_endpoints
    from pachifast.routing.core_filters import filter_router
    from pachifast.routing.discover import get_routers

    settings = get_settings()
    _configure_logging(CustomFormatter, _log_level(settings), settings.log_format)
    log = logging.getLogger(__name__)
    if (settings.swagger_noauth or settings.global_noauth) and not settings.debug:
        log.warning(
            "swagger_noauth / global_noauth are on without debug: they switch authentication off, for development only"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info("Application startup")
        async with AsyncExitStack() as stack:
            await run_startup_hooks(app, configured_modules)
            await enter_lifespan_hooks(stack, app, configured_modules)
            try:
                yield
            finally:
                log.info("Application shutdown")
                await run_shutdown_hooks(app, configured_modules)

    app = FastAPI(
        title=settings.project_name
        or getattr(settings, "app_name", "")
        or getattr(settings, "title", "")
        or "PachiFast",
        version=settings.version or "0.1.0",
        description=settings.description,
        debug=settings.debug,
        lifespan=lifespan,
        redirect_slashes=False,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    install_error_handlers(app)
    # messages of the core, of the modules (locale_package) and of the project's locale/ directory
    configure_catalogs(configured_modules, Path.cwd())
    app.state.modules = configured_modules
    app.state.udp_registry = udp_registry

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_headers=["*"],
            allow_methods=["*"],
            expose_headers=["Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"],
        )

    if settings.use_accept_language:
        from pachifast.i18n.middleware import LocaleMiddleware

        app.add_middleware(LocaleMiddleware)

    if settings.activity_log_enabled:
        from pachifast.audit.middleware import ActivityLogMiddleware

        app.add_middleware(ActivityLogMiddleware)

    # a middleware added here wraps the ones above; the request id (added next) stays the outermost
    run_setup_hooks(app, configured_modules)

    if settings.request_id_enabled:
        from pachifast.logging.request_id import RequestIdMiddleware

        app.add_middleware(RequestIdMiddleware)  # added last, so it is the outermost: every log line has the id

    mounts: list[tuple[APIRouter, str]] = []
    for router in get_routers(configured_modules):
        # Modules can set pachifast_mount_prefix="" to mount at root (e.g. OAuth2 callbacks)
        mount_prefix = getattr(router, "pachifast_mount_prefix", "/api")
        filtered_router = filter_router(
            router,
            mount_prefix=mount_prefix,
            disabled_routers=settings.disable_routers,
            disabled_routes=settings.disable_routes,
            core_disabled_routers=settings.disable_core_routers,
            core_disabled_routes=settings.disable_core_routes,
        )
        if filtered_router is None:
            continue
        router = filtered_router
        app.include_router(router, prefix=mount_prefix)
        mounts.append((router, mount_prefix))
        log.info("Mounted router %s (prefix=%r)", router.prefix, mount_prefix)

    if settings.activity_log_enabled:
        from pachifast.audit.root import router as _audit_router

        _audit_router.pachifast_module_kind = "app"  # type: ignore[attr-defined]
        app.include_router(_audit_router, prefix="/api")
        mounts.append((_audit_router, "/api"))
        log.info("Mounted router %s (activity log)", _audit_router.prefix)

    if settings.events_broadcast_enabled:
        from pachifast.events.root import router as _events_router
        from pachifast.events.root import ticket_router as _ticket_router

        for _router in (_events_router, _ticket_router):
            _router.pachifast_module_kind = "app"  # type: ignore[attr-defined]
            app.include_router(_router, prefix="/api")
            mounts.append((_router, "/api"))
        log.info("Mounted router %s (events broadcast)", _events_router.prefix)

    if settings.docs_enabled:
        install_openapi_endpoints(app, mounts=mounts)
    else:
        log.info("Swagger UI and OpenAPI schemas are switched off (docs_enabled=false)")
    return app


def _log_level(settings: object) -> int:
    """Level of the application loggers: ``log_level`` if set, else DEBUG with ``debug`` on and INFO otherwise."""
    name = getattr(settings, "log_level", "") or ("DEBUG" if getattr(settings, "debug", False) else "INFO")
    return logging.getLevelName(name)


def _configure_logging(formatter_class: type, level: int = logging.INFO, log_format: str = "text") -> None:
    """Install the default console logger once for the current process."""
    from pachifast.logging.request_id import RequestIdFilter

    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    if log_format == "json":
        from pachifast.logging.jsonformatter import JsonFormatter

        formatter: logging.Formatter = JsonFormatter()
    else:
        formatter = formatter_class(
            "[%(levelname)s] [{%(pathname)s:%(lineno)d} %(levelname)s] %(asctime)s - "
            "%(name)s [%(request_id)s]: %(message)s"
        )
    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=[handler])
