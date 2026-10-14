"""A request id on every request: the ``X-Request-ID`` response header and a ``request_id`` field in every log line.

A client or a reverse proxy may send its own id (letters, digits, ``-``, ``_``, ``.``, up to 64 characters); anything
else is replaced by a fresh one. The id lives in a context variable, so the log lines written while the request is
handled (also in the tasks it starts) carry it without passing it around.
"""

from __future__ import annotations

import logging
import re
import uuid
from contextvars import ContextVar

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HEADER = "X-Request-ID"
_VALID = re.compile(r"[A-Za-z0-9._-]{1,64}")

request_id_var: ContextVar[str | None] = ContextVar("pachifast_request_id", default=None)


def get_request_id() -> str | None:
    """The id of the request being handled, or ``None`` outside a request."""
    return request_id_var.get()


class RequestIdFilter(logging.Filter):
    """Copy the id of the current request into each log record as ``record.request_id`` (``-`` outside a request)."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Always keep the record; only add the field."""
        record.request_id = request_id_var.get() or "-"
        return True


class RequestIdMiddleware:
    """ASGI middleware: take the id of the request (or make one), expose it in the logs and the response header."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Set the context variable for the request and add the header to the response."""
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get(HEADER, "")
        request_id = incoming if _VALID.fullmatch(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)
