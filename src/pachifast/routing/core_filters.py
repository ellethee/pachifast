"""Disable routers and routes from configuration (a quick switch, e.g. in an emergency).

Settings (environment variables and ``.env`` carry the ``PACHIFAST_`` prefix; the YAML file uses the bare names)::

    PACHIFAST_DISABLE_ROUTERS=billing,admin              # any module: core, apps and common
    PACHIFAST_DISABLE_ROUTES=POST /billing/pay,/user/settings
    PACHIFAST_DISABLE_CORE_ROUTERS=permissions           # only the built-in (core) module
    PACHIFAST_DISABLE_CORE_ROUTES=/user/settings

The ``disable_*`` settings apply to every module, the ``disable_core_*`` ones only to the core: the effective
lists for a core router are the union of both.
"""

import logging
from collections.abc import Iterable
from fnmatch import fnmatchcase

from fastapi import APIRouter
from starlette.routing import BaseRoute

log = logging.getLogger(__name__)

CORE_MODULE_KIND = "core"
_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_IGNORED_MODULE_PARTS = frozenset({"root", "main", "__init__"})


def router_key(module_path: str, package: str = "pachifast") -> str | None:
    """Return the dotted key identifying a router, derived from the module that declares it.

    Built-in routers are relative to the package (``pachifast.auth.root`` gives ``auth``,
    ``pachifast.health.health`` gives ``health``); any other module keeps its full path
    (``apps.billing.root`` gives ``apps.billing``, ``common.tools.invoices`` gives ``common.tools.invoices``).
    """
    parts = module_path.split(".")
    if parts[0] == package:
        parts = parts[1:]
    key_parts: list[str] = []
    for part in parts:
        if part in _IGNORED_MODULE_PARTS or (key_parts and key_parts[-1] == part):
            continue
        key_parts.append(part)
    return ".".join(key_parts) or None


def _normalize_router_name(name: str) -> str:
    """``/Admin/Permissions`` and ``admin.permissions`` both become ``admin.permissions``."""
    return name.strip().strip("/").replace("/", ".").lower()


def is_router_disabled(key: str, disabled: Iterable[str]) -> bool:
    """Tell whether the router ``key`` is switched off by one of the ``disabled`` names.

    A name matches when it is the whole key or a run of whole dotted segments of it: ``admin.permissions``
    is switched off by ``admin.permissions``, by the group ``admin`` and by the single segment ``permissions``
    (``user`` switches off every router with a ``user`` segment, ``billing`` also ``apps.billing.invoices``).
    """
    dotted_key = f".{key}."
    return any(name and f".{name}." in dotted_key for name in map(_normalize_router_name, disabled))


def _split_method(entry: str) -> tuple[str | None, str]:
    """Split ``"PUT /path"`` into ``("PUT", "/path")``; a bare path has no method."""
    head, _, rest = entry.strip().partition(" ")
    if rest and head.upper() in _HTTP_METHODS:
        return head.upper(), rest.strip()
    return None, entry.strip()


def _normalize_path(path: str) -> str:
    """Leading slash, no trailing slash (the root stays ``/``)."""
    return "/" + path.strip().strip("/")


def _candidate_paths(path: str) -> list[str]:
    """The path and each of its trailing sub-paths: ``/a/b/c`` gives ``/a/b/c``, ``/b/c``, ``/c``."""
    segments = [segment for segment in path.split("/") if segment]
    return ["/" + "/".join(segments[index:]) for index in range(len(segments))] or ["/"]


def is_route_disabled(path: str, methods: Iterable[str] | None, disabled: Iterable[str]) -> bool:
    """Tell whether a route (path relative to the mount prefix, e.g. ``/auth/user/settings``) is switched off.

    An entry is ``[METHOD ]/path`` and matches the route when its path equals the route path or one of its
    trailing sub-paths (``/user/settings`` matches ``/auth/user/settings``), including everything below it
    (``/user/settings/{category}/{key}``). ``*`` is a wildcard. Without METHOD every method is disabled.
    """
    route_methods = {method.upper() for method in (methods or [])}
    candidates = _candidate_paths(_normalize_path(path))
    for entry in disabled:
        method, pattern = _split_method(entry)
        if not pattern:
            continue
        if method is not None and route_methods and method not in route_methods:
            continue
        if method is not None and not route_methods:
            continue  # method-specific entries never apply to WebSocket routes
        pattern = _normalize_path(pattern)
        if any(fnmatchcase(candidate, pattern) or fnmatchcase(candidate, f"{pattern}/*") for candidate in candidates):
            return True
    return False


def _route_path_without_mount(route: BaseRoute, mount_prefix: str) -> str:
    """Route path relative to the mount prefix (``/api``)."""
    path = getattr(route, "path", "") or ""
    full = f"{mount_prefix}{path}"
    return full[len(mount_prefix) :] if mount_prefix and full.startswith(mount_prefix) else full


def filter_router(
    router: APIRouter,
    *,
    mount_prefix: str,
    disabled_routers: Iterable[str] = (),
    disabled_routes: Iterable[str] = (),
    core_disabled_routers: Iterable[str] = (),
    core_disabled_routes: Iterable[str] = (),
) -> APIRouter | None:
    """Apply the ``disable_*`` settings (all modules) and ``disable_core_*`` settings (core only) to a router.

    Returns:
        ``None`` if the whole router is disabled; the same router if nothing is removed; otherwise a new
        router holding only the remaining routes (the original module-level router is never modified).
    """
    is_core = getattr(router, "pachifast_module_kind", None) == CORE_MODULE_KIND
    key = getattr(router, "pachifast_router_key", None)
    routers_off = [*disabled_routers, *(core_disabled_routers if is_core else ())]
    routes_off = [*disabled_routes, *(core_disabled_routes if is_core else ())]

    if key and is_router_disabled(key, routers_off):
        log.warning("Router %r disabled by configuration", key)
        return None
    if not routes_off:
        return router

    kept: list[BaseRoute] = []
    for route in router.routes:
        path = _route_path_without_mount(route, mount_prefix)
        methods = getattr(route, "methods", None)
        if is_route_disabled(path, methods, routes_off):
            log.warning("Route %s %s disabled by configuration", sorted(methods or ["WS"]), path)
            continue
        kept.append(route)
    if len(kept) == len(router.routes):
        return router
    if not kept:
        log.warning("Router %r has no routes left: not mounted", key)
        return None

    filtered = APIRouter(routes=kept)
    for attribute in (
        "pachifast_module_kind",
        "pachifast_module_name",
        "pachifast_router_key",
        "pachifast_mount_prefix",
    ):
        if hasattr(router, attribute):
            setattr(filtered, attribute, getattr(router, attribute))
    return filtered
