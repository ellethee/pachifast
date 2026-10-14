"""Endpoints to try the HTTP client (use-alova): slow, errors with a code, 401 once, blob, system info."""

import asyncio
import hashlib
import io
import time
import uuid
from typing import Annotated

from fastapi import Header, Query
from fastapi.responses import Response, StreamingResponse

from pachifast.auth.dependencies import CurrentUserDep
from pachifast.auth.schemas import UserRead
from pachifast.i18n import api_error
from pachifast.routing.router_factory import get_router

router = get_router(name="demo/lab")

_STARTED = time.monotonic()


class _AuthOnceState:
    """Per user: whether the next request is still to be refused (``armed``) and the tokens already refused."""

    def __init__(self) -> None:
        self.armed = True
        self.expired: set[str] = set()


_auth_once: dict[uuid.UUID, _AuthOnceState] = {}


def _token_key(authorization: str | None) -> str:
    """What identifies the presented token (its hash, never the token)."""
    return hashlib.sha256((authorization or "").encode()).hexdigest()


@router.get("/slow", summary="Answer after `delay` seconds (spinner / overlay)")
async def slow(delay: float = Query(2.0, ge=0, le=30)):
    await asyncio.sleep(delay)
    return {"waited": delay}


@router.get("/error/{http_status}", summary="Fail with the given 4xx/5xx and a stable code")
async def fail(http_status: int):
    """``demo.simulated_error`` with ``{status}``: the frontend translates it from the code."""
    if not 400 <= http_status <= 599:
        http_status = 400
    raise api_error(http_status, "demo.simulated_error", loc=("path", "http_status"), status=http_status)


@router.get("/auth-once", summary="401 once per arming, for the 401 -> refresh -> retry flow")
async def auth_once(user: Annotated[UserRead, CurrentUserDep], authorization: Annotated[str | None, Header()] = None):
    """State per user. Armed (the initial state): the first request refuses the token it carries (that token
    gets 401 from then on) and disarms. Any other valid token of the same user gets 200, so the retry with the
    refreshed token succeeds and the following calls too, until ``POST /auth-once/reset`` arms it again."""
    state = _auth_once.setdefault(user.id, _AuthOnceState())
    key = _token_key(authorization)
    if key in state.expired:
        raise api_error(401, "demo.token_expired_once", loc=("header", "Authorization"))
    if state.armed:
        state.armed = False
        state.expired.add(key)
        raise api_error(401, "demo.token_expired_once", loc=("header", "Authorization"))
    return {"ok": True}


@router.post("/auth-once/reset", summary="Arm /auth-once again for the current user")
async def auth_once_reset(user: Annotated[UserRead, CurrentUserDep]):
    """Forget the refused tokens and arm: the next ``GET /auth-once`` answers 401 again."""
    if len(_auth_once) > 1000:
        _auth_once.clear()
    _auth_once[user.id] = _AuthOnceState()
    return {"armed": True}


@router.get("/blob", summary="A file generated on the fly (csv or png)")
async def blob(kind: str = Query("csv", pattern="^(csv|png)$")):
    if kind == "png":
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360f8cfc0f01f00050001ff"
            "89993d1d0000000049454e44ae426082"
        )
        return Response(
            png, media_type="image/png", headers={"Content-Disposition": 'attachment; filename="pixel.png"'}
        )
    csv = "id;name\n" + "\n".join(f"{n};item {n}" for n in range(1, 101)) + "\n"
    return StreamingResponse(
        io.BytesIO(csv.encode()),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="items.csv"'},
    )


@router.get("/system", summary="System info for fastquasar-dashboard-widgets")
async def system():
    """``{version, environment, status, uptime}``."""
    from importlib.metadata import version as installed_version

    from pachifast.config import get_settings

    seconds = int(time.monotonic() - _STARTED)
    settings = get_settings()
    return {
        "version": installed_version("pachifast"),
        "environment": "debug" if settings.debug else "production",
        "status": "ok",
        "uptime": f"{seconds // 3600}h {seconds % 3600 // 60}m {seconds % 60}s",
    }
