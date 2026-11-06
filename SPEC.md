# Webshare 多账号会话轮换网关项目规范

更新日期：2026-11-06。阶段：v0.2.11。

v0.2.11：IP 变化按资源席位查 Webshare 已确认的换号记录；链接变化按工位查历次绑定。事件倒序展示，箭头按旧到新。

v0.2.10：按分组选择新 session 的切换策略；原位替换在供应商对账与准入完成前，合并同一工位的请求。追踪双泳道显示实际事件及耗时。

v0.2.9：分组补货目标按供应商库存选择，保存校验各账号的共同库存。

v0.2.8：各组可独立配置退役 IP 补货规则；代理链接获配和 Webshare 补货分别记录，详情见 [v0.2.8](docs/RELEASE_V0.2.8.md)。

v0.2.6 新增 [代理链接与工位规则](docs/NODE_PROXY_LINKS_PLAN.md)：行内复制立即预留，普通待用链接首次使用才分配。持久化 schema 4，未使用预留取消不调用 Replace。

v0.2.7 保留 IPQuality 检测及开关。Ping0 仅提供当前 IP 的网页入口，不发起服务端请求。

v0.2.5 生命周期以本文及 [升级方案](docs/V0.2.5_UPGRADE_PLAN.md)、[发布说明](docs/RELEASE_V0.2.5.md) 为准：固定配置长期保留、取消空闲回收，定时轮换按组明确开启。

本文件定义第一版的业务行为和验收要求。选型比较见 [技术方案](docs/TECHNICAL_DESIGN.md)，首版页面见 [管理页面规范](docs/ADMIN_UI_SPEC.md)，后续扩展见 [多用户升级方案](docs/MULTI_USER_UPGRADE.md)。

下一版本新增“选择 IP 建立质量组、各组独立准入、失败后有预算地自动替换”的需求，见 [v0.2 升级计划](docs/V0.2_UPGRADE_PLAN.md)及 [IP 质量分组方案](docs/IP_QUALITY_GROUPS_PLAN.md)。该能力已随 v0.2 分组调度实现，旧用户名继续映射到 default 组。本文更新为当前基础生命周期，分组准入、预算与当前交付以质量分组方案及 [v0.2 发布说明](docs/RELEASE_V0.2.md) 为补充。

## 1. 已确定事项与待定事项

### 1.1 用户已确定

- 最低按 5 个上游 IP 投入设计，实际规模暂不确定，后续需要能够继续扩容。
- 热备耗尽时接受有界等待后失败；不得抢占其他活跃会话的 IP，不得复用已弃用 IP 顶替。
- 同时提供平滑切换和强制切换两种连接处理方式。
- 固定模式默认保留同一 session 与原 IP，不因断连、无流量或经过时间而回收；故障时同 session 换 IP 仅作为按组明确开启的可选能力。业务包含浏览器自动化与长连接。
- 首个发布版本配套管理页面；前端采用用户喜欢的 React Bits 组件与视觉方向。
- 第一版由本人业务使用；多用户能力单独规划，为未来升级保留必要边界。
- 已授权按方案开发首版；保留标准 SOCKS5，并增加 HTTP 代理及 HTTPS CONNECT 兼容入口。真实换号已完成一次授权实测，后续新增换号实测另说明额度。
- 当前账号实际同步为 6 个 US IP，250GB/2,500 次额度为用户提供的套餐信息。当前电脑自身使用 Webshare 落地，不能据该嵌套链路的测活结果判断节点损坏。

### 1.2 首版选型与可配置默认值

- 保持 Python 3.11+ 最低要求，具体部署小版本和依赖在实施时锁定。
- 第一版采用单进程、单调度器，asyncio 负责 SOCKS5，FastAPI 负责管理 API，aiohttp 负责供应商 HTTP 请求，SQLite/aiosqlite 负责持久化。
- 管理前端建议 React + TypeScript + Vite + Tailwind CSS，React Bits 按需引入，shadcn/ui 补充表格、表单与对话框基础组件；构建后和后端一起发布。
- 不写死 IP 总量上限；1,000 个受管 IP 作为重新评估与压测的规划节点，不代表已有容量保证。
- 最大保留绑定数与最少备用 IP 数显式配置；默认不隐式扣除 40% 容量。保留备用有助于平滑切换。
- 考虑长连接，建议正常切换默认 graceful、排空上限 120 秒；已触发风控或必须立即撤销旧出口时显式使用 hard。
- 自动故障转移按组配置，默认关闭；开启时保留逻辑 session，已有断开的 TCP 由客户端重建，不承诺流量透明续传。

## 2. 项目目标与保证范围

聚合多个 Webshare 账号的 Static Residential 直连代理，提供统一 SOCKS5 TCP 入口。客户端通过用户名中的 worker、session、country 控制出口绑定。网关从热备池分配节点，在后台更换退出使用的节点。

### 2.1 可承诺的行为

- 有符合国家条件的热备时，前台分配不等待 Replace API；仍有调度、握手和上游建连耗时。
- 同一活跃绑定可承载多条 TCP 连接；健康且未发生显式切换时保持同一出口，不同时分配给其他绑定。
- 固定模式节点故障保留原绑定，原 IP 不可用则提示；只有明确开启本组故障转移时才允许同 session 获得新出口，TCP 不迁移或重放。
- 已分配给业务的节点退出绑定后不直接回到热备池，必须经过上游替换和重新准入。
- 冷却期内禁止分配本系统已弃用的同一 IP，并对受管池内同一 IP 跨账号去重。
- 会话、使用记录、黑名单和替换任务持久化，重启不能把已知使用过的节点直接重置为热备。

### 2.2 保证边界

- 不承诺真实“零延迟”、无限并发、无限持续换号或已有 TCP 流的透明迁移。
- `FRESH` 仅表示本系统当前使用周期尚未分配给业务、通过准入的节点；不代表供应商全局从未使用，也不保证目标网站信誉。
- 冷却过期后同一 IP 可能重新准入；无限期不重复需要不同的历史保留与资源策略。
- 住宅属性、国家供给、套餐流量、替换额度和费用以实际订阅与实测为准，原规模示例不作为权益承诺。

### 2.3 第一版范围

支持 SOCKS5 CONNECT、HTTP 正向代理、HTTPS CONNECT、用户名/密码认证、IPv4/域名/IPv6 目标透传、多账号、绑定、两种切换、健康检查、持久化、管理 REST API 和可操作的单管理员 Web 页面。

不包含 BIND、UDP ASSOCIATE、HTTPS 解密、多用户注册、计费套餐或分布式网关。可以解析和转发 IPv6 目标，不代表上游必定支持 IPv6 出口。

## 3. 资源规模与容量策略

### 3.1 最小投入

5 个可用 IP 可按业务保留备用，也可全部绑定；没有备用时换 session/平滑轮换可能无法完成。默认最大保留绑定数 0 按受管节点数减最少备用数计算，最少备用默认为 0。初始有效资源不足时展示降级原因，不把无效节点计入可用容量。

总节点包含在用、热备、排空、隔离和替换中。50 个总 IP 不等于 50 个工位外加 50 个热备。一个会话多条 TCP 连接通常占用一个节点，连接数与 IP 数分别限流。

### 3.2 扩容与准入

- IP 总量不设固定业务上限，但完整分页同步，不能只读前 100 个。
- 最大保留绑定数为显式上限，0 时按已启用未归档节点数减最少备用数计算（最低 0）；最少备用约束新绑定，不能挤出现有绑定。
- 上限约束新绑定；已有 worker 换 session 仍可消耗剩余热备，避免预留规则阻止切换。
- 匿名 session 同样计入上限；排空旧节点不计为当前活跃绑定，但仍占节点资源。
- 按国家监控库存；指定国家缺货不得静默降级到其他国家。
- 使用共享热备池，不保证每个 worker 独占备用节点。
- TCP 总连接数、单绑定连接数和等待队列长度设可配置上限，默认值通过小规模测试确定。
- 资源不足时保留已有绑定，限制新绑定；供应商 API 故障时继续服务健康已有连接。

平均容量估算：`替换中节点数 ≈ 每秒弃用节点数 × 平均补货耗时`。平滑排空、重试和集中换号需要额外余量，该关系不是并发服务承诺。

## 4. 认证、用户名与身份

### 4.1 格式

```text
{user}-worker-{worker_id}-session-{session_id}
{user}-country-{ISO_CODE}-worker-{worker_id}-session-{session_id}
{user}-session-{session_id}
```

例如 `admin-country-US-worker-01-session-a1b2c3`。兼容全下划线形式，例如 `admin_country_US_worker_01_session_a1b2c3`。

- 一个用户名只使用一种分隔符，不支持混合。
- 第一版 user、worker_id、session_id 使用 ASCII 字母和数字，各 1–64 字符，字段内部不含分隔符。
- country 为大小写不敏感的两位 ASCII 国家代码，归一化为大写；省略时选择任意已启用国家，不触发前台跨国替换。
- 完整用户名不超过 255 字节；缺字段、重复、额外字段和未知顺序直接拒绝。
- 密码是独立的网关凭据，不是 Webshare 密码；认证成功后才能分配节点。
- 第一版固定内部 `principal_id` 标识本人，用户名经认证后映射到主体，不只靠 user 字符串授权。

SOCKS5 用户名/密码认证本身不加密链路。默认在本机或受信网络使用，远程业务副本通过受保护的网络接入，管理端口不默认公开。

### 4.2 绑定与粘性

- 有 worker：工位键为 `(principal_id, worker_id)`，当前绑定含 session、country、节点和 generation。
- 无 worker：绑定键为 `(principal_id, session_id)`，同一匿名 session 必须复用绑定，不能每连接占一个节点。
- 绑定内 country 固定；同一 session 携带不同 country 时拒绝，请创建新 session。
- 不同工位允许相同 session 字符串，管理操作不能只靠 session 字符串唯一定位。
- 记录 last_connection_at、last_traffic_at、active_connections 和最后一条连接关闭时间；运行时超时使用单调时钟，持久期限使用 UTC 时间。

### 4.3 迟到请求与并发

- 被替代或 kill 的 session 写入失效记录，迟到连接拒绝，不能把工位切回旧 session。
- 失效键包含主体和工位命名空间，匿名 session 使用独立命名空间；建议保留 24 小时，每次轮换生成新的随机 ID。
- 同工位切换串行化；相同新 session 的并发请求合并结果，不重复占 IP。
- 不同新 session 同时竞争同工位时按接受顺序处理，后者可能立即替代前者；业务端应由一个控制者驱动工位，严格并发控制通过管理接口预期版本实现。

## 5. 节点生命周期与准入

| 状态 | 含义 | 新业务连接 |
| --- | --- | --- |
| `FRESH` | 已准入、本使用周期尚未绑定业务 | 可原子分配给一个绑定 |
| `IN_USE` | 归属一个当前绑定 | 仅限该绑定 |
| `DRAINING` | 已退出当前绑定，等待旧隧道结束 | 禁止 |
| `QUARANTINED` | 验证中、暂时故障、碰撞、账号异常或结果待核对 | 禁止 |
| `REPLACING` | 旧节点退出服务，持久化替换任务处理中 | 禁止 |

典型路径为 `QUARANTINED → FRESH → IN_USE → DRAINING/REPLACING`。替换后旧节点归档，新节点以新使用周期经 `QUARANTINED → FRESH` 准入。

- 节点身份至少包含 account_id、供应商标识（若提供）和本地 generation；物理 IP 用于去重，不作为所有历史记录的唯一主键。
- 初次导入未知 IP 先验证；重启发现已使用、已弃用或结果不明节点不能直接置 FRESH。
- 从未分配的 FRESH 暂时故障可隔离复测后恢复；已分配过的节点不能仅凭复测返回 FRESH。
- 测活提交结果时复核 generation 和当前状态，不能用过期结果处理已经被分配的节点。
- 冷却默认 21,600 秒，在弃用时写入；排空中及当前池内 IP 同样禁止跨账号重复分配。
- 碰撞后最多额外重抽 3 次，计入预算；超过上限保持隔离并告警，不能退让为可用。
- 碰撞时优先保留已在用节点，隔离未分配副本，不关闭已有健康会话。

## 6. 分配、切换与回收

### 6.1 首次连接与资源不足

绑定限额允许时，从符合国家的 FRESH 集合选择节点并原子建立独占绑定。先持久化已使用事实，再允许向业务转发。

无合格热备或绑定限额已满时有界等待，默认总期限 5 秒。队列满立即拒绝；超时返回 SOCKS5 一般失败，内部记录 POOL_EXHAUSTED 或 BINDING_LIMIT_REACHED，不扩展私有协议回复码。

不得抢占其他绑定、回收活跃节点或复用黑名单 IP。等待调度需有界公平，避免新绑定或已有工位切换长期饥饿。

### 6.2 两种切换

| 模式 | 旧连接 | 补货时机 |
| --- | --- | --- |
| `graceful` 平滑 | 旧连接代次禁止新增，已有连接排空，建议最长 120 秒 | 连接归零或期限到达并关闭后 |
| `hard` 强制 | 撤销旧绑定并关闭已有连接 | 关闭后立即排队替换 |

用户名换 session 使用 SESSION_SWITCH_MODE，本稿建议默认 graceful；管理接口允许按次指定。换 session 时旧 session 立即失效；TCP 流不能迁移，客户端需重建连接。风控强制切换使用 hard。

管理页面还须提供“保留 session 换 IP”，供不方便修改客户端代理用户名的长连接业务使用。该操作仅更新绑定 generation，不失效当前 session；新连接走新 IP，旧 generation 按 graceful/hard 处理。graceful 期间同一逻辑 session 可能暂时存在新旧两个出口，页面明确显示排空状态。

建议每个绑定最多保留一个排空中的旧 generation。再次平滑切换前若旧代次尚未排空，返回忙碌/冲突，不静默关闭它；用户可显式强制切换。排空需要额外库存，小池无法保证集中切换都能立即完成。

用户名触发切换先等待并保留新节点，再提交新绑定和旧节点退出。等待失败保留原绑定；提交后新建连失败不自动复活旧 session，按故障转移规则处理。

显式风控操作可以 release，不等待新库存就关闭旧连接并启动替换，适用于立即停止使用旧出口。

### 6.3 长期绑定与可选定时轮换

固定模式取消空闲回收。无连接数小时/数天后继续保留 session、原 IP 与独占归属，不需要保活；只有成功改 session 或显式人工操作才主动换 IP。空闲保留计入绑定容量，不能自动抢占。

质量组可单独启用定时轮换，保持客户端配置和 session 不变。必有有限每日/本次启用总额度，0 表示暂停；还受账号、分组和供应商余额限制。首次成功建连开始计时；无连接超过设定时长只暂停计划，不释放 IP；重连先用保留 IP，再计完整周期，不补执行错过次数。每个绑定独立判定活动，重启和普通保存不重置额度。

执行前预留旧节点补货预算；无合格备用则保持当前出口。已有一代排空或组内补货/对账未完成时不重复轮换。质量失败资源不进入待分配池。显式替代/释放的旧 session 仍有防重放记录，与当前 session 永不因空闲过期独立。

安静但仍建立的长连接不由此机制关闭；隧道无流量超时建议默认禁用，不设默认最大连接寿命。TCP keepalive 可配置，用于发现部分失效链路，不替代业务心跳或重连。

## 7. Webshare 同步与替换

### 7.1 API 合约

- 列表：`GET https://proxy.webshare.io/api/v2/proxy/list/?mode=direct&page=1&page_size=100`，跟随分页完整读取，page_size 以实际支持为准。
- 鉴权：`Authorization: Token {API_KEY}`。
- 提取 proxy_address、port、username、password、valid、country_code 及可获得的稳定节点标识。
- 创建：`POST https://proxy.webshare.io/api/v3/proxy/replace/`。
- 查询：`GET https://proxy.webshare.io/api/v3/proxy/replace/{id}/`。
- 账号有多个套餐时明确 plan_id，列表和替换针对同一套餐，具体传参在联调时核对。

```json
{
  "to_replace": {
    "type": "ip_address",
    "ip_addresses": ["198.51.100.10"]
  },
  "replace_with": [
    {"type": "country", "country_code": "US", "count": 1}
  ],
  "dry_run": false
}
```

不限国家时为 `[{"type": "any", "count": 1}]`。默认补回旧节点原国家，保持库存结构；改变国家配置只影响后续补货，不主动烧掉可用节点调配库存。

任务状态包含 validating、validated、processing、completed、failed；failed 读取 error/error_code，completed 后仍需同步验证，不能直接当作热备。

### 7.2 持久化任务与对账

- 不同账号受控并行；同账号从快照、提交、轮询到列表对账串行，账号同步共享协调机制。
- 记录本地操作 ID、旧 generation、原因、上游任务 ID、阶段、尝试次数和 next_retry_at。
- 旧节点退役与本地任务创建在同一事务提交，然后才能执行外部请求。
- POST 前记 SUBMITTING 意图；超时或崩溃可能已经创建任务，必须先核对替换历史与列表。无法唯一确认则保持待核对，不盲目再 POST。
- 建议观察窗口约 20 秒、初始轮询间隔 1.5 秒，之后退避继续跟踪。观察超时不等于失败，不能据此再创建任务。
- 差集只是候选识别，结合数量、国家、节点标识、任务结果和账号快照核对；无法归属的结果保持隔离。
- 供应商自动替换或人工修改会破坏简单差集假设，建议每 5 分钟错峰全量对账。

### 7.3 限流和额度

- 启动请求加入 0.3–0.5 秒抖动，同时有全局/账号级并发限制、429/Retry-After 处理和抖动退避，不能只靠启动错峰。
- 只读请求可分类重试，替换 POST 不使用无条件自动重试。
- 分别统计创建尝试、成功任务、成功替换 IP、碰撞重抽和可靠获取的供应商余额；本地计数不冒充实际扣费。
- 每账号可设预算、已知额度与周期；不足时停止继续替换并告警，旧节点保持不可复用。
- 认证失败、额度耗尽和套餐限制分别标记，不能产生无限更换循环。

## 8. 健康检查与故障转移

- 新节点准入检查 SOCKS5 和受控目标；FRESH 巡检建议间隔 900 秒、单次超时 5 秒、并发 10，均可配置。
- 测活复用上游 SOCKS5 适配器，目标域名由上游解析；普通 aiohttp HTTP 会话不直接当作 SOCKS5 传输。
- 测活终点配置化，设第二个独立终点复核；大范围关联故障先检查本机网络或终点，暂停批量烧号。
- 单次失败先隔离复测，节点级持续故障才替换；阈值建议连续 2 次。
- 上游认证失败优先暂停账号、刷新凭据；目标站 CONNECT 拒绝不单独证明节点损坏。
- 仅在回复客户端 SOCKS5 成功前有限重试，默认最多换一个节点；不重放已开始的业务流量。
- 本组 fault_failover 默认 false，明确开启后：确认节点故障后关闭受影响的旧隧道，保留 session 身份，合并分配新节点，后续同 session 重连走新出口，并记录原因、时间和 generation。
- 热备不足时逻辑绑定显示“等待可用出口”，每次重连依旧有界等待；不能让每个重连请求各自创建一份替换任务。session 长期保留，不因无连接过期。
- 单条长连接 EOF、目标端关闭或 reset 不单独证明代理节点故障；节点级熔断仍需上游错误分类和复核。
- 同绑定并发故障合并一次转移，更新 generation，不能每条失败连接各烧一个 IP。

## 9. 持久化、恢复与关闭

建议内存索引 + SQLite/aiosqlite，保存节点周期、绑定、失效 session、冷却、替换任务和额度事件。配置仍用 JSON，数据库与配置挂持久卷。

- 关键事务成功后才发布状态；存储失败停止新分配、切换和替换提交，保留可转发的已有隧道并告警。
- 一个调度所有者，显式串行写事务；异步数据库不自动解决业务事务交错。
- 重启先恢复持久化节点、绑定和任务并核对上游；IN_USE 节点保持归属与原 IP，不因重启退役。旧 TCP 不能恢复，DRAINING 旧代次因连接已结束而完成既有退役。
- 当前逻辑 session 与原 IP 长期恢复，无 300 秒重连窗口。供应商原出口不存在时显式显示不可用，不挪用另一节点；明确替代/释放的旧 session 不复活。
- 重启本身不为当前 IN_USE 出口创建替换；已提交任务、排空中的旧出口仍需对账/完成补货，不能丢弃。
- 使用适当事务与同步设置，评估 WAL；不把 SQLite 放共享网络盘，日志与备份纳入凭据保护。
- 退出时停止新绑定，有界排空，保存任务，关闭隧道、HTTP 会话和数据库。
- 禁止多个实例或多个 HTTP worker 同时管理同一池，升级路径另见文档。

## 10. HTTP 管理接口

管理默认端口 8080（当前本机改用 18080），同源提供管理页面和 API；API 统一加 `/api/v1` 前缀，下表列出相对该前缀的路径。默认本机/私网访问。

脚本使用管理 Bearer Token；页面使用独立单管理员登录及服务端会话，Cookie 为 HttpOnly、SameSite，HTTPS 部署启用 Secure；写请求验证 CSRF/Origin。不将管理 Token 或上游密钥保存在浏览器 localStorage。单管理员不等于多用户系统。

除不暴露详情的存活检查及登录入口外均需鉴权，不向浏览器返回 API Key 或上游密码。页面规范另见独立文档。

| 接口 | 语义 |
| --- | --- |
| `GET /health/live` | 进程存活，不因暂时缺热备判死 |
| `GET /health/ready` | 初始化、存储与调度可操作；库存不足单独展示 |
| `POST /auth/login`、`POST /auth/logout`、`GET /auth/me` | 单管理员登录、退出与当前身份 |
| `GET /stats` | 状态、账号/国家库存、队列、错误、预算与容量 |
| `POST /connection-config` | 鉴权后按需生成完整带密码链接及不含真实凭据的 AI 接入说明，不分配节点 |
| `GET /accounts`、`POST /accounts`、`PATCH /accounts/{account_id}` | 查看/新增账号，更新预算与新分配开关，校验后生效 |
| `GET /nodes`、`GET /events` | 分页筛选节点和脱敏事件 |
| `GET /settings`、`PATCH /settings` | 查看配置、更新明确允许热改的参数 |
| `GET /bindings` | 分页查看版本、连接数、最近活动与节点归属 |
| `GET /operations/{operation_id}` | 查询控制操作结果 |
| `POST /rotate/{worker_id}` | 默认保留 session 换 IP，也可显式指定新 session，选择 graceful/hard |
| `POST /bindings/{binding_id}/rotate` | 以稳定 ID 轮换，包括匿名 session |
| `POST /bindings/{binding_id}/release` | 不等新库存，使绑定失效并退出，可选 graceful/hard |
| `POST /kill/{session_id}` | 兼容入口，须 worker_id 或匿名标记；歧义为 409 |
| `POST /reload` | 校验后热加载，首版仅新增及不改变资源归属的配置更新 |
| `POST /sync` | 触发受控同步，返回操作 ID |

rotate 示例：

```json
{
  "session_policy": "keep",
  "mode": "graceful",
  "expected_generation": 12
}
```

- 控制操作支持 Idempotency-Key，主体和键相同且请求相同返回同一操作；同键不同请求为 409。幂等结果持久化，建议保留 24 小时。
- 受理返回 202 和 operation_id，不宣称已切换；无库存沿用 5 秒期限，分配失败保留旧绑定。
- expected_generation 为可选并发条件；失配返回或记录冲突。release 不要求新库存。
- session_policy 为 keep/new；new 必须提供 new_session_id，并使旧 session 失效。keep 不允许同时传入新 session，避免歧义。
- 账号“暂停新分配”仅阻止该账号新节点准入业务，保留健康在用连接和必要的已提交任务；不隐式批量烧号。首版不做账号删除或跨套餐迁移。
- 不支持的账号移除、改套餐等热更新明确拒绝，不悄悄停止已有账号。

统计至少包含五类状态、各国热备、等待数、隧道数、账号健康、替换延迟/失败、冷却命中和故障转移。状态总数不混入归档旧节点。

## 11. 建议配置默认值

以下为待实测建议，可覆盖，凭据不提交代码仓库。

| 参数 | 默认建议 | 说明 |
| --- | --- | --- |
| SOCKS_PORT / HTTP_PORT | 1080 / 8080 | TCP / 管理入口，本机管理端口为 18080 |
| PROXY_HTTP_PORT | 8888 | HTTP 代理/HTTPS CONNECT，区别于管理入口 |
| POOL_WAIT_TIMEOUT | 5 秒 | 分配与切换总等待 |
| SESSION_SWITCH_MODE | graceful | 用户名变更行为；风控显式 hard |
| DRAIN_TIMEOUT | 120 秒 | 最大排空时间 |
| TUNNEL_IDLE_TIMEOUT | 0（禁用） | 不因长连接安静而关闭 |
| RECOVERY_RECONNECT_WINDOW | 已移除 | 当前 session 与原 IP 长期恢复 |
| SESSION_IDLE_TIMEOUT | 已移除 | 空闲不释放 IP，不让 session 过期 |
| MAINTENANCE_INTERVAL | 10 秒 | 排空、轮换及有界历史维护，不回收空闲绑定 |
| BLACKLIST_TTL | 21,600 秒 | IP 冷却 |
| RETIRED_SESSION_TTL | 86,400 秒 | 拒绝旧 session |
| PROBE_INTERVAL / PROBE_TIMEOUT | 900 / 5 秒 | 巡检与超时 |
| PROBE_CONCURRENCY | 10 | 巡检并发 |
| SYNC_INTERVAL | 300 秒 | 列表对账 |
| MAX_FAIL_COUNT | 2 | 节点失败阈值 |
| MAX_COLLISION_REDRAWS | 3 | 碰撞后额外替换 |
| fault_failover（每组） | false | 明确开启才允许故障时同 session 换 IP |
| MAX_ACTIVE_BINDINGS / MIN_SPARE_IPS | 0 / 0 | 自动容量 / 最少备用，见容量策略 |

`config/accounts.json` 每账号含稳定 account_id、API Key 引用、可选 plan_id、默认补货国家、enabled 和可选预算，提供无真实密钥的 example 文件。

## 12. 建议工程结构

```text
RapIP/
├── SPEC.md
├── docs/
│   ├── TECHNICAL_DESIGN.md
│   ├── ADMIN_UI_SPEC.md
│   └── MULTI_USER_UPGRADE.md
├── web/                  # React 管理页面，构建静态资源
├── config/accounts.example.json
├── src/rapip/
│   ├── config.py
│   ├── domain/           # 节点、绑定、操作、领域规则
│   ├── scheduler.py      # 唯一调度所有者
│   ├── storage/          # 事务、迁移、恢复
│   ├── providers/        # Webshare API 适配
│   ├── replacement.py    # 补货与对账
│   ├── inspector.py
│   ├── socks/            # 协议、握手、隧道
│   ├── http_api/         # FastAPI、鉴权、接口模型、静态页面
│   └── main.py
├── tests/
├── pyproject.toml
├── Dockerfile
└── compose.yaml
```

目录已按首版职责实现；具体文件以当前源码为准。

## 13. 实施与验收

1. **供应商验证**：确认套餐、直连 SOCKS5、分页、dry_run、额度与任务结果；真实替换留到实施阶段。
2. **单账号闭环**：5 个 IP，验证认证、独占、切换、排空与补货，测量替换耗时。
3. **可靠性**：假上游覆盖竞争分配、迟到请求、缺货、提交超时、重启、存储失败、列表漂移和测活站故障。
4. **多账号与扩容**：验证重复 IP、额度耗尽、限流；逐步压测带宽、隧道、内存、事件循环和替换吞吐。
5. **首版管理页面**：管理员登录、真实库存/连接状态、保留 session 的两种换 IP、账号新增、任务追踪、脱敏日志及配置编辑闭环；轮询刷新不能重复提交控制操作。

验收重点：不重复分配、不抢占、有界失败；两种切换和 session 身份规则；长时间静默连接不被误杀；故障后同 session 重连获取新出口；不盲目重复替换；重启不误入热备；域名上游解析；页面与真实状态一致，凭据与权限不泄漏。

## 14. 官方参考与待验证前提

- [创建替换任务](https://apidocs.webshare.io/proxy-replacement/proxy_replacement/proxy_replacement_create)
- [替换对象、状态与自动替换原因](https://apidocs.webshare.io/proxy-replacement/proxy_replacement)
- [查询替换任务](https://apidocs.webshare.io/proxy-replacement/proxy_replacement/proxy_replacement_retrieve)
- [SOCKS5 RFC 1928](https://www.rfc-editor.org/rfc/rfc1928)
- [认证 RFC 1929](https://www.rfc-editor.org/rfc/rfc1929)

尚未以真实账号验证权益、限流、替换延迟、列表一致性或 IPv6；默认值需在闭环中校准。

## 15. 首版实现与验证边界

- 管理页面、API、SOCKS5、HTTP/HTTPS CONNECT、持久化调度、两种轮换和补货队列已实现。
- 当前采用单所有者 SQLite 状态快照，关键状态变更在同一调度事务锁内落盘，锁内不等待外部网络。这是当前小规模的一致性取舍；大规模应迁移为增量事务并压测。
- 任务提交结果不明时保持 UNCERTAIN，阻止同账号后续替换，需供应商后台核对；首版不提供自动猜测结果的重试。
- 健康检查整体失败仅隔离，不批量消耗额度；单节点重复失败且有健康同伴时才自动补货。
- 可选 RAPIP_EGRESS_PROXY 只配置到达供应商节点的网络跳转，默认直连；不得把当前 Webshare 落地再次当作嵌套连通性验收依据。
- 后端自动测试及常规页面验证使用模拟上游；另已在 Linux ARM64 服务器完成 Docker 部署、6 节点真实测活、SOCKS5/HTTP CONNECT 同会话转发与 1 次授权的真实 Replace 验收。替换计数为 submitted=1、completed=1、redraws=0；长期运行及容量压测仍待验证，见 docs/VALIDATION.md。

## 16. 部署实测前的体验更新

- 默认冰蓝主题，设置中可切换紫罗兰、琥珀、玫瑰；主题为本浏览器偏好，不影响代理行为。
- 出口卡片与节点列表使用本地 SVG 国旗。未知国家使用地球图标。
- 专用鉴权连接接口返回 percent-encoded 的带密码 SOCKS5、SOCKS5h、HTTP 链接，仅在连接弹窗按需请求，不写入前端构建或持久存储。
- AI 接入说明包含当前服务器、用户名语法与 session 规则，但用占位符代替真实密码和管理 Token，便于复制分享。
- 主动测活可在设置中即时开启/关闭并持久化；已有设置优先于环境初值。关闭不影响已建立连接，也不把未验证节点变为可用。
- 管理、SOCKS5、HTTP 代理端口均由环境变量驱动，Compose 监听、发布与健康检查保持一致；NAT/域名通过 PUBLIC_* 参数描述。
- 服务器流程见 [部署文档](docs/DEPLOYMENT.md)，接入规范见 [客户端文档](docs/CLIENT_INTEGRATION.md)。
