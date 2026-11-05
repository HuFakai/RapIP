from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .api_v2 import register as register_v2
from .config import Account, Config
from .connection import build_connection
from .gateway import Gateway
from .http_proxy import HttpProxy
from .models import GatewayError, IPState
from .provider import ProviderError
from .retention import RetentionPolicy
from .socks import SocksServer


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Model):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class Rotation(Model):
    mode: Literal["graceful", "hard"] = "graceful"
    session_policy: Literal["keep", "new"] = "keep"
    new_session_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9]{1,64}$")
    expected_generation: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def coherent(self):
        if (self.session_policy == "new") != bool(self.new_session_id):
            raise ValueError("new 策略必须提供新 session；keep 策略不能提供")
        return self


class Release(Model):
    mode: Literal["graceful", "hard"] = "graceful"
    expected_generation: int | None = Field(default=None, ge=1)


class AccountInput(Model):
    account_id: str = Field(pattern=r"^[A-Za-z0-9]{1,32}$")
    name: str = Field(min_length=1, max_length=64)
    api_key: str = Field(min_length=16, max_length=256, repr=False)
    replacement_budget: int | None = Field(default=None, ge=0)
    traffic_limit_gb: int | None = Field(default=None, ge=0)
    replacement_country: str = Field(default="original", pattern=r"^(original|any|[A-Z]{2})$")
    plan_id: str | None = Field(default=None, pattern=r"^[0-9]+$")


class AccountPatch(Model):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    replacement_budget: int | None = Field(default=None, ge=0)
    replacement_country: str | None = Field(default=None, pattern=r"^(original|any|[A-Z]{2})$")


class SettingsPatch(Model):
    retention: RetentionPolicy | None = None
    connection_log_mode: Literal["off", "errors", "all"] | None = None
    quality_daily_limit: int | None = Field(default=None, ge=0, le=100000)
    probe_enabled: bool | None = None
    pool_wait_timeout: float | None = Field(default=None, ge=0.1, le=30)
    drain_timeout: float | None = Field(default=None, ge=1, le=3600)
    session_switch_mode: Literal["graceful", "hard"] | None = None
    session_idle_timeout: float | None = Field(default=None, ge=10, le=86400)
    max_active_bindings: int | None = Field(default=None, ge=0, le=100000)
    probe_interval: float | None = Field(default=None, ge=60, le=86400)
    probe_timeout: float | None = Field(default=None, ge=1, le=30)
    sync_interval: float | None = Field(default=None, ge=30, le=86400)


class ConnectionInput(Model):
    tunnel: bool = False
    group_id: str = Field(default="default", pattern=r"^[A-Za-z0-9]{1,32}$")
    host: str | None = Field(default=None, min_length=1, max_length=253)
    worker_id: str | None = Field(default="01", pattern=r"^[A-Za-z0-9]{1,64}$")
    session_id: str = Field(pattern=r"^[A-Za-z0-9]{1,64}$")
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")

    @model_validator(mode="after")
    def country_requires_worker(self):
        if self.country_code and self.worker_id is None:
            raise ValueError("国家参数需要配合 worker 使用")
        return self


def page(items, number: int, size: int):
    return {
        "items": items[(number - 1) * size : number * size],
        "total": len(items),
        "page": number,
        "page_size": size,
    }


def pagination(request: Request):
    try:
        number, size = (
            int(request.query_params.get("page", 1)),
            int(request.query_params.get("page_size", 50)),
        )
    except ValueError:
        raise HTTPException(400, "分页参数必须是整数") from None
    if number < 1 or not 1 <= size <= 100:
        raise HTTPException(400, "分页参数超出范围")
    return number, size


def create_app(config: Config | None = None, gateway: Gateway | None = None, lifecycle: bool = True):
    config = config or Config.from_env()
    gateway = gateway or Gateway(config)
    cookie_name = f"rapip_session_{config.http_port}"
    sessions: dict[str, dict] = {}
    attempts = defaultdict(deque)
    config_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(app):
        if lifecycle:
            try:
                await gateway.start()
            except BaseException:
                if hasattr(gateway, "client"):
                    await gateway.client.close()
                if hasattr(gateway.storage, "db"):
                    await gateway.storage.close()
                if hasattr(gateway, "owner_file"):
                    gateway.owner_file.close()
                raise
            socks = SocksServer(gateway)
            http_proxy = HttpProxy(gateway)
            try:
                await socks.start()
                await http_proxy.start()
            except Exception:
                if hasattr(socks, "server"):
                    await socks.stop()
                await gateway.stop()
                raise
            app.state.socks = socks
            app.state.http_proxy = http_proxy
        try:
            yield
        finally:
            if lifecycle:
                gateway.stopping, gateway.ready = True, False
                socks.server.close()
                http_proxy.server.close()
                await socks.server.wait_closed()
                deadline = asyncio.get_running_loop().time() + gateway.settings.drain_timeout
                while gateway.tunnels and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.1)
                await socks.stop()
                await http_proxy.stop()
                await gateway.stop()

    app = FastAPI(
        title="RapIP", version="0.2.1", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.gateway = gateway

    @app.exception_handler(GatewayError)
    async def domain_error(request, error):
        return JSONResponse({"code": error.code, "message": error.message}, status_code=error.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Never echo submitted API keys or passwords through validation detail.input.
        return JSONResponse(
            {
                "code": "VALIDATION_ERROR",
                "message": "请求参数不符合要求",
                "fields": [".".join(str(part) for part in item["loc"]) for item in error.errors()],
            },
            status_code=422,
        )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def check_origin(request: Request):
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "来源校验失败")

    async def auth(request: Request):
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Bearer ") and hmac.compare_digest(authorization[7:], config.admin_token):
            return {"username": config.admin_user, "csrf": None, "token": True}
        item = sessions.get(request.cookies.get(cookie_name, ""))
        if not item or item["expires"] < time.time():
            raise HTTPException(401, "请先登录")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            check_origin(request)
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), item["csrf"]):
                raise HTTPException(403, "CSRF 校验失败")
        return item

    prefix = "/api/v1"

    @app.get(prefix + "/health/live")
    async def live():
        return {"live": True}

    @app.get(prefix + "/health/ready", dependencies=[Depends(auth)])
    async def ready():
        return JSONResponse(
            {"ready": gateway.ready and not gateway.storage_failed},
            status_code=200 if gateway.ready and not gateway.storage_failed else 503,
        )

    @app.post(prefix + "/auth/login")
    async def login(body: Login, request: Request, response: Response):
        check_origin(request)
        peer = request.client.host if request.client else "unknown"
        recent = attempts[peer]
        while recent and recent[0] < time.time() - 60:
            recent.popleft()
        if len(recent) >= 8:
            raise HTTPException(429, "登录尝试过于频繁，请稍后再试")
        recent.append(time.time())
        if not (
            hmac.compare_digest(body.username.encode(), config.admin_user.encode())
            and hmac.compare_digest(body.password.encode(), config.admin_password.encode())
        ):
            raise HTTPException(401, "用户名或密码不正确")
        recent.clear()
        for token in [key for key, value in sessions.items() if value["expires"] < time.time()]:
            sessions.pop(token, None)
        if len(sessions) > 100:
            sessions.clear()
        token = secrets.token_urlsafe(32)
        item = {
            "username": config.admin_user,
            "csrf": secrets.token_urlsafe(24),
            "expires": time.time() + 43200,
        }
        sessions[token] = item
        response.set_cookie(
            cookie_name,
            token,
            httponly=True,
            secure=config.cookie_secure,
            samesite="strict",
            max_age=43200,
            path="/",
        )
        return {"username": item["username"], "csrf": item["csrf"]}

    @app.get(prefix + "/auth/me")
    async def me(identity=Depends(auth)):
        return {"username": identity["username"], "csrf": identity["csrf"]}

    @app.post(prefix + "/auth/logout", dependencies=[Depends(auth)])
    async def logout(request: Request, response: Response):
        sessions.pop(request.cookies.get(cookie_name, ""), None)
        response.delete_cookie(cookie_name, path="/")
        return {"ok": True}

    @app.get(prefix + "/openapi.json", dependencies=[Depends(auth)])
    async def openapi():
        return app.openapi()

    @app.get(prefix + "/stats", dependencies=[Depends(auth)])
    async def stats():
        return gateway.stats()

    @app.post(prefix + "/connection-config", dependencies=[Depends(auth)])
    async def connection_config(body: ConnectionInput, request: Request):
        if body.group_id not in gateway.quality.groups:
            raise GatewayError("NOT_FOUND", "分组不存在", 404)
        host = body.host or config.public_proxy_host or request.url.hostname or "127.0.0.1"
        try:
            return build_connection(
                config, host, body.worker_id, body.session_id, body.country_code, body.group_id, body.tunnel
            )
        except ValueError as error:
            raise GatewayError("INVALID_CONNECTION_CONFIG", str(error), 422) from None

    @app.get(prefix + "/nodes", dependencies=[Depends(auth)])
    async def nodes(
        request: Request,
        state: str = "",
        country: str = "",
        account_id: str = "",
        q: str = "",
        group_id: str = "",
        quality: str = "",
        sort: Literal[
            "ip", "latency_ms", "checked_at", "fail_count", "created_at", "state", "account_id"
        ] = "ip",
        direction: Literal["asc", "desc"] = "asc",
    ):
        import ipaddress

        network = None
        if "/" in q:
            try:
                network = ipaddress.ip_network(q, strict=False)
            except ValueError:
                raise HTTPException(422, "IP/CIDR 格式无效") from None
        rows = [
            n.public()
            for n in gateway.nodes.values()
            if n.state != IPState.ARCHIVED
            and (not state or n.state == state)
            and (not country or n.country == country.upper())
            and (not account_id or n.account_id == account_id)
            and (not group_id or n.group_id == group_id)
            and (not quality or gateway.quality.state(n) == quality)
            and (
                not q
                or (network and ipaddress.ip_address(n.ip) in network)
                or (not network and q.lower() in f"{n.ip} {n.account_id} {n.id}".lower())
            )
        ]
        for row in rows:
            row["connections"] = gateway._count_node(row["id"])
            row["worker"] = (
                gateway.bindings[row["binding_id"]].worker
                if row.get("binding_id") in gateway.bindings
                else None
            )
            row["quality"] = {**row["quality"], "state": gateway.quality.state(gateway.nodes[row["id"]])}

        def key(row):
            value = row.get(sort)
            if sort == "ip":
                address = ipaddress.ip_address(row["ip"])
                return (address.version, int(address))
            return (value is None, value if value is not None else 0)

        return page(sorted(rows, key=key, reverse=direction == "desc"), *pagination(request))

    @app.get(prefix + "/accounts", dependencies=[Depends(auth)])
    async def accounts():
        return {"items": gateway.public_accounts()}

    @app.post(prefix + "/accounts", dependencies=[Depends(auth)], status_code=201)
    async def add_account(body: AccountInput):
        async with config_lock:
            if body.account_id in gateway.accounts:
                raise GatewayError("ACCOUNT_EXISTS", "账号 ID 已存在")
            account = Account(**body.model_dump(exclude={"api_key"}))
            provider = gateway.provider_factory(account, body.api_key, gateway.client)
            try:
                await provider.list_nodes()
            except ProviderError:
                raise GatewayError("ACCOUNT_VALIDATION_FAILED", "API Key 或套餐验证失败", 400) from None
            await gateway.save_account(account, body.api_key)
        gateway.spawn(gateway.sync_account(account.account_id), "account-sync")
        return {"account_id": account.account_id, "status": "syncing"}

    @app.patch(prefix + "/accounts/{account_id}", dependencies=[Depends(auth)])
    async def patch_account(account_id: str, body: AccountPatch):
        async with config_lock:
            previous = gateway.accounts.get(account_id)
            if not previous:
                raise HTTPException(404, "账号不存在")
            changes = body.model_dump(exclude_unset=True)
            if any(value is None for key, value in changes.items() if key != "replacement_budget"):
                raise HTTPException(422, "参数不能为 null")
            account = Account(**{**asdict(previous), **changes})
            await gateway.save_account(account)
        async with gateway.changed:
            gateway.event("account", "账号配置已更新", account_id=account_id)
            for job in gateway.replacements.values():
                if job.account_id == account_id and job.state == "BUDGET_BLOCKED":
                    job.state = "QUEUED"
            await gateway._persist()
            gateway.changed.notify_all()
        return {"ok": True}

    @app.get(prefix + "/bindings", dependencies=[Depends(auth)])
    async def bindings(request: Request, q: str = "", include_released: bool = False, group_id: str = ""):
        rows = [
            gateway.public_binding(b)
            for b in gateway.bindings.values()
            if (include_released or not b.released)
            and (not group_id or b.group_id == group_id)
            and (not q or q.lower() in f"{b.worker or ''} {b.session}".lower())
        ]
        return page(sorted(rows, key=lambda b: b["created_at"], reverse=True), *pagination(request))

    @app.get(prefix + "/bindings/{binding_id}", dependencies=[Depends(auth)])
    async def binding(binding_id: str):
        item = gateway.bindings.get(binding_id)
        if not item:
            raise HTTPException(404, "绑定不存在")
        return gateway.public_binding(item)

    async def operate(request, kind, body, action):
        key = request.headers.get("idempotency-key", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
            raise HTTPException(400, "请提供 8–128 字符的 Idempotency-Key")
        fingerprint = hashlib.sha256(
            (request.url.path + json.dumps(body, sort_keys=True)).encode()
        ).hexdigest()
        group_id, account_id = "", ""
        if match := re.search(r"/groups/([A-Za-z0-9]+)(?:/|$)", request.url.path):
            if match[1] in gateway.quality.groups:
                group_id = match[1]
        elif kind == "group_create":
            group_id = body.get("group_id", "")
        elif match := re.search(r"/bindings/([A-Za-z0-9]+)/", request.url.path):
            if binding := gateway.bindings.get(match[1]):
                group_id = binding.group_id
                if node := gateway.nodes.get(binding.node_id):
                    account_id = node.account_id
        op = await gateway.operation(key, fingerprint, kind, action, group_id, account_id)
        return JSONResponse(asdict(op), status_code=202)

    @app.post(prefix + "/bindings/{binding_id}/rotate", dependencies=[Depends(auth)])
    async def rotate(binding_id: str, body: Rotation, request: Request):
        return await operate(
            request,
            "rotate",
            body.model_dump(),
            lambda: gateway.rotate(binding_id, body.mode, body.new_session_id, body.expected_generation),
        )

    @app.post(prefix + "/rotate/{worker_id}", dependencies=[Depends(auth)])
    async def rotate_worker(worker_id: str, body: Rotation, request: Request, group_id: str = "default"):
        prefix = "owner" if group_id == "default" else f"owner:group:{group_id}"
        item = gateway.binding_for(f"{prefix}:worker:{worker_id}")
        if not item:
            raise HTTPException(404, "工位不存在")
        return await rotate(item.id, body, request)

    @app.post(prefix + "/bindings/{binding_id}/release", dependencies=[Depends(auth)])
    async def release(binding_id: str, body: Release, request: Request):
        return await operate(
            request,
            "release",
            body.model_dump(),
            lambda: gateway.release(binding_id, body.mode, body.expected_generation),
        )

    @app.post(prefix + "/kill/{session_id}", dependencies=[Depends(auth)])
    async def kill(
        session_id: str,
        body: Release,
        request: Request,
        worker_id: str | None = None,
        anonymous: bool = False,
        group_id: str = "default",
    ):
        items = [
            b
            for b in gateway.bindings.values()
            if not b.released
            and b.session == session_id
            and b.group_id == group_id
            and ((anonymous and b.worker is None) or (worker_id and b.worker == worker_id))
        ]
        if len(items) != 1:
            raise GatewayError("AMBIGUOUS_SESSION", "请用 worker_id 或 anonymous 明确定位 session")
        return await release(items[0].id, body, request)

    @app.get(prefix + "/operations/{operation_id}", dependencies=[Depends(auth)])
    async def operation(operation_id: str):
        item = gateway.operations.get(operation_id)
        if not item:
            raise HTTPException(404, "操作不存在")
        return asdict(item)

    @app.get(prefix + "/tasks", dependencies=[Depends(auth)])
    async def tasks(
        request: Request, account_id: str = "", group_id: str = "", state: str = "", category: str = ""
    ):
        rows = [{**asdict(op), "category": "operation"} for op in gateway.operations.values()]
        rows += [
            {**asdict(job), "category": "replacement", "kind": "replacement"}
            for job in gateway.replacements.values()
        ]
        rows = [
            r
            for r in rows
            if (not account_id or r.get("account_id") == account_id)
            and (not group_id or r.get("group_id") == group_id)
            and (not state or r["state"] == state)
            and (not category or r["category"] == category)
        ]
        return page(sorted(rows, key=lambda item: item["created_at"], reverse=True), *pagination(request))

    @app.get(prefix + "/events", dependencies=[Depends(auth)])
    async def events(request: Request, account_id: str = "", group_id: str = ""):
        rows = [
            e
            for e in reversed(gateway.events)
            if (not account_id or e.get("account_id") == account_id)
            and (not group_id or e.get("group_id") == group_id)
        ]
        return page(rows, *pagination(request))

    @app.post(prefix + "/sync", dependencies=[Depends(auth)])
    async def sync(request: Request):
        return await operate(request, "sync", {}, gateway.sync_all)

    @app.post(prefix + "/reload", dependencies=[Depends(auth)])
    async def reload(request: Request):
        async def reload_action():
            async with config_lock:
                gateway.reload_accounts()
            await gateway.sync_all()

        return await operate(request, "reload", {}, reload_action)

    @app.get(prefix + "/settings", dependencies=[Depends(auth)])
    async def settings():
        return {
            "values": {
                **gateway.settings.public(),
                "probe_enabled": config.probe_enabled,
                "connection_log_mode": gateway.observer.mode,
                "quality_daily_limit": gateway.quality.global_daily_limit,
                "retention": gateway.observer.retention.model_dump(),
            },
            "source": "持久化配置覆盖默认值",
            "deployment": {
                "socks_host": config.socks_host,
                "socks_port": config.socks_port,
                "proxy_http_host": config.proxy_http_host,
                "proxy_http_port": config.proxy_http_port,
                "http_host": config.http_host,
                "http_port": config.http_port,
                "socks_user": config.socks_user,
                "public_proxy_host": config.public_proxy_host,
                "public_socks_port": config.public_socks_port or config.socks_port,
                "public_http_proxy_port": config.public_http_proxy_port or config.proxy_http_port,
                "cookie_secure": config.cookie_secure,
                "egress_mode": "configured_proxy" if config.egress_proxy else "direct",
                "tunnel_idle_timeout": 0,
                "version": "0.2.1",
            },
        }

    @app.patch(prefix + "/settings", dependencies=[Depends(auth)])
    async def patch_settings(body: SettingsPatch):
        return {"values": await gateway.update_settings(body.model_dump(exclude_none=True))}

    @app.get(prefix + "/settings/storage", dependencies=[Depends(auth)])
    async def storage_status():
        return {
            **await gateway.observer.storage_status(),
            "operations": len(gateway.operations),
            "events": len(gateway.events),
            "protected": "替换任务、席位、额度账本和恢复状态持续保留；事件最多 1000 条",
        }

    @app.post(prefix + "/settings/storage/cleanup", dependencies=[Depends(auth)])
    async def cleanup_storage(request: Request):
        return await operate(request, "history_cleanup", {}, gateway.cleanup_history)

    register_v2(app, gateway, auth, operate)

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "API 不存在")
        root = config.web_dir.resolve()
        file = (root / path).resolve()
        if not file.is_relative_to(root):
            raise HTTPException(404)
        if file.is_file():
            return FileResponse(file)
        if path.startswith("assets/"):
            raise HTTPException(404)
        index = root / "index.html"
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return HTMLResponse(
            "<h1>RapIP</h1><p>Build the console with: cd web &amp;&amp; npm ci &amp;&amp; npm run build</p>",
            status_code=503,
        )

    return app
