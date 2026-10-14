"""Reusable asyncio DatagramProtocol implementations."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

UdpMessageHandler = Callable[[bytes, tuple[str, int], asyncio.DatagramTransport], Awaitable[None] | None]


class BaseUdpProtocol(asyncio.DatagramProtocol):
    """Base protocol that delegates datagrams to a sync or async handler."""

    def __init__(self, name: str, handler: UdpMessageHandler, logger: logging.Logger | None = None):
        self.name = name
        self.handler = handler
        self.log = logger or logging.getLogger(f"{__name__}.{name}")
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        sockname = self.transport.get_extra_info("sockname") if self.transport else None
        self.log.info("UDP listener %s started on %s", self.name, sockname)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.transport is None:
            self.log.warning("UDP listener %s received data before transport init", self.name)
            return

        result = self.handler(data, addr, self.transport)
        if asyncio.iscoroutine(result):
            task = asyncio.create_task(result)
            task.add_done_callback(self._log_task_error)

    def error_received(self, exc: Exception) -> None:
        self.log.exception("UDP listener %s received socket error: %s", self.name, exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            self.log.exception("UDP listener %s stopped with error: %s", self.name, exc)
        else:
            self.log.info("UDP listener %s stopped", self.name)

    def _log_task_error(self, task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except Exception:
            self.log.exception("UDP listener %s handler failed", self.name)
