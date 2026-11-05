import { useState } from "react";
import { api, refreshAll, useQuery, type Paged } from "@/lib/api";
import { queryString, type ConnectionRecord } from "@/lib/v2";
import { bytes } from "@/lib/utils";
import { Dialog } from "../ui/dialog";
import {
  GroupLabel,
  ErrorMessage,
  Pages,
  Refresh,
  Scope,
  Status,
  Time,
} from "./Common";

export function Connections({ notify }: { notify: (v: string) => void }) {
  const initial = new URLSearchParams(location.hash.split("?")[1]);
  const [account, setAccount] = useState(initial.get("account_id") ?? ""),
    [group, setGroup] = useState(initial.get("group_id") ?? ""),
    [active, setActive] = useState(initial.get("active") === "true"),
    [protocol, setProtocol] = useState(""),
    [result, setResult] = useState(""),
    [ip, setIP] = useState(""),
    [target, setTarget] = useState(""),
    [worker, setWorker] = useState(""),
    [session, setSession] = useState(""),
    [since, setSince] = useState(""),
    [until, setUntil] = useState(""),
    [page, setPage] = useState(1),
    [size, setSize] = useState(20),
    [selected, setSelected] = useState<ConnectionRecord | null>(null),
    [saving, setSaving] = useState(false);
  const status = useQuery<{ values: { connection_log_mode: string } }>(
    "/settings",
    true,
    15000,
  );
  const query = useQuery<Paged<ConnectionRecord> & { error?: string }>(
    "/connections?" +
      queryString({
        account_id: account,
        group_id: group,
        active,
        protocol,
        result,
        ip,
        target,
        worker,
        session,
        since: since ? new Date(since).getTime() / 1000 : undefined,
        until: until ? new Date(until).getTime() / 1000 : undefined,
        page,
        page_size: size,
      }),
    true,
    5000,
  );
  const change = (set: (v: string) => void) => (value: string) => {
    set(value);
    setPage(1);
  };
  async function mode(value: string) {
    setSaving(true);
    try {
      await api("/settings", {
        method: "PATCH",
        body: JSON.stringify({ connection_log_mode: value }),
      });
      notify("连接记录采集方式已保存");
      refreshAll();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="page-enter">
      <div className="ops-toolbar">
        <div className="ops-filter-row">
          <Scope
            account={account}
            group={group}
            onAccount={change(setAccount)}
            onGroup={change(setGroup)}
          />
          <label className="ops-check">
            <input
              type="checkbox"
              checked={active}
              onChange={(e) => {
                setActive(e.target.checked);
                setPage(1);
              }}
            />
            当前活跃连接
          </label>
        </div>
        <label className="ops-filter-row">
          明细采集
          <select
            aria-label="连接记录采集方式"
            disabled={saving}
            value={status.data?.values.connection_log_mode ?? "errors"}
            onChange={(e) => void mode(e.target.value)}
          >
            <option value="off">关闭</option>
            <option value="errors">仅失败（默认）</option>
            <option value="all">全量 · 按保留策略清理</option>
          </select>
        </label>
      </div>
      <div className="surface ops-panel">
        <div className="ops-toolbar ops-log-filters">
          <label>
            开始时间
            <input
              type="datetime-local"
              value={since}
              onChange={(e) => change(setSince)(e.target.value)}
            />
          </label>
          <label>
            结束时间
            <input
              type="datetime-local"
              value={until}
              onChange={(e) => change(setUntil)(e.target.value)}
            />
          </label>
          <label>
            协议
            <select
              value={protocol}
              onChange={(e) => change(setProtocol)(e.target.value)}
            >
              <option value="">全部协议</option>
              <option value="socks5">SOCKS5</option>
              <option value="connect">HTTP CONNECT</option>
              <option value="http">HTTP</option>
            </select>
          </label>
          <label>
            结果
            <select
              value={result}
              onChange={(e) => change(setResult)(e.target.value)}
            >
              <option value="">全部结果</option>
              {[
                "success",
                "authentication_rejected",
                "POOL_EXHAUSTED",
                "upstream_transport",
                "upstream_target",
                "protocol_error",
              ].map((x) => (
                <option key={x} value={x}>
                  {x}
                </option>
              ))}
            </select>
          </label>
          <label>
            出口 IP
            <input
              value={ip}
              onChange={(e) => change(setIP)(e.target.value)}
              placeholder="精确出口 IP"
            />
          </label>
          <label>
            目标主机
            <input
              value={target}
              onChange={(e) => change(setTarget)(e.target.value)}
              placeholder="host:port"
            />
          </label>
          <label>
            工位 / Session
            <div className="ops-filter-row">
              <input
                aria-label="筛选工位"
                placeholder="worker"
                value={worker}
                onChange={(e) => change(setWorker)(e.target.value)}
              />
              <input
                aria-label="筛选 Session"
                placeholder="session"
                value={session}
                onChange={(e) => change(setSession)(e.target.value)}
              />
            </div>
          </label>
          <Refresh onClick={() => void query.refresh()} />
        </div>
        <ErrorMessage message={query.error || query.data?.error} />
        <div className="ops-table-wrap">
          <table className="ops-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>协议</th>
                <th>分组 / 工位</th>
                <th>目标</th>
                <th>建立结果</th>
                <th>建立耗时</th>
                <th>存活时长</th>
                <th>流量</th>
                <th>出口</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.items.map((r) => (
                <tr
                  key={r.id}
                  className="ops-clickable"
                  onClick={() => setSelected(r)}
                >
                  <td>
                    <Time value={r.started_at} />
                  </td>
                  <td className="mono">{r.protocol.toUpperCase()}</td>
                  <td>
                    {r.group_id ? <GroupLabel id={r.group_id} /> : "未鉴权"}
                    <small>
                      {r.worker || "匿名"} / {r.session || "—"}
                    </small>
                  </td>
                  <td className="mono">{r.target || "—"}</td>
                  <td>
                    <Status state={r.result} />
                    {r.socks_reply != null && (
                      <small>SOCKS 返回码 {r.socks_reply}</small>
                    )}
                    {r.error_message && <small>{r.error_message}</small>}
                    {r.phase === "active" && <small>连接中</small>}
                  </td>
                  <td>{r.established_ms?.toFixed(0) ?? "—"} ms</td>
                  <td>
                    {(
                      (r.duration_ms ?? Date.now() - r.started_at * 1000) / 1000
                    ).toFixed(1)}{" "}
                    s
                  </td>
                  <td>{bytes(r.up + r.down)}</td>
                  <td className="mono">{r.ip || "未分配"}</td>
                </tr>
              ))}
              {query.data && !query.data.items.length && (
                <tr>
                  <td colSpan={9} className="ops-empty">
                    暂无记录。默认仅采集失败；成功连接可在“当前活跃连接”查看，或显式开启全量采集。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <Pages
          data={query.data}
          page={page}
          setPage={setPage}
          size={size}
          setSize={setSize}
        />
      </div>
      <p className="ops-footnote">
        连接建立成功不代表目标 HTTP 或业务操作成功。SOCKS5 / CONNECT 内部 HTTP
        状态不可观测；不记录密码、Cookie、URL 参数和业务正文。
      </p>
      <Dialog
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        title="连接详情"
        description="仅展示连接元数据，不读取 TLS 内部业务内容。"
      >
        {selected && (
          <div className="ops-detail">
            <Status state={selected.result} />
            <dl>
              {Object.entries({
                目标: selected.target,
                资源组: selected.group_id,
                账号: selected.account_id,
                工位: selected.worker,
                Session: selected.session,
                出口: selected.ip,
                关闭原因: selected.close_reason ?? "仍在连接",
                错误说明: selected.error_message,
                SOCKS命令: selected.socks_command,
                SOCKS返回码: selected.socks_reply,
                代次: selected.generation,
                HTTP状态: "不可观测 / 不适用",
              }).map(([k, v]) => (
                <div className="ops-dl-row" key={k}>
                  <dt>{k}</dt>
                  <dd>
                    {k === "资源组" ? (
                      <GroupLabel id={selected.group_id} />
                    ) : (
                      v || "—"
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}
      </Dialog>
    </div>
  );
}
