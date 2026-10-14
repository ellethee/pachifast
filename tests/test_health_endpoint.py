"""``GET /api/health/``: the database flag reflects reachability of the auth database."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient

from pachifast import create_app
from pachifast.config import get_settings
from pachifast.presets import core_module


def _health(monkeypatch: pytest.MonkeyPatch, url: str | None) -> dict:
    if url is None:
        monkeypatch.delenv("PACHIFAST_AUTH_DATABASE_URL", raising=False)
    else:
        monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        with TestClient(create_app((core_module,))) as client:
            response = client.get("/api/health/")
    finally:
        get_settings.cache_clear()
    assert response.status_code == 200
    return response.json()["data"]


def test_health_with_reachable_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    data = _health(monkeypatch, f"sqlite+aiosqlite:///{tmp_path / 'ok.sqlite3'}")
    assert data["database"] is True
    assert set(data) == {"name", "version", "description", "database"}
    assert data["name"] and data["version"]


def test_health_reports_the_configured_name_version_and_description(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PACHIFAST_PROJECT_NAME", "Billing")
    monkeypatch.setenv("PACHIFAST_VERSION", "2.3.4")
    monkeypatch.setenv("PACHIFAST_DESCRIPTION", "Billing API")
    data = _health(monkeypatch, f"sqlite+aiosqlite:///{tmp_path / 'ok.sqlite3'}")
    assert (data["name"], data["version"], data["description"]) == ("Billing", "2.3.4", "Billing API")


def test_health_with_unreachable_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "no" / "such" / "dir" / "db.sqlite3"
    assert _health(monkeypatch, f"sqlite+aiosqlite:///{missing}")["database"] is False


def test_health_without_database_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _health(monkeypatch, "")["database"] is False


def _get(monkeypatch: pytest.MonkeyPatch, url: str, path: str):
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        with TestClient(create_app((core_module,))) as client:
            return client.get(path)
    finally:
        get_settings.cache_clear()


def test_liveness_does_not_need_the_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    response = _get(monkeypatch, f"sqlite+aiosqlite:///{tmp_path / 'no' / 'dir' / 'db.sqlite3'}", "/api/health/live")
    assert response.status_code == 200
    assert response.json()["data"] == {"status": "ok"}


def test_readiness_is_200_with_the_database_and_503_without(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ready = _get(monkeypatch, f"sqlite+aiosqlite:///{tmp_path / 'ok.sqlite3'}", "/api/health/ready")
    assert ready.status_code == 200 and ready.json()["data"]["database"] is True
    down = _get(monkeypatch, f"sqlite+aiosqlite:///{tmp_path / 'no' / 'dir' / 'db.sqlite3'}", "/api/health/ready")
    assert down.status_code == 503
    assert down.json()["detail"][0]["code"] == "health.not_ready"
