"""MFA flows: TOTP and backup codes, challenges, password-guarded routes, the sender adapter and the pending token."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

os.environ.setdefault("PACHIFAST_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnop")

import jwt
import pyotp
import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from pachifast.auth import cruds as auth_cruds
from pachifast.config import get_settings
from pachifast.mfa import MfaSenderAdapter, configure_sender, get_sender
from pachifast.mfa import cruds as mfa_cruds
from pachifast.mfa import sender as sender_module
from pachifast.mfa.models import UserMfaPendingChallenge
from pachifast.mfa.security import (
    MFA_PENDING_TOKEN_TYPE,
    _deliver_otp,
    create_mfa_pending_token,
    generate_otp_code,
    verify_mfa_pending_token,
    verify_totp_code,
)

# The app fixture and the helpers are the ones of the security tests (``tests`` is a package).
from tests.test_security_hardening import (  # noqa: F401
    PASSWORD,
    Env,
    _data,
    _enrol_totp,
    _give_email,
    _login,
    _next_code,
)
from tests.test_security_hardening import (
    env as env,
)


def _verify(client, mfa_token: str, code: str):
    """Second step of the login."""
    return client.post("/api/mfa/verify", json={"mfa_token": mfa_token, "code": code})


def _code_of(response) -> str:
    """The error code of a failed response."""
    return response.json()["detail"][0]["code"]


def _jti(token: str) -> str:
    """The challenge id carried by an MFA pending token."""
    payload = verify_mfa_pending_token(token, get_settings())
    assert payload is not None
    return payload["jti"]


class Recorder(MfaSenderAdapter):
    """A sender that records the codes instead of delivering them (a class attribute is shared by instances)."""

    emails: list[tuple[str, str]] = []
    sms: list[tuple[str, str]] = []
    delivered = threading.Event()

    async def send_email_otp(self, *, user_id, email, otp_code, expires_in_minutes) -> None:
        type(self).emails.append((email, otp_code))
        type(self).delivered.set()

    async def send_sms_otp(self, *, user_id, phone_number, otp_code, expires_in_minutes) -> None:
        type(self).sms.append((phone_number, otp_code))
        type(self).delivered.set()


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> type[Recorder]:
    """Register :class:`Recorder` as the sender with the OTP code fixed to ``123456``."""
    Recorder.emails, Recorder.sms = [], []
    Recorder.delivered = threading.Event()
    monkeypatch.setattr("pachifast.mfa.sender._sender_class", Recorder)
    monkeypatch.setattr("pachifast.mfa.security.generate_otp_code", lambda length=6: "123456")
    return Recorder


def _wait_for_delivery(recorder: type[Recorder]) -> None:
    """The delivery runs as a background task of the app loop: wait for it without sleeping."""
    assert recorder.delivered.wait(timeout=10), "the OTP was not delivered"
    recorder.delivered.clear()


# --- status, TOTP setup and confirmation ---------------------------------------------------------------


def test_status_follows_the_enrolment(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    assert _data(client.get("/api/mfa/status", headers=headers)) == {"enabled": False, "method": None}
    assert client.get("/api/mfa/status").status_code == 401

    setup = _data(client.post("/api/mfa/totp/setup", headers=headers))
    assert setup["otpauth_uri"].startswith("otpauth://totp/") and len(setup["backup_codes"]) == 8
    assert _data(client.get("/api/mfa/status", headers=headers))["enabled"] is False  # not confirmed yet

    code = pyotp.TOTP(setup["secret"]).now()
    assert client.post("/api/mfa/totp/confirm", json={"code": code}, headers=headers).status_code == 200
    assert _data(client.get("/api/mfa/status", headers=headers)) == {"enabled": True, "method": "totp"}


def test_totp_confirm_needs_a_setup_and_a_valid_unused_code(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    nothing = client.post("/api/mfa/totp/confirm", json={"code": "123456"}, headers=headers)
    assert nothing.status_code == 400 and _code_of(nothing) == "mfa.no_totp_setup"

    secret = _data(client.post("/api/mfa/totp/setup", headers=headers))["secret"]
    wrong = client.post("/api/mfa/totp/confirm", json={"code": "000000"}, headers=headers)
    assert wrong.status_code == 400 and _code_of(wrong) == "mfa.totp_invalid"

    code = pyotp.TOTP(secret).now()
    assert client.post("/api/mfa/totp/confirm", json={"code": code}, headers=headers).status_code == 200
    # the step of the confirmation is spent: the same code cannot be used again for the first login
    token = _login(client, "ann").json()["mfa_token"]
    assert _verify(client, token, code).status_code == 401


def test_the_totp_helpers_accept_one_period_of_drift_and_no_more() -> None:
    secret = pyotp.random_base32()
    import time

    now = time.time()
    assert verify_totp_code(secret, pyotp.TOTP(secret).at(now))
    assert verify_totp_code(secret, pyotp.TOTP(secret).at(now + 30))
    assert not verify_totp_code(secret, pyotp.TOTP(secret).at(now + 300))
    assert not verify_totp_code(secret, "abcdef")


def test_email_setup_without_an_address_is_refused(env: Env) -> None:
    client = env.build()
    refused = client.post("/api/mfa/email/setup", headers=env.token("ann"))
    assert refused.status_code == 400 and _code_of(refused) == "mfa.no_email"


# --- login in two steps ---------------------------------------------------------------------------------


def test_the_two_step_login_returns_tokens_that_work(env: Env) -> None:
    client = env.build()
    secret, _ = _enrol_totp(client, env.token("ann"))
    first = _login(client, "ann")
    assert first.json()["mfa_required"] is True and "access_token" not in first.json()

    done = _verify(client, first.json()["mfa_token"], _next_code(secret))
    assert done.status_code == 200, done.text
    body = done.json()
    assert body["token_type"] == "bearer"
    me = client.get("/api/auth/user", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200

    # the challenge is spent: the same mfa_token cannot be used for another code
    again = _verify(client, first.json()["mfa_token"], _next_code(secret, 2))
    assert again.status_code == 401 and _code_of(again) == "mfa.challenge_not_found"


def test_a_totp_code_cannot_be_replayed_on_the_same_or_another_challenge(env: Env) -> None:
    client = env.build()
    secret, _ = _enrol_totp(client, env.token("ann"))
    code = _next_code(secret)
    first = _login(client, "ann").json()["mfa_token"]
    assert _verify(client, first, code).status_code == 200

    second = _login(client, "ann").json()["mfa_token"]
    replay = _verify(client, second, code)
    assert replay.status_code == 401 and _code_of(replay) == "mfa.code_invalid"


def test_a_backup_code_is_accepted_once_in_any_case_and_with_spaces(env: Env) -> None:
    client = env.build()
    _, backups = _enrol_totp(client, env.token("ann"))
    token = _login(client, "ann").json()["mfa_token"]
    assert _verify(client, token, f"  {backups[0].lower()} ").status_code == 200

    token = _login(client, "ann").json()["mfa_token"]
    spent = _verify(client, token, backups[0])
    assert spent.status_code == 401 and _code_of(spent) == "mfa.code_invalid"
    assert _verify(client, token, backups[1]).status_code == 200


def test_a_malformed_or_foreign_mfa_token_is_refused(env: Env) -> None:
    client = env.build()
    _enrol_totp(client, env.token("ann"))
    garbage = _verify(client, "not-a-jwt", "000000")
    assert garbage.status_code == 401 and _code_of(garbage) == "mfa.token_invalid"

    settings = get_settings()
    access = env.token("ann")["Authorization"].split()[1]  # a real access token is not a pending token
    assert _code_of(_verify(client, access, "000000")) == "mfa.token_invalid"

    bad_sub = jwt.encode(
        {
            "sub": "not-a-uuid",
            "type": MFA_PENDING_TOKEN_TYPE,
            "jti": "abc",
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    assert _code_of(_verify(client, bad_sub, "000000")) == "mfa.token_invalid"


def test_an_expired_mfa_token_is_refused_at_verify(env: Env) -> None:
    client = env.build()
    _enrol_totp(client, env.token("ann"))
    token = _login(client, "ann").json()["mfa_token"]
    expired = jwt.encode(
        {
            "sub": str(env.ids["ann"]),
            "type": MFA_PENDING_TOKEN_TYPE,
            "jti": _jti(token),
            "exp": datetime.now(UTC) - timedelta(seconds=1),
        },
        get_settings().SECRET_KEY,
        algorithm="HS256",
    )
    refused = _verify(client, expired, "000000")
    assert refused.status_code == 401 and _code_of(refused) == "mfa.token_invalid"


def test_a_valid_token_without_a_challenge_row_is_refused(env: Env) -> None:
    client = env.build()
    secret, _ = _enrol_totp(client, env.token("ann"))
    orphan, _ = create_mfa_pending_token(env.ids["ann"], get_settings())  # type: ignore[arg-type]
    refused = _verify(client, orphan, _next_code(secret))
    assert refused.status_code == 401 and _code_of(refused) == "mfa.challenge_not_found"


def test_too_many_attempts_on_one_challenge_answer_429(env: Env) -> None:
    client = env.build()
    secret, _ = _enrol_totp(client, env.token("ann"))
    token = _login(client, "ann").json()["mfa_token"]
    jti = _jti(token)

    async def spend(session: AsyncSession) -> None:
        await session.execute(
            update(UserMfaPendingChallenge)
            .where(UserMfaPendingChallenge.jti == jti)
            .values(attempts=get_settings().MFA_MAX_ATTEMPTS)
        )
        await session.commit()

    env.run(spend)
    refused = _verify(client, token, _next_code(secret))  # even the right code: the guesses are used up
    assert refused.status_code == 429 and _code_of(refused) == "mfa.too_many_attempts"
    # a fresh login gives a fresh challenge
    assert _verify(client, _login(client, "ann").json()["mfa_token"], _next_code(secret)).status_code == 200


def test_the_mfa_attempts_limit_is_a_setting(env: Env) -> None:
    client = env.build(mfa_max_attempts="2")
    secret, _ = _enrol_totp(client, env.token("ann"))
    token = _login(client, "ann").json()["mfa_token"]
    assert _verify(client, token, "000000").status_code == 401
    assert _verify(client, token, "000001").status_code == 401
    third = _verify(client, token, _next_code(secret))
    assert third.status_code == 429 and _code_of(third) == "mfa.too_many_attempts"


def test_verify_refuses_inactive_users_and_removed_configuration(env: Env) -> None:
    client = env.build()
    secret, _ = _enrol_totp(client, env.token("ann"))

    gone = _login(client, "ann").json()["mfa_token"]
    assert client.post("/api/mfa/disable", json={"password": PASSWORD}, headers=env.token("ann")).status_code == 200
    # the challenge row is still there but the second factor is not
    refused = _verify(client, gone, _next_code(secret))
    assert refused.status_code == 401 and _code_of(refused) == "mfa.not_configured"

    _enrol_totp(client, env.token("ann"))
    token = _login(client, "ann").json()["mfa_token"]

    async def deactivate(session: AsyncSession) -> None:
        user = await auth_cruds.get_user_by_id(session, env.ids["ann"])
        await auth_cruds.update_user(session, user, is_active=False)

    env.run(deactivate)
    inactive = _verify(client, token, "000000")
    assert inactive.status_code == 401 and _code_of(inactive) == "user.inactive"


def test_a_locked_account_cannot_verify(env: Env) -> None:
    client = env.build()
    _enrol_totp(client, env.token("ann"))
    token = _login(client, "ann").json()["mfa_token"]
    limit = get_settings().login_max_attempts
    # the wrong codes of one challenge also count on the account; the last one locks it
    outcomes = [_verify(client, token, f"00000{i}").status_code for i in range(limit)]
    assert outcomes[0] == 401
    locked = _verify(client, token, "999999")
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) >= 1


# --- email and SMS codes: delivery and the second step -----------------------------------------------------


def test_the_email_otp_is_delivered_and_logs_in_once(env: Env, recorder: type[Recorder]) -> None:
    _give_email(env, "ann", "ann@example.org")
    client = env.build()
    headers = env.token("ann")
    assert client.post("/api/mfa/email/setup", headers=headers).status_code == 200
    _wait_for_delivery(recorder)
    assert recorder.emails == [("ann@example.org", "123456")]
    assert client.post("/api/mfa/email/confirm", json={"code": "123456"}, headers=headers).status_code == 200

    token = _login(client, "ann").json()["mfa_token"]
    _wait_for_delivery(recorder)
    assert recorder.emails[-1] == ("ann@example.org", "123456") and len(recorder.emails) == 2
    wrong = _verify(client, token, "000000")
    assert wrong.status_code == 401 and _code_of(wrong) == "mfa.code_invalid"
    assert _verify(client, token, "123456").status_code == 200
    assert _verify(client, token, "123456").status_code == 401  # the challenge is gone


def test_the_sms_otp_is_delivered_at_login_and_a_backup_code_replaces_it(env: Env, recorder: type[Recorder]) -> None:
    client = env.build()
    headers = env.token("ann")
    setup = client.post("/api/mfa/sms/setup", json={"phone_number": "+390000001"}, headers=headers)
    backups = _data(setup)["backup_codes"]
    _wait_for_delivery(recorder)
    assert recorder.sms == [("+390000001", "123456")]
    assert client.post("/api/mfa/sms/confirm", json={"code": "123456"}, headers=headers).status_code == 200

    token = _login(client, "ann").json()["mfa_token"]
    _wait_for_delivery(recorder)
    assert recorder.sms[-1] == ("+390000001", "123456")
    assert _verify(client, token, backups[0]).status_code == 200  # the SMS never arrived: a backup code does


def test_confirming_with_an_expired_or_missing_code_is_refused(env: Env, recorder: type[Recorder]) -> None:
    _give_email(env, "ann", "ann@example.org")
    client = env.build()
    headers = env.token("ann")
    assert client.post("/api/mfa/email/confirm", json={"code": "123456"}, headers=headers).status_code == 400
    client.post("/api/mfa/email/setup", headers=headers)
    _wait_for_delivery(recorder)

    async def expire(session: AsyncSession) -> None:
        await session.execute(
            update(UserMfaPendingChallenge).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
        )
        await session.commit()

    env.run(expire)
    late = client.post("/api/mfa/email/confirm", json={"code": "123456"}, headers=headers)
    assert late.status_code == 400 and _code_of(late) == "mfa.no_email_setup"
    # an SMS confirmation does not fit an email setup either
    other = client.post("/api/mfa/sms/confirm", json={"code": "123456"}, headers=headers)
    assert other.status_code == 400 and _code_of(other) == "mfa.no_sms_setup"


def test_too_many_wrong_enrolment_codes_answer_429(env: Env, recorder: type[Recorder]) -> None:
    _give_email(env, "ann", "ann@example.org")
    client = env.build()
    headers = env.token("ann")
    client.post("/api/mfa/email/setup", headers=headers)
    _wait_for_delivery(recorder)
    limit = get_settings().MFA_MAX_ATTEMPTS
    for _ in range(limit):
        assert client.post("/api/mfa/email/confirm", json={"code": "000000"}, headers=headers).status_code == 400
    refused = client.post("/api/mfa/email/confirm", json={"code": "123456"}, headers=headers)
    assert refused.status_code == 429 and _code_of(refused) == "mfa.too_many_attempts"


def test_a_failing_sender_does_not_break_the_login(env: Env, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    class Broken(MfaSenderAdapter):
        async def send_email_otp(self, **kwargs) -> None:
            raise RuntimeError("smtp is down")

    monkeypatch.setattr("pachifast.mfa.sender._sender_class", Broken)
    monkeypatch.setattr("pachifast.mfa.security.generate_otp_code", lambda length=6: "123456")
    _give_email(env, "ann", "ann@example.org")
    client = env.build()
    headers = env.token("ann")
    assert client.post("/api/mfa/email/setup", headers=headers).status_code == 200
    assert client.post("/api/mfa/email/confirm", json={"code": "123456"}, headers=headers).status_code == 200
    assert _login(client, "ann").json()["mfa_required"] is True


# --- disable and regenerate ----------------------------------------------------------------------------------


def test_disable_needs_the_right_password(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    _enrol_totp(client, headers)
    wrong = client.post("/api/mfa/disable", json={"password": "not-the-password-x"}, headers=headers)
    assert wrong.status_code == 400 and _code_of(wrong) == "mfa.password_incorrect"
    assert _data(client.get("/api/mfa/status", headers=headers))["enabled"] is True

    assert _data(client.post("/api/mfa/disable", json={"password": PASSWORD}, headers=headers)) == {"ok": True}
    assert _data(client.get("/api/mfa/status", headers=headers))["enabled"] is False
    assert "mfa_required" not in _login(client, "ann").json()
    assert client.post("/api/mfa/disable", json={"password": PASSWORD}).status_code == 401  # needs the access token


def test_regenerate_backup_codes_replaces_the_old_ones(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    _, old = _enrol_totp(client, headers)

    wrong = client.post("/api/mfa/backup-codes/regenerate", json={"password": "not-the-password-x"}, headers=headers)
    assert wrong.status_code == 400 and _code_of(wrong) == "mfa.password_incorrect"

    new = _data(client.post("/api/mfa/backup-codes/regenerate", json={"password": PASSWORD}, headers=headers))
    assert len(new["backup_codes"]) == 8 and not set(new["backup_codes"]) & set(old)

    token = _login(client, "ann").json()["mfa_token"]
    assert _verify(client, token, old[0]).status_code == 401
    assert _verify(client, token, new["backup_codes"][0]).status_code == 200


def test_regenerate_needs_an_active_second_factor(env: Env) -> None:
    client = env.build()
    headers = env.token("ann")
    nothing = client.post("/api/mfa/backup-codes/regenerate", json={"password": PASSWORD}, headers=headers)
    assert nothing.status_code == 400 and _code_of(nothing) == "mfa.not_active"
    client.post("/api/mfa/totp/setup", headers=headers)  # set up but not confirmed
    pending = client.post("/api/mfa/backup-codes/regenerate", json={"password": PASSWORD}, headers=headers)
    assert pending.status_code == 400 and _code_of(pending) == "mfa.not_active"


@pytest.mark.parametrize("route", ["/api/mfa/disable", "/api/mfa/backup-codes/regenerate"])
def test_a_locked_account_cannot_disable_or_regenerate(env: Env, route: str) -> None:
    client = env.build()
    headers = env.token("ann")
    _enrol_totp(client, headers)

    async def lock(session: AsyncSession) -> None:
        from pachifast.auth.security import utcnow

        user = await auth_cruds.get_user_by_id(session, env.ids["ann"])
        user.locked_until = utcnow() + timedelta(minutes=10)
        await session.commit()

    env.run(lock)
    for password in (PASSWORD, "not-the-password-x"):
        response = client.post(route, json={"password": password}, headers=headers)
        assert response.status_code == 429 and _code_of(response) == "mfa.too_many_attempts"
        assert 590 <= int(response.headers["Retry-After"]) <= 600
    assert _data(client.get("/api/mfa/status", headers=headers))["enabled"] is True  # nothing changed


# --- the sender ------------------------------------------------------------------------------------------------


def test_configure_sender_registers_the_class_and_get_sender_builds_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sender_module, "_sender_class", None)
    assert type(get_sender()) is MfaSenderAdapter

    class Mine(MfaSenderAdapter):
        pass

    configure_sender(Mine)
    assert type(get_sender()) is Mine
    assert get_sender() is not get_sender()  # one instance per call


def test_the_default_adapter_only_logs_a_warning(caplog: pytest.LogCaptureFixture, monkeypatch) -> None:
    monkeypatch.setattr(sender_module, "_sender_class", None)
    sender = get_sender()
    with caplog.at_level(logging.WARNING, logger="pachifast.mfa.sender"):
        asyncio.run(sender.send_email_otp(user_id=uuid.uuid4(), email="a@b.c", otp_code="123456", expires_in_minutes=5))
        asyncio.run(
            sender.send_sms_otp(user_id=uuid.uuid4(), phone_number="+39", otp_code="123456", expires_in_minutes=5)
        )
    messages = [r.getMessage() for r in caplog.records]
    assert any("send_email_otp" in m for m in messages) and any("send_sms_otp" in m for m in messages)
    assert not any("123456" in m for m in messages)  # the code is never written to the log


def test_deliver_otp_routes_by_method_and_swallows_errors(caplog: pytest.LogCaptureFixture) -> None:
    class Cfg:
        def __init__(self, method: str, phone_number: str | None = None) -> None:
            self.method, self.phone_number = method, phone_number

    class Sink:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        async def send_email_otp(self, **kw) -> None:
            self.calls.append(("email", kw))

        async def send_sms_otp(self, **kw) -> None:
            self.calls.append(("sms", kw))

    user_id = uuid.uuid4()
    sink = Sink()
    asyncio.run(_deliver_otp(sink, user_id, "a@b.c", Cfg("email"), "111111", 7))  # type: ignore[arg-type]
    asyncio.run(_deliver_otp(sink, user_id, "a@b.c", Cfg("sms", "+39123"), "222222", 7))  # type: ignore[arg-type]
    asyncio.run(_deliver_otp(sink, user_id, "a@b.c", Cfg("sms", None), "333333", 7))  # type: ignore[arg-type]
    asyncio.run(_deliver_otp(sink, user_id, "a@b.c", Cfg("totp"), "444444", 7))  # type: ignore[arg-type]
    assert sink.calls == [
        ("email", {"user_id": user_id, "email": "a@b.c", "otp_code": "111111", "expires_in_minutes": 7}),
        ("sms", {"user_id": user_id, "phone_number": "+39123", "otp_code": "222222", "expires_in_minutes": 7}),
    ]

    class Boom:
        async def send_email_otp(self, **kw) -> None:
            raise RuntimeError("down")

    with caplog.at_level(logging.ERROR, logger="pachifast.mfa.security"):
        asyncio.run(_deliver_otp(Boom(), user_id, "a@b.c", Cfg("email"), "555555", 7))  # type: ignore[arg-type]
    assert any("delivery of the one-time code failed" in r.getMessage() for r in caplog.records)


def test_generate_otp_code_is_numeric_with_the_requested_length() -> None:
    assert len(generate_otp_code()) == 6 and generate_otp_code(8).isdigit() and len(generate_otp_code(8)) == 8


# --- the pending token --------------------------------------------------------------------------------------------


def test_the_pending_token_roundtrips_and_carries_a_jti(env: Env) -> None:
    settings = get_settings()
    user_id = uuid.uuid4()
    token, jti = create_mfa_pending_token(user_id, settings)
    payload = verify_mfa_pending_token(token, settings)
    assert payload is not None and payload["sub"] == str(user_id) and payload["jti"] == jti
    assert payload["type"] == MFA_PENDING_TOKEN_TYPE


def _encode(settings, **overrides) -> str:
    claims = {
        "sub": str(uuid.uuid4()),
        "type": MFA_PENDING_TOKEN_TYPE,
        "jti": "abc",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, settings.SECRET_KEY, algorithm="HS256")


def test_the_pending_token_is_refused_when_expired_of_the_wrong_type_or_incomplete() -> None:
    settings = get_settings()
    assert verify_mfa_pending_token(_encode(settings), settings) is not None
    assert verify_mfa_pending_token(_encode(settings, exp=datetime.now(UTC) - timedelta(seconds=5)), settings) is None
    assert verify_mfa_pending_token(_encode(settings, type="access"), settings) is None
    assert verify_mfa_pending_token(_encode(settings, type=None), settings) is None
    assert verify_mfa_pending_token(_encode(settings, jti=None), settings) is None
    assert verify_mfa_pending_token(_encode(settings, sub=None), settings) is None
    assert verify_mfa_pending_token(_encode(settings, exp=None), settings) is None
    assert verify_mfa_pending_token("garbage", settings) is None
    forged = jwt.encode(
        {"sub": "x", "type": MFA_PENDING_TOKEN_TYPE, "jti": "a", "exp": 9999999999},
        "other-secret-key-0123456789-abcdefghijklmnop",
        "HS256",
    )
    assert verify_mfa_pending_token(forged, settings) is None


def test_the_pending_token_lifetime_follows_the_otp_expiry_setting(env: Env) -> None:
    client = env.build(mfa_otp_expire_minutes="1")
    _enrol_totp(client, env.token("ann"))
    token = _login(client, "ann").json()["mfa_token"]
    payload = verify_mfa_pending_token(token, get_settings())
    assert payload is not None
    assert 0 < payload["exp"] - datetime.now(UTC).timestamp() <= 60


# --- the data layer, directly -----------------------------------------------------------------------------------------


def test_cruds_cover_the_rest_of_the_challenge_and_config_lifecycle(env: Env) -> None:
    async def scenario(session: AsyncSession) -> None:
        user_id = env.ids["ann"]
        assert await mfa_cruds.get_mfa_config(session, user_id) is None  # type: ignore[arg-type]
        assert await mfa_cruds.enable_mfa_config(session, user_id) is None  # type: ignore[arg-type]
        assert await mfa_cruds.update_backup_codes(session, user_id, ["x"]) is None  # type: ignore[arg-type]
        assert await mfa_cruds.delete_mfa_config(session, user_id) is False  # type: ignore[arg-type]

        cfg = await mfa_cruds.create_or_update_mfa_config(
            session,
            user_id=user_id,
            method="totp",
            totp_secret="S",
            backup_codes_hashed=["a"],  # type: ignore[arg-type]
        )
        assert cfg.is_enabled is False
        again = await mfa_cruds.create_or_update_mfa_config(
            session,
            user_id=user_id,  # type: ignore[arg-type]
            method="sms",
            backup_codes_hashed=["b"],
            phone_number="+39",
        )
        assert again.id == cfg.id and again.method == "sms" and again.totp_secret is None
        enabled = await mfa_cruds.enable_mfa_config(session, user_id)  # type: ignore[arg-type]
        assert enabled is not None and enabled.is_enabled is True
        updated = await mfa_cruds.update_backup_codes(session, user_id, ["c", "d"])  # type: ignore[arg-type]
        assert updated is not None and updated.backup_codes == ["c", "d"]

        # a backup code goes once, and only while the list is the one it was checked against
        stale = await mfa_cruds.get_mfa_config(session, user_id)  # type: ignore[arg-type]
        assert stale is not None
        assert await mfa_cruds.remove_used_backup_code(session, stale, 0) is True
        await session.refresh(stale)
        assert stale.backup_codes == ["d"]

        old = datetime.now(UTC) - timedelta(minutes=1)
        soon = datetime.now(UTC) + timedelta(minutes=5)
        gone = await mfa_cruds.create_pending_challenge(
            session,
            user_id=user_id,
            jti="old",
            method="sms",
            otp_hash="h",
            expires_at=old,  # type: ignore[arg-type]
        )
        kept = await mfa_cruds.create_pending_challenge(
            session,
            user_id=user_id,
            jti="new",
            method="sms",
            otp_hash="h",
            expires_at=soon,  # type: ignore[arg-type]
        )
        latest = await mfa_cruds.get_latest_challenge(session, user_id, "sms")  # type: ignore[arg-type]
        assert latest is not None and latest.id == kept.id
        assert await mfa_cruds.get_latest_challenge(session, user_id, "email") is None  # type: ignore[arg-type]
        assert await mfa_cruds.increment_challenge_attempts(session, kept.id) == 1
        assert await mfa_cruds.increment_challenge_attempts(session, kept.id) == 2
        assert await mfa_cruds.cleanup_expired_challenges(session) == 1
        assert await mfa_cruds.get_pending_challenge_by_jti(session, "old") is None
        assert gone.id != kept.id
        assert await mfa_cruds.claim_challenge(session, kept.id) is True
        assert await mfa_cruds.claim_challenge(session, kept.id) is False  # only one request removes it
        await mfa_cruds.delete_pending_challenge(session, kept.id)  # already gone: harmless

        assert await mfa_cruds.claim_totp_step(session, cfg.id, 10) is True
        assert await mfa_cruds.claim_totp_step(session, cfg.id, 10) is False
        assert await mfa_cruds.claim_totp_step(session, cfg.id, 9) is False
        assert await mfa_cruds.claim_totp_step(session, cfg.id, 11) is True
        assert await mfa_cruds.delete_mfa_config(session, user_id) is True  # type: ignore[arg-type]

    env.run(scenario)


def test_the_email_and_sms_setup_wait_between_two_codes(env: Env, recorder: type[Recorder]) -> None:
    _give_email(env, "ann", "ann@example.org")
    client = env.build(mfa_send_cooldown_seconds="600")
    headers = env.token("ann")
    assert client.post("/api/mfa/email/setup", headers=headers).status_code == 200
    again = client.post("/api/mfa/sms/setup", json={"phone_number": "+390000001"}, headers=headers)
    assert again.status_code == 429 and again.json()["detail"][0]["code"] == "auth.rate_limited"
    assert int(again.headers["Retry-After"]) == 600


@pytest.mark.parametrize("number", ["0390000001", "+0390000001", "+39 abc", "+39", "3900000001234567890"])
def test_the_sms_setup_refuses_a_number_that_is_not_international(env: Env, number: str) -> None:
    client = env.build()
    response = client.post("/api/mfa/sms/setup", json={"phone_number": number}, headers=env.token("ann"))
    assert response.status_code == 422


def test_the_sms_setup_normalizes_the_number(env: Env, recorder: type[Recorder]) -> None:
    client = env.build()
    response = client.post("/api/mfa/sms/setup", json={"phone_number": "+39 (000) 000-1234"}, headers=env.token("ann"))
    assert response.status_code == 200
    _wait_for_delivery(recorder)
    assert recorder.sms[0][0] == "+390000001234"
