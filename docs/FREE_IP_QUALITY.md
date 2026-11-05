# 免费 IP 质量检测的使用方式

核对日期：2026-11-05。免费网络检测与第三方信誉判断是两种不同的证据，不合并成一个“万能质量分”。

## RapIP 内置检测

质量分组的“基础可用”和“稳定优先”预设无需购买检测 API 或提供第三方 Key。通过所选出口建立 HTTPS 连接，核对返回的出口地址，并按样本成功数、最大耗时与有效期准入。检测消耗少量现有代理流量；这是网络质量证据，不能证明住宅属性、无举报或特定业务成功。

“高质量”预设另要求 IPinfo 属性证据及 AbuseIPDB 举报分。没有配置对应 Key/字段权限，结果为 UNKNOWN，资源不准入，也不自动更换。IPinfo Lite 的 ASN/地区字段不等于具备完整网络类型与风险标签权限。

## GitHub 上的开源工具

[xykt/IPQuality](https://github.com/xykt/IPQuality) 是可供手动复核的候选。它支持指定 HTTP/SOCKS5 代理、JSON 输出和隐私模式。源码会向多个外部数据库或查询服务请求数据，适合对少量出口交叉检查；不能据此承诺第三方数据长期免费、无限额或与目标业务判断一致。

- [README：参数、代理与 JSON 输出](https://github.com/xykt/IPQuality/blob/main/README.md)
- [ip.sh：检测实现](https://github.com/xykt/IPQuality/blob/main/ip.sh)
- [LICENSE：AGPL-3.0](https://github.com/xykt/IPQuality/blob/main/LICENSE)

建议先下载固定 commit 的源码检查后单独运行，不在 RapIP 网关容器中以 root 动态下载执行。参数 `-4 -p -j -x <PROXY_URL>` 可用于 IPv4、隐私模式、JSON、指定代理；`-p` 关闭在线报告生成，但检测请求仍会到达外部服务。不要将含密码的 URL 或完整报告上传到公开仓库。

如果使用 RapIP 的业务代理入口运行该脚本，会创建或复用会话，占用资源。若只想检测指定原始节点，应在自己的安全终端单独使用该节点的上游凭证；不要把 Webshare Key 当成代理密码。复核结束不应自动调用轮换或清理接口。

本次实现保留独立工具入口与使用说明，没有把外部 Shell 脚本或其查询服务加入自动替换路径。后续如接入 JSON 报告，应额外验证报告 IP、时间、来源、字段缺失、过期与来源额度；未验证的手动报告不能替代准入证据。
