"""``rate_limit()``: the reusable per-route rate limit (opt-in, off unless a limit is given)."""

import os

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from pachifast import ratelimit
from pachifast.auth.security import create_access_token
from pachifast.config import get_settings
from pachifast.errors import install_error_handlers
from pachifast.ratelimit import rate_limit

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fixed instant, so a test never straddles the end of a window."""
    monkeypatch.setattr(ratelimit.time, "time", lambda: 1_000_000.0)
    get_settings.cache_clear()


def _app(*dependencies) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.get("/x", dependencies=[Depends(dep) for dep in dependencies])(lambda: {"ok": True})
    return TestClient(app)


def _bearer(user_id: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user_id)}"}


def test_the_limit_answers_429_with_retry_after_and_headers() -> None:
    client = _app(rate_limit(2, 60, scope="t-basic"))
    first = client.get("/x")
    assert first.status_code == 200
    assert first.headers["x-ratelimit-limit"] == "2" and first.headers["x-ratelimit-remaining"] == "1"
    assert client.get("/x").headers["x-ratelimit-remaining"] == "0"
    blocked = client.get("/x")
    assert blocked.status_code == 429
    assert blocked.json()["detail"][0]["code"] == "api.rate_limited"
    assert blocked.headers["retry-after"] == blocked.headers["x-ratelimit-reset"]
    assert blocked.headers["x-ratelimit-remaining"] == "0"


def test_a_limit_of_zero_switches_the_route_off() -> None:
    client = _app(rate_limit(0, 60, scope="t-off"))
    assert all(client.get("/x").status_code == 200 for _ in range(5))
    assert "x-ratelimit-limit" not in client.get("/x").headers


def test_without_a_limit_the_default_setting_applies_and_it_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _app(rate_limit(scope="t-default"))
    assert all(client.get("/x").status_code == 200 for _ in range(5))  # rate_limit_default is 0
    monkeypatch.setenv("PACHIFAST_RATE_LIMIT_DEFAULT", "1")
    get_settings.cache_clear()
    assert client.get("/x").status_code == 200
    assert client.get("/x").status_code == 429


def test_a_limit_given_to_the_route_wins_over_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACHIFAST_RATE_LIMIT_DEFAULT", "1")
    get_settings.cache_clear()
    client = _app(rate_limit(3, 60, scope="t-override"))
    assert [client.get("/x").status_code for _ in range(4)] == [200, 200, 200, 429]


def test_the_window_setting_is_the_default_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACHIFAST_RATE_LIMIT_WINDOW_SECONDS", "10")
    get_settings.cache_clear()
    client = _app(rate_limit(1, scope="t-window"))
    client.get("/x")
    # 1_000_000 is the start of a 10 s bucket: 10 s are left, plus the rounding second
    assert client.get("/x").headers["x-ratelimit-reset"] == "11"


def test_user_key_counts_each_user_apart_and_anonymous_requests_by_address() -> None:
    client = _app(rate_limit(1, 60, key="user", scope="t-user"))
    assert client.get("/x", headers=_bearer(USER_A)).status_code == 200
    assert client.get("/x", headers=_bearer(USER_A)).status_code == 429
    assert client.get("/x", headers=_bearer(USER_B)).status_code == 200
    assert client.get("/x").status_code == 200  # anonymous: counted by address
    assert client.get("/x").status_code == 429


def test_a_bad_token_is_counted_by_address() -> None:
    client = _app(rate_limit(1, 60, key="user", scope="t-badtoken"))
    assert client.get("/x", headers={"Authorization": "Bearer nonsense"}).status_code == 200
    assert client.get("/x", headers={"Authorization": "Bearer other-nonsense"}).status_code == 429


def test_a_key_function_picks_the_counter_and_none_falls_back_to_the_address() -> None:
    def by_header(request: Request) -> str | None:
        return request.headers.get("x-tenant")

    client = _app(rate_limit(1, 60, key=by_header, scope="t-fn"))
    assert client.get("/x", headers={"x-tenant": "a"}).status_code == 200
    assert client.get("/x", headers={"x-tenant": "a"}).status_code == 429
    assert client.get("/x", headers={"x-tenant": "b"}).status_code == 200
    assert client.get("/x").status_code == 200
    assert client.get("/x").status_code == 429


def test_an_async_key_function_is_awaited() -> None:
    async def tenant(request: Request) -> str:
        return "same"

    client = _app(rate_limit(1, 60, key=tenant, scope="t-async"))
    assert client.get("/x").status_code == 200
    assert client.get("/x").status_code == 429


def test_the_same_scope_shares_one_budget_and_different_scopes_do_not() -> None:
    shared = _app(rate_limit(1, 60, scope="t-shared"), rate_limit(5, 60, scope="t-shared-b"))
    assert shared.get("/x").status_code == 200
    assert shared.get("/x").status_code == 429
    other = _app(rate_limit(1, 60, scope="t-other"))
    assert other.get("/x").status_code == 200


def test_the_counter_is_shared_by_two_routes_with_the_same_scope() -> None:
    app = FastAPI()
    install_error_handlers(app)
    limiter = rate_limit(1, 60, scope="t-two-routes")
    app.get("/a", dependencies=[Depends(limiter)])(lambda: 1)
    app.get("/b", dependencies=[Depends(limiter)])(lambda: 2)
    client = TestClient(app)
    assert client.get("/a").status_code == 200
    assert client.get("/b").status_code == 429


def test_the_rate_limit_section_of_the_yaml_maps_to_the_settings(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from pachifast.config.sections import field_locations

    locations = field_locations()
    assert locations["rate_limit_default"] == "rate_limit.default"
    assert locations["rate_limit_window_seconds"] == "rate_limit.window_seconds"


def test_default_limits_the_route_when_nobody_sets_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PACHIFAST_RATE_LIMIT_DEFAULT", raising=False)
    get_settings.cache_clear()
    client = _app(rate_limit(default=2, scope="t-app-default"))
    assert [client.get("/x").status_code for _ in range(3)] == [200, 200, 429]


def test_the_setting_wins_over_default_and_zero_switches_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PACHIFAST_RATE_LIMIT_DEFAULT", "0")
    get_settings.cache_clear()
    off = _app(rate_limit(default=1, scope="t-default-off"))
    assert all(off.get("/x").status_code == 200 for _ in range(3))
    monkeypatch.setenv("PACHIFAST_RATE_LIMIT_DEFAULT", "3")
    get_settings.cache_clear()
    three = _app(rate_limit(default=1, scope="t-default-three"))
    assert [three.get("/x").status_code for _ in range(4)] == [200, 200, 200, 429]


def test_a_limit_given_to_the_route_wins_over_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PACHIFAST_RATE_LIMIT_DEFAULT", raising=False)
    get_settings.cache_clear()
    client = _app(rate_limit(1, 60, default=5, scope="t-limit-over-default"))
    assert [client.get("/x").status_code for _ in range(2)] == [200, 429]
