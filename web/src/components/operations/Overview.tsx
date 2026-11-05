import { useEffect, useState } from "react";
import {
  Activity,
  RefreshCw as RefreshIcon,
  ArrowDownLeft,
  ArrowUpRight,
  ArrowUpRight as OpenIcon,
  Clock3,
  Database,
  Gauge,
  Layers3,
  Network,
  ShieldCheck,
  Users,
  Zap,
} from "lucide-react";
import { useQuery } from "@/lib/api";
import { queryString, type OverviewData } from "@/lib/v2";
import { quotaPair, trafficPair } from "@/lib/quotas";
import { bytes } from "@/lib/utils";
import { Button } from "../ui/button";
import { Chart } from "./Chart";
import { ErrorMessage, Refresh, Scope, Status } from "./Common";

type Destination =
  "nodes" | "bindings" | "groups" | "accounts" | "tasks" | "connections";
export function Overview({
  onNavigate,
  onConnect,
}: {
  onNavigate: (page: Destination, filters?: Record<string, string>) => void;
  onConnect: () => void;
}) {
  const initial = new URLSearchParams(location.hash.split("?")[1]);
  const [account, setAccount] = useState(initial.get("account_id") ?? ""),
    [group, setGroup] = useState(initial.get("group_id") ?? ""),
    [range, setRange] = useState(initial.get("range") ?? "1h"),
    [automatic, setAutomatic] = useState(true),
    [plot, setPlot] = useState("connections");
  useEffect(() => {
    history.replaceState(
      null,
      "",
      "#overview?" +
        queryString({ account_id: account, group_id: group, range }),
    );
  }, [account, group, range]);
  const probeTarget =
    group && group !== "default" ? "HTTPS api.ipify.org" : "generate_204";
  const query = useQuery<OverviewData>(
    "/overview?" +
      queryString({
        account_id: account,
        group_id: group,
        range,
        probe_target: probeTarget,
      }),
    true,
    automatic ? 5000 : 0,
  );
  const d = query.data,
    s = d?.stats,
    points = d?.metrics.points ?? [],
    last = points.at(-1);
  const filter = { account_id: account, group_id: group };
  const cards: {
    name: string;
    value: string | number | undefined;
    detail: string;
    icon: typeof Layers3;
    page: Destination;
    filters: Record<string, string>;
  }[] = [
    {
      name: "可分配库存",
      value: s?.fresh_count,
      detail: `总资源 ${s?.total_ips ?? "—"} · 隔离 ${s?.quarantined_count ?? "—"}`,
      icon: Layers3,
      page: "nodes",
      filters: { state: "FRESH" },
    },
    {
      name: "健康验证",
      value: d ? `${d.health.healthy} / ${d.health.total}` : undefined,
      detail: d
        ? `30 分钟有效证据覆盖 ${d.health.covered} 个`
        : "正在读取验证证据",
      icon: ShieldCheck,
      page: "nodes",
      filters: {},
    },
    {
      name: "活跃会话",
      value: s?.active_bindings,
      detail: `当前容量 ${s?.binding_limit ?? "—"} · 等待 ${s?.waiters ?? "—"}`,
      icon: Users,
      page: "bindings",
      filters: {},
    },
    {
      name: "TCP 连接",
      value: s?.active_connections,
      detail: `窗口峰值 ${d?.metrics.peak ?? "—"} · 新建 ${d?.metrics.summary?.attempts ?? "—"}`,
      icon: Network,
      page: "connections",
      filters: { active: "true" },
    },
    {
      name: "最近采样吞吐",
      value: last ? bytes(last.up_rate + last.down_rate) + "/s" : undefined,
      detail: last
        ? `↓ ${bytes(last.down_rate)}/s · ↑ ${bytes(last.up_rate)}/s`
        : "等待服务端历史采样",
      icon: Gauge,
      page: "connections",
      filters: {},
    },
    {
      name: "连接建立成功率",
      value:
        d?.success_rate != null
          ? `${(d.success_rate * 100).toFixed(1)}%`
          : undefined,
      detail: d?.metrics.summary
        ? `${d.metrics.summary.successes} 成功 / ${d.metrics.summary.attempts} 次尝试`
        : "选定窗口内尚无连接",
      icon: Zap,
      page: "connections",
      filters: {},
    },
    {
      name: "补货任务",
      value: s?.replacement_pending,
      detail: `质量合格 ${d?.quality.PASS ?? 0} · 待检 ${d?.quality.PENDING ?? 0}`,
      icon: Database,
      page: "tasks",
      filters: {},
    },
    {
      name: "上游账号",
      value: d?.accounts_total,
      detail: d ? `异常待处理 ${d.alerts_total} 项` : "正在读取账号状态",
      icon: Activity,
      page: "accounts",
      filters: {},
    },
  ];
  const labels = points.map((p) =>
    new Date(p.at * 1000).toLocaleTimeString("zh-CN", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: range === "15m" ? "2-digit" : undefined,
    }),
  );
  const gauge = (
    name:
      "active_connections" | "fresh_count" | "total_ips" | "quarantined_count",
  ) => points.map((p) => (p.gap ? null : p.gauges[name]));
  const cum = (name: "up" | "down") => {
    let sum = 0;
    return points.map((p) => {
      sum += p[name];
      return p.gap ? null : sum;
    });
  };
  const right =
    plot === "pool"
      ? [
          { name: "总节点", values: gauge("total_ips") },
          { name: "可分配", values: gauge("fresh_count"), color: "#e0ad73" },
          {
            name: "隔离",
            values: gauge("quarantined_count"),
            color: "#c38e9b",
          },
        ]
      : plot === "requests"
        ? [
            {
              name: "建立成功",
              values: points.map((p) => (p.gap ? null : p.successes)),
            },
            {
              name: "建立失败",
              values: points.map((p) => (p.gap ? null : p.failures)),
              color: "#d996a4",
            },
          ]
        : plot === "probes"
          ? [
              {
                name: "探测次数",
                values: points.map((p) => (p.gap ? null : p.probes)),
              },
              {
                name: "成功次数",
                values: points.map((p) => (p.gap ? null : p.probe_ok)),
                color: "#e0ad73",
              },
            ]
          : plot === "cumulative"
            ? [
                { name: "窗口累计下载", values: cum("down"), area: true },
                { name: "窗口累计上传", values: cum("up"), color: "#e0ad73" },
              ]
            : plot === "latency"
              ? [{ name: "成功样本数", values: d?.probes.histogram ?? [] }]
              : [
                  {
                    name: "当前 TCP",
                    values: gauge("active_connections"),
                    area: true,
                  },
                  {
                    name: "窗口采样峰值",
                    values: points.map((p) => (p.gap ? null : p.peak)),
                    color: "#e0ad73",
                  },
                ];
  return (
    <div className="ops-overview page-enter">
      <div className="ops-toolbar">
        <div className="ops-filter-row">
          <Scope
            account={account}
            group={group}
            onAccount={setAccount}
            onGroup={setGroup}
          />
          <select
            aria-label="统计时间范围"
            value={range}
            onChange={(e) => setRange(e.target.value)}
          >
            {[
              ["15m", "最近 15 分钟"],
              ["1h", "最近 1 小时"],
              ["6h", "最近 6 小时"],
              ["24h", "最近 24 小时"],
              ["7d", "最近 7 天"],
            ].map(([v, n]) => (
              <option key={v} value={v}>
                {n}
              </option>
            ))}
          </select>
        </div>
        <div className="ops-filter-row">
          <label className="ops-check">
            <input
              type="checkbox"
              checked={automatic}
              onChange={(e) => setAutomatic(e.target.checked)}
            />
            5 秒刷新
          </label>
          <Refresh onClick={() => void query.refresh()} />
          <Button size="sm" onClick={onConnect}>
            <Network size={13} />
            连接配置
          </Button>
        </div>
      </div>
      <ErrorMessage message={query.error || d?.metrics.error} />
      <div className="overview-quota-row">
        <button
          className="overview-quota-card quota-replacements"
          onClick={() => onNavigate("accounts")}
        >
          <span>
            <RefreshIcon size={17} />
            IP 更换额度
          </span>
          <strong>
            {quotaPair(
              d?.supplier_totals?.replacement.remaining,
              d?.supplier_totals?.replacement.total,
            )}
            <small>次</small>
          </strong>
          <p>
            剩余额度 / 总额度 ·{" "}
            {d?.supplier_totals?.replacement.complete
              ? "所有账号已同步"
              : "仅汇总已同步套餐，可能不完整"}
          </p>
        </button>
        <button
          className="overview-quota-card quota-traffic"
          onClick={() => onNavigate("accounts")}
        >
          <span>
            <Gauge size={17} />
            套餐总流量
          </span>
          <strong>
            {d?.supplier_totals?.traffic.unlimited_plans
              ? "包含不限量套餐"
              : trafficPair(
                  d?.supplier_totals?.traffic.remaining_bytes,
                  d?.supplier_totals?.traffic.total_bytes,
                )}
          </strong>
          <p>
            剩余流量 / 总流量 ·{" "}
            {d?.supplier_totals?.traffic.complete
              ? "所有账号已同步"
              : "仅汇总已同步套餐，可能不完整"}
          </p>
        </button>
      </div>
      <p className="quota-overview-note">
        按所选账号的完整套餐汇总，质量分组不拆分套餐额度；重复套餐去重。
        {d?.supplier_totals?.oldest_checked_at
          ? `最早同步 ${new Date(d.supplier_totals.oldest_checked_at * 1000).toLocaleTimeString("zh-CN", { hour12: false })}`
          : "等待供应商同步"}{" "}
        · 用量按小时聚合
      </p>
      <div className="ops-kpis">
        {cards.map((c) => (
          <button
            className="ops-kpi"
            key={c.name}
            onClick={() =>
              onNavigate(c.page as Destination, { ...filter, ...c.filters })
            }
          >
            <span className="ops-kpi-label">
              <c.icon size={13} />
              {c.name}
              <OpenIcon size={11} />
            </span>
            <strong>{c.value ?? "—"}</strong>
            <small>{c.detail}</small>
          </button>
        ))}
      </div>
      <div className="ops-chart-grid">
        <section className="surface ops-panel">
          <div className="ops-panel-head">
            <div>
              <span className="eyebrow">THROUGHPUT</span>
              <h2>吞吐趋势</h2>
            </div>
            <span>服务端采集 · 自动换算单位</span>
          </div>
          <Chart
            label="服务端历史上传下载吞吐"
            labels={labels}
            series={[
              {
                name: "下载",
                values: points.map((p) => (p.gap ? null : p.down_rate)),
                area: true,
              },
              {
                name: "上传",
                values: points.map((p) => (p.gap ? null : p.up_rate)),
                color: "#e0ad73",
              },
            ]}
            unit=" B/s"
          />
          <div className="ops-chart-foot">
            <span>
              <ArrowDownLeft size={12} />
              窗口下载{" "}
              {d?.metrics.summary ? bytes(d.metrics.summary.down) : "—"}
            </span>
            <span>
              <ArrowUpRight size={12} />
              窗口上传 {d?.metrics.summary ? bytes(d.metrics.summary.up) : "—"}
            </span>
            {!points.length && <span>尚无历史；采样后自动出现</span>}
          </div>
        </section>
        <section className="surface ops-panel">
          <div className="ops-panel-head">
            <div>
              <span className="eyebrow">OBSERVABILITY</span>
              <h2>运行趋势</h2>
            </div>
            <select
              aria-label="运行趋势类型"
              value={plot}
              onChange={(e) => setPlot(e.target.value)}
            >
              <option value="connections">TCP 连接</option>
              <option value="pool">节点池状态</option>
              <option value="latency">探测耗时分布</option>
              <option value="requests">连接建立统计</option>
              <option value="probes">探测任务量</option>
              <option value="cumulative">窗口流量累计</option>
            </select>
          </div>
          <Chart
            label="运行历史趋势"
            labels={
              plot === "latency"
                ? [
                    "<100ms",
                    "100–300",
                    "300–500",
                    "0.5–1s",
                    "1–2s",
                    "2–5s",
                    "≥5s",
                  ]
                : labels
            }
            series={right}
            kind={plot === "latency" ? "bar" : "line"}
            unit={plot === "cumulative" ? " B" : ""}
          />
          <div className="ops-chart-foot">
            {plot === "latency" ? (
              <span>
                目标 {d?.probes.target} · 最近最多 1000 次 · p50{" "}
                {d?.probes.p50?.toFixed(0) ?? "—"}ms / p95{" "}
                {d?.probes.p95?.toFixed(0) ?? "—"}ms
              </span>
            ) : (
              <>
                <span>时间窗口内真实数据</span>
                <span>断线/缺测保留空档</span>
              </>
            )}
          </div>
        </section>
      </div>
      <div className="ops-bottom-grid">
        <section className="surface ops-panel">
          <div className="ops-panel-head">
            <div>
              <span className="eyebrow">RESOURCE INVENTORY</span>
              <h2>账号与国家库存</h2>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onNavigate("accounts")}
            >
              全部账号 <OpenIcon size={12} />
            </Button>
          </div>
          <div className="ops-country-strip">
            {Object.entries(s?.countries ?? {}).map(([code, n]) => (
              <button
                key={code}
                onClick={() =>
                  onNavigate("nodes", { ...filter, country: code })
                }
              >
                {code || "未知"} <b>{n.fresh}</b>
                <small> / {n.total}</small>
              </button>
            ))}
            {!s && <span>正在获取库存</span>}
          </div>
          <div className="ops-table-wrap">
            <table className="ops-table">
              <thead>
                <tr>
                  <th>账号</th>
                  <th>状态</th>
                  <th>可分配 / 总量</th>
                  <th>剩余更换 / 总额</th>
                </tr>
              </thead>
              <tbody>
                {d?.accounts.map((a) => (
                  <tr
                    key={a.account_id}
                    onClick={() =>
                      onNavigate("nodes", {
                        ...filter,
                        account_id: a.account_id,
                      })
                    }
                    className="ops-clickable"
                  >
                    <td>
                      <strong>{a.name}</strong>
                      <small>{a.account_id}</small>
                    </td>
                    <td>
                      <Status state={a.health.state} />
                    </td>
                    <td>
                      <b className="accent">{a.scoped_fresh}</b> /{" "}
                      {a.scoped_total}
                    </td>
                    <td>
                      {quotaPair(
                        a.supplier_usage?.replacement?.remaining,
                        a.supplier_usage?.replacement?.total,
                      )}
                    </td>
                  </tr>
                ))}
                {d && !d.accounts.length && (
                  <tr>
                    <td colSpan={4} className="ops-empty">
                      当前范围没有账号资源
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="ops-chart-foot">
            <span>
              展示 {d?.accounts.length ?? "—"} / {d?.accounts_total ?? "—"}{" "}
              个账号
            </span>
            <span>更换额度来自供应商套餐快照，非本地提交计数</span>
          </div>
        </section>
        <section className="surface ops-panel">
          <div className="ops-panel-head">
            <div>
              <span className="eyebrow">ATTENTION</span>
              <h2>异常与最近事件</h2>
            </div>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onNavigate("tasks", filter)}
            >
              全部记录 <OpenIcon size={12} />
            </Button>
          </div>
          <div className="ops-attention">
            {d?.alerts.map((a) => (
              <button
                className="ops-alert-row"
                key={a.id}
                onClick={() =>
                  onNavigate("tasks", {
                    group_id: a.group_id ?? group,
                    account_id: a.account_id ?? account,
                  })
                }
              >
                <span className="ops-alert-dot" />
                <div>
                  <strong>{a.kind}</strong>
                  <p>{a.message}</p>
                </div>
                <OpenIcon size={13} />
              </button>
            ))}
            {d && !d.alerts.length && (
              <div className="ops-healthy">
                <ShieldCheck size={16} />
                <span>当前范围暂无待处理异常</span>
              </div>
            )}
            {d?.events.slice(0, d.alerts.length ? 2 : 3).map((e) => (
              <div className="ops-event" key={e.id}>
                <Clock3 size={11} />
                <span>{e.message}</span>
                <time>
                  {new Date(e.at * 1000).toLocaleTimeString("zh-CN", {
                    hour12: false,
                  })}
                </time>
              </div>
            ))}
          </div>
          <div className="ops-chart-foot">
            <button onClick={() => onNavigate("groups")}>
              质量分组 <OpenIcon size={11} />
            </button>
            <span>
              明细日志：
              {d?.observer.mode === "all"
                ? "全量"
                : d?.observer.mode === "errors"
                  ? "仅失败"
                  : "关闭"}
            </span>
          </div>
        </section>
      </div>
      <div className="ops-observation-note">
        <span>
          质量通过 {d?.quality.PASS ?? 0} · 未知 {d?.quality.UNKNOWN ?? 0} ·
          基础健康准入 {d?.quality.BASIC ?? 0}
        </span>
        <span>
          {query.updatedAt
            ? `更新 ${new Date(query.updatedAt).toLocaleTimeString("zh-CN", { hour12: false })}`
            : "正在连接"}{" "}
          · 观测丢弃 {d?.observer.dropped_records ?? 0} 条
        </span>
      </div>
    </div>
  );
}
