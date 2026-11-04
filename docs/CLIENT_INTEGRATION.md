# RapIP 代理接入说明（可直接交给 AI；不含真实密码或 API Token）

你正在接入一个标准 SOCKS5 / HTTP CONNECT 代理网关。只负责代理配置和 session 生命周期，不假设已有 TCP 能迁移。

服务器：proxy.example.com
SOCKS5 端口：1080；HTTP/HTTPS CONNECT 代理端口：8888
基础用户：admin
代理密码：<PROXY_PASSWORD>（由使用者另行提供，和管理员密码、Webshare Key 不同）
当前示例用户名：admin-worker-01-session-a1b2c3

用户名规则：
1. admin-worker-01-session-a1b2c3
2. admin-country-US-worker-01-session-a1b2c3
3. admin-session-a1b2c3（匿名会话，同 session 保持粘性）
4. 兼容全下划线：admin_worker_01_session_a1b2c3
不要混用分隔符。user/worker/session 各 1–64 个 ASCII 字母或数字；完整用户名不超过 255 字节。
country 使用两位国家代码；省略表示任意可用国家。国家参数需要配合 worker 使用。

连接 URL（用户名/密码放入 URL 时分别进行 percent-encoding）：
SOCKS5：socks5://admin-worker-01-session-a1b2c3:<URL_ENCODED_PROXY_PASSWORD>@proxy.example.com:1080
curl 远程 DNS：socks5h://admin-worker-01-session-a1b2c3:<URL_ENCODED_PROXY_PASSWORD>@proxy.example.com:1080
HTTP 代理：https 目标同样填写 http://admin-worker-01-session-a1b2c3:<URL_ENCODED_PROXY_PASSWORD>@proxy.example.com:8888
浏览器的结构化 proxy 配置：server=http://proxy.example.com:8888；username=admin-worker-01-session-a1b2c3；password=<PROXY_PASSWORD>。

会话与换号：
- 同一 worker + session 的新连接共用出口。不同 worker 相互隔离；匿名 session 以 session 为绑定键。
- 正常健康且未主动轮换时保持出口。节点故障可自动换 IP，原 session 可重连。
- 改变 worker 的 session 会申请新 IP，旧 session 立即失效，不要让迟到请求切回旧 ID。
- 管理页面支持“保留 session 换 IP”，客户端用户名可不变。
- graceful 让旧 TCP 排空，新连接走新出口；hard 立即关闭旧 TCP，由客户端重连。
- 没有符合国家条件的热备时，默认等待最多 5 秒后失败；客户端应有界退避，不抢占其他工位。
- 目标域名交给上游解析；curl 使用 socks5h://。已建立的 TCP/TLS/WebSocket 流不重放、不迁移。
- 标准 Chromium 不支持 SOCKS5 账密时使用 HTTP 代理入口及代理认证。

可选管理 API：管理页面同源 /api/v1，脚本需 Bearer <ADMIN_TOKEN>，不要使用代理密码代替 Token。
POST /api/v1/rotate/<worker_id>，请求头 Idempotency-Key: <UNIQUE_OPERATION_ID>。
请求体 {"session_policy":"keep","mode":"graceful"}，可额外传 expected_generation 防止过期操作。
返回 202 和 operation id；GET /api/v1/operations/<id> 查询结果。重试同一操作使用同一幂等键。

实际部署时可在管理页面“查看连接配置 → AI 接入说明”复制包含真实服务器地址的版本。完整带密码链接在“连接链接”标签单独复制。
