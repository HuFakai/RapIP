import { copyText } from "@/lib/browser";
import { ProxyLinksDialog, type ProxyLink } from "./ProxyLinksDialog";
import { TableLoading } from "../ui/loading";
import { useEffect, useState } from "react";
import {
  ArrowUpDown,
  Search,
  SlidersHorizontal,
  Zap,
  FlaskConical,
} from "lucide-react";
import {
  mutate,
  refreshAll,
  useQuery,
  type NodeData,
  type Paged,
} from "@/lib/api";
import { operation, queryString } from "@/lib/v2";
import { ReplacementDialog } from "./ReplacementDialog";
import { IPQualityDialog } from "./IPQualityDialog";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { CountryFlag } from "../CountryFlag";
import {
  AccountLabel,
  GroupLabel,
  ErrorMessage,
  Pages,
  Refresh,
  Scope,
  Status,
  Time,
} from "./Common";

const columns = [
  ["group", "分组"],
  ["latency", "探测耗时"],
  ["checked", "最近检查"],
  ["fail", "连续失败"],
  ["connections", "连接"],
  ["quality", "质量结论"],
] as const;
export function Nodes({ notify }: { notify: (v: string) => void }) {
  const initial = new URLSearchParams(location.hash.split("?")[1]);
  const [account, setAccount] = useState(initial.get("account_id") ?? ""),
    [group, setGroup] = useState(initial.get("group_id") ?? ""),
    [state, setState] = useState(initial.get("state") ?? ""),
    [quality, setQuality] = useState(initial.get("quality") ?? ""),
    [country, setCountry] = useState(initial.get("country") ?? ""),
    [draft, setDraft] = useState(""),
    [q, setQ] = useState("");
  const [sort, setSort] = useState(initial.get("sort") ?? "ip"),
    [direction, setDirection] = useState(initial.get("direction") ?? "asc"),
    [page, setPage] = useState(Math.max(1, Number(initial.get("page")) || 1)),
    [size, setSize] = useState(20),
    [selected, setSelected] = useState<NodeData | null>(null),
    [busy, setBusy] = useState(""),
    [reportNode, setReportNode] = useState<NodeData | null>(null);
  const [copyDialog, setCopyDialog] = useState(false);
  async function copyProxy(node: NodeData, protocol: "socks5" | "http") {
    setBusy(node.id);
    try {
      const result = await mutate<ProxyLink>("/proxy-links", {
        node_id: node.id,
      });
      refreshAll();
      await copyText(result.links[protocol]);
      notify(
        `${result.reserved ? "已预留此 IP 并复制" : "已复制当前绑定链接"} · 工位 ${result.worker_id ?? "匿名"}`,
      );
    } catch (e) {
      notify((e as Error).message + "；若已预留可再次点击复制");
    } finally {
      setBusy("");
      void query.refresh();
    }
  }
  async function cancelReservation(node: NodeData) {
    setBusy(node.id);
    try {
      await operation(`/bindings/${node.binding_id}/cancel-reservation`);
      notify("预留已取消，旧链接失效；未消耗更换额度");
      refreshAll();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  const [checked, setChecked] = useState<NodeData[]>([]);
  const [replacing, setReplacing] = useState<NodeData[] | null>(null);
  const [visible, setVisible] = useState<string[]>(() => {
    try {
      const parsed = JSON.parse(
        localStorage.getItem("rapip.nodeColumns") ?? "null",
      );
      return Array.isArray(parsed)
        ? parsed.filter(
            (v): v is string =>
              typeof v === "string" && columns.some((c) => c[0] === v),
          )
        : columns.map((c) => c[0]);
    } catch {
      return columns.map((c) => c[0]);
    }
  });
  useEffect(() => {
    history.replaceState(
      null,
      "",
      "#nodes?" +
        queryString({
          account_id: account,
          group_id: group,
          state,
          quality,
          country,
          q,
          sort,
          direction,
          page,
          page_size: size,
        }),
    );
  }, [account, group, state, quality, country, q, sort, direction, page, size]);
  const query = useQuery<Paged<NodeData>>(
    "/nodes?" +
      queryString({
        account_id: account,
        group_id: group,
        state,
        quality,
        country,
        q,
        sort,
        direction,
        page,
        page_size: size,
      }),
    true,
    5000,
  );
  const change = (set: (v: string) => void) => (v: string) => {
    set(v);
    setPage(1);
  };
  function toggle(name: string) {
    const next = visible.includes(name)
      ? visible.filter((x) => x !== name)
      : [...visible, name];
    setVisible(next);
    try {
      localStorage.setItem("rapip.nodeColumns", JSON.stringify(next));
    } catch {
      /* preference only */
    }
  }
  function sorting(value: string) {
    setDirection(sort === value && direction === "asc" ? "desc" : "asc");
    setSort(value);
    setPage(1);
  }
  async function probe(node: NodeData) {
    setBusy(node.id);
    notify("正在执行只读探测，不调用 Replace");
    try {
      await operation(`/nodes/${node.id}/probe`);
      notify("只读探测完成，可查看任务结果");
      void query.refresh();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  const has = (name: string) => visible.includes(name);
  const current =
    query.data?.items.find((n) => n.id === selected?.id) ?? selected;
  return (
    <div className="surface ops-panel page-enter">
      {copyDialog && (
        <ProxyLinksDialog
          initialGroup={group}
          close={() => setCopyDialog(false)}
          notify={notify}
        />
      )}
      {replacing && (
        <ReplacementDialog
          nodes={replacing}
          close={() => setReplacing(null)}
          notify={notify}
        />
      )}
      <form
        className="ops-toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          setQ(draft);
          setPage(1);
        }}
      >
        <div className="ops-filter-row">
          <div className="ops-search">
            <Search size={13} />
            <input
              aria-label="搜索 IP 或 CIDR"
              placeholder="IP / CIDR / 节点 ID"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          </div>
          <Scope
            account={account}
            group={group}
            onAccount={change(setAccount)}
            onGroup={change(setGroup)}
          />
          <select
            aria-label="筛选资源状态"
            value={state}
            onChange={(e) => change(setState)(e.target.value)}
          >
            <option value="">全部状态</option>
            {[
              "FRESH",
              "RESERVED",
              "IN_USE",
              "DRAINING",
              "QUARANTINED",
              "REPLACING",
            ].map((v) => (
              <option key={v} value={v}>
                {
                  {
                    FRESH: "待分配",
                    RESERVED: "已预留·待连接",
                    IN_USE: "使用中",
                    DRAINING: "排空中",
                    QUARANTINED: "隔离",
                    REPLACING: "替换中",
                  }[v]
                }
              </option>
            ))}
          </select>
          <select
            aria-label="筛选质量"
            value={quality}
            onChange={(e) => change(setQuality)(e.target.value)}
          >
            <option value="">全部质量结论</option>
            {["PASS", "FAIL", "UNKNOWN", "EXPIRED", "PENDING", "BASIC"].map(
              (v) => (
                <option key={v}>{v}</option>
              ),
            )}
          </select>
          <input
            className="ops-country-input"
            aria-label="筛选国家代码"
            placeholder="国家 US"
            maxLength={2}
            value={country}
            onChange={(e) => change(setCountry)(e.target.value.toUpperCase())}
          />
          <Button variant="secondary" size="sm" type="submit">
            检索
          </Button>
        </div>
        <div className="ops-filter-row">
          <Button size="sm" type="button" onClick={() => setCopyDialog(true)}>
            复制代理链接
          </Button>
          <Button
            variant="secondary"
            size="sm"
            type="button"
            disabled={!checked.length}
            onClick={() => setReplacing(checked)}
          >
            更换已选 IP（{checked.length}）
          </Button>
          {!!checked.length && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setChecked([])}
            >
              清空选择
            </Button>
          )}
          <details className="ops-column-picker">
            <summary>
              <SlidersHorizontal size={13} />
              列设置
            </summary>
            <div>
              {columns.map(([key, label]) => (
                <label key={key}>
                  <input
                    type="checkbox"
                    checked={has(key)}
                    onChange={() => toggle(key)}
                  />
                  {label}
                </label>
              ))}
            </div>
          </details>
          <Refresh
            onClick={() => void query.refresh()}
            busy={query.isFetching}
          />
        </div>
      </form>
      <ErrorMessage message={query.error} />
      <div className="ops-table-wrap">
        <table className="ops-table ops-node-table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  aria-label="选择本页 IP"
                  checked={
                    !!query.data?.items.length &&
                    query.data.items.every((n) =>
                      checked.some((c) => c.id === n.id),
                    )
                  }
                  onChange={(e) =>
                    setChecked(
                      e.target.checked
                        ? [
                            ...checked.filter(
                              (n) =>
                                !query.data?.items.some((c) => c.id === n.id),
                            ),
                            ...(query.data?.items ?? []),
                          ]
                        : checked.filter(
                            (n) =>
                              !query.data?.items.some((c) => c.id === n.id),
                          ),
                    )
                  }
                />
              </th>
              <th>
                <button onClick={() => sorting("ip")}>
                  出口 IP <ArrowUpDown size={11} />
                </button>
              </th>
              <th>国家 / 账号</th>
              {has("group") && <th>资源组</th>}
              <th>状态</th>
              {has("quality") && <th>质量准入</th>}
              {has("latency") && (
                <th>
                  <button onClick={() => sorting("latency_ms")}>
                    探测耗时 <ArrowUpDown size={11} />
                  </button>
                </th>
              )}
              {has("checked") && (
                <th>
                  <button onClick={() => sorting("checked_at")}>
                    最近检查 <ArrowUpDown size={11} />
                  </button>
                </th>
              )}
              {has("fail") && (
                <th>
                  <button onClick={() => sorting("fail_count")}>
                    连续失败 <ArrowUpDown size={11} />
                  </button>
                </th>
              )}
              {has("connections") && <th>连接 / 工位</th>}
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {query.isLoading && (
              <TableLoading
                columns={5 + visible.length}
                label="正在加载 IP 资源…"
              />
            )}
            {query.data?.items.map((n) => (
              <tr key={n.id}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`选择 IP ${n.ip}`}
                    checked={checked.some((c) => c.id === n.id)}
                    onChange={(e) =>
                      setChecked(
                        e.target.checked
                          ? [...checked, n]
                          : checked.filter((c) => c.id !== n.id),
                      )
                    }
                  />
                </td>
                <td>
                  <button
                    className="ops-link mono"
                    onClick={() => setSelected(n)}
                  >
                    {n.ip}
                  </button>
                </td>
                <td>
                  <span className="country-code">
                    <CountryFlag code={n.country} />
                    {n.country || "—"}
                  </span>
                  <small>
                    <AccountLabel id={n.account_id} />
                  </small>
                </td>
                {has("group") && (
                  <td>
                    <GroupLabel id={n.group_id} />
                  </td>
                )}
                <td>
                  <Status state={n.state} />
                </td>
                {has("quality") && (
                  <td>
                    <Status
                      state={
                        n.quality.state ??
                        (n.group_id === "default" ? "BASIC" : "PENDING")
                      }
                    />
                  </td>
                )}
                {has("latency") && (
                  <td>
                    {n.latency_ms != null
                      ? `${n.latency_ms.toFixed(0)} ms`
                      : "—"}
                  </td>
                )}
                {has("checked") && (
                  <td>
                    <Time value={n.checked_at} />
                  </td>
                )}
                {has("fail") && <td>{n.fail_count}</td>}
                {has("connections") && (
                  <td>
                    {n.connections} 条<small>{n.worker ?? "未绑定"}</small>
                  </td>
                )}
                <td>
                  <div className="ops-inline-actions">
                    {(["socks5", "http"] as const).map((protocol) => (
                      <Button
                        key={protocol}
                        size="sm"
                        variant="secondary"
                        disabled={
                          !!busy ||
                          !["FRESH", "RESERVED", "IN_USE"].includes(n.state)
                        }
                        onClick={() => void copyProxy(n, protocol)}
                        title={
                          n.state === "FRESH"
                            ? "立即预留此 IP 并复制完整链接"
                            : "复制当前绑定的完整链接"
                        }
                      >
                        {protocol.toUpperCase()}
                      </Button>
                    ))}
                    {n.state === "RESERVED" && (
                      <Button
                        size="sm"
                        variant="secondary"
                        disabled={!!busy}
                        onClick={() => void cancelReservation(n)}
                      >
                        取消预留
                      </Button>
                    )}
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setReplacing([n])}
                      disabled={n.state === "RESERVED"}
                      title={
                        n.state === "RESERVED"
                          ? "请先使用链接或取消预留"
                          : undefined
                      }
                    >
                      更换 IP
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setSelected(n)}
                    >
                      详情
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => setReportNode(n)}
                      disabled={
                        ![
                          "FRESH",
                          "RESERVED",
                          "QUARANTINED",
                          "IN_USE",
                        ].includes(n.state)
                      }
                      aria-label={`查看 IP ${n.ip} 质量报告`}
                    >
                      <FlaskConical size={13} />
                      质量
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={
                        !!busy ||
                        n.used ||
                        !["FRESH", "QUARANTINED"].includes(n.state)
                      }
                      onClick={() => void probe(n)}
                      title="只读检测，不调用 Replace"
                    >
                      <Zap size={12} />
                      {busy === n.id ? "处理中" : "探测"}
                    </Button>
                  </div>
                </td>
              </tr>
            ))}
            {query.data && !query.data.items.length && (
              <tr>
                <td colSpan={12} className="ops-empty">
                  没有符合筛选条件的资源
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
      <IPQualityDialog
        key={reportNode?.id ?? "closed"}
        node={reportNode}
        onClose={() => setReportNode(null)}
      />
      <Dialog
        open={!!selected}
        onOpenChange={(open) => {
          if (!open) setSelected(null);
        }}
        title="节点详情"
        description="资源状态与质量结论分别展示；只读探测不会触发替换。"
      >
        {current && (
          <div className="ops-detail">
            <div className="ops-detail-title">
              <CountryFlag code={current.country} />
              <strong className="mono">{current.ip}</strong>
              <Status state={current.state} />
            </div>
            <dl>
              <dt>资源组</dt>
              <dd>
                <GroupLabel id={current.group_id} />
              </dd>
              <dt>账号</dt>
              <dd>
                <AccountLabel id={current.account_id} showId />
              </dd>
              <dt>当前原因</dt>
              <dd>{current.reason}</dd>
              <dt>稳定资源席位</dt>
              <dd className="mono">{current.slot_id}</dd>
              <dt>质量结论</dt>
              <dd>
                <Status state={current.quality.state ?? "BASIC"} />
              </dd>
              <dt>质量有效至</dt>
              <dd>
                <Time value={current.quality.expires_at} />
              </dd>
              <dt>连接 / 工位</dt>
              <dd>
                {current.connections} / {current.worker ?? "—"}
              </dd>
            </dl>
            {current.quality.reasons?.map((r) => (
              <p className="ops-error" key={r}>
                {r}
              </p>
            ))}
            {current.quality.evidence && (
              <details>
                <summary>检测证据与来源</summary>
                <pre>{JSON.stringify(current.quality.evidence, null, 2)}</pre>
              </details>
            )}
            <Button
              variant="secondary"
              disabled={!!busy || current.used || current.state === "RESERVED"}
              onClick={() => void probe(current)}
            >
              执行只读探测
            </Button>
          </div>
        )}
      </Dialog>
    </div>
  );
}
