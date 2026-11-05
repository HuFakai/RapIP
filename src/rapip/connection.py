"""Build client configuration without allocating nodes or disclosing supplier credentials."""

import ipaddress
import re
from urllib.parse import quote

from .config import Config


def normalize_host(value: str) -> str:
    host = value.strip().removeprefix("[").removesuffix("]")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        try:
            encoded = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise ValueError("请填写有效的服务器 IP 或域名，不包含协议与端口") from None
        if len(encoded) > 253 or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in encoded.split(".")
        ):
            raise ValueError("请填写有效的服务器 IP 或域名，不包含协议与端口")
        return encoded.lower()


def build_connection(
    config: Config,
    host: str,
    worker: str | None,
    session: str,
    country: str | None,
    group: str = "default",
    tunnel: bool = False,
):
    host = normalize_host(host)
    authority = f"[{host}]" if ":" in host else host
    username = config.socks_user
    if country:
        username += f"-country-{country}"
    if group != "default":
        username += f"-group-{group}"
    if worker:
        username += f"-worker-{worker}"
    username += f"-session-{session}"
    if len(username.encode()) > 255:
        raise ValueError("完整代理用户名不能超过 255 字节，请缩短工位或会话 ID")
    socks_port = config.socks_port if tunnel else (config.public_socks_port or config.socks_port)
    http_port = (
        config.proxy_http_port if tunnel else (config.public_http_proxy_port or config.proxy_http_port)
    )
    credentials = f"{quote(username, safe='')}:{quote(config.socks_password, safe='')}"
    socks_server, http_server = f"{authority}:{socks_port}", f"{authority}:{http_port}"
    guide = f"""# RapIP 代理接入说明（可直接交给 AI；不含真实密码或 API Token）

你正在接入一个标准 SOCKS5 / HTTP CONNECT 代理网关。只负责代理配置和 session 生命周期，不假设已有 TCP 能迁移。

服务器：{host}
SOCKS5 端口：{socks_port}；HTTP/HTTPS CONNECT 代理端口：{http_port}
基础用户：{config.socks_user}
代理密码：<PROXY_PASSWORD>（由使用者另行提供，和管理员密码、Webshare Key 不同）
当前资源组：{group}
当前示例用户名：{username}
按组路由：{config.socks_user}-group-{group}-worker-01-session-a1b2c3。省略 group 使用 default 组；不同组资源不混用。

用户名规则：
1. {config.socks_user}-worker-01-session-a1b2c3
2. {config.socks_user}-country-US-worker-01-session-a1b2c3
3. {config.socks_user}-session-a1b2c3（匿名会话，同 session 保持粘性）
4. 兼容全下划线：{config.socks_user}_worker_01_session_a1b2c3
不要混用分隔符。user/worker/session 各 1–64 个 ASCII 字母或数字；完整用户名不超过 255 字节。
country 使用两位国家代码；省略表示任意可用国家。国家参数需要配合 worker 使用。

连接 URL（用户名/密码放入 URL 时分别进行 percent-encoding）：
SOCKS5：socks5://{quote(username, safe="")}:<URL_ENCODED_PROXY_PASSWORD>@{socks_server}
curl 远程 DNS：socks5h://{quote(username, safe="")}:<URL_ENCODED_PROXY_PASSWORD>@{socks_server}
HTTP 代理：https 目标同样填写 http://{quote(username, safe="")}:<URL_ENCODED_PROXY_PASSWORD>@{http_server}
浏览器的结构化 proxy 配置：server=http://{http_server}；username={username}；password=<PROXY_PASSWORD>。

会话与换号：
- 同一 worker + session 的新连接共用出口。不同 worker 相互隔离；匿名 session 以 session 为绑定键。
- 正常健康且未主动轮换时保持出口。节点故障可自动换 IP，原 session 可重连。
- 改变 worker 的 session 会申请新 IP，旧 session 立即失效，不要让迟到请求切回旧 ID。
- 管理页面支持“保留 session 换 IP”，客户端用户名可不变。
- graceful 让旧 TCP 排空，新连接走新出口；hard 立即关闭旧 TCP，由客户端重连。
- 没有符合国家条件的热备时，等待最多 {config.settings.pool_wait_timeout:g} 秒后失败；客户端应有界退避，不抢占其他工位。
- 目标域名交给上游解析；curl 使用 socks5h://。已建立的 TCP/TLS/WebSocket 流不重放、不迁移。
- 标准 Chromium 不支持 SOCKS5 账密时使用 HTTP 代理入口及代理认证。

可选管理 API：管理页面同源 /api/v1，脚本需 Bearer <ADMIN_TOKEN>，不要使用代理密码代替 Token。
POST /api/v1/rotate/<worker_id>?group_id={group}，请求头 Idempotency-Key: <UNIQUE_OPERATION_ID>。
请求体 {{"session_policy":"keep","mode":"graceful"}}，可额外传 expected_generation 防止过期操作。
返回 202 和 operation id；GET /api/v1/operations/<id> 查询结果。重试同一操作使用同一幂等键。
"""
    return {
        "host": host,
        "group_id": group,
        "username": username,
        "socks_port": socks_port,
        "http_port": http_port,
        "links": {
            "socks5": f"socks5://{credentials}@{socks_server}",
            "socks5h": f"socks5h://{credentials}@{socks_server}",
            "http": f"http://{credentials}@{http_server}",
        },
        "structured": {
            "http": {
                "server": f"http://{http_server}",
                "username": username,
                "password": config.socks_password,
            },
            "socks5": {
                "server": f"socks5://{socks_server}",
                "username": username,
                "password": config.socks_password,
            },
        },
        "ai_guide": guide,
    }
