import json
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from pachifast.config import get_settings
from pachifast.schemas.responses import StandardResponse


def _resolve_request_database(request: Request) -> list[str] | None:
    """The request's "business" database, falling back to the core/auth database.

    request.state.database (filled by add_request_database) deliberately excludes the
    auth/core database, which runs on the auth check of almost every request and would show up as
    noise. If no business database was used (endpoints that only read/write the
    core database), that one is shown instead.
    """
    business = getattr(request.state, "database", None)
    if business:
        return business
    core = getattr(request.state, "core_database", None)
    return [core] if core else None


class EnvelopeRoute(APIRoute):
    """Wrap every JSON response in StandardResponse (success/message/database/data).

    Wrapping happens AFTER the endpoint has already serialized its own natural
    response_model: endpoints do not need to declare StandardResponse[T] (a
    fragile pattern). Non-JSON responses (downloads, blobs, streaming)
    pass through unchanged. A route declared with skip_envelope=True (see
    PermissionedRouter.add_api_route) skips the wrapping entirely, used for endpoints
    that must honour an external response contract (e.g. the OAuth2 password grant).

    With ``envelope_enabled=false`` no response is wrapped: the frontend must be
    configured the same way (``expose_response_database`` then has no effect).

    Contract (envelope enabled): the envelope exists only on 2xx responses and always has
    ``success: true``; an error is always a non-2xx response with ``{"detail": [...]}``
    (never a 2xx with ``success: false``). A non-2xx JSON response returned directly
    by an endpoint (``JSONResponse(status_code=400)``) therefore passes through unchanged, without an envelope.
    """

    def get_route_handler(self) -> Callable:
        original_route_handler = super().get_route_handler()
        skip_envelope = bool((self.openapi_extra or {}).get("x-skip-envelope"))
        if skip_envelope:
            return original_route_handler

        async def custom_route_handler(request: Request) -> Response:
            response = await original_route_handler(request)
            if not get_settings().envelope_enabled:
                return response
            content_type = response.headers.get("content-type", "")
            if not content_type.startswith("application/json") or not 200 <= response.status_code < 300:
                return response
            database = _resolve_request_database(request) if get_settings().expose_response_database else None
            body = json.loads(response.body)
            envelope = StandardResponse(data=body, database=database)
            content = jsonable_encoder(envelope, by_alias=True)
            if database is None:
                content.pop("database", None)
            headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
            return JSONResponse(
                content=content,
                status_code=response.status_code,
                headers=headers,
            )

        return custom_route_handler
