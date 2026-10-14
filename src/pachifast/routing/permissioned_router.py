"""PermissionedRouter: APIRouter con supporto nativo ai controlli di permesso."""

from typing import Any

from fastapi import APIRouter, Depends

from .envelope_route import EnvelopeRoute


class PermissionedRouter(APIRouter):
    """APIRouter che accetta parametri di autorizzazione sui decoratori delle route.

    I parametri extra (permissions, require_staff, require_superuser, skip_auth,
    skip_envelope, log_as) vengono intercettati e convertiti automaticamente in
    dependency FastAPI o metadata. Possono essere usati sia su get_router()
    (a livello di router intero) sia sui singoli decoratori
    @router.get / .post / .put / .patch / .delete.

    Esempi:
        # Tutte le route dello stesso router richiedono is_staff
        router = get_router(require_staff=True)

        # Solo questa route richiede il permesso specifico
        @router.get("/report", permissions=["reports.view"])
        async def get_report(): ...

        # Solo superuser
        @router.delete("/{id}", require_superuser=True)
        async def delete_item(id: int): ...

        # Salta l'autenticazione per una singola route (il router usa auth di default)
        @router.get("/public", skip_auth=True)
        async def public_endpoint(): ...

        # Risposta grezza, non avvolta in StandardResponse (es. endpoint OAuth2
        # standard che devono restituire {access_token, ...} in cima al body)
        @router.post("/token", skip_envelope=True)
        async def login(): ...

        # Descrizione leggibile per il log attività (interpolata con path params e username)
        @router.patch("/{id}", log_as="<b>{username}</b> ha modificato #{id}")
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
        """Aggiunge la dependency di permesso alle dependencies della route."""
        if not (permissions or require_staff or require_superuser):
            return kwargs
        from pachifast.auth.dependencies import make_permission_checker

        checker = make_permission_checker(permissions, require_staff, require_superuser)
        deps = list(kwargs.get("dependencies") or [])
        deps.append(Depends(checker))
        return {**kwargs, "dependencies": deps}

    def _decorate_with_log_as(self, decorator: Any, log_as: str) -> Any:
        """Wrappa un decorator di route per salvare log_as in openapi_extra.

        Usiamo openapi_extra perché è l'unico dict che FastAPI copia esplicitamente
        quando include_router() ricrea le route sull'app principale.
        Il middleware lo legge come openapi_extra["x-log-as"].
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

        skip_envelope=True marca la route perché EnvelopeRoute non la avvolga in
        StandardResponse (vedi routing/envelope_route.py) — il flag viaggia dentro
        openapi_extra["x-skip-envelope"] perché è l'unico dict che FastAPI copia
        esplicitamente quando include_router() ricrea le route sull'app principale.
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
