"""The request id (``X-Request-ID``) and the JSON log format."""

from __future__ import annotations

import json
import logging
import os

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi.testclient import TestClient

from pachifast import create_app
from pachifast.config import get_settings
from pachifast.logging.jsonformatter import JsonFormatter
from pachifast.logging.request_id import RequestIdFilter, get_request_id, request_id_var
from pachifast.presets import core_module


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path, **settings: str) -> TestClient:
    monkeypatch.setenv("PACHIFAST_AUTH_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'db.sqlite3'}")
    for key, value in settings.items():
        monkeypatch.setenv(f"PACHIFAST_{key.upper()}", value)
    get_settings.cache_clear()
    return TestClient(create_app((core_module,)))


def test_every_response_has_a_request_id(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        first = client.get("/api/health/live").headers["X-Request-ID"]
        second = client.get("/api/health/live").headers["X-Request-ID"]
        assert first and second and first != second
        assert client.get("/api/does-not-exist").headers["X-Request-ID"]  # also an error response
    get_settings.cache_clear()


def test_a_sane_id_of_the_client_is_kept_and_a_bad_one_replaced(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path) as client:
        assert client.get("/api/health/live", headers={"X-Request-ID": "abc-123_x.y"}).headers["X-Request-ID"] == (
            "abc-123_x.y"
        )
        for bad in ("a b", "x" * 65, "evil\tid", "<script>"):
            got = client.get("/api/health/live", headers={"X-Request-ID": bad}).headers["X-Request-ID"]
            assert got != bad and len(got) == 32
    get_settings.cache_clear()


def test_the_request_id_can_be_switched_off(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    with _client(monkeypatch, tmp_path, request_id_enabled="false") as client:
        assert "X-Request-ID" not in client.get("/api/health/live").headers
    get_settings.cache_clear()


def test_the_filter_puts_the_id_of_the_request_in_the_record() -> None:
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", (), None)
    RequestIdFilter().filter(record)
    assert record.request_id == "-" and get_request_id() is None
    token = request_id_var.set("req-1")
    try:
        RequestIdFilter().filter(record)
    finally:
        request_id_var.reset(token)
    assert record.request_id == "req-1"


def test_the_json_formatter_writes_one_object_per_line() -> None:
    record = logging.LogRecord("pachifast.test", logging.WARNING, __file__, 1, "value %s", ("é",), None)
    record.request_id = "req-2"
    line = JsonFormatter().format(record)
    data = json.loads(line)
    assert "\n" not in line
    assert (data["level"], data["logger"], data["message"], data["request_id"]) == (
        "WARNING",
        "pachifast.test",
        "value é",
        "req-2",
    )
    assert data["time"].endswith("+00:00")
    try:
        raise ValueError("boom")
    except ValueError:
        import sys

        failing = logging.LogRecord("x", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
    assert "ValueError: boom" in json.loads(JsonFormatter().format(failing))["exc_info"]


def test_the_json_log_format_is_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    from pachifast.app_factory import _configure_logging
    from pachifast.logging.customformatter import CustomFormatter

    root = logging.getLogger()
    saved, saved_level = root.handlers[:], root.level
    root.handlers = []
    try:
        _configure_logging(CustomFormatter, logging.INFO, "json")
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
    finally:
        root.handlers = saved
        root.setLevel(saved_level)
