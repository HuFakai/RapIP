#!/usr/bin/env bash
# 首次部署与更新共用入口；无需在宿主机安装 Python / Node。
set -Eeuo pipefail
umask 077
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
LOCK="${ROOT}/.rapip-deploy.lock"
TEMP_ENV=""
LOCKED=false

fail() { printf '\n错误：%s\n' "$*" >&2; exit 1; }
cleanup() {
    [[ -z "$TEMP_ENV" ]] || rm -f -- "$TEMP_ENV"
    if [[ "$LOCKED" == true ]]; then rmdir -- "$LOCK" 2>/dev/null || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

collect_ports() {
    local sockets ids id published
    if command -v ss >/dev/null 2>&1; then
        sockets=$(ss -H -ltn) || fail "无法读取监听端口。"
        USED_PORTS=" $(printf '%s\n' "$sockets" | awk '{sub(/^.*:/,"",$4); print $4}' | tr '\n' ' ') "
    elif command -v lsof >/dev/null 2>&1; then
        sockets=$(lsof -nP -iTCP -sTCP:LISTEN -Fn) || {
            [[ $? == 1 ]] || fail "无法读取监听端口。"
        }
        USED_PORTS=" $(printf '%s\n' "$sockets" | sed -n 's/^n.*:\([0-9][0-9]*\)$/\1/p' | tr '\n' ' ') "
    else
        fail "端口检测需要 ss（Linux 通常由 iproute2 提供）或 lsof，请先安装其中一个。"
    fi
    # Docker 可能通过 NAT 发布端口、没有对应监听进程，因此还要查询发布映射。
    ids=$(docker ps -q) || fail "无法读取 Docker 容器列表。"
    for id in $ids; do
        published=$(docker inspect --type container --format '{{range .NetworkSettings.Ports}}{{range .}}{{println .HostPort}}{{end}}{{end}}' "$id") || fail "无法读取 Docker 端口映射。"
        USED_PORTS+=" $(printf '%s\n' "$published" | tr '\n' ' ') "
    done
}

choose_port() {
    local attempt random
    for ((attempt=0; attempt<1000; attempt++)); do
        random=$(openssl rand -hex 2) || fail "随机端口生成失败。"
        CHOSEN_PORT=$((20000 + 16#$random % 10000))
        if [[ "$USED_PORTS" != *" $CHOSEN_PORT "* ]]; then
            USED_PORTS+=" $CHOSEN_PORT "
            return
        fi
    done
    fail "尝试 1000 次仍无法找到空闲端口，请检查服务器端口使用情况。"
}

initialize() {
    local admin_password proxy_password token http socks proxy
    # 配置丢失时不能把现有数据当成新实例覆盖。由使用者恢复原配置。
    if [[ -e data/gateway.sqlite3 || -e config/accounts.json || -e config/secrets.json ]]; then
        fail "发现已有账号或状态，但缺少 .env；请恢复原 .env 后再更新。"
    fi
    collect_ports
    choose_port; http=$CHOSEN_PORT
    choose_port; socks=$CHOSEN_PORT
    choose_port; proxy=$CHOSEN_PORT
    admin_password=$(openssl rand -hex 24)
    proxy_password=$(openssl rand -hex 24)
    token=$(openssl rand -hex 24)
    TEMP_ENV=$(mktemp "${ROOT}/.env.rapip.XXXXXX")
    # 随机值仅含字母数字，awk 替换不涉及 shell 执行或 Compose 插值。
    RAPIP_INIT_ADMIN="$admin_password" RAPIP_INIT_PROXY="$proxy_password" RAPIP_INIT_TOKEN="$token" \
    awk -v http="$http" -v socks="$socks" -v proxy="$proxy" '
        /^RAPIP_ADMIN_PASSWORD=/ {$0="RAPIP_ADMIN_PASSWORD=" ENVIRON["RAPIP_INIT_ADMIN"]}
        /^RAPIP_SOCKS_PASSWORD=/ {$0="RAPIP_SOCKS_PASSWORD=" ENVIRON["RAPIP_INIT_PROXY"]}
        /^RAPIP_ADMIN_TOKEN=/ {$0="RAPIP_ADMIN_TOKEN=" ENVIRON["RAPIP_INIT_TOKEN"]}
        /^RAPIP_HTTP_PORT=/ {$0="RAPIP_HTTP_PORT=" http}
        /^RAPIP_SOCKS_PORT=/ {$0="RAPIP_SOCKS_PORT=" socks}
        /^RAPIP_PROXY_HTTP_PORT=/ {$0="RAPIP_PROXY_HTTP_PORT=" proxy}
        {print}
    ' .env.example > "$TEMP_ENV"
    # 硬链接原子创建；即使有外部进程同时创建 .env，也不会覆盖。
    ln "$TEMP_ENV" .env || fail ".env 已存在或无法创建，未覆盖；请检查后重试。"
    rm -f -- "$TEMP_ENV"
    TEMP_ENV=""
    printf '首次部署：已生成三份独立的 48 位随机凭据，端口为 %s / %s / %s。\n' "$http" "$socks" "$proxy"
}

main() {
    local no_pull=false command_name host
    case "${1:-}" in
        --help|-h)
            printf '用法：bash scripts/deploy.sh [--no-pull]\n默认：拉取当前分支最新代码，首次初始化或保留配置更新，并等待健康检查。\n--no-pull：不拉取代码，使用当前源码部署/重建（离线源码或本地修改场景）。\n'
            return ;;
        --no-pull) no_pull=true ;;
        '') ;;
        *) fail "未知参数：$1；请使用 --help。" ;;
    esac
    [[ $# -le 1 ]] || fail "参数过多；请使用 --help。"
    cd "$ROOT"
    mkdir "$LOCK" 2>/dev/null || fail "另一个部署正在执行；如上次被强行终止，请确认无部署进程后删除 ${LOCK}。"
    LOCKED=true
    for command_name in docker openssl awk sed tr mktemp; do
        command -v "$command_name" >/dev/null 2>&1 || fail "缺少命令：${command_name}。请先安装 Docker Compose、OpenSSL 及系统基础工具。"
    done
    docker info >/dev/null 2>&1 || fail "Docker 引擎未启动或当前用户没有访问权限。"
    docker compose version >/dev/null 2>&1 || fail "请安装 Docker Compose v2 插件。"
    [[ $(docker compose up --help) == *--wait-timeout* ]] || fail "Compose 版本过旧，需要支持 --wait 和 --wait-timeout 的版本。"
    if [[ "$no_pull" == false ]]; then
        command -v git >/dev/null 2>&1 || fail "自动更新需要 Git；源码压缩包可使用 --no-pull。"
        git rev-parse --is-inside-work-tree >/dev/null 2>&1 || fail "当前不是 Git 仓库；源码压缩包请使用 --no-pull。"
        if ! git diff --quiet || ! git diff --cached --quiet; then
            fail "源码有未提交修改，未拉取或部署；请先处理修改，或显式使用 --no-pull。"
        fi
        printf '正在拉取当前分支的最新代码…\n'
        git pull --ff-only || fail "拉取失败，尚未停止现有服务。"
        # 重新执行更新后的脚本，避免使用本轮已读取的旧实现。
        rmdir "$LOCK"
        LOCKED=false
        exec bash "$ROOT/scripts/deploy.sh" --no-pull
    fi
    [[ -f .env.example && -f compose.yaml ]] || fail "项目配置文件不完整。"
    if [[ -f .env ]]; then
        printf '检测到已有 .env：保留密码、端口、账号和数据，更新/重建服务。\n'
    else
        initialize
    fi
    if ! awk -F= '
        /^RAPIP_(ADMIN_PASSWORD|SOCKS_PASSWORD|ADMIN_TOKEN)=/ {
            seen[$1]++
            value=substr($0,index($0,"=")+1)
            gsub(/^[[:space:]\047"]+|[[:space:]\047"]+$/, "", value)
            if (value == "" || value ~ /^replace-with-/) bad=1
        }
        END {exit bad || seen["RAPIP_ADMIN_PASSWORD"] != 1 || seen["RAPIP_SOCKS_PASSWORD"] != 1 || seen["RAPIP_ADMIN_TOKEN"] != 1}
    ' .env; then
        fail ".env 中三份凭据缺失、重复或仍为示例值；请修正。仅在没有 .env 时自动生成，已有文件不会覆盖。"
    fi
    docker compose config --quiet || fail "Compose 配置无效，尚未停止现有服务。"
    printf '正在构建镜像（构建失败不会主动停止现有容器）…\n'
    docker compose build gateway || fail "镜像构建失败，保留现有服务和配置。"
    printf '正在重建网关并等待健康检查；已有长连接会断开…\n'
    docker compose up -d --no-build --force-recreate --wait --wait-timeout 180 gateway || \
        fail "启动或健康检查失败。配置与数据已保留，请运行 docker compose logs --tail=100 gateway 排查后重试。"
    host="YOUR_SERVER_IP"
    if command -v ip >/dev/null 2>&1; then
        host=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1);exit}}') || host=""
        host=${host:-YOUR_SERVER_IP}
    fi
    docker compose exec -T -e "RAPIP_DEPLOY_SERVER_HOST=$host" gateway python scripts/deployment_info.py || \
        fail "服务已启动，但部署信息生成失败；请从 .env 查看凭据，并检查 data 目录写入权限。"
}

main "$@"
