# 实际部署与验收

更新日期：2026-11-05。推荐独立 Linux 服务器 + Docker Compose。服务器应能直接连接 Webshare 节点；当前开发电脑本身使用 Webshare 落地，不作为真实出口验收环境。

## 1. 准备项目

服务器需要 Git、Docker Engine、支持 `--wait-timeout` 的 Compose v2 插件、OpenSSL，以及 `ss` 或 `lsof`（端口检测）。宿主机无需 Python、Node 或 uv。Docker 安装见 [官方文档](https://docs.docker.com/engine/install/)。脚本检查依赖和 Docker 访问权限，不自动安装系统组件。

```sh
git clone https://github.com/HuFakai/RapIP.git
cd RapIP
```

## 2. 一键启动服务

在项目目录执行同一个入口：

```sh
bash scripts/deploy.sh
```

脚本会自动完成：

1. 拉取当前分支最新代码（`git pull --ff-only`）。源码有未提交修改或拉取失败时停止，不覆盖本地修改、不停止旧服务。
2. 没有 `.env` 时按中文模板生成配置，分别生成管理员密码、代理密码、管理 API Token，均为独立的 48 位随机字母数字（十六进制字符）。
3. 检测系统 TCP 监听及 Docker 已发布的端口，从 `20000–29999` 随机选择三个互不相同的空闲端口；不会停止其他程序。
4. 已有 `.env` 时保留原密码、端口、账号和数据，直接更新；即使上次启动失败，也不会重新生成凭据。已有数据却丢失 `.env` 时要求恢复原文件。
5. 校验 Compose，先构建镜像，再重建网关并等待最多 180 秒的容器健康检查。构建失败时不会主动停止旧容器；启动/健康检查失败时不显示成功。
6. 成功后在终端展示管理地址、三种端口、管理员凭据、代理凭据、API Token、完整示例链接、SSH 隧道命令和后续操作。相同信息另存于 `data/DEPLOYMENT_ACCESS.txt`。

`.env` 和信息文件包含真实凭据，脚本自动以仅所有者可读写的方式创建，不需要额外执行权限设置命令。不要公开终端截图或信息文件；私有配置、数据和部署信息均被 Git 忽略。

首次无需提前复制 `.env`、手工生成密码或创建 `accounts.json`。账号启动后通过页面添加。脚本不自动开放防火墙，也不自动查询公网 IP；默认三个端口仅发布到服务器本机，按终端输出的 SSH 命令从客户端访问。

端口检测到容器绑定之间仍有短暂竞争窗口；若另一程序恰好抢占端口，Docker 会报错，脚本不会显示部署成功。检查占用后修改 `.env` 的对应端口再重跑，不会自动改动已生成的配置。

## 3. 更新与重启（启动之后）

已部署后，在项目目录再次执行：

```sh
bash scripts/deploy.sh
```

脚本自动拉取源码、重新构建并强制重建容器，保留 `.env`、`config/` 和 `data/`。即使没有新提交也会重建容器；已有长连接会断开，客户端需要重连。按网关恢复规则，旧业务节点恢复时可能退役并创建真实补货任务，应避开业务高峰。

只使用当前已下载的源码部署或修改 `.env` 后重建，不拉取 Git：

```sh
bash scripts/deploy.sh --no-pull
```

手工更新的等价流程：

```sh
git pull --ff-only
docker compose up -d --build --force-recreate --wait --wait-timeout 180
```

查看状态与日志：

```sh
docker compose ps
docker compose logs --tail=100 gateway
```

更新不自动删除卷、重置数据库或重新生成凭据，也不提供自动回滚。启动失败请查看日志修复后重跑；不要通过删除 `data/` 解决。部署锁 `.rapip-deploy.lock/` 防止同时执行；若机器断电或进程被强杀留下锁目录，先确认没有部署进程，再移除残留目录。

## 4. 参数说明、端口与手工部署

[.env.example](../.env.example) 已逐项提供中文说明。一键生成的 `.env` 保留相同说明；更新已有文件时不重写其内容。当前开发机 `.env` 也已补齐中文说明，原凭据及端口保持不变。未来模板新增参数时，可参照模板手工补充已有 `.env`。

需要手工配置而不使用脚本时，仅首次复制模板：

```sh
cp .env.example .env
```

用习惯的文件编辑器修改 `.env` 中三个 `replace-with-...` 占位值，分别使用不同的随机值。终端快速生成 **32 位随机字母数字**：

```sh
openssl rand -hex 16
```

输出为 32 位十六进制字符（`0–9`、`a–f`），属于字母数字；运行三次分别用于管理员密码、代理密码和 API Token。一键脚本使用 `openssl rand -hex 24` 生成 48 位值。含 `$`、`#` 或空格的自定义值应使用单引号包住。

手工启动命令：

```sh
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 180
```

`config --quiet` 校验配置而不输出密钥；不要公开完整 `docker compose config` 或 `.env`。直接使用模板占位密码不会自动变安全，一键脚本会拒绝这些占位值。

常用参数：

| 参数 | 用途 |
| --- | --- |
| `RAPIP_HTTP_PORT` | 管理页面/API 端口，同时用于容器监听、宿主机映射与健康检查 |
| `RAPIP_SOCKS_PORT` | SOCKS5 入口端口 |
| `RAPIP_PROXY_HTTP_PORT` | HTTP/HTTPS CONNECT 代理入口端口 |
| `RAPIP_ADMIN_BIND_IP` | 管理端口发布范围，默认 `127.0.0.1` |
| `RAPIP_PROXY_BIND_IP` | 两个代理端口发布范围，默认 `127.0.0.1` |
| `RAPIP_PUBLIC_ADMIN_URL` | 正式 HTTPS 管理地址，用于部署信息展示，不改变监听与认证 |
| `RAPIP_PUBLIC_PROXY_HOST` | 客户端可达的代理 IP/域名，无协议和端口；留空使用页面主机名 |
| `RAPIP_PUBLIC_SOCKS_PORT` / `RAPIP_PUBLIC_HTTP_PROXY_PORT` | NAT 对外端口与实际监听不同时填写，留空沿用监听端口 |
| `RAPIP_COOKIE_SECURE` | HTTPS 管理页设 true，HTTP 或 SSH 隧道设 false |
| `RAPIP_PROBE_ENABLED` | 首次主动测活默认值，持久化后的页面设置优先 |

三个监听端口不得相同，范围 `1–65535`。手工默认值为 `8080 / 1080 / 8888`；一键部署选择的实际端口以 `.env` 和成功信息为准。修改后运行 `bash scripts/deploy.sh --no-pull`。

原生监听地址由 `RAPIP_HTTP_HOST`、`RAPIP_SOCKS_HOST`、`RAPIP_PROXY_HTTP_HOST` 控制。容器内部由 Compose 固定监听 `0.0.0.0`，宿主机发布范围由 `*_BIND_IP` 控制。

远程直接使用代理时，将 `RAPIP_PROXY_BIND_IP=0.0.0.0`，设置客户端可达的 `RAPIP_PUBLIC_PROXY_HOST`，并按实际选定端口配置服务器安全组/防火墙的允许来源。管理入口可继续只绑定本机，或经 HTTPS 反向代理提供。

### 管理域名与代理地址分开配置

HTTPS 管理域名反向代理到服务器本机的管理端口；SOCKS5/HTTP 客户端直接使用服务器 IP 和各自的代理端口。域名经过 Cloudflare 时，不要把它直接填写为原始代理入口，参见 [Cloudflare 支持的端口](https://developers.cloudflare.com/fundamentals/reference/network-ports/)。

```dotenv
RAPIP_ADMIN_BIND_IP=127.0.0.1
RAPIP_COOKIE_SECURE=true
RAPIP_PROXY_BIND_IP=0.0.0.0
RAPIP_PUBLIC_PROXY_HOST=YOUR_SERVER_IP
```

`RAPIP_PUBLIC_PROXY_HOST` 是显式配置的客户端可达地址，不自动从容器网卡或管理页主机名推测。修改后重建容器，连接弹窗和 AI 接入说明都会使用该值；公网 NAT 端口不同再填写 PUBLIC_*_PORT。

反向代理须保留原始 Host，转发 X-Forwarded-Proto=https；`/api/` 不缓存。证书配置在反向代理层，并配置自动续期及成功续期后的安全 reload。这里开启了安全 Cookie，应通过 HTTPS 域名登录；原 HTTP 隧道地址不作为普通登录入口。

现有部署已完成上述配置：HTTPS 管理页可登录，两个公网代理端口能从外部到达且要求认证。该检查只做认证握手和连接配置生成，不创建业务绑定，也不额外提交 Replace；统计计数仍保留此前唯一一次授权实测。

## 5. 远程访问、添加账号与连接

脚本会按实际端口展示 SSH 转发命令。以下仅为手工默认端口示例，在客户端电脑运行：

```sh
ssh -N -L 18080:127.0.0.1:8080 -L 1080:127.0.0.1:1080 -L 8888:127.0.0.1:8888 user@YOUR_SERVER_IP
```

打开 `http://127.0.0.1:18080`。`user` 和服务器地址按实际替换；一键部署请使用终端输出的端口，不要照搬本节默认端口。

1. 用部署信息中的管理员凭据登录。
2. “上游账号 → 添加账号”输入 Webshare API Key。Key 写入服务器私有配置，不回显；未添加账号时资源为空是正常情况。
3. 在“网关设置”确认主动测活开启，等待节点准入后进入可分配状态。关闭测活不会自动信任未验证节点。
4. “查看连接配置”填写客户端实际可达地址、工位、session 和国家，复制带密码的 SOCKS5 或 HTTP 链接。
5. 客户端不支持 SOCKS5 账密时使用 HTTP 入口；HTTPS 目标同样配置 `http://` 代理，经 CONNECT 转发。
6. AI 接入说明包含命名规范和生命周期，真实密码、管理员 Token 为占位符。

所有调度在一个网关进程执行，不要增加 Uvicorn workers 或 Compose replicas 来扩容同一个池。

## 6. 测活与实测

`RAPIP_PROBE_ENABLED` 仅是初始默认值。页面开关保存到 SQLite 后优先，重启仍生效。开启后立即检查未使用节点；关闭后不提交新的主动测活，在途结果不会因关闭而改变准入状态，不影响已建立业务连接。

脚本成功仅表示容器管理服务健康，真实供应商出口仍需验收：

```sh
# 只读取列表，不分配会话、不换 IP。
docker compose exec gateway python -m rapip.diagnostics --list-only

# 经上游访问探测站点；会产生少量流量，不调用 Replace API。
docker compose exec gateway python -m rapip.diagnostics
```

从页面复制完整代理链接到业务客户端或 curl 的 `--proxy` 参数；curl SOCKS 建议用 `socks5h` 以便上游解析域名。先验证一个工位，再扩大规模。

创建业务绑定后的轮换、释放、空闲回收和重启恢复可能创建真实补货任务，消耗供应商额度。真实操作和上游补货分别在任务页确认完成。

## 7. 备份与迁移

保留 `.env`、`config/` 和 `data/`，包含 `data/DEPLOYMENT_ACCESS.txt`。一致备份时先停止服务，再复制私有文件；不要只复制运行中的 SQLite 主文件而遗漏 WAL。停机/重启会断开长连接，并可能按恢复规则创建补货任务。

开发机 `data/` 不是新部署的默认数据。迁移需要完整停机复制，避免新旧实例同时管理同一批供应商账号。宿主机持久化路径由 Compose 挂载固定，容器内 `RAPIP_DATA_DIR=data`、`RAPIP_CONFIG_DIR=config` 请保留；改变路径必须同步修改挂载。

## 8. 常见问题与验证范围

| 现象 | 检查方式 |
| --- | --- |
| Docker 引擎/权限检查失败 | 启动 Docker，确保运行脚本的用户有 Docker 权限 |
| Git 拉取失败 | 检查网络、当前分支上游和本地修改；不拉取可显式使用 `--no-pull` |
| 已有账号或数据却没有 `.env` | 恢复原配置，脚本不会自动换密码接管旧数据 |
| 构建或健康检查失败 | 查看 `docker compose logs --tail=100 gateway`；原配置与数据保留 |
| 管理页面无法从外部打开 | 默认仅本机绑定，使用输出的 SSH 隧道；检查真实端口、安全组与绑定地址 |
| 登录后立刻退出 | 普通 HTTP 是否误设 Cookie Secure=true |
| 有节点但可分配为 0 | 检查测活开关、节点原因、账号状态、国家条件，并在独立网络只读诊断 |
| 链接地址/端口不对 | 修正 PUBLIC_PROXY_HOST / PUBLIC_*_PORT 或页面弹窗的地址 |
| 当前电脑握手失败，其他服务器可用 | 检查 Webshare 落地嵌套路径，不据此批量替换节点 |
| UNCERTAIN 任务阻塞补货 | 供应商后台核对结果，不盲目重发 Replace 请求 |

部署脚本的首次/更新、端口排除、凭据保留、构建/健康失败路径以模拟 Docker 自动测试覆盖。已在 Linux ARM64 服务器完成真实 Docker 构建、健康启动、SSH 隧道页面访问、6 个节点测活、SOCKS5/HTTP CONNECT 转发以及用户授权的 1 次真实替换。完整证据和未覆盖范围见 [验证记录](VALIDATION.md)；新服务器仍应按本节逐步验收。

## v0.2.1 升级说明

仍使用相同的 Docker Compose 更新方式，保留 `.env`、`config/`、`data/`。新版本首次读取旧快照时自动采用默认生命周期策略，无需手动迁移数据库。更新前备份配置、数据并确认没有活动业务连接；更新会重建网关容器。

新设置位于“网关设置 → 数据生命周期”，不是环境变量。代理/管理端口仍由现有 `.env` 控制。分组 ID 可以由后端自动生成，AI 接入说明从“连接配置”重新复制即可获取最新版。


## v0.2.5 生命周期与更新

当前有效 session 与原 IP 长期保留。更新、重启导致 TCP 断开，但不再退役当前 IN_USE 节点或设置 300 秒重连期限。无连接时可以备份/更新并保留绑定，不必等待绑定数为 0；正在排空的旧代次和已提交 Replace 仍需完成对账。实际入口取 RAPIP_PUBLIC_PROXY_HOST / RAPIP_PUBLIC_SOCKS_PORT / RAPIP_PUBLIC_HTTP_PROXY_PORT，0.0.0.0 仅是监听地址。

本版持久化 schema 3，包含轮换授权、额度预留与手动等待任务。回退须停服务并同时恢复发布前数据库、配置和旧镜像，不能只换旧代码。定时轮换升级后全部默认关闭；session_idle_timeout 的旧值被忽略，不迁移为轮换授权。


## v0.2.6 升级与验收

本版后端和页面必须同时更新，持久化升级到 schema 4。先记录各账号 submitted/completed/redraws 与当前绑定，再选择业务连接空档，备份 `.env`、`config/`、`data/` 和当前镜像。继续使用 Docker Compose 构建和启动：

```sh
git pull --ff-only
docker compose build
docker compose up -d --no-deps gateway
docker compose ps
```

生产数据迁移不可只回退代码。需回退时先停服务，再成套恢复升级前数据、配置与镜像。恢复旧备份会撤回备份之后的预留与报告；应先核对供应商任务状态，禁止重放不明 Replace。

验收：原绑定 / IP / 更换计数保持；质量检测源可分别开关；报告标签页能独立读取；IP 行内复制已绑定资源不改变其 session；模拟环境验证行内预留与取消、两个协议同身份、首次连接启用、普通配置使用后生成下一份、池耗尽、并发及重启。不要用真实节点释放测试来消耗更换额度。

自 v0.2.7 起，Ping0 仅由浏览器通过报告顶部的快捷链接打开，服务器无需访问 Ping0。IPQuality 检测及原保留期限不变，旧 Ping0 记录仍按原期限清理。
