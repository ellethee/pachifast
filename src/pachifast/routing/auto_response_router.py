from logging import getLogger

from .permissioned_router import PermissionedRouter

log = getLogger(__name__)


class AutoResponseRouter(PermissionedRouter):
    def __init__(self, default_response_model=None, *args, **kwargs):
        self._default_response_model = default_response_model
        super().__init__(*args, **kwargs)

    def add_api_route(self, path, endpoint, *, response_model=None, **kwargs):
        if self._default_response_model and response_model:
            response_model = self._default_response_model[response_model]
        return super().add_api_route(path, endpoint, response_model=response_model, **kwargs)
