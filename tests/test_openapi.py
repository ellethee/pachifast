"""Gli schemi OpenAPI devono contenere le route dei router montati (core in /docs/core)."""

import os

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

from fastapi.testclient import TestClient

from pachifast import create_app
from pachifast.presets import core_module


def test_core_and_full_schemas_list_core_routes_and_apps_is_empty() -> None:
    """Senza app/common: /docs (apps) vuoto, core e schema completo con le route di auth/admin."""
    app = create_app((core_module,))
    client = TestClient(app)

    core_paths = client.get("/api/v1/openapi-core.json").json()["paths"]
    assert "/api/auth/token" in core_paths
    assert any(path.startswith("/api/admin/") for path in core_paths)

    all_paths = client.get("/api/v1/openapi.json").json()["paths"]
    assert set(core_paths) <= set(all_paths)

    assert client.get("/api/v1/openapi-apps.json").json()["paths"] == {}
    assert "/debug/headers" not in all_paths
    assert client.get("/debug/headers").status_code == 404


def test_docs_can_be_switched_off(monkeypatch) -> None:
    """``docs_enabled=false`` removes the Swagger UI and every OpenAPI schema: nothing lists the routes."""
    from pachifast.config import get_settings

    monkeypatch.setenv("PACHIFAST_DOCS_ENABLED", "false")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app((core_module,)))
        for path in (
            "/docs",
            "/docs/core",
            "/api/v1/openapi.json",
            "/api/v1/openapi-core.json",
            "/api/v1/openapi-apps.json",
        ):
            assert client.get(path).status_code == 404, path
        assert client.get("/api/auth/user").status_code != 404  # the API itself is still mounted
    finally:
        monkeypatch.delenv("PACHIFAST_DOCS_ENABLED")
        get_settings.cache_clear()
