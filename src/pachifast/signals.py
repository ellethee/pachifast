"""Lifecycle signals: let a project react to what happens in the core without patching it.

A project connects a handler (a plain function or a coroutine function) to a signal; the core *emits* the signal at
the matching point, passing keyword arguments::

    from pachifast import signals

    @signals.connect(signals.USER_CREATED)
    async def welcome(*, user, **_):
        ...

========================  ===============================  ==============================================
signal                    emitted                          keyword arguments
========================  ===============================  ==============================================
``USER_CREATED``          a user was saved                 ``user``
``USER_LOGGED_IN``        a login finished (any factor)    ``user``
``LOGIN_FAILED``          a password login was refused     ``username`` (as typed; the account may not exist)
``PASSWORD_CHANGED``      a password was saved             ``user``
``USER_LOGGED_OUT``       ``POST /auth/logout``            ``user``
========================  ===============================  ==============================================

Handlers run one after the other, in the order they were connected, in the request that emitted the signal: keep them
short, and start a task (:func:`pachifast.utils.tasks.spawn`) for slow work. A handler that raises is logged and
skipped: it can neither fail the request nor stop the other handlers. Signals are per process: with several workers
each one runs the handlers of the requests it serves.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

USER_CREATED = "user_created"
USER_LOGGED_IN = "user_logged_in"
LOGIN_FAILED = "login_failed"
PASSWORD_CHANGED = "password_changed"
USER_LOGGED_OUT = "user_logged_out"

SIGNALS = frozenset({USER_CREATED, USER_LOGGED_IN, LOGIN_FAILED, PASSWORD_CHANGED, USER_LOGGED_OUT})

_handlers: dict[str, list[Callable[..., Any]]] = {name: [] for name in SIGNALS}


def connect(signal: str, handler: Callable[..., Any] | None = None) -> Callable[..., Any]:
    """Connect ``handler`` to ``signal``; without ``handler`` it is a decorator. Connecting twice is a no-op.

    Raises:
        ValueError: ``signal`` is not one of :data:`SIGNALS`.
    """
    if signal not in SIGNALS:
        raise ValueError(f"Unknown signal {signal!r}: use one of {sorted(SIGNALS)}")

    def register(func: Callable[..., Any]) -> Callable[..., Any]:
        if func not in _handlers[signal]:
            _handlers[signal].append(func)
        return func

    return register if handler is None else register(handler)


def disconnect(signal: str, handler: Callable[..., Any]) -> None:
    """Remove ``handler`` from ``signal`` (no error if it was not connected)."""
    if handler in _handlers.get(signal, []):
        _handlers[signal].remove(handler)


def clear() -> None:
    """Remove every handler (for tests)."""
    for handlers in _handlers.values():
        handlers.clear()


async def emit(signal: str, **kwargs: Any) -> None:
    """Call the handlers of ``signal`` with ``kwargs``; their errors are logged, never raised."""
    for handler in list(_handlers.get(signal, ())):
        try:
            result = handler(**kwargs)
            if inspect.isawaitable(result):
                await result
        except Exception:
            log.exception("Handler %r of the signal %s failed", handler, signal)
