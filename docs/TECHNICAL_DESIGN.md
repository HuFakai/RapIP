# 技术方案与框架选型讨论稿

更新日期：2026-11-05。业务约束以 [SPEC.md](../SPEC.md) 为准，管理功能见 [管理页面规范](ADMIN_UI_SPEC.md)。已确认长连接、同 session 自动故障转移、首版配套管理页面及 React Bits 视觉偏好；当前已按推荐组合实现首版。

## 1. 推荐结论

第一版建议：**asyncio SOCKS5 + FastAPI 管理 API + aiohttp 供应商客户端 + SQLite/aiosqlite + React/TypeScript/Vite 管理页面**。

前端采用 Tailwind CSS、按需引入 React Bits，使用 shadcn/ui 基础组件补齐后台交互。首版单进程、单调度器，Python 3.11+；类型注解/dataclass 表达领域对象，API 模型负责边界校验，不将 HTTP 模型耦合到调度器。

首版已有页面和较多管理操作，FastAPI 的接口模型与 OpenAPI 更有价值，因此本稿将其从候选提升为推荐。它不替代 SOCKS5 通路，也不自动提高 TCP 转发性能。暂不引入任务中间件、ORM、Redis 或多进程 worker。

## 2. 候选比较

| 方案 | 适合场景 | 代价与限制 | 判断 |
| --- | --- | --- | --- |
| asyncio + FastAPI + aiohttp + aiosqlite + React | 首版管理页面、较多控制操作、类型化 API | 增加 ASGI 生命周期协调与前端构建 | 当前首选 |
| asyncio + aiohttp + aiosqlite + React | 更少后端依赖，团队熟悉 aiohttp | 自行维护 API 模型和文档，同样可实现管理页面 | 保留为简化备选 |
| Go 实现整个网关 | 已测出 Python 转发瓶颈，有 Go 维护能力 | 重新实现状态机、恢复和测试，不只是换框架 | 以实测决定，暂不采用 |
| 通用代理服务器 + 外部控制服务 | 基础转发为主，配置较简单 | 需验证逐连接路由、撤销、独占与补货协同 | 后续单独验证，不默认采用 |

FastAPI 不会自动提供多用户隔离、额度或计费，这些依靠领域模型、数据库约束和一致性设计。是否未来多用户，不是单独选择 HTTP 框架的理由。

## 3. 分层与生命周期

```text
业务客户端 ──SOCKS5──> 协议层 ──> 调度器 ──> 上游适配器 ──> Webshare SOCKS5
管理客户端 ──HTTP────> 管理 API ──> 同一调度器
                                    │
                         SQLite 事务与持久化任务
                                    │
                         每账号替换执行器 ──> Webshare HTTPS API
```

- 协议层负责认证、路由参数、编解码与隧道，不直接调用 Replace API。
- 调度器是分配唯一写入所有者，管理与后台任务都经它操作。
- 内存按国家、状态、主体/工位索引；数据库保存影响恢复的决定。
- 供应商适配器隔离分页、错误、任务和凭据，不将原始字典扩散到领域层。
- 隧道注册表按绑定和 generation 管理连接，支持撤销和主动关闭。

一个事件循环运行 SOCKS5 TCP、FastAPI/ASGI 服务和后台任务，使用单个 Uvicorn worker。通过 FastAPI lifespan 协调存储、ClientSession、唯一调度器、TCP listener 与任务集合。关键任务退出须降级或受控停止，不能静默消失。

SOCKS5 监听器与已有隧道须显式参与排空；不能认为 HTTP 服务停止就已完成网关关闭。容器终止宽限期需要覆盖排空和状态落盘，部署超时与应用排空参数配套设置。参考：[FastAPI lifespan](https://fastapi.tiangolo.com/advanced/events/)。

## 4. SOCKS5 数据通路

用 asyncio.start_server、readexactly、write/drain 实现受限的标准子集：CONNECT、RFC 1929 认证、三类目标地址，拒绝不支持的方法和命令。

网关只连接上游节点；目标域名按 SOCKS5 域名类型发送给上游，不本地解析。客户端如已自行解析成 IP，网关无法撤销此前的 DNS 行为。

双向桥接采用有界缓冲与 drain 背压，区分 EOF、异常、取消、强制关闭与半关闭；握手、建连分别超时，计数批量更新。上游成功后还须复核绑定 generation，才回复客户端成功。

长连接默认不设无流量关闭期限或最大寿命；TCP keepalive 是可调链路检测手段，不模拟业务心跳。字节和连接状态先更新内存、按窗口汇总，不能每个转发块都同步写数据库。

节点故障时撤销旧 generation 并合并分配新节点，逻辑 session 保留。已断开的 TCP 由业务客户端重连；网关不重放 TLS、WebSocket、浏览器或其他协议的数据。单连接 EOF 不等同节点故障。

显式轮换支持 session_policy=keep/new。keep + graceful 会在排空窗口保留新旧两代隧道，必须记录各自实际节点；不能把全体旧连接的展示 IP 直接改成新 IP。

Streams 提供 TCP 和流控原语，可减少底层代码，但 SOCKS5 仍需协议测试。参考：[Python Streams](https://docs.python.org/3/library/asyncio-stream.html)。

暂不指定 SOCKS5 第三方框架：选择前须验证远程 DNS、IPv6、错误保真、半关闭和取消清理。上游客户端封装成接口，后续可替换；测活复用该接口，不把 socks5:// 直接交给普通 aiohttp ClientSession 并假设可用。

## 5. 调度、事务与恢复

1. 工位/匿名身份键串行操作；等待不占全局池锁。
2. 首版采用唯一调度事务锁保护选取、状态更新和 SQLite 快照提交，保证落盘前不向业务发布；锁内不等待外部 HTTP。数据库提交是本版明确的一致性取舍，规模增长后改增量事务并评估锁延迟。
3. 锁内选择节点、写入已用事实并持久化，提交成功后返回绑定；失败停止后续新分配，避免未落盘节点被业务使用。
4. 首版持有调度锁直到分配提交，不另设可见预留状态；现有隧道转发不等待这把锁。
5. 请求取消时也须确认事务结果并完成内存协调，不能留下重复分配窗口。
6. 退役与替换任务写入同一事务 outbox；内存队列只唤醒，数据库是任务事实源。
7. SQLite 异步访问不等于业务事务隔离，必须提供项目级串行事务入口。

aiosqlite 每连接通过一个共享工作线程和队列执行操作；本项目据此避免阻塞事件循环，但不依赖它自动解决多语句事务交错。参考：[aiosqlite](https://aiosqlite.omnilib.dev/en/stable/)。

优先显式 SQL、少量可审计迁移与仓储接口；复杂查询或 PostgreSQL 迁移需求明确后再评估 ORM，不为假设中的未来提前增加一套框架。

## 6. 外部 API 执行

- 长期复用 aiohttp ClientSession，各账号单独 Token，不写入 URL 或日志。
- 配置连接、读取与总超时，限制全局/账号并发，尊重限流。
- 读请求分类重试，替换 POST 用提交意图和对账解决结果不明。
- 同账号串行补货会限制该账号吞吐，是首版为归属清晰作出的取舍；多账号并行仍受供应商限流约束。
- 如同账号串行成为瓶颈，只有能可靠关联结果后才考虑批量/并发替换，不直接扩大并发数。
- 检查、对账、预算和替换分任务运行，接口慢不能阻塞转发事件循环。

## 7. 管理前端与 API

### 7.1 React Bits 的使用方式

React Bits 是用户指定的组件与视觉参考。官方提供 JS/TS 与 CSS/Tailwind 四种变体，按组件复制源代码或通过 CLI 引入，每个组件单独核对依赖。本项目建议统一 TS + Tailwind，源码进入本项目后维护，不整站导入全部组件。参考：[React Bits 安装](https://reactbits.dev/get-started/installation)。

建议组合：React + TypeScript + Vite + Tailwind CSS；React Bits 用于页面背景、卡片和少量状态过渡，shadcn/ui 提供表格、表单、菜单、对话框和抽屉。两者的颜色、圆角、间距及焦点样式统一，避免组件来源决定页面风格。参考：[shadcn/ui Vite 安装](https://ui.shadcn.com/docs/installation/vite)。

视觉暂建议深色工作台、清晰状态色、局部渐变或光晕，具体样式等布局评审确认。后台表格不持续播放文字动画；支持 reduced-motion，隐藏页面暂停动画和轮询。动效只在数据变化和操作反馈时提供辅助。

React Bits CLI 可以使用 shadcn registry，但这不强制采用整套 shadcn/ui；后者是本稿为基础交互提出的补充建议。

### 7.2 请求与数据刷新

- 所有业务请求走 `/api/v1`，后台接口模型作为契约，前端可从 OpenAPI 生成 TypeScript 类型。
- 首版 REST + 2–5 秒可见页轮询，操作受理后单独追踪 operation_id；不为管理页面先引入 WebSocket。
- 页面隐藏暂停轮询，网络失败退避，显示最后更新时间；避免重叠请求和旧响应覆盖新状态。
- 轮询只读，副作用只能来自显式操作；重试使用同一幂等键，不在 React effect 中发起换号。
- 首版使用小型统一 fetch 封装和查询 hooks；复杂缓存出现后再评估专用查询库，不提前增加全局状态框架。
- 浏览器和 API 同源，不默认开放跨域；前端路由回退不能把未知 `/api/v1/*` 路径变成 index.html。

### 7.3 单管理员认证与配置

页面使用独立管理员登录，服务端持有会话、浏览器只持 HttpOnly Cookie；写操作检查 CSRF/Origin，远程部署经 HTTPS。脚本 Bearer Token、管理员登录凭据和 SOCKS5 业务密码相互独立。

账号配置仍以 accounts.json 为准，页面与 reload 通过同一配置服务校验、更新、同步；版本冲突拒绝，不同时维护两套账号真相。密钥由后台写入私有持久存储并以引用关联，只写不回显，禁止进入前端构建变量。

允许热改的运行参数使用数据库版本化 overrides；有效值按“持久 overrides 覆盖初始默认值”解析，页面显示值来源。监听端口、数据库路径和密钥属于部署配置，不假装能够热改。文件写入与运行时应用有失败时须显示待应用/失败状态，并在重载时核对。

### 7.4 单实例发布

多阶段 Docker 构建：Node 阶段构建 web/dist，Python 运行镜像只携带静态资源和后端。管理端口提供页面/API，1080 提供 SOCKS5，生产不运行 Vite 开发服务器或单独 Node 服务。

Vite 官方说明构建产物可静态部署；结合本项目私有后台、无 SEO/SSR 要求，建议不引入 Next.js 服务端运行层。此为项目取舍，不表示 React 官方对所有项目都优先推荐裸 SPA。参考：[Vite 构建](https://vite.dev/guide/build.html)、[React 自建应用说明](https://react.dev/learn/build-a-react-app-from-scratch)。

采用单 ASGI worker，或让独立管理 API 调用唯一调度服务。多个 worker 各持一套池会破坏独占，共享 SQLite 文件也不能代替所有权设计。FastAPI 官方说明多进程通常不共享内存：[部署概念](https://fastapi.tiangolo.com/deployment/concepts/)。

这里比较的是维护成本和职责适配，不作无基准的性能排名。

## 8. 扩容与性能验证

IP 数、TCP 隧道数、带宽和换号速率分别测量。1,000 个空闲节点与几百条满带宽隧道是不同负载，不按 IP 数直接推算吞吐。

压测记录事件循环延迟、CPU、RSS、文件描述符、缓冲、带宽、事务延迟、池等待与补货耗时分位数。先定位网络、上游、数据库或 Python 瓶颈，再决定优化。

uvloop 可作为后续基准实验，未经目标环境测试不设为必选；不承诺其解决带宽或补货瓶颈。横向扩展先按账号所有权分片，冷却与会话需共享协调，不能直接随机负载均衡有状态网关。

## 9. 后续可调事项

- 首版已经采用 FastAPI + React/TypeScript/Vite + React Bits/Radix 基础组件组合，后续按使用反馈调整。
- 长连接优先的 graceful 默认方式与 120 秒排空期限是否合适；hard 供风控场景显式使用。
- 六个管理模块和深色工作台已经实现；后续按实际资源与使用习惯调整。
- 真实供应商连接与替换在独立服务器验证，当前 Webshare 落地网络不作为该验收依据；部署后启用 RAPIP_PROBE_ENABLED。

现在无需先确定最终 IP 总数或远期多用户架构。

## 10. HTTP 浏览器兼容入口

新增独立 8888 入口，接受 Basic 代理认证并复用同一 Route、绑定和隧道注册。CONNECT 建立透明 TCP 隧道；普通 HTTP 转为 origin-form，移除代理认证和逐跳头，每连接只处理一个请求。支持有界请求体和 chunked 编码，拒绝歧义长度，不解密 HTTPS。

可选 python-socks 仅用于显式配置的网络出口，不自动继承系统代理，避免本机已有 Webshare 落地形成不明确的嵌套验收路径。
