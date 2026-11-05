#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
# Standalone adapter for the pinned xykt/IPQuality database functions.
set -o pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
export RAPIP_IPQUALITY_LIBRARY=1
source "$here/ip.sh"
read -r expected_ip
read -r local_proxy
[[ "$expected_ip" =~ ^[0-9.]+$ && "$local_proxy" =~ ^socks5h://127\.0\.0\.1:[0-9]+$ ]] || exit 2
curl() {
  local arg
  for arg in "$@"; do
    if [[ "$arg" == *"/ref/iso3166.json" ]]; then cat "$here/iso3166.json"; return; fi
  done
  command curl --proxy "$local_proxy" --noproxy '' --proto '=https' --proto-redir '=https' \
    --connect-timeout 8 --max-time 12 --max-filesize 1048576 "$@"
}
show_progress_bar() { :; }
kill_progress_bar() { :; }
actual_ip=$(curl -fsS https://api.ipify.org)
[[ "$actual_ip" == "$expected_ip" ]] || { echo 'Exit verification failed' >&2; exit 3; }
IP="$expected_ip"
fullIP=1
mode_json=1
mode_privacy=1
rawgithub='https://raw.githubusercontent.com/xykt/IPQuality/'
generate_random_user_agent
set_language
ipjson='{"Head":{},"Info":{},"Type":{},"Score":{},"Factor":{},"Media":{},"Mail":{}}'
# Database queries only; no upstream main/telemetry, media tests, mail or DNSBL.
db_maxmind 4
db_ipinfo
db_scamalytics 4
db_ipapi 4
# A missing raw score must not become 0% through upstream awk coercion.
[[ "${ipapi[scorenum]:-}" =~ ^[0-9]+([.][0-9]+)?$ ]] || unset 'ipapi[score]'
db_abuseipdb 4
db_ip2location 4
db_ipdata 4
db_ipqs 4
save_json
printf '%s\n' "$ipjson" | jq '{Head,Info,Type,Score,Factor}'
