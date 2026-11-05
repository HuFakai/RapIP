"""Authenticated HTTP/1 proxy. CONNECT is a byte tunnel; HTTP requests are origin-form, one per connection."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hmac
import time
from urllib.parse import urlsplit

from .models import GatewayError, Route
from .socks import SocksServer, UpstreamError, address_bytes, close_writer, upstream_connect


class HttpProxy(SocksServer):
    async def start(self):
        config = self.gateway.config
        self.server = await asyncio.start_server(
            self.handle, config.proxy_http_host, config.proxy_http_port, limit=65536
        )
        return self.server

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        upstream_writer = None
        tunnel_id = None
        replied = False
        observation = self.gateway.observer.begin("http")
        result, reason = "protocol_error", "peer_closed"
        try:
            if len(self.handlers) > self.gateway.settings.max_connections:
                raise GatewayError("CONNECTION_LIMIT", "连接数超限", 503)
            async with asyncio.timeout(15):
                raw = await reader.readuntil(b"\r\n\r\n")
                if len(raw) > 32768:
                    raise ValueError("headers too large")
                lines = raw[:-4].decode("iso-8859-1").split("\r\n")
                method, uri, version = lines[0].split(" ")
                observation["protocol"] = "connect" if method == "CONNECT" else "http"
                if version not in {"HTTP/1.1", "HTTP/1.0"} or not method.isalpha():
                    raise ValueError("invalid method")
                headers = []
                for line in lines[1:]:
                    key, value = line.split(":", 1)
                    if not key or any(c.isspace() for c in key):
                        raise ValueError("invalid header")
                    headers.append((key.lower(), value.strip()))
                authentication = [v for k, v in headers if k == "proxy-authorization"]
                if len(authentication) != 1 or not authentication[0].startswith("Basic "):
                    result = "authentication_rejected"
                    writer.write(
                        b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="RapIP"\r\nConnection: close\r\nContent-Length: 0\r\n\r\n'
                    )
                    await writer.drain()
                    return
                username, password = (
                    base64.b64decode(authentication[0][6:], validate=True).decode("utf-8").split(":", 1)
                )
                route = Route.parse(username)
                if not (
                    hmac.compare_digest(route.user.encode(), self.gateway.config.socks_user.encode())
                    and hmac.compare_digest(password.encode(), self.gateway.config.socks_password.encode())
                ):
                    result = "authentication_rejected"
                    writer.write(
                        b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="RapIP"\r\nConnection: close\r\nContent-Length: 0\r\n\r\n'
                    )
                    await writer.drain()
                    return
                if route.group in self.gateway.quality.groups:
                    self.gateway.observer.routed(observation, route)
                if method == "CONNECT":
                    observation["protocol"] = "connect"
                    destination = urlsplit("//" + uri)
                    host, port = destination.hostname, destination.port
                    if not host or not port or destination.path or destination.username:
                        raise ValueError("invalid CONNECT target")
                else:
                    destination = urlsplit(uri)
                    if (
                        destination.scheme != "http"
                        or not destination.hostname
                        or destination.username
                        or destination.fragment
                    ):
                        raise ValueError("absolute HTTP URI required")
                    host, port = destination.hostname, destination.port or 80
                target = address_bytes(host, port)
                observation["target"] = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            for attempt in range(2):
                binding, node, generation = await self.gateway.acquire(route)
                observation.update(account_id=node.account_id, group_id=node.group_id, ip=node.ip)
                try:
                    upstream_reader, upstream_writer, _ = await upstream_connect(
                        node, target, egress_proxy=self.gateway.config.egress_proxy
                    )
                    tunnel_id = await self.gateway.attach(
                        binding.id, node.id, generation, writer, upstream_writer, observation=observation
                    )
                    await self.gateway.connection_success(node.id)
                    break
                except UpstreamError as error:
                    if not await self.gateway.connection_failure(node.id, error.kind) or attempt:
                        raise
            if method == "CONNECT":
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                replied = True

                async def pump(source, sink, direction):
                    while data := await source.read(65536):
                        sink.write(data)
                        await sink.drain()
                        self.gateway.traffic(tunnel_id, direction, len(data), time.time())
                    if sink.can_write_eof():
                        with contextlib.suppress(OSError):
                            sink.write_eof()
                            await sink.drain()

                async with asyncio.TaskGroup() as group:
                    group.create_task(pump(reader, upstream_writer, "up"))
                    group.create_task(pump(upstream_reader, writer, "down"))
            else:
                lengths = [v for k, v in headers if k == "content-length"]
                encodings = [v for k, v in headers if k == "transfer-encoding"]
                if len(lengths) > 1 or (lengths and encodings) or len(encodings) > 1:
                    raise ValueError("ambiguous request framing")
                if encodings and encodings[0].lower() != "chunked":
                    raise ValueError("unsupported transfer encoding")
                length = int(lengths[0]) if lengths else 0
                if not 0 <= length <= 64 * 1024 * 1024:
                    raise ValueError("request body too large")
                hop = {
                    "proxy-authorization",
                    "proxy-connection",
                    "connection",
                    "keep-alive",
                    "upgrade",
                    "host",
                    "expect",
                }
                for key, value in headers:
                    if key == "connection":
                        hop.update(part.strip().lower() for part in value.split(","))
                path = destination.path or "/"
                if destination.query:
                    path += "?" + destination.query
                authority = f"[{host}]" if ":" in host else host.encode("idna").decode()
                if port != 80:
                    authority += f":{port}"
                outgoing = f"{method} {path} HTTP/1.1\r\nHost: {authority}\r\nConnection: close\r\n"
                outgoing += "".join(f"{k}: {v}\r\n" for k, v in headers if k not in hop)
                upstream_writer.write((outgoing + "\r\n").encode("iso-8859-1"))
                await upstream_writer.drain()
                if any(k == "expect" and v.lower() == "100-continue" for k, v in headers):
                    writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                    await writer.drain()

                async def forward_exact(count):
                    while count:
                        chunk = await reader.readexactly(min(65536, count))
                        upstream_writer.write(chunk)
                        await upstream_writer.drain()
                        self.gateway.traffic(tunnel_id, "up", len(chunk), time.time())
                        count -= len(chunk)

                async def request_body():
                    if not encodings:
                        await forward_exact(length)
                        return
                    total = 0
                    while True:
                        line = await reader.readuntil(b"\r\n")
                        if len(line) > 1024:
                            raise ValueError("chunk header too long")
                        size = int(line.split(b";", 1)[0].strip(), 16)
                        total += size
                        if size < 0 or total > 64 * 1024 * 1024:
                            raise ValueError("request body too large")
                        # Re-encode chunk metadata and discard trailers, including proxy credentials.
                        upstream_writer.write(f"{size:x}\r\n".encode())
                        if not size:
                            trailer_bytes = 0
                            while trailer := await reader.readuntil(b"\r\n"):
                                trailer_bytes += len(trailer)
                                if trailer_bytes > 32768:
                                    raise ValueError("trailers too large")
                                if trailer == b"\r\n":
                                    break
                            upstream_writer.write(b"\r\n")
                            await upstream_writer.drain()
                            return
                        await forward_exact(size)
                        if await reader.readexactly(2) != b"\r\n":
                            raise ValueError("invalid chunk")
                        upstream_writer.write(b"\r\n")

                async def response_body():
                    nonlocal replied
                    try:
                        while data := await upstream_reader.read(65536):
                            replied = True
                            writer.write(data)
                            await writer.drain()
                            self.gateway.traffic(tunnel_id, "down", len(data), time.time())
                    finally:
                        body_task.cancel()

                async with asyncio.TaskGroup() as group:
                    body_task = group.create_task(request_body())
                    group.create_task(response_body())
        except (GatewayError, UpstreamError) as error:
            result = error.code if isinstance(error, GatewayError) else "upstream_" + error.kind
            reason = "connect_failed"
            if isinstance(error, UpstreamError):
                observation["socks_reply"] = error.reply
            if not replied:
                status = 503 if isinstance(error, GatewayError) else 502
                writer.write(
                    f"HTTP/1.1 {status} Proxy Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode()
                )
                with contextlib.suppress(OSError):
                    await writer.drain()
        except (Exception, asyncio.CancelledError):
            reason = "interrupted" if self.gateway.stopping else "io_error"
            if not replied:
                writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
        finally:
            await close_writer(upstream_writer)
            await close_writer(writer)
            if tunnel_id:
                await self.gateway.detach(tunnel_id)
            self.gateway.observer.finish(observation, result, reason)
            self.handlers.discard(task)
