"""Locale of the current request, message lookup and the exceptions that carry a message code."""

import logging
from collections.abc import Mapping
from contextvars import ContextVar, Token
from typing import Any

from fastapi import HTTPException

from pachifast.i18n.catalogs import CATALOGS, FALLBACK_LOCALE

log = logging.getLogger(__name__)

# Locale chosen for the request being handled (None: use the configured default).
_current_locale: ContextVar[str | None] = ContextVar("pachifast_locale", default=None)

GENERIC_ERROR_TYPE = "value_error"


def default_locale() -> str:
    """The catalog language for the ``default_locale`` setting, or English when there is no catalog for it."""
    from pachifast.config import get_settings

    return _match_catalog(getattr(get_settings(), "default_locale", FALLBACK_LOCALE)) or FALLBACK_LOCALE


def current_locale() -> str:
    """The locale in use: the one negotiated for this request, else the configured default."""
    return _current_locale.get() or default_locale()


def set_locale(locale: str | None) -> Token:
    """Set the locale of the current context (returns the token for :func:`reset_locale`)."""
    return _current_locale.set(locale)


def reset_locale(token: Token) -> None:
    """Restore the locale that was in use before :func:`set_locale`."""
    _current_locale.reset(token)


def _normalize_tag(tag: str) -> str:
    """Compare language tags without caring for case or ``_`` versus ``-``."""
    return tag.strip().lower().replace("_", "-")


def _match_catalog(tag: str) -> str | None:
    """The catalog language that serves ``tag``: the same tag, else its base language (``it-IT`` -> ``it``)."""
    wanted = _normalize_tag(tag)
    by_tag = {_normalize_tag(name): name for name in CATALOGS}
    if wanted in by_tag:
        return by_tag[wanted]
    return by_tag.get(wanted.split("-")[0])


def negotiate_locale(accept_language: str | None) -> str | None:
    """Pick a language that has a catalog from an ``Accept-Language`` header value, ``None`` when none matches.

    Handles quality values (``it;q=0.8``) and regional tags (``it-IT`` is served by ``it`` when there is no ``it-IT``).
    """
    if not accept_language:
        return None
    candidates: list[tuple[float, int, str]] = []
    for position, part in enumerate(accept_language.split(",")):
        tag, _, params = part.strip().partition(";")
        quality = 1.0
        if params.strip().startswith("q="):
            try:
                quality = float(params.strip()[2:])
            except ValueError:
                continue
        matched = _match_catalog(tag) if quality > 0 else None
        if matched is not None:
            candidates.append((-quality, position, matched))
    return min(candidates)[2] if candidates else None


def translate(code: str, params: Mapping[str, Any] | None = None, locale: str | None = None) -> str:
    """Text of ``code`` in ``locale`` (default: the current one), falling back to English, then to the code itself."""
    for name in (_match_catalog(locale) if locale else current_locale(), FALLBACK_LOCALE):
        template = CATALOGS.get(name or FALLBACK_LOCALE, {}).get(code)
        if template is not None:
            break
    else:
        log.warning("i18n: no message for code %r", code)
        return code
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError):
        log.warning("i18n: parameters %s do not fit the message %r", sorted(params or {}), code)
        return template


def lookup(code: str, params: Mapping[str, Any] | None = None, locale: str | None = None) -> str | None:
    """Text of ``code`` in ``locale`` (default: the current one) **without** falling back, ``None`` when missing.

    For texts that have a default written in the code (the user settings schema): the default is used when the
    language has no entry.
    """
    name = _match_catalog(locale) if locale else current_locale()
    template = CATALOGS.get(name or "", {}).get(code)
    if template is None:
        return None
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError):
        return template


t = translate


class MessageError(ValueError):
    """A ``ValueError`` with a message code: the text is built in the locale of the moment it is raised.

    The API layer turns it into the uniform error body keeping the code (see :func:`detail_from_error`).
    """

    def __init__(self, code: str, **params: Any) -> None:
        self.code = code
        self.params = params
        super().__init__(translate(code, params))


def error_item(code: str, *, loc: tuple[str | int, ...] | list[str | int] = (), **params: Any) -> dict[str, Any]:
    """One item of the uniform error ``detail``: ``{loc, msg, type, code}`` (``msg`` is localized).

    When the message has variable parts (``{min_length}``) they are also sent as ``params``, so that a client
    translating from the ``code`` can fill its own text.
    """
    item: dict[str, Any] = {"loc": list(loc), "msg": translate(code, params), "type": GENERIC_ERROR_TYPE, "code": code}
    if params:
        item["params"] = params
    return item


def api_error(
    status_code: int,
    code: str,
    *,
    loc: tuple[str | int, ...] | list[str | int] = (),
    headers: dict[str, str] | None = None,
    **params: Any,
) -> HTTPException:
    """Build the ``HTTPException`` of an API error with a stable ``code`` and a localized message.

    Use as ``raise api_error(404, "user.not_found", loc=("path", "user_id"))``.
    """
    return HTTPException(status_code=status_code, detail=[error_item(code, loc=loc, **params)], headers=headers)


def detail_from_error(exc: ValueError, *, loc: tuple[str | int, ...] | list[str | int] = ()) -> list[dict[str, Any]]:
    """Uniform ``detail`` for a ``ValueError``: with its code when it is a :class:`MessageError`."""
    if isinstance(exc, MessageError):
        return [error_item(exc.code, loc=loc, **exc.params)]
    return [{"loc": list(loc), "msg": str(exc), "type": GENERIC_ERROR_TYPE}]
