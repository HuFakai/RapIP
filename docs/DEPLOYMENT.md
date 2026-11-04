# 实际部署与验收

更新日期：2026-11-05。本文面向独立 Linux 服务器，使用 Docker Compose。服务器应能直接连接 Webshare 节点；当前开发电脑本身使用 Webshare 落地，不作为真实连通验收环境。

## 1. 准备项目

安装 Git、Python 3（仅用于初始化配置）和带 Compose 插件的 Docker。参见 [Docker 官方安装文档](https://docs.docker.com/engine/install/)。构建容器时会安装 Python 依赖并构建 React 页面，服务器无需额外安装 Node 或 uv。

```sh
git clone https://github.com/HuFakai/RapIP.git
cd RapIP
python3 scripts/setup.py
```

初始化生成完整 `.env` 和空账号列表，不覆盖已有配置。管理员密码、代理密码与脚本 Token 各自独立；从服务器 `.env` 查看，不要使用 `.env.example` 中的示例密码。

## 2. 配置端口与地址

修改 `.env`。三个端口分别控制管理页面、SOCKS5 和 HTTP 代理，不要使用相同端口：

```dotenv
RAPIP_HTTP_PORT=8080
RAPIP_SOCKS_PORT=1080
RAPIP_PROXY_HTTP_PORT=8888

# 默认只发布到服务器本机；推荐经 SSH 隧道或受保护网络接入。
RAPIP_ADMIN_BIND_IP=127.0.0.1
RAPIP_PROXY_BIND_IP=127.0.0.1

# 填写客户端实际可达的代理 IP 或域名，无需协议和端口。
# 留空时，连接配置弹窗默认使用当前页面的主机名，也可在弹窗中修改。
RAPIP_PUBLIC_PROXY_HOST=

# 仅当公网 NAT / 反向端口映射与实际监听端口不同时填写。
RAPIP_PUBLIC_SOCKS_PORT=
RAPIP_PUBLIC_HTTP_PROXY_PORT=

RAPIP_COOKIE_SECURE=false
RAPIP_PROBE_ENABLED=true
```

Compose 会将同一个端口值用于容器监听、宿主机映射及健康检查。只修改 `.env` 即可改端口，无需编辑 Compose 或 Dockerfile；例如改为 `18080 / 11080 / 18888`，生成的代理链接也会使用正确端口。

原生运行时监听地址由 `RAPIP_HTTP_HOST`、`RAPIP_SOCKS_HOST`、`RAPIP_PROXY_HTTP_HOST` 控制。容器内部自动监听 `0.0.0.0`，宿主机发布范围由上述 `*_BIND_IP` 控制，两者职责不同。

需要代理端口直接供远程客户端访问时，将 `RAPIP_PROXY_BIND_IP=0.0.0.0`，并在服务器网络规则中限制来源。管理入口可以继续只绑定本机；如果经域名反向代理提供 HTTPS 管理页，再设置 `RAPIP_COOKIE_SECURE=true`。普通 HTTP 或 SSH 转发到 HTTP 的场景保持 false。

## 3. 启动服务

```sh
docker compose config --quiet
docker compose up --build -d
docker compose ps
docker compose logs --tail=100 gateway
```

`config --quiet` 校验配置且不输出密钥。不要把完整的 `docker compose config` 或 `.env` 内容发到公开渠道。

默认绑定本机时，可在客户端电脑建立 SSH 隧道（以下为默认服务器端口）：

```sh
ssh -N -L 18080:127.0.0.1:8080 -L 1080:127.0.0.1:1080 -L 8888:127.0.0.1:8888 user@YOUR_SERVER_IP
```

浏览器打开 `http://127.0.0.1:18080`。此时代理也经隧道连接到客户端本机 1080/8888。若更改了服务器端口，相应修改隧道目标端口。

## 4. 添加账号与获取连接

1. 用 `.env` 中的管理员用户名、密码登录。
2. “上游账号 → 添加账号”输入 Webshare API Key。Key 只写入服务器私有配置，不回显。
3. 在“网关设置”确认主动测活已开启，等待节点通过准入后进入可分配状态。关闭测活不代表自动信任未验证节点。
4. 总览“查看连接配置”填写客户端能访问的服务器地址、工位、session 和可选国家，复制带密码的完整 SOCKS5 或 HTTP 链接。
5. 客户端不支持 SOCKS5 账密时使用 HTTP 链接；HTTPS 目标同样配置 `http://` 代理，由 CONNECT 转发。
6. 需要 AI 帮助接入时，复制“AI 接入说明”。该说明包含命名规范和生命周期，真实密码与管理员 Token 为占位符。

所有调度操作均在一个网关进程执行，不要通过增加 Uvicorn workers 或 Compose replicas 扩容同一个池。

## 5. 测活设置优先级

`RAPIP_PROBE_ENABLED` 是首次初始化的默认值。页面开关保存到 SQLite，重启后仍生效，并优先于该环境变量。升级已有数据目录时，请在页面确认实际开关状态。

开启后立即检查未使用节点；关闭后不再提交新的主动测活，在途结果不会因关闭而改变准入状态，不影响已建立的业务连接。手动只读诊断命令仍按显式请求执行。

## 6. 分步验收

先检查服务存活，再同步账号和测活，最后验证代理与轮换。不要只看到 HTTP 200 就认为代理可用。

```sh
# 默认管理端口为 8080；自定义时替换此处端口。
curl --fail http://127.0.0.1:8080/api/v1/health/live

# 只读取列表，不分配会话、不换 IP。
docker compose exec gateway python -m rapip.diagnostics --list-only

# 经上游请求探测站点；会产生少量流量，不调用 Replace API。
docker compose exec gateway python -m rapip.diagnostics
```

代理连通测试：从管理页复制生成的完整链接，填写到客户端或 curl 的 `--proxy` 参数；SOCKS curl 建议使用弹窗中的 socks5h 链接以便远程解析域名。

首次创建业务绑定后，该节点退出绑定、空闲回收或重启恢复都可能创建真实补货任务。真实轮换会消耗供应商额度；建议先验证一个工位，在任务页确认操作与上游补货分别完成，再扩大规模。

## 7. 改端口、升级与备份

修改端口或部署环境变量后重建容器使其生效：

```sh
docker compose up -d --force-recreate
```

源码升级：

```sh
git pull --ff-only
docker compose up --build -d
```

保留 `.env`、`config/accounts.json`、`config/secrets.json`（存在时）和 `data/`。一致备份时先停止服务，再复制这些私有文件；不要只复制运行中的 SQLite 主文件而遗漏 WAL。停止/重启可能断开长连接，保守退役旧节点后会创建补货任务。

不把开发机的 data 目录当作新部署的默认数据。需要迁移时完整停机复制，并避免新旧实例同时管理同一批供应商账号。

## 8. 常见问题

| 现象 | 检查方式 |
| --- | --- |
| 登录后立刻被退出 | 是否在 HTTP 环境错误启用了 Cookie Secure；检查客户端时间和实际 HTTPS 配置 |
| 显示节点但可分配为 0 | 查看测活开关、节点原因、账号状态和国家筛选，使用独立网络执行只读诊断 |
| 完整链接地址或端口不对 | 设置 PUBLIC_PROXY_HOST / PUBLIC_*_PORT，或在弹窗修正主机名；公网反向代理的页面域名不一定是代理地址 |
| 当前电脑握手失败，其他服务器可用 | 检查是否以同一 Webshare 落地形成嵌套链路；不要据此批量替换节点 |
| UNCERTAIN 任务阻塞账号补货 | 在供应商后台核对提交结果，不重复点击或重发 Replace 请求 |
| 端口被占用 | 修改相应环境变量，重新创建容器；不要停止服务器上的无关进程 |

Docker 引擎构建和真实供应商换号仍需要在目标服务器执行；本地自动测试使用模拟供应商，不消耗真实更换额度。
