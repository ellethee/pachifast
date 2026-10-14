import json
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from pachifast.config import get_settings
from pachifast.schemas.responses import StandardResponse


def _resolve_request_database(request: Request) -> list[str] | None:
    """Database "business" della richiesta, con fallback sul database core/auth.

    request.state.database (popolata da add_request_database) esclude di proposito il
    database auth/core, che gira sull'auth-check di quasi ogni richiesta e comparirebbe come
    rumore. Se nessun database business è stato usato (endpoint che leggono/scrivono solo sul
    database core), si torna a mostrare quello.
    """
    business = getattr(request.state, "database", None)
    if business:
        return business
    core = getattr(request.state, "core_database", None)
    return [core] if core else None


class EnvelopeRoute(APIRoute):
    """Avvolge ogni risposta JSON in StandardResponse (success/message/database/data).

    Il wrapping avviene DOPO che l'endpoint ha già serializzato il proprio response_model
    naturale: non richiede che i singoli endpoint dichiarino StandardResponse[T] (pattern
    fragile, vedi apps/portal/files.py). Risposte non-JSON (download, blob, streaming)
    passano invariate. Una route dichiarata con skip_envelope=True (vedi
    PermissionedRouter.add_api_route) salta del tutto il wrapping — usata per endpoint
    che devono rispettare un contratto di risposta esterno (es. OAuth2 password grant).

    Con ``envelope_enabled=false`` nessuna risposta viene avvolta: il frontend deve
    essere configurato allo stesso modo (``expose_response_database`` non ha più effetto).

    Contratto (envelope attivo): l'envelope esiste solo sulle risposte 2xx e ha sempre
    ``success: true``; un errore è sempre una risposta non-2xx con ``{"detail": [...]}``
    (mai un 2xx con ``success: false``). Una risposta JSON non-2xx restituita direttamente
    da un endpoint (``JSONResponse(status_code=400)``) passa quindi invariata, senza envelope.
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
