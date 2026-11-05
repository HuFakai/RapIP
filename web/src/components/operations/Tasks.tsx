import { useState } from "react";
import { useQuery, type EventData, type Paged, type TaskData } from "@/lib/api";
import { Button } from "../ui/button";
import { operation, queryString } from "@/lib/v2";
import { labels } from "@/lib/utils";
import {
  AccountLabel,
  GroupLabel,
  ErrorMessage,
  Pages,
  Refresh,
  Scope,
  Time,
} from "./Common";
const states: Record<string, string> = {
  COMPLETED: "已完成",
  FAILED: "失败",
  RUNNING: "执行中",
  ACCEPTED: "已受理",
  QUEUED: "待提交",
  PROCESSING: "供应商处理中",
  RECONCILING: "列表对账中",
  SUBMITTING: "提交中",
  UNCERTAIN: "结果待核对",
  BUDGET_BLOCKED: "预算 / 策略阻塞",
};
const causes: Record<string, string> = {
  scheduled: "定时轮换补货",
  manual_supplier: "手动替换",
  manual: "主动切换补货",
  quality: "质量拒绝",
  business: "业务补货",
  collision: "冷却碰撞",
  health: "健康故障",
};
export function Tasks() {
  const initial = new URLSearchParams(location.hash.split("?")[1]);
  const [account, setAccount] = useState(initial.get("account_id") ?? ""),
    [group, setGroup] = useState(initial.get("group_id") ?? ""),
    [state, setState] = useState(""),
    [category, setCategory] = useState(""),
    [page, setPage] = useState(1),
    [size, setSize] = useState(20),
    [detail, setDetail] = useState("");
  const query = useQuery<
    Paged<
      TaskData & {
        group_id?: string;
        cause?: string;
        result?: Record<string, unknown>;
      }
    >
  >(
    "/tasks?" +
      queryString({
        account_id: account,
        group_id: group,
        state,
        category,
        page,
        page_size: size,
      }),
    true,
    4000,
  );
  const waiting = useQuery<{
    items: {
      id: string;
      ip: string;
      state: string;
      error?: string;
      account_id: string;
      replacement?: { state: string; error?: string };
    }[];
  }>("/replacements/requests", true, 4000);
  const [cancelError, setCancelError] = useState("");
  async function cancel(id: string) {
    try {
      await operation(`/replacements/requests/${id}/cancel`);
      await waiting.refresh();
    } catch (e) {
      setCancelError((e as Error).message);
    }
  }
  const events = useQuery<Paged<EventData>>(
    "/events?" +
      queryString({ account_id: account, group_id: group, page_size: 20 }),
    true,
    5000,
  );
  const change = (set: (v: string) => void) => (value: string) => {
    set(value);
    setPage(1);
  };
  return (
    <div className="page-enter">
      {!!waiting.data?.items.length && (
        <section className="surface ops-panel replacement-request-panel">
          <h2>手动更换请求</h2>
          <p className="field-help">
            等待断连的任务已预留额度；开始提交后不能取消，结果不明先对账。
          </p>
          <ErrorMessage message={cancelError} />
          {waiting.data.items.slice(0, 20).map((r) => (
            <div key={r.id} className="replacement-request-row">
              <b className="mono">{r.ip}</b>
              <AccountLabel id={r.account_id} />
              <span>
                {r.replacement
                  ? (states[r.replacement.state] ?? r.replacement.state)
                  : r.state === "WAITING"
                    ? "等待执行条件"
                    : r.state === "CANCELLED"
                      ? "已取消"
                      : r.state}
              </span>
              <small className="ops-warning">
                {r.replacement?.error || r.error}
              </small>
              {r.state === "WAITING" && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void cancel(r.id)}
                >
                  取消等待
                </Button>
              )}
            </div>
          ))}
        </section>
      )}

      <div className="surface ops-panel">
        <div className="ops-toolbar">
          <div className="ops-filter-row">
            <Scope
              account={account}
              group={group}
              onAccount={change(setAccount)}
              onGroup={change(setGroup)}
            />
            <select
              aria-label="筛选任务状态"
              value={state}
              onChange={(e) => change(setState)(e.target.value)}
            >
              <option value="">全部状态</option>
              {Object.entries(states).map(([v, n]) => (
                <option value={v} key={v}>
                  {n}
                </option>
              ))}
            </select>
            <select
              aria-label="筛选任务类别"
              value={category}
              onChange={(e) => change(setCategory)(e.target.value)}
            >
              <option value="">全部任务</option>
              <option value="operation">管理操作</option>
              <option value="replacement">上游替换</option>
            </select>
          </div>
          <Refresh onClick={() => void query.refresh()} />
        </div>
        <ErrorMessage message={query.error} />
        <div className="ops-table-wrap">
          <table className="ops-table">
            <thead>
              <tr>
                <th>任务</th>
                <th>类型 / 原因</th>
                <th>分组 / 账号</th>
                <th>状态</th>
                <th>创建时间</th>
                <th>结果说明</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.items.map((t) => (
                <tr key={t.id}>
                  <td>
                    <button
                      className="ops-link mono"
                      onClick={() => setDetail(detail === t.id ? "" : t.id)}
                    >
                      {t.id.slice(0, 10)}
                    </button>
                    {detail === t.id && (
                      <pre className="ops-task-result">
                        {JSON.stringify(
                          t.result ?? { error: t.error },
                          null,
                          2,
                        )}
                      </pre>
                    )}
                  </td>
                  <td>
                    {labels[t.kind] ?? t.kind}
                    <small>
                      {t.cause
                        ? (causes[t.cause] ?? t.cause)
                        : t.category === "replacement"
                          ? "上游补货"
                          : "管理操作"}
                    </small>
                  </td>
                  <td>
                    <GroupLabel id={t.group_id} />
                    <small>
                      <AccountLabel id={t.account_id} />
                    </small>
                  </td>
                  <td>
                    <span
                      className={`ops-status tone-${t.state === "COMPLETED" ? "good" : t.state === "FAILED" || t.state === "UNCERTAIN" ? "bad" : "neutral"}`}
                    >
                      {states[t.state] ?? t.state}
                    </span>
                  </td>
                  <td>
                    <Time value={t.created_at} />
                  </td>
                  <td className="ops-result-cell">
                    {t.error ??
                      (t.state === "COMPLETED" ? "已完成" : "处理中，自动刷新")}
                  </td>
                </tr>
              ))}
              {query.data && !query.data.items.length && (
                <tr>
                  <td colSpan={6} className="ops-empty">
                    当前范围没有任务
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
      <section className="surface ops-panel ops-events-panel">
        <div className="ops-panel-head">
          <h2>最近事件</h2>
          <span>与当前账号 / 分组筛选一致</span>
        </div>
        <ErrorMessage message={events.error} />
        {events.data?.items.map((e) => (
          <div className="ops-event" key={e.id}>
            <Time value={e.at} />
            <b>{e.kind.toUpperCase()}</b>
            <span>{e.message}</span>
            <small>
              <AccountLabel id={e.account_id} />
            </small>
          </div>
        ))}
      </section>
    </div>
  );
}
