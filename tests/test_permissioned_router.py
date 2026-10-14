"""PermissionedRouter: dependencies and metadata added by the extra decorator parameters."""

from __future__ import annotations

import os

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest

from pachifast.routing.permissioned_router import PermissionedRouter


def _route(router: PermissionedRouter, path: str):
    return next(r for r in router.routes if getattr(r, "path", None) == path)


@pytest.mark.parametrize("verb", ["get", "post", "put", "patch", "delete"])
def test_verbs_register_route_with_permission_dependency(verb: str) -> None:
    router = PermissionedRouter()

    @getattr(router, verb)("/x", permissions=["a.b"], log_as="did {id}", skip_envelope=True)
    async def handler() -> dict:
        return {}

    route = _route(router, "/x")
    assert route.methods == {verb.upper()}
    assert len(route.dependencies) == 1
    assert route.openapi_extra["x-log-as"] == "did {id}"
    assert route.openapi_extra["x-skip-envelope"] is True


@pytest.mark.parametrize("flag", ["require_staff", "require_superuser"])
def test_staff_and_superuser_flags_add_a_dependency(flag: str) -> None:
    router = PermissionedRouter()
    router.get("/x", **{flag: True})(lambda: None)
    assert len(_route(router, "/x").dependencies) == 1


def test_route_without_extras_has_no_dependency() -> None:
    router = PermissionedRouter()
    router.get("/x")(lambda: None)
    assert _route(router, "/x").dependencies == []
    assert "x-log-as" not in (_route(router, "/x").openapi_extra or {})


def test_skip_auth_removes_only_the_router_auth_dependencies() -> None:
    from fastapi import Depends

    def auth() -> None: ...

    def other() -> None: ...

    auth_dep, other_dep = Depends(auth), Depends(other)
    router = PermissionedRouter(dependencies=[auth_dep, other_dep])
    router._auth_deps = [auth_dep]
    router.get("/public", skip_auth=True)(lambda: None)
    router.get("/private")(lambda: None)
    assert [d.dependency for d in _route(router, "/public").dependencies] == [other]
    assert [d.dependency for d in _route(router, "/private").dependencies] == [auth, other]
    assert router.dependencies == [auth_dep, other_dep]  # restored


def test_websocket_with_permissions_adds_a_dependency() -> None:
    router = PermissionedRouter()
    router.websocket("/ws", permissions=["a.b"])(lambda ws: None)
    router.websocket("/plain")(lambda ws: None)
    assert len(_route(router, "/ws").dependant.dependencies) >= 1
    assert _route(router, "/plain").path == "/plain"
