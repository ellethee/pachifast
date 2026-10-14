"""Uniform API error format.

Every error response has the same body: ``{"detail": [{"loc": [...], "msg": "...", "type": "..."}]}``; the errors
raised by pachifast itself also carry a stable ``code`` (``"auth.invalid_credentials"``) whose ``msg`` is localized
(see :mod:`pachifast.i18n`).
FastAPI validation errors (422) and the errors raised by the framework already use it; the handlers installed
by :func:`install_error_handlers` bring the rest to the same shape: ``HTTPException`` with a string detail
(for example 404 ``Not Found`` or 405), an invalid ``orderBy``, a database conflict (409), a refused value (400)
and unexpected server errors.
"""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

GENERIC_ERROR_TYPE = "http_error"
SERVER_ERROR_TYPE = "server_error"


class InvalidOrderByError(ValueError):
    """``orderBy`` names a field the model does not have: a 422 with the code ``api.invalid_order_by``."""

    def __init__(self, field: str) -> None:
        super().__init__(f"Invalid field '{field}' for ordering.")
        self.field = field


def error_detail(
    msg: str, *, loc: tuple[str | int, ...] | list[str | int] = (), type: str = GENERIC_ERROR_TYPE
) -> list[dict[str, Any]]:
    """Build the uniform ``detail`` value: a list with one ``{loc, msg, type}`` item."""
    return [{"loc": list(loc), "msg": msg, "type": type}]


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Keep list details as they are, wrap any other detail (usually a string) in the uniform list."""
    detail = exc.detail if isinstance(exc.detail, list) else error_detail(str(exc.detail))
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


async def _invalid_order_by_handler(request: Request, exc: InvalidOrderByError) -> JSONResponse:
    """422 pointing at the ``orderBy`` query parameter."""
    from pachifast.i18n import api_error

    error = api_error(422, "api.invalid_order_by", loc=("query", "orderBy"), field=exc.field)
    return JSONResponse({"detail": error.detail}, 422)


async def _integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """409: the change breaks a constraint of the database (duplicate, missing parent row); details stay in the log."""
    from pachifast.i18n import api_error

    log.info("Integrity error on %s %s: %s", request.method, request.url.path, exc.orig)
    return JSONResponse({"detail": api_error(409, "db.conflict", loc=("body",)).detail}, 409)


async def _data_error_handler(request: Request, exc: DataError) -> JSONResponse:
    """400: the database refused a value (too long, out of range); details stay in the log."""
    from pachifast.i18n import api_error

    log.info("Data error on %s %s: %s", request.method, request.url.path, exc.orig)
    return JSONResponse({"detail": api_error(400, "db.invalid_value", loc=("body",)).detail}, 400)


async def _unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """500 with the uniform body; the exception is logged, never sent to the client."""
    log.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": error_detail("Internal Server Error", type=SERVER_ERROR_TYPE)}, 500)


def install_error_handlers(app: FastAPI) -> None:
    """Register the handlers that give every error response the uniform body.

    Note: with ``debug=True`` Starlette answers unexpected errors with its debug traceback page and bypasses
    the 500 handler; ``debug`` is off by default: turn it on only in development.
    """
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(InvalidOrderByError, _invalid_order_by_handler)  # type: ignore[arg-type]
    app.add_exception_handler(IntegrityError, _integrity_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(DataError, _data_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unexpected_error_handler)
