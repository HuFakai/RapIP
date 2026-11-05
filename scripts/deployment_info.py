"""Print and privately persist deployment access details after container health succeeds."""

import os
import tempfile
from pathlib import Path
from urllib.parse import quote


def main():
    env = os.environ
    admin_port = int(env.get("RAPIP_HTTP_PORT", "8080"))
    socks_port = int(env.get("RAPIP_SOCKS_PORT", "1080"))
    http_port = int(env.get("RAPIP_PROXY_HTTP_PORT", "8888"))
    admin_user = env.get("RAPIP_ADMIN_USER", "admin")
    proxy_user = env.get("RAPIP_SOCKS_USER", "admin")
    admin_password = env["RAPIP_ADMIN_PASSWORD"]
    proxy_password = env["RAPIP_SOCKS_PASSWORD"]
    token = env["RAPIP_ADMIN_TOKEN"]
    server = env.get("RAPIP_DEPLOY_SERVER_HOST", "YOUR_SERVER_IP")
    admin_bind = env.get("RAPIP_ADMIN_BIND_IP") or env.get("RAPIP_BIND_IP") or "127.0.0.1"
    proxy_bind = env.get("RAPIP_PROXY_BIND_IP") or env.get("RAPIP_BIND_IP") or "127.0.0.1"
    loopback = {"127.0.0.1", "::1", "localhost"}
    admin_host = "127.0.0.1" if admin_bind in loopback else server
    admin_url = env.get("RAPIP_PUBLIC_ADMIN_URL") or f"http://{admin_host}:{admin_port}"
    proxy_host = env.get("RAPIP_PUBLIC_PROXY_HOST") or ("127.0.0.1" if proxy_bind in loopback else server)
    proxy_host = f"[{proxy_host.strip('[]')}]" if ":" in proxy_host else proxy_host
    public_socks = env.get("RAPIP_PUBLIC_SOCKS_PORT") or str(socks_port)
    public_http = env.get("RAPIP_PUBLIC_HTTP_PROXY_PORT") or str(http_port)
    username = f"{proxy_user}-worker-01-session-first"
    credentials = f"{quote(username, safe='')}:{quote(proxy_password, safe='')}"
    report = f"""RapIP 部署/更新成功（容器健康检查已通过）

管理页面：{admin_url}
管理员用户名：{admin_user}
管理员密码：{admin_password}
代理基础用户名：{proxy_user}
代理密码：{proxy_password}
管理 API Token：{token}

管理 / SOCKS5 / HTTP 监听端口：{admin_port} / {socks_port} / {http_port}
管理 / 代理宿主机发布地址：{admin_bind} / {proxy_bind}
示例 SOCKS5 链接：socks5://{credentials}@{proxy_host}:{public_socks}
示例 HTTP 链接：http://{credentials}@{proxy_host}:{public_http}
示例用户名只是配置，不会分配 IP；添加账号并通过测活后才可用于业务。

默认仅本机发布时，在客户端电脑建立 SSH 隧道：
ssh -N -L {admin_port}:127.0.0.1:{admin_port} -L {socks_port}:127.0.0.1:{socks_port} -L {http_port}:127.0.0.1:{http_port} user@{server}
隧道代理通过本机 {socks_port}/{http_port} 接入；管理页面优先访问上方正式地址。
启用 Cookie Secure 后，应通过 HTTPS 域名登录，普通 HTTP 隧道不作登录入口。
user 替换为服务器 SSH 用户；检测到的服务器地址可能是内网 IP，需替换为实际可达地址。
公网 NAT 和 HTTPS 反代地址请按实际配置，脚本不自动识别公网 IP、不开放防火墙。

下一步：登录“上游账号”添加 Webshare Key，在“网关设置”确认主动测活，再进行代理连通实测。
健康检查仅证明管理服务存活，未验证供应商出口或消耗换 IP 额度。
更新命令：bash scripts/deploy.sh
查看状态：docker compose ps
查看日志：docker compose logs --tail=100 gateway
凭据保存在 .env；本页另存于 data/DEPLOYMENT_ACCESS.txt（默认挂载）。请勿公开或上传。
"""
    directory = Path(env.get("RAPIP_DATA_DIR", "data"))
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".deployment-access-", dir=directory)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(report)
        os.replace(temporary, directory / "DEPLOYMENT_ACCESS.txt")
    finally:
        Path(temporary).unlink(missing_ok=True)
    print(report)


if __name__ == "__main__":
    main()
