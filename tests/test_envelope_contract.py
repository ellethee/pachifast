"""Contract: every 2xx JSON response has ``success: true``; an error is never a 2xx with ``success: false``."""

import os

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from pachifast.routing.envelope_route import EnvelopeRoute
from pachifast.schemas.responses import StandardResponse


def _client() -> TestClient:
    router = APIRouter(route_class=EnvelopeRoute)

    @router.get("/ok")
    async def ok() -> dict:
        return {"a": 1}

    @router.get("/created", status_code=201)
    async def created() -> dict:
        return {"id": 1}

    @router.get("/direct-error")
    async def direct_error() -> JSONResponse:
        return JSONResponse({"detail": [{"loc": ["x"], "msg": "no", "type": "value_error"}]}, status_code=400)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_2xx_is_wrapped_with_success_true() -> None:
    client = _client()
    for path, status in (("/ok", 200), ("/created", 201)):
        response = client.get(path)
        assert response.status_code == status
        assert response.json()["success"] is True and "data" in response.json()


def test_a_json_error_returned_by_an_endpoint_is_not_wrapped() -> None:
    response = _client().get("/direct-error")
    assert response.status_code == 400
    assert set(response.json()) == {"detail"}


def test_the_envelope_model_defaults_to_success_true() -> None:
    assert StandardResponse().success is True


def test_envelope_can_be_disabled_for_the_whole_app(monkeypatch: pytest.MonkeyPatch) -> None:
    from pachifast.config import get_settings

    monkeypatch.setenv("PACHIFAST_ENVELOPE_ENABLED", "false")
    get_settings.cache_clear()
    try:
        response = _client().get("/ok")
        assert response.status_code == 200 and response.json() == {"a": 1}
    finally:
        monkeypatch.delenv("PACHIFAST_ENVELOPE_ENABLED")
        get_settings.cache_clear()


def test_version_endpoint_reports_the_installed_version() -> None:
    from importlib.metadata import version

    from pachifast.version.root import router

    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/version/")
    assert response.status_code == 200
    assert version("pachifast") in response.text
