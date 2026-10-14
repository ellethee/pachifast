"""PermissionedRouter: APIRouter with native support for permission checks."""

from typing import Any

from fastapi import APIRouter, Depends

from .envelope_route import EnvelopeRoute


class PermissionedRouter(APIRouter):
    """APIRouter that accepts authorization parameters on the route decorators.

    The extra parameters (permissions, require_staff, require_superuser, skip_auth,
    skip_envelope, log_as) are intercepted and automatically converted into
    FastAPI dependencies or metadata. They can be used both on get_router()
    (for the whole router) and on the single decorators
    @router.get / .post / .put / .patch / .delete.

    Examples:
        # All routes of the same router require is_staff
        router = get_router(require_staff=True)

        # Only this route requires the specific permission
        @router.get("/report", permissions=["reports.view"])
        async def get_report(): ...

        # Superusers only
        @router.delete("/{id}", require_superuser=True)
        async def delete_item(id: int): ...

        # Skip authentication for a single route (the router uses auth by default)
        @router.get("/public", skip_auth=True)
        async def public_endpoint(): ...

        # Raw response, not wrapped in StandardResponse (e.g. standard OAuth2
        # endpoints that must return {access_token, ...} at the top of the body)
        @router.post("/token", skip_envelope=True)
        async def login(): ...

        # Human-readable description for the activity log (interpolated with path params and username)
        @router.patch("/{id}", log_as="<b>{username}</b> edited #{id}")
        async def update_item(id: int): ...
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("route_class", EnvelopeRoute)
        super().__init__(*args, **kwargs)
        # Populated by get_router() with the Depends objects it added for auth.
        # skip_auth=True on a route removes exactly these deps for that route.
        self._auth_deps: list[Any] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _inject_permission_dep(
        kwargs: dict[str, Any],
        permissions: list[str] | None,
        require_staff: bool,
        require_superuser: bool,
    ) -> dict[str, Any]:
        """Add the permission dependency to the route dependencies."""
        if not (permissions or require_staff or require_superuser):
            return kwargs
        from pachifast.auth.dependencies import make_permission_checker

        checker = make_permission_checker(permissions, require_staff, require_superuser)
        deps = list(kwargs.get("dependencies") or [])
        deps.append(Depends(checker))
        return {**kwargs, "dependencies": deps}

    def _decorate_with_log_as(self, decorator: Any, log_as: str) -> Any:
        """Wrap a route decorator to store log_as in openapi_extra.

        We use openapi_extra because it is the only dict FastAPI copies explicitly
        when include_router() recreates the routes on the main app.
        The middleware reads it as openapi_extra["x-log-as"].
        """

        def wrapper(func: Any) -> Any:
            result = decorator(func)
            if self.routes:
                route = self.routes[-1]
                extra: dict[str, Any] = dict(route.openapi_extra or {})
                extra["x-log-as"] = log_as
                route.openapi_extra = extra
            return result

        return wrapper

    # ------------------------------------------------------------------
    # add_api_route override — handles skip_auth, skip_envelope and log_as
    # ------------------------------------------------------------------

    def add_api_route(
        self,
        path: str,
        endpoint: Any,
        *,
        skip_auth: bool = False,
        skip_envelope: bool = False,
        log_as: str | None = None,
        **kwargs: Any,
    ) -> None:  # type: ignore[override]
        """Add a route, temporarily stripping auth deps when skip_auth=True.

        skip_envelope=True marks the route so that EnvelopeRoute does not wrap it in
        StandardResponse (see routing/envelope_route.py); the flag travels inside
        openapi_extra["x-skip-envelope"] because it is the only dict FastAPI copies
        explicitly when include_router() recreates the routes on the main app.
        """
        if skip_envelope:
            extra: dict[str, Any] = dict(kwargs.get("openapi_extra") or {})
            extra["x-skip-envelope"] = True
            kwargs["openapi_extra"] = extra

        if skip_auth and self._auth_deps:
            saved = list(self.dependencies)
            self.dependencies = [d for d in saved if d not in self._auth_deps]
            try:
                super().add_api_route(path, endpoint, **kwargs)
            finally:
                self.dependencies = saved
        else:
            super().add_api_route(path, endpoint, **kwargs)

        if log_as and self.routes:
            route = self.routes[-1]
            extra = dict(route.openapi_extra or {})
            extra["x-log-as"] = log_as
            route.openapi_extra = extra

    # ------------------------------------------------------------------
    # Route decorators — forward skip_auth + skip_envelope + permission + log_as params
    # ------------------------------------------------------------------

    def _route_decorator(self, path: str, methods: list[str], *, skip_auth: bool, skip_envelope: bool, **kwargs: Any):
        """Return a decorator that registers a route via add_api_route (always,
        regardless of skip_auth/skip_envelope) so both flags reach it reliably."""

        def decorator(func: Any) -> Any:
            self.add_api_route(path, func, methods=methods, skip_auth=skip_auth, skip_envelope=skip_envelope, **kwargs)
            return func

        return decorator

    def get(
        self,
        *args: Any,
        skip_auth: bool = False,
        skip_envelope: bool = False,
        permissions: list[str] | None = None,
        require_staff: bool = False,
        require_superuser: bool = False,
        log_as: str | None = None,
        **kwargs: Any,
    ):  # type: ignore[override]
        kwargs = self._inject_permission_dep(kwargs, permissions, require_staff, require_superuser)
        dec = self._route_decorator(args[0], ["GET"], skip_auth=skip_auth, skip_envelope=skip_envelope, **kwargs)
        return self._decorate_with_log_as(dec, log_as) if log_as else dec

    def post(
        self,
        *args: Any,
        skip_auth: bool = False,
        skip_envelope: bool = False,
        permissions: list[str] | None = None,
        require_staff: bool = False,
        require_superuser: bool = False,
        log_as: str | None = None,
        **kwargs: Any,
    ):  # type: ignore[override]
        kwargs = self._inject_permission_dep(kwargs, permissions, require_staff, require_superuser)
        dec = self._route_decorator(args[0], ["POST"], skip_auth=skip_auth, skip_envelope=skip_envelope, **kwargs)
        return self._decorate_with_log_as(dec, log_as) if log_as else dec

    def put(
        self,
        *args: Any,
        skip_auth: bool = False,
        skip_envelope: bool = False,
        permissions: list[str] | None = None,
        require_staff: bool = False,
        require_superuser: bool = False,
        log_as: str | None = None,
        **kwargs: Any,
    ):  # type: ignore[override]
        kwargs = self._inject_permission_dep(kwargs, permissions, require_staff, require_superuser)
        dec = self._route_decorator(args[0], ["PUT"], skip_auth=skip_auth, skip_envelope=skip_envelope, **kwargs)
        return self._decorate_with_log_as(dec, log_as) if log_as else dec

    def patch(
        self,
        *args: Any,
        skip_auth: bool = False,
        skip_envelope: bool = False,
        permissions: list[str] | None = None,
        require_staff: bool = False,
        require_superuser: bool = False,
        log_as: str | None = None,
        **kwargs: Any,
    ):  # type: ignore[override]
        kwargs = self._inject_permission_dep(kwargs, permissions, require_staff, require_superuser)
        dec = self._route_decorator(args[0], ["PATCH"], skip_auth=skip_auth, skip_envelope=skip_envelope, **kwargs)
        return self._decorate_with_log_as(dec, log_as) if log_as else dec

    def delete(
        self,
        *args: Any,
        skip_auth: bool = False,
        skip_envelope: bool = False,
        permissions: list[str] | None = None,
        require_staff: bool = False,
        require_superuser: bool = False,
        log_as: str | None = None,
        **kwargs: Any,
    ):  # type: ignore[override]
        kwargs = self._inject_permission_dep(kwargs, permissions, require_staff, require_superuser)
        dec = self._route_decorator(args[0], ["DELETE"], skip_auth=skip_auth, skip_envelope=skip_envelope, **kwargs)
        return self._decorate_with_log_as(dec, log_as) if log_as else dec

    def websocket(
        self,
        *args: Any,
        permissions: list[str] | None = None,
        require_staff: bool = False,
        require_superuser: bool = False,
        **kwargs: Any,
    ):  # type: ignore[override]
        if permissions or require_staff or require_superuser:
            from pachifast.auth.dependencies import make_ws_permission_checker

            checker = make_ws_permission_checker(permissions, require_staff, require_superuser)
            deps = list(kwargs.get("dependencies") or [])
            deps.append(Depends(checker))
            kwargs = {**kwargs, "dependencies": deps}
        return super().websocket(*args, **kwargs)
