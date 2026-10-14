"""ASGI middleware that sets the locale of a request from its ``Accept-Language`` header."""

from starlette.types import ASGIApp, Receive, Scope, Send

from pachifast.i18n.translator import negotiate_locale, reset_locale, set_locale


class LocaleMiddleware:
    """Pure ASGI middleware (no per-request task, no body buffering): one header lookup per request.

    Without a supported language in the header the configured ``default_locale`` stays in force.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        header = next((value for key, value in scope["headers"] if key == b"accept-language"), None)
        locale = negotiate_locale(header.decode("latin-1") if header else None)
        if locale is None:
            await self.app(scope, receive, send)
            return
        token = set_locale(locale)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_locale(token)
