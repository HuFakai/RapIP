# RapIP

当前版本：**v0.2.2**。紧凑总览、服务端历史指标、质量分组与连接记录已实现；使用说明见 [v0.2 发布说明](docs/RELEASE_V0.2.md)。

带私有管理页面的 Webshare 多账号代理网关。SOCKS5 与 HTTP/HTTPS CONNECT 共用独占 session、冷却与持久化补货队列。

项目仓库：[HuFakai/RapIP](https://github.com/HuFakai/RapIP)。服务器部署请直接阅读 [实际部署与验收](docs/DEPLOYMENT.md)。

## Docker 部署（推荐）

服务器需要 Git、Docker/Compose、OpenSSL 和 `ss` 或 `lsof`，无需宿主机 Python、Node 或 uv。首次部署：

```sh
git clone https://github.com/HuFakai/RapIP.git
cd RapIP
bash scripts/deploy.sh
```

脚本自动生成三份独立的 48 位随机凭据，排除系统监听与 Docker 发布端口，从 `20000–29999` 选择三个空闲端口，构建并等待容器健康。成功后输出地址、端口、凭据、完整代理链接和 SSH 隧道命令，另存于私有的 `data/DEPLOYMENT_ACCESS.txt`。无需提前复制或编辑 `.env`，首次在页面添加上游账号即可。

部署后更新/重启仍使用同一个命令：

```sh
bash scripts/deploy.sh
```

已有 `.env` 时保留密码、端口、账号和数据，自动拉取源码后重新构建并重建容器。只使用本地源码、不拉取时运行 `bash scripts/deploy.sh --no-pull`。更新会断开已有长连接，恢复时可能按规则创建补货任务。

默认只发布到服务器本机，使用输出的 SSH 命令访问；脚本不自动开放防火墙。三个端口通过 `RAPIP_HTTP_PORT`、`RAPIP_SOCKS_PORT`、`RAPIP_PROXY_HTTP_PORT` 配置，`.env.example` 与新生成 `.env` 均有逐项中文说明。终端生成 32 位随机字母数字可用 `openssl rand -hex 16`。手工部署、远程直连、备份及实测步骤见 [部署文档](docs/DEPLOYMENT.md)。

Node 仅参与镜像构建，生产 Python 容器提供页面、API 和两个代理入口。`config/`、`data/` 为持久目录，停止宽限期为 150 秒；不要同时运行本机进程和容器管理同一数据目录。

## 本地开发运行（可选）

需要 Python 3.11+、[uv](https://docs.astral.sh/uv/) 和 Node 22.12+（或兼容 Vite 的更新版本）。

```sh
python3 scripts/setup.py
uv sync --extra dev
cd web
npm ci
npm run build
cd ..
uv run rapip
```

首次设置会生成不同的管理员密码、代理密码和脚本 Token，写入权限为 600 的 `.env`，不会覆盖已有配置。打开管理页面后，在“上游账号”添加 API Key；也可参照 `config/accounts.example.json` 配置账号及密钥环境变量。API Key、账号配置、数据及凭据均已排除版本控制与 Docker 构建上下文。

默认管理地址 `http://127.0.0.1:8080`，SOCKS5 为 `127.0.0.1:1080`，HTTP 代理为 `127.0.0.1:8888`。若端口被占用，在 `.env` 修改 `RAPIP_HTTP_PORT` 等对应参数。当前本机已配置的管理端口为 **18080**；本机登录信息可查阅 `data/ACCESS.md`。

## 客户端接入

两种入口使用同一代理密码 `RAPIP_SOCKS_PASSWORD`，用户名例如：

```text
admin-worker-01-session-first
admin-country-US-worker-01-session-first
admin-session-first
```

字段仅支持字母数字，兼容全下划线分隔。客户端连接到同一 worker/session 时共用出口。域名通过上游解析。

管理页“查看连接配置”可直接生成、复制**带代理密码的完整 SOCKS5 / HTTP 链接**，特殊字符会正确编码。填写工位、session、国家及客户端可达的服务器地址即可，生成配置不会分配 IP。独立的“AI 接入说明”可一键复制用户名命名规范、会话语义与接口用法，其中密码和管理员 Token 使用占位符。文档版见 [客户端命名与接入规范](docs/CLIENT_INTEGRATION.md)。

浏览器自动化可使用 HTTP 入口及普通代理认证，HTTPS 通过 CONNECT 转发；浏览器不支持带账密的 SOCKS5 时无需改变网关会话规则。HTTP 明文请求采用单请求连接，HTTPS CONNECT 和 SOCKS5 隧道支持长连接。HTTP 请求体上限 64MiB，不支持 HTTP/2 明文代理协议、UDP 或 SOCKS5 BIND。

建议每批浏览器任务结束并关闭相关连接后，在“工位与会话”使用“保留 session 换 IP”。平滑模式允许旧连接最多排空 120 秒；强制模式关闭旧连接。新出口不会接管原 TCP/TLS 流，客户端需要重连。节点级故障转移保留 session，普通目标端断连不会直接烧号。

## 管理与额度

- 总览、工位、IP 池、质量分组、连接记录、账号、任务/事件、设置共八个模块，页面只读取后端真实状态。
- 设置中提供冰蓝（默认）、紫罗兰、琥珀、玫瑰四套主题，按浏览器保存；国家旗帜为本地按需加载的 SVG，不依赖外部图片服务。
- 普通浏览器使用 HttpOnly Cookie、CSRF 与 Origin 检查；脚本使用 `Authorization: Bearer <RAPIP_ADMIN_TOKEN>`。
- 控制操作必须提供 `Idempotency-Key`，返回 operation_id；结果通过 `/api/v1/operations/{id}` 获取。
- `/api/v1/openapi.json` 是鉴权后的接口定义；静态页面不会吞掉 API 404。
- `/api/v1/connection-config` 是鉴权并校验 CSRF 的专用 POST 接口，按需返回含网关代理密码的链接，不返回 Webshare Key 或上游节点密码，不缓存结果。
- 本地预算是网关提交计数，**不代表供应商实际剩余额度**。流量套餐大小是手动配置，不展示虚构的供应商实时已用流量。
- 替换可能真实消耗额度。运行网关后，已用节点的轮换、释放、空闲回收或重启恢复都会产生补货任务。
- 提交超时的任务标为 UNCERTAIN，同账号后续补货暂停以免重复提交；需要在供应商后台核对后处理。首版不提供盲目重试按钮。
- 每账号先串行替换，跨账号并行；无合格热备默认等待 5 秒后失败，不抢占其他连接。

默认只监听本机。远程使用时通过受保护网络接入，管理页面通过 HTTPS；端口监听与 Cookie Secure 配置见 `.env.example`。多个实例不能共享同一池，进程锁会拒绝同数据目录的第二个实例。

### 网络与只读诊断

当前电脑本身使用 Webshare 静态住宅落地，嵌套链路的握手失败不能证明供应商节点损坏。本机管理页可能显示 6 个节点隔离、0 个可分配，这是未通过当前路径测活的真实结果，不会触发整池自动更换。

为避免在当前嵌套网络重复测试，本机已关闭主动测活，仍可同步列表；未验证节点不能分配。部署到独立可用服务器后，在“网关设置 → 主动测活”打开开关，立即验证未使用节点，无需重启。开关持久化，重启后保留；`RAPIP_PROBE_ENABLED` 仅是没有持久设置时的初始默认值（新环境默认 true）。

在能够直连节点的独立服务器或其他已知可用网络运行：

```sh
uv run python -m rapip.diagnostics --list-only
uv run python -m rapip.diagnostics
# 容器内同样可运行：
docker compose exec gateway python -m rapip.diagnostics
```

该命令只列资源或测活，不建立业务绑定、不调用 Replace API、不改变网关状态。需要额外网络跳转时显式设置 `RAPIP_EGRESS_PROXY`，默认不继承系统代理。Docker 使用单独的 `RAPIP_CONTAINER_EGRESS_PROXY`；容器内的 127.0.0.1 不是宿主机，不应直接复用本机代理地址。

## 开发与验证

```sh
uv run pytest -q
uv run ruff check src tests scripts
cd web
npm test
npm run build
```

后端测试使用本地模拟供应商和代理，不调用真实 Replace API。前端开发可运行 `npm run dev`，通过 Vite 将 `/api` 转发到后端（默认 8080，可在 vite.config.ts 调整）。

需要可操作的隔离页面测试环境时，运行 `uv run python tests/ui_server.py`，打开 `http://127.0.0.1:18081`，使用 `admin` / `qa-password-only`。该进程仅监听本机，使用临时目录与文档示例 IP，停止后删除测试状态，不包含真实密钥或对供应商的请求。

持久化采用单所有者状态快照事务，适合当前小规模部署。达到更大连接数或 IP 规模时需压测；当前未承诺 1,000 个 IP 或指定带宽吞吐。

## 文档

- [v0.2 升级计划：紧凑总览、运行观测与质量分组](docs/V0.2_UPGRADE_PLAN.md)
- [IP 质量分组与自动准入方案](docs/IP_QUALITY_GROUPS_PLAN.md)
- [项目规范](SPEC.md)
- [技术方案](docs/TECHNICAL_DESIGN.md)
- [管理页面](docs/ADMIN_UI_SPEC.md)
- [多用户升级](docs/MULTI_USER_UPGRADE.md)
- [第三方组件来源](THIRD_PARTY_NOTICES.md)
- [验证记录与限制](docs/VALIDATION.md)
- [实际部署与验收](docs/DEPLOYMENT.md)
- [客户端命名与接入规范](docs/CLIENT_INTEGRATION.md)

### 使用体验与历史数据管理

- [分组创建向导、状态配色与数据生命周期](docs/UX_AND_RETENTION.md)
- [免费检测与开源 IPQuality 脚本复核方式](docs/FREE_IP_QUALITY.md)

- v0.2.2：资源池支持逐 IP 的 IPQuality 数据库参考报告；分组展示名称和 ID；吞吐与累计流量自动适配 B/KB/MB/GB 等单位。报告与质量组准入规则独立，详见 [免费检测与模块说明](docs/FREE_IP_QUALITY.md)。
