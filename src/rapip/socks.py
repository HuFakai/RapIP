from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import socket
import struct
import time
from typing import TYPE_CHECKING

from python_socks import ProxyConnectionError, ProxyError, ProxyTimeoutError
from python_socks.async_.asyncio import Proxy

from .models import GatewayError, Node, Route

if TYPE_CHECKING:
    from .gateway import Gateway


class UpstreamError(Exception):
    def __init__(self, kind: str, reply: int = 1):
        super().__init__(kind)
        self.kind, self.reply = kind, reply


async def read_address(reader: asyncio.StreamReader, atyp: int) -> bytes:
    if atyp == 1:
        return await reader.readexactly(4)
    if atyp == 4:
        return await reader.readexactly(16)
    if atyp == 3:
        length = await reader.readexactly(1)
        if not length[0]:
            raise UpstreamError("invalid_address", 8)
        return length + await reader.readexactly(length[0])
    raise UpstreamError("invalid_address", 8)


def address_bytes(host: str, port: int) -> bytes:
    try:
        address = ipaddress.ip_address(host)
        target = bytes([1 if address.version == 4 else 4]) + address.packed
    except ValueError:
        encoded = host.encode("idna")
        if not 0 < len(encoded) < 256:
            raise ValueError("Invalid domain length") from None
        target = b"\x03" + bytes([len(encoded)]) + encoded
    return target + struct.pack("!H", port)


def target_text(target: bytes) -> str:
    port = struct.unpack("!H", target[-2:])[0]
    if target[0] == 3:
        host = target[2 : 2 + target[1]].decode("ascii", "replace")
    else:
        host = str(ipaddress.ip_address(target[1:-2]))
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


async def close_writer(writer: asyncio.StreamWriter | None) -> None:
    if writer:
        writer.close()
        with contextlib.suppress(Exception):
            async with asyncio.timeout(2):
                await writer.wait_closed()


async def upstream_connect(node: Node, target: bytes, timeout: float = 10, egress_proxy: str = ""):
    writer = None
    try:
        async with asyncio.timeout(timeout):
            if egress_proxy:
                raw_socket = await Proxy.from_url(egress_proxy).connect(
                    dest_host=node.ip, dest_port=node.port, timeout=timeout
                )
                try:
                    reader, writer = await asyncio.open_connection(sock=raw_socket)
                except BaseException:
                    raw_socket.close()
                    raise
            else:
                reader, writer = await asyncio.open_connection(node.ip, node.port)
            writer.write(b"\x05\x01\x02")
            await writer.drain()
            response = await reader.readexactly(2)
            if response != b"\x05\x02":
                raise UpstreamError("authentication")
            user, password = node.username.encode(), node.password.encode()
            if not 0 < len(user) <= 255 or not 0 < len(password) <= 255:
                raise UpstreamError("authentication")
            writer.write(b"\x01" + bytes([len(user)]) + user + bytes([len(password)]) + password)
            await writer.drain()
            if await reader.readexactly(2) != b"\x01\x00":
                raise UpstreamError("authentication")
            writer.write(b"\x05\x01\x00" + target)
            await writer.drain()
            header = await reader.readexactly(4)
            if header[0] != 5 or header[2] != 0:
                raise UpstreamError("protocol")
            bound = await read_address(reader, header[3]) + await reader.readexactly(2)
            if header[1]:
                raise UpstreamError("target", header[1])
            return reader, writer, header + bound
    except BaseException as exc:
        await close_writer(writer)
        if isinstance(
            exc,
            (
                OSError,
                TimeoutError,
                asyncio.IncompleteReadError,
                ProxyError,
                ProxyConnectionError,
                ProxyTimeoutError,
            ),
        ):
            raise UpstreamError("transport") from None
        raise


async def probe_node(node: Node, timeout: float = 5, egress_proxy: str = "") -> bool:
    for host in ("cp.cloudflare.com", "www.gstatic.com"):
        writer = None
        try:
            reader, writer, _ = await upstream_connect(node, address_bytes(host, 80), timeout, egress_proxy)
            async with asyncio.timeout(timeout):
                writer.write(
                    f"GET /generate_204 HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
                )
                await writer.drain()
                status = await reader.readuntil(b"\r\n")
                if status.startswith((b"HTTP/1.1 204", b"HTTP/1.0 204")):
                    return True
        except (UpstreamError, OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            pass
        finally:
            await close_writer(writer)
    return False


class SocksServer:
    def __init__(self, gateway: Gateway):
        self.gateway = gateway
        self.handlers: set[asyncio.Task] = set()

    async def start(self):
        config = self.gateway.config
        self.server = await asyncio.start_server(
            self.handle, config.socks_host, config.socks_port, limit=65536
        )
        return self.server

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()
        for task in list(self.handlers):
            task.cancel()
        if self.handlers:
            await asyncio.gather(*self.handlers, return_exceptions=True)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        task = asyncio.current_task()
        self.handlers.add(task)
        dial_binding_id = None
        tunnel_id = None
        upstream_writer = None
        replied = False
        binding = None
        observation = self.gateway.observer.begin("socks5")
        result, reason = "protocol_error", "peer_closed"
        try:
            if len(self.handlers) > self.gateway.settings.max_connections:
                raise GatewayError(
                    "ADMISSION_LIMIT",
                    f"SOCKS5 接入处理数 {len(self.handlers)}/{self.gateway.settings.max_connections}",
                    503,
                )
            sock = writer.get_extra_info("socket")
            if sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            async with asyncio.timeout(15):
                header = await reader.readexactly(2)
                if header[0] != 5 or not header[1]:
                    return
                methods = await reader.readexactly(header[1])
                if 2 not in methods:
                    writer.write(b"\x05\xff")
                    await writer.drain()
                    return
                writer.write(b"\x05\x02")
                await writer.drain()
                auth = await reader.readexactly(2)
                if auth[0] != 1 or not auth[1]:
                    return
                username = (await reader.readexactly(auth[1])).decode("ascii")
                length = (await reader.readexactly(1))[0]
                password = await reader.readexactly(length)
                route = Route.parse(username)
                config = self.gateway.config
                if not (
                    hmac.compare_digest(route.user, config.socks_user)
                    and hmac.compare_digest(password, config.socks_password.encode())
                ):
                    result = "authentication_rejected"
                    writer.write(b"\x01\x01")
                    await writer.drain()
                    return
                if route.group in self.gateway.quality.groups:
                    self.gateway.observer.routed(observation, route)
                writer.write(b"\x01\x00")
                await writer.drain()
                request = await reader.readexactly(4)
                if request[:3] != b"\x05\x01\x00":
                    observation["socks_command"] = request[1]
                    observation["socks_reply"] = 7
                    raise UpstreamError("unsupported_command", 7)
                target = bytes([request[3]]) + await read_address(reader, request[3])
                target += await reader.readexactly(2)
                observation["target"] = target_text(target)
            for attempt in range(2):
                binding, node, generation = await self.gateway.acquire(route)
                observation.update(account_id=node.account_id, group_id=node.group_id, ip=node.ip)
                try:
                    await self.gateway.reserve_dial(binding)
                    dial_binding_id = binding.id
                    upstream_reader, upstream_writer, reply = await upstream_connect(
                        node, target, egress_proxy=self.gateway.config.egress_proxy
                    )
                    tunnel_id = await self.gateway.attach(
                        binding.id, node.id, generation, writer, upstream_writer, observation=observation
                    )
                    self.gateway.release_dial(dial_binding_id)
                    dial_binding_id = None
                    await self.gateway.connection_success(node.id)
                    break
                except UpstreamError as error:
                    self.gateway.release_dial(dial_binding_id)
                    dial_binding_id = None
                    moved = await self.gateway.connection_failure(node.id, error.kind)
                    if attempt or not moved:
                        raise
                except GatewayError:
                    await close_writer(upstream_writer)
                    raise
            writer.write(reply)
            await writer.drain()
            replied = True

            async def pump(source, destination, direction):
                while data := await source.read(65536):
                    destination.write(data)
                    await destination.drain()
                    self.gateway.traffic(tunnel_id, direction, len(data), time.time())
                if destination.can_write_eof():
                    with contextlib.suppress(OSError):
                        destination.write_eof()
                        await destination.drain()

            async with asyncio.TaskGroup() as group:
                group.create_task(pump(reader, upstream_writer, "up"))
                group.create_task(pump(upstream_reader, writer, "down"))
        except UpstreamError as error:
            result = (
                "unsupported_command" if error.kind == "unsupported_command" else "upstream_" + error.kind
            )
            reason = "unsupported_proxy_command" if error.kind == "unsupported_command" else "upstream_error"
            observation["socks_reply"] = error.reply
            if not replied:
                writer.write(bytes([5, error.reply, 0, 1]) + bytes(6))
                with contextlib.suppress(OSError):
                    await writer.drain()
        except GatewayError as error:
            observation["error_message"] = error.message
            result, reason = error.code, "scheduler_rejected"
            if not replied:
                writer.write(b"\x05\x01\x00\x01" + bytes(6))
                with contextlib.suppress(OSError):
                    await writer.drain()
        except (Exception, asyncio.CancelledError):
            reason = "interrupted" if self.gateway.stopping else "io_error"
        finally:
            self.gateway.release_dial(dial_binding_id)
            await close_writer(upstream_writer)
            await close_writer(writer)
            if tunnel_id:
                await self.gateway.detach(tunnel_id)
            self.gateway.observer.finish(observation, result, reason)
            self.handlers.discard(task)
