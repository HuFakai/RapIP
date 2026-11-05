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

请作为接入工程师，在现有项目中实现 RapIP 代理配置、分组路由与 session 生命周期，并给出可运行示例、配置项和有界重试逻辑。先阅读当前项目代理入口，优先复用现有客户端；仅在缺少业务约束时提出问题。

下面的地址、端口、分组和示例用户名来自当前管理页面配置。真实凭证由使用者另行注入环境变量；不要向用户索取供应商 Key，不要把凭证写入源码、日志或提交到 Git。不要通过管理 API 自动创建分组、迁移资源或启用自动更换。

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
country 使用两位国家代码；省略表示当前组内任意可用国家。国家参数需要配合 worker 使用。
命名完整顺序：<user>[-country-<CC>][-group-<GROUP>][-worker-<WORKER>]-session-<SESSION>。
group 为 1–32 个 ASCII 字母或数字，ID 与分组显示名称不同；名称可为中文。使用管理页面生成的真实 ID，不要把示例 register 当作已存在的组。分组 ID 可自动生成，创建后在分组详情/连接配置中取得。每个项目使用独立 group，不同进程并行时使用不同 worker。

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

质量筛查与库存语义：
- default 组按基础健康准入；命名质量组只分配属于该组、策略版本匹配且证据未过期的 PASS 资源，不借用其他组。
- 基础可用/稳定优先等级只检查网络；高质量等级需要配置对应检测源权限。UNKNOWN、PENDING、EXPIRED 不代表合格，缺 Key 或检测源限流不得判断为 IP 不合格。
- 质量策略、自动更换开关与替换预算由管理员控制；网络超时不能触发客户端循环调用 rotate。自动更换只针对明确 FAIL，且受账号/全局/分组/本轮/席位预算约束。
- 用完一批任务再切换 session；复用相同 group + worker + session 便于维持出口。多浏览器共享 worker 时，切换前统一停止该工位的新请求，防止旧 session 回流。
- 区分代理连接失败、代理认证失败与目标网站响应。HTTP CONNECT 建立成功不等于目标业务成功。SOCKS5/HTTPS 透传不解密目标 HTTP 状态码，也无法据此判断账号注册结果。

可选管理 API：管理基址使用管理页面的 HTTPS 域名（不是代理端口），路径 /api/v1。脚本需 Bearer <ADMIN_TOKEN>，不要使用代理密码代替 Token。只在明确授权管理操作时使用 Token。
只读查询：GET /api/v1/groups、GET /api/v1/bindings?group_id={group}、GET /api/v1/nodes?group_id={group}&page=1&page_size=20。列表有分页，不能只读第一页就当全部资源。
POST /api/v1/rotate/<worker_id>?group_id={group}，请求头 Idempotency-Key: <UNIQUE_OPERATION_ID>。
请求体 {{"session_policy":"keep","mode":"graceful"}}，可额外传 expected_generation 防止过期操作。
返回 202 和 operation id；GET /api/v1/operations/<id> 查询结果。重试同一操作使用同一幂等键；结果未知时先查原操作，禁止换新幂等键重复提交。
轮换与任务清理可能消耗供应商更换额度，先确认业务已完成及授权范围；读取配置不分配 IP。无热备时保留原绑定并报错；最多有界重试，返回明确错误让调用者决定下一步。

交付要求：
1. 配置 RAPIP_PROXY_SERVER、RAPIP_PROXY_USERNAME、RAPIP_PROXY_PASSWORD；管理 Token 作为可选独立配置。
2. 浏览器优先 HTTP 代理结构化配置 server / username / password；HTTPS 目标的代理 server 仍是 http://。curl 的 SOCKS5 使用 socks5h:// 让上游解析域名。
3. 一个项目固定分组，worker 表示并发工位，session 表示一批任务。session 只使用字母数字，按批次生成并记录，不要每个请求随机切换。
4. 用小规模网络连通性测试验证接入，输出脱敏地址、错误分类和重试次数；不得自动进行真实注册、批量任务、资源迁移或 Replace 测试。
5. 说明 graceful/hard 的连接行为、故障可更换出口、无合格库存时的处理，以及如何安全终止任务。
6. RapIP 的历史日志受管理员保留期限和条数上限影响，成功日志可能未启用；需要持久业务审计时由项目保存脱敏业务记录，不依赖网关无限保留。

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
