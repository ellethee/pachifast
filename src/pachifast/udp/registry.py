"""Registry and lifecycle helpers for UDP listeners."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from pachifast.udp.protocol import BaseUdpProtocol, UdpMessageHandler

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UdpListenerConfig:
    name: str
    host: str
    port: int
    handler: UdpMessageHandler
    protocol_factory: Callable[[UdpListenerConfig], asyncio.DatagramProtocol] | None = None


@dataclass(slots=True)
class UdpListenerHandle:
    config: UdpListenerConfig
    transport: asyncio.DatagramTransport
    protocol: asyncio.DatagramProtocol

    @property
    def address(self) -> tuple[str, int] | None:
        sockname = self.transport.get_extra_info("sockname")
        if isinstance(sockname, tuple) and len(sockname) >= 2:
            return sockname[0], sockname[1]
        return None


class UdpRegistry:
    """Tracks active UDP listeners for the current process."""

    def __init__(self) -> None:
        self._listeners: dict[str, UdpListenerHandle] = {}

    async def start(self, config: UdpListenerConfig) -> UdpListenerHandle:
        if config.name in self._listeners:
            return self._listeners[config.name]

        loop = asyncio.get_running_loop()
        protocol_factory = config.protocol_factory or self._default_protocol_factory
        transport, protocol = await loop.create_datagram_endpoint(
            lambda: protocol_factory(config),
            local_addr=(config.host, config.port),
        )
        handle = UdpListenerHandle(
            config=config,
            transport=transport,  # type: ignore[arg-type]
            protocol=protocol,
        )
        self._listeners[config.name] = handle
        return handle

    async def stop(self, name: str) -> None:
        handle = self._listeners.pop(name, None)
        if handle is None:
            return
        handle.transport.close()
        await asyncio.sleep(0)

    async def stop_all(self) -> None:
        for name in list(self._listeners):
            await self.stop(name)

    def get(self, name: str) -> UdpListenerHandle | None:
        return self._listeners.get(name)

    def list(self) -> list[dict[str, object]]:
        return [
            {
                "name": handle.config.name,
                "host": handle.config.host,
                "port": handle.config.port,
                "address": handle.address,
            }
            for handle in self._listeners.values()
        ]

    def sendto(self, name: str, data: bytes, addr: tuple[str, int]) -> None:
        handle = self._listeners.get(name)
        if handle is None:
            raise KeyError(f"UDP listener not found: {name}")
        handle.transport.sendto(data, addr)

    @staticmethod
    def _default_protocol_factory(config: UdpListenerConfig) -> asyncio.DatagramProtocol:
        return BaseUdpProtocol(config.name, config.handler, logger=log.getChild(config.name))


udp_registry = UdpRegistry()
