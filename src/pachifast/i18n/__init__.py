"""Localization of the API error messages: stable codes, catalogs and the request locale."""

from pachifast.i18n.catalogs import CATALOGS, configure_catalogs, supported_locales
from pachifast.i18n.translator import (
    MessageError,
    api_error,
    current_locale,
    detail_from_error,
    error_item,
    lookup,
    negotiate_locale,
    t,
    translate,
)

__all__ = [
    "CATALOGS",
    "MessageError",
    "api_error",
    "configure_catalogs",
    "current_locale",
    "detail_from_error",
    "error_item",
    "lookup",
    "negotiate_locale",
    "supported_locales",
    "t",
    "translate",
]
