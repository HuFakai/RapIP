import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import {
  ArrowUp,
  ArrowLeft,
  ArrowRight,
  ChevronLeft,
  ChevronRight,
  Clock3,
  GitBranch,
  Layers3,
  Maximize2,
  Minimize2,
  Radio,
  Search,
} from "lucide-react";
import { useQuery, type BindingData, type Paged } from "@/lib/api";
import { queryString, type Group } from "@/lib/v2";
import { date } from "@/lib/utils";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { LoadingState } from "../ui/loading";
import { ErrorMessage, GroupLabel, Status } from "./Common";

const labels: Record<string, string> = {
  issued: "生成待用链接",
  reserved: "为链接预留 IP",
  assigned: "链接获配 IP",
  connected: "链接首次连通",
  switched: "链接切换出口",
  draining: "等待旧连接结束",
  fault_retired: "旧出口因故障退役",
  released: "解除链接绑定",
  admission: "IP 准入检查",
  moved: "IP 转入分组",
  baseline: "开始追踪时已绑定",
  in_place_started: "申请替换原 IP",
  in_place_join: "更新待接收 session",
  in_place_admission: "检查新 IP",
  in_place_failed: "原位替换未完成",
};
const statuses: Record<string, string> = {
  PASS: "准入通过",
  QUEUED: "已排队",
  SUBMITTING: "正在提交",
  PROCESSING: "供应商处理",
  RECONCILING: "核对库存",
  COMPLETED: "已完成",
  FAILED: "失败",
  UNCERTAIN: "结果待核对",
  BUDGET_BLOCKED: "额度不足",
  CHECKING: "待检测",
  QUARANTINED: "已隔离",
  FRESH: "待分配",
  IN_USE: "已准入",
  CANCELLED: "已取消",
  graceful: "平滑切换",
  hard: "强制切换",
  EXISTING: "已有绑定",
  RESERVED: "已预留",
};
const sources: Record<string, string> = {
  client_session: "客户端更换 session",
  scheduled: "定时轮换",
  manual: "手动切换",
  manual_supplier: "手动供应商替换",
  client_in_place: "客户端原位替换",
};
const runningSupplier = new Set([
  "QUEUED",
  "SUBMITTING",
  "PROCESSING",
  "RECONCILING",
]);
interface Selection {
  type: string;
  country_code?: string;
  ip_ranges?: string[];
  asn_numbers?: string[];
}
function selectionLabel(s: Selection) {
  if (s.type === "country") return `国家 ${s.country_code}`;
  if (s.type === "ip_range") return s.ip_ranges?.join("、");
  if (s.type === "asn") return `ASN ${s.asn_numbers?.join("、")}`;
  return "随机";
}
interface Entry {
  id: string;
  at: number;
  kind: string;
  group_id: string;
  binding_id?: string;
  slot_id?: string;
  old_slot_id?: string;
  worker?: string;
  session?: string;
  old_session?: string;
  generation?: number;
  ip?: string;
  old_ip?: string;
  new_ip?: string;
  job_id?: string;
  supplier_id?: string;
  previous_supplier_id?: string;
  status?: string;
  reason?: string;
  source?: string;
  baseline?: boolean;
  historical?: boolean;
  selection?: Selection;
  preferred_selection?: Selection;
  fallback_reason?: string;
  attempt?: number;
  release_action?: "unused_reservation" | "used_exit" | "other";
}
interface Chain {
  id: string;
  status?: string;
  events: Entry[];
}
function traceLink(params: Record<string, string>) {
  return "#lifecycle?" + queryString(params);
}
const supplierTitles: Record<string, string> = {
  QUEUED: "旧 IP 排队更换",
  SUBMITTING: "提交 Webshare",
  PROCESSING: "Webshare 正在换 IP",
  RECONCILING: "确认新旧 IP",
  COMPLETED: "Webshare 已换号",
  FAILED: "Webshare 更换失败",
  UNCERTAIN: "更换结果待核对",
  BUDGET_BLOCKED: "更换额度不足",
  CANCELLED: "更换已取消",
};
const ipStates: Record<string, string> = {
  FRESH: "待分配",
  RESERVED: "已预留",
  IN_USE: "已绑定",
  DRAINING: "旧连接排空中",
  REPLACING: "更换中",
  QUARANTINED: "隔离中",
};
function title(e: Entry) {
  if (e.kind === "webshare")
    return e.status === "COMPLETED" && !e.new_ip
      ? "新 IP 未能确认"
      : (supplierTitles[e.status ?? ""] ?? "Webshare 更换");
  if (e.kind === "released" && e.release_action === "unused_reservation")
    return "取消未使用预留";
  if (e.kind === "assigned" && e.reason === "预留链接已启用，会话独占")
    return "预留链接已启用";
  if (e.kind === "in_place_admission" && e.status === "PASS")
    return "新 IP 通过准入";
  if (e.kind === "admission" && e.status)
    return e.status === "FRESH" || e.status === "IN_USE"
      ? "IP 通过准入"
      : e.status === "QUARANTINED"
        ? "IP 隔离"
        : "IP 准入检查";
  if (e.kind === "baseline" && e.status === "RESERVED")
    return "开始追踪时已预留";
  return labels[e.kind] ?? e.kind;
}
function ipHref(ip: string, slot = "") {
  return traceLink({ view: "ips", q: ip, slot_id: slot });
}
function IPValue({ ip, slot = "" }: { ip: string; slot?: string }) {
  return (
    <a className="trace-value" href={ipHref(ip, slot)} title={`追踪 IP ${ip}`}>
      <code>{ip}</code>
    </a>
  );
}
interface IPPath {
  slot_id: string;
  current: { ip: string; state: string; group_id: string } | null;
  changes: { ip: string; new_ip: string; at: number; group_id: string }[];
}
function IPPaths({
  paths,
  focusedIP,
  slot,
}: {
  paths: IPPath[];
  focusedIP: string;
  slot: string;
}) {
  return (
    <aside className="surface trace-focus">
      <header>
        <Layers3 size={15} />
        <strong>
          {focusedIP ? (
            <>
              正在追踪 <code>{focusedIP}</code>
            </>
          ) : (
            "正在追踪同一资源席位"
          )}
        </strong>
        {paths.length > 1 && <span>{paths.length} 个不同席位</span>}
      </header>
      {paths.map((path) => (
        <div key={path.slot_id} className="trace-focus-row">
          <div className="trace-focus-current">
            <span>
              席位 <code>{path.slot_id.slice(0, 8)}</code>
            </span>
            {path.current ? (
              <>
                <b>当前 IP</b>
                <IPValue ip={path.current.ip} slot={path.slot_id} />
                <span>
                  {ipStates[path.current.state] ?? path.current.state}
                </span>
                <GroupLabel id={path.current.group_id} showId={false} />
              </>
            ) : (
              <span>此席位暂无当前 IP</span>
            )}
          </div>
          {path.changes.length ? (
            <ol className="trace-ip-history" aria-label="Webshare 更换历史">
              {path.changes.map((change) => (
                <li key={`${change.at}:${change.ip}:${change.new_ip}`}>
                  <time>{date(change.at)}</time>
                  <IPValue ip={change.ip} slot={path.slot_id} />
                  <ArrowRight size={13} aria-label="更换为" />
                  <IPValue ip={change.new_ip} slot={path.slot_id} />
                </li>
              ))}
            </ol>
          ) : (
            <p>没有查到已确认的 Webshare 更换记录。</p>
          )}
        </div>
      ))}
      {!paths.length && (
        <p>没有查到这个 IP 的资源席位，仍可查看下方直接匹配的记录。</p>
      )}
      {!!slot && focusedIP && (
        <a className="trace-text-link" href={ipHref(focusedIP)}>
          查看该 IP 的所有席位
        </a>
      )}
    </aside>
  );
}
function duration(seconds: number) {
  if (seconds < 1) return `${Math.max(0, Math.round(seconds * 1000))} ms`;
  if (seconds < 60) return `${seconds.toFixed(1)} s`;
  if (seconds < 3600)
    return `${Math.floor(seconds / 60)} 分 ${Math.floor(seconds % 60)} 秒`;
  return `${Math.floor(seconds / 3600)} 小时 ${Math.floor((seconds % 3600) / 60)} 分`;
}
function laneElapsed(items: Entry[], running: boolean, now: number) {
  if (!items.length || (items.length === 1 && !running)) return "—";
  return duration((running ? now : items[0].at) - items[items.length - 1].at);
}

function Lane({
  name,
  items,
  supplier,
  expanded,
  onPick,
  running,
  now,
}: {
  name: string;
  items: Entry[];
  supplier?: boolean;
  expanded: boolean;
  onPick: (entry: Entry) => void;
  running: boolean;
  now: number;
}) {
  const viewport = useRef<HTMLDivElement>(null);
  const nodes = useRef(new Map<string, HTMLButtonElement>());
  const [cols, setCols] = useState(3);
  const [position, setPosition] = useState(0);
  const [edges, setEdges] = useState({ start: true, end: false });
  const topId = items[0]?.id;
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const update = () => {
      setCols(Math.max(1, Math.floor(el.clientWidth / 158)));
      setEdges({
        start: el.scrollLeft < 3,
        end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 3,
      });
    };
    const observer = new ResizeObserver(update);
    observer.observe(el);
    el.addEventListener("scroll", update, { passive: true });
    update();
    return () => {
      observer.disconnect();
      el.removeEventListener("scroll", update);
    };
  }, [expanded, topId, items.length]);
  function navigate(step: number) {
    if (expanded) {
      const next = Math.max(0, Math.min(items.length - 1, position + step));
      const node = nodes.current.get(items[next]?.id);
      node?.focus({ preventScroll: true });
      node?.scrollIntoView({ block: "nearest", inline: "nearest" });
      setPosition(next);
    } else {
      const el = viewport.current;
      if (!el) return;
      el.scrollBy({
        left: step * Math.max(158, el.clientWidth - 70),
        behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
      });
    }
  }
  const rows = expanded
    ? Array.from({ length: Math.ceil(items.length / cols) }, (_, i) =>
        items.slice(i * cols, (i + 1) * cols),
      )
    : [items];
  return (
    <div className={`trace-lane ${supplier ? "supplier" : "business"}`}>
      <div className="trace-lane-label">
        <b>
          {supplier ? <Layers3 size={13} /> : <GitBranch size={13} />}
          {name}
        </b>
        <small>
          <Clock3 size={12} />
          {laneElapsed(items, running, now)}
          {running && <i title="进行中" />}
        </small>
        {items.length > 1 && (
          <div className="trace-stepper">
            <button
              type="button"
              title="查看更新的节点"
              aria-label={`${name}查看更新的节点`}
              disabled={expanded ? position === 0 : edges.start}
              onClick={() => navigate(-1)}
            >
              <ChevronLeft size={14} />
            </button>
            <button
              type="button"
              title="查看更早的节点"
              aria-label={`${name}查看更早的节点`}
              disabled={expanded ? position >= items.length - 1 : edges.end}
              onClick={() => navigate(1)}
            >
              <ChevronRight size={14} />
            </button>
          </div>
        )}
      </div>
      <div
        className={`trace-viewport ${expanded ? "snake" : ""}`}
        ref={viewport}
        style={
          {
            "--snake-width": `${Math.min(cols, items.length) * 138 + Math.max(0, Math.min(cols, items.length) - 1) * 20}px`,
          } as CSSProperties
        }
      >
        {!items.length && <span className="trace-no-stage">— 没有记录</span>}
        {rows.map((row, rowIndex) => (
          <div
            className={`trace-track ${expanded && rowIndex % 2 ? "reverse" : ""}`}
            key={rowIndex}
          >
            {row.map((e, i) => {
              const index = expanded ? rowIndex * cols + i : i;
              const previous = items[index + 1];
              const stamp = new Date(e.at * 1000).toLocaleTimeString("zh-CN", {
                hour12: false,
              });
              return (
                <div className="trace-segment" key={e.id}>
                  {i > 0 &&
                    (expanded && rowIndex % 2 ? (
                      <ArrowRight
                        className="trace-arrow"
                        size={14}
                        aria-hidden="true"
                      />
                    ) : (
                      <ArrowLeft
                        className="trace-arrow"
                        size={14}
                        aria-hidden="true"
                      />
                    ))}
                  <button
                    ref={(element) => {
                      if (element) nodes.current.set(e.id, element);
                      else nodes.current.delete(e.id);
                    }}
                    className={`trace-node ${e.status === "FAILED" || e.status === "UNCERTAIN" || e.status === "QUARANTINED" ? "needs-attention" : ""}`}
                    onFocus={() => setPosition(index)}
                    onClick={() => onPick(e)}
                    title={`${title(e)} · ${date(e.at)}${e.reason ? "\n" + e.reason : ""}`}
                  >
                    <strong>{title(e)}</strong>
                    <code>{e.new_ip || e.ip || e.session || "—"}</code>
                    <small>
                      <time>{stamp}</time>
                      {previous && <span>+{duration(e.at - previous.at)}</span>}
                    </small>
                  </button>
                  {expanded &&
                    i === row.length - 1 &&
                    rowIndex < rows.length - 1 && (
                      <ArrowUp
                        className="trace-turn"
                        size={14}
                        aria-hidden="true"
                      />
                    )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

function TraceCard({
  chain,
  onPick,
  now,
}: {
  chain: Chain;
  onPick: (e: Entry) => void;
  now: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const events = chain.events;
  const supplier = events.filter((e) =>
    ["draining", "webshare", "admission", "in_place_admission"].includes(
      e.kind,
    ),
  );
  const business = events.filter((e) => !supplier.includes(e));
  const latest = events[0];
  const summary =
    business.find((e) => e.kind === "switched" || e.kind === "in_place_join") ??
    business[0] ??
    latest;
  const supplierLatest = supplier.find((e) => e.kind === "webshare");
  const switched = business.find((e) => e.kind === "switched");
  const released = business.find((e) => e.kind === "released");
  const reserved = business.find(
    (e) => e.kind === "reserved" || e.status === "RESERVED",
  );
  const connected = business.find(
    (e) => e.kind === "connected" || e.kind === "assigned",
  );
  const supplierChange = supplier.find(
    (e) =>
      e.kind === "webshare" &&
      e.status === "COMPLETED" &&
      e.new_ip &&
      e.ip &&
      e.ip !== e.new_ip,
  );
  const supplierActive =
    !!supplierLatest && runningSupplier.has(supplierLatest.status ?? "");
  const businessActive = chain.status === "RUNNING";
  const state =
    chain.status === "RESERVED"
      ? "已预留 · 待连接"
      : chain.status === "CURRENT"
        ? "当前绑定"
        : chain.status === "UNAVAILABLE"
          ? "当前出口不可用"
          : chain.status === "RUNNING"
            ? "切换中"
            : chain.status === "REVIEW"
              ? "切换待处理"
              : released
                ? "绑定已解除"
                : latest.kind === "issued"
                  ? "待首次连接"
                  : events.some((e) => e.kind === "in_place_failed")
                    ? "切换未完成"
                    : "历史";
  const businessNote =
    released?.release_action === "unused_reservation"
      ? "预留未使用，已解除，没有更换这个 IP"
      : released && reserved && !connected && !switched
        ? "预留已解除；这段记录没有显示链接连通过"
        : businessActive && !switched
          ? "仍在等待 Webshare 或新 IP 准入"
          : !switched && connected
            ? connected.kind === "connected"
              ? "链接已连通；这一代沿用此出口"
              : "链接已获配出口；这段记录未见首次连通"
            : !switched && reserved && !released
              ? "IP 已预留给该链接，等待客户端首次连接"
              : "";
  const cardIP = switched?.ip || summary.ip || supplierLatest?.ip;
  return (
    <article className={`trace-card ${chain.status ? "trace-current" : ""}`}>
      <header className="trace-card-head">
        <div className="trace-card-ident">
          <span
            className={`trace-status ${chain.status ? "on" : ""} ${chain.status === "REVIEW" || chain.status === "UNAVAILABLE" ? "warning" : ""}`}
            key={state}
          >
            <i />
            {state}
          </span>
          <b>
            {summary.worker
              ? `工位 ${summary.worker}`
              : summary.binding_id
                ? "匿名会话"
                : "资源链路"}
          </b>
          <GroupLabel id={summary.group_id} showId={false} />
          {!!summary.generation && (
            <span className="muted">第 {summary.generation} 代</span>
          )}
          {summary.session && (
            <code title={summary.session}>{summary.session}</code>
          )}
        </div>
        <div className="trace-card-tools">
          <time title={date(latest.at)}>{date(latest.at)}</time>
          <span title="记录跨度：首条事件到末条事件或当前时间">
            <Clock3 size={13} />
            {laneElapsed(events, businessActive || supplierActive, now)}
          </span>
          {Math.max(supplier.length, business.length) > 2 && (
            <Button
              size="sm"
              variant="secondary"
              aria-expanded={expanded}
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
              {expanded ? "收起" : "展开"}
            </Button>
          )}
        </div>
      </header>
      <div className="trace-outcome">
        {business.length > 0 && (
          <div className="trace-outcome-row">
            <span className="trace-outcome-label">
              <GitBranch size={13} />
              链接
            </span>
            {switched?.old_session &&
              switched.old_session !== switched.session && (
                <>
                  <code title="旧 session">{switched.old_session}</code>
                  <ArrowRight size={12} />
                  <code title="新 session">{switched.session}</code>
                  <span className="trace-outcome-divider" />
                </>
              )}
            {switched?.old_ip && switched.old_ip !== cardIP && (
              <>
                <IPValue ip={switched.old_ip} slot={switched.old_slot_id} />
                <ArrowRight size={13} />
              </>
            )}
            {cardIP && (
              <IPValue
                ip={cardIP}
                slot={switched?.slot_id ?? summary.slot_id}
              />
            )}
            {switched?.source && (
              <span>{sources[switched.source] ?? switched.source}</span>
            )}
            {businessNote && <span>{businessNote}</span>}
            {summary.binding_id && (
              <a
                className="trace-text-link"
                href={traceLink({
                  view: "links",
                  ...(summary.worker
                    ? { worker: summary.worker, group_id: summary.group_id }
                    : { binding_id: summary.binding_id }),
                })}
              >
                看工位历次变化
              </a>
            )}
          </div>
        )}
        {supplier.length > 0 && (
          <div className="trace-outcome-row trace-outcome-supplier">
            <span className="trace-outcome-label">
              <Layers3 size={13} />
              资源
            </span>
            {supplierChange ? (
              <>
                <IPValue
                  ip={supplierChange.ip!}
                  slot={supplierChange.slot_id}
                />
                <ArrowRight size={13} />
                <IPValue
                  ip={supplierChange.new_ip!}
                  slot={supplierChange.slot_id}
                />
                <span>Webshare 已更换</span>
              </>
            ) : (
              <>
                {(supplierLatest?.ip || supplier[0].ip) && (
                  <IPValue
                    ip={(supplierLatest?.ip || supplier[0].ip)!}
                    slot={supplierLatest?.slot_id ?? supplier[0].slot_id}
                  />
                )}
                <span>{title(supplierLatest ?? supplier[0])}</span>
              </>
            )}
          </div>
        )}
      </div>
      {business.length > 0 && (
        <Lane
          name="链接事件"
          items={business}
          expanded={expanded}
          onPick={onPick}
          running={businessActive}
          now={now}
        />
      )}
      {supplier.length > 0 && (
        <Lane
          name="资源事件"
          items={supplier}
          supplier
          expanded={expanded}
          onPick={onPick}
          running={supplierActive}
          now={now}
        />
      )}
    </article>
  );
}

export function Lifecycle() {
  const initial = new URLSearchParams(location.hash.split("?")[1]);
  const view = initial.get("view") === "ips" ? "ips" : "links";
  const group = initial.get("group_id") ?? "",
    binding = initial.get("binding_id") ?? "";
  const slot = initial.get("slot_id") ?? "",
    job = initial.get("job_id") ?? "",
    q = initial.get("q") ?? "";
  const [draft, setDraft] = useState(q),
    [page, setPage] = useState(1);
  const [picked, setPicked] = useState<Entry | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const groups = useQuery<{ items: Group[] }>("/groups", true, 15000);
  const size = 12;
  const worker = initial.get("worker") ?? "";
  const focusedIP =
    view === "ips" &&
    (/^\d{1,3}(\.\d{1,3}){3}$/.test(q) ||
      (q.includes(":") && /^[0-9a-f:]+$/i.test(q)))
      ? q
      : "";
  const query = useQuery<
    Paged<Chain> & {
      paths?: IPPath[];
      focus?: Pick<
        BindingData,
        "worker" | "group_id" | "session" | "ip" | "generation" | "status"
      > | null;
    }
  >(
    "/lifecycle/chains?" +
      queryString({
        view,
        group_id: group,
        binding_id: binding,
        worker,
        slot_id: slot,
        job_id: job,
        q,
        page,
        page_size: size,
      }),
    true,
    3000,
  );
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!document.hidden) setNow(Date.now() / 1000);
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);
  const focus = query.data?.focus;
  function search(event: FormEvent) {
    event.preventDefault();
    const value = draft.trim();
    const ip =
      /^\d{1,3}(\.\d{1,3}){3}$/.test(value) ||
      (value.includes(":") && /^[0-9a-f:]+$/i.test(value));
    location.hash = traceLink({
      view: ip ? "ips" : view,
      group_id: group,
      q: value,
    });
  }
  return (
    <section className="trace-page">
      <div className="surface trace-toolbar">
        <nav aria-label="追踪范围">
          <Button
            asChild
            size="sm"
            variant={view === "links" ? "default" : "secondary"}
          >
            <a
              href={traceLink({ view: "links", group_id: group })}
              aria-current={view === "links" ? "page" : undefined}
            >
              <GitBranch size={14} />
              链接变化
            </a>
          </Button>
          <Button
            asChild
            size="sm"
            variant={view === "ips" ? "default" : "secondary"}
          >
            <a
              href={traceLink({ view: "ips", group_id: group })}
              aria-current={view === "ips" ? "page" : undefined}
            >
              <Layers3 size={14} />
              IP 变化
            </a>
          </Button>
        </nav>
        <form onSubmit={search}>
          <select
            aria-label="追踪分组"
            value={group}
            onChange={(e) => {
              location.hash = traceLink({ view, group_id: e.target.value, q });
            }}
          >
            <option value="">全部分组</option>
            {groups.data?.items.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
          <input
            aria-label="搜索工位、session、IP 或任务"
            placeholder="工位 / session / IP / 任务"
            value={draft}
            maxLength={128}
            onChange={(e) => setDraft(e.target.value)}
          />
          <Button size="sm" variant="secondary" type="submit" aria-label="查询">
            <Search size={14} />
          </Button>
        </form>
        <span
          className={`trace-live ${query.error ? "offline" : ""}`}
          title="每 3 秒自动刷新；最新链路在前"
        >
          <Radio size={14} />
          {query.error ? "更新中断" : "实时更新"}
        </span>
      </div>
      <div className="trace-index">
        <span>
          <ArrowLeft size={13} />
          最新记录在左 · 箭头从旧事件指向新事件
        </span>
        {(binding || worker || slot || job || q) && (
          <a href={traceLink({ view, group_id: group })}>清除筛选</a>
        )}
        {page > 1 && (
          <Button size="sm" variant="secondary" onClick={() => setPage(1)}>
            回到最新
          </Button>
        )}
      </div>
      {view === "ips" && (focusedIP || slot) && query.data && (
        <IPPaths
          paths={query.data.paths ?? []}
          focusedIP={focusedIP}
          slot={slot}
        />
      )}
      {view === "links" && focus && (
        <aside className="surface trace-focus">
          <header>
            <GitBranch size={15} />
            <strong>
              {focus.worker ? `工位 ${focus.worker}` : "匿名会话"} ·
              历次链接变化
            </strong>
            <GroupLabel id={focus.group_id} showId={false} />
          </header>
          <div className="trace-focus-current">
            <b>
              {focus.status === "RELEASED"
                ? "绑定已解除"
                : focus.status === "RESERVED"
                  ? "当前预留"
                  : "当前绑定"}
            </b>
            <code>{focus.session}</code>
            {focus.ip && (
              <>
                <span>出口</span>
                <IPValue ip={focus.ip} />
              </>
            )}
            <span>第 {focus.generation} 代</span>
          </div>
        </aside>
      )}
      <ErrorMessage message={query.error} />
      {query.isLoading && <LoadingState label="正在读取轨迹…" />}
      {query.data?.items.length === 0 && (
        <p className="surface ops-empty">暂无符合条件的轨迹。</p>
      )}
      <div className="trace-deck">
        {query.data?.items.map((chain) => (
          <TraceCard
            key={chain.id}
            chain={chain}
            onPick={setPicked}
            now={now}
          />
        ))}
      </div>
      <div className="ops-pager">
        <span>共 {query.data?.total ?? "—"} 条链路</span>
        <Button
          size="sm"
          variant="secondary"
          disabled={page === 1}
          onClick={() => setPage(page - 1)}
          aria-label="上一页"
        >
          <ChevronLeft size={14} />
        </Button>
        <span>
          {page} / {Math.max(1, Math.ceil((query.data?.total ?? 0) / size))}
        </span>
        <Button
          size="sm"
          variant="secondary"
          disabled={!query.data || page * size >= query.data.total}
          onClick={() => setPage(page + 1)}
          aria-label="下一页"
        >
          <ChevronRight size={14} />
        </Button>
      </div>
      {picked && (
        <Dialog
          open
          onOpenChange={(open) => {
            if (!open) setPicked(null);
          }}
          title={title(picked)}
          description={date(picked.at)}
        >
          <dl className="trace-details">
            <dt>资源组</dt>
            <dd>
              <GroupLabel id={picked.group_id} />
            </dd>
            {picked.status && (
              <>
                <dt>状态</dt>
                <dd>
                  {statuses[picked.status] ?? <Status state={picked.status} />}
                </dd>
              </>
            )}
            {picked.worker && (
              <>
                <dt>工位</dt>
                <dd>{picked.worker}</dd>
              </>
            )}
            {picked.session && (
              <>
                <dt>Session</dt>
                <dd>
                  <code>
                    {picked.old_session && picked.old_session !== picked.session
                      ? picked.old_session + " → "
                      : ""}
                    {picked.session}
                  </code>
                </dd>
              </>
            )}
            {!!picked.generation && (
              <>
                <dt>代次</dt>
                <dd>{picked.generation}</dd>
              </>
            )}
            {picked.ip && (
              <>
                <dt>出口</dt>
                <dd>
                  <code>
                    {[picked.old_ip, picked.ip, picked.new_ip]
                      .filter(Boolean)
                      .join(" → ")}
                  </code>
                </dd>
              </>
            )}
            {picked.source && (
              <>
                <dt>触发</dt>
                <dd>{sources[picked.source] ?? picked.source}</dd>
              </>
            )}
            {picked.reason && (
              <>
                <dt>说明</dt>
                <dd>{picked.reason}</dd>
              </>
            )}
            {picked.selection && (
              <>
                <dt>补货规则</dt>
                <dd>
                  {picked.fallback_reason && picked.preferred_selection
                    ? `${selectionLabel(picked.preferred_selection)} → `
                    : ""}
                  {selectionLabel(picked.selection)}
                </dd>
              </>
            )}
            {picked.supplier_id && (
              <>
                <dt>Webshare 单号</dt>
                <dd>
                  <code>{picked.supplier_id}</code>
                  {picked.attempt ? ` · 第 ${picked.attempt} 次提交` : ""}
                </dd>
              </>
            )}
            {picked.previous_supplier_id && (
              <>
                <dt>原单号</dt>
                <dd>
                  <code>{picked.previous_supplier_id}</code>
                </dd>
              </>
            )}
            {(picked.historical || picked.baseline) && (
              <>
                <dt>记录来源</dt>
                <dd>旧版历史可能不完整</dd>
              </>
            )}
          </dl>
          <div className="trace-detail-links">
            {picked.binding_id && (
              <a
                href={traceLink({
                  view: "links",
                  binding_id: picked.binding_id,
                })}
                onClick={() => setPicked(null)}
              >
                查看此绑定的各代出口
              </a>
            )}
            {picked.slot_id && (
              <a
                href={
                  picked.ip
                    ? ipHref(picked.ip, picked.slot_id)
                    : traceLink({ view: "ips", slot_id: picked.slot_id })
                }
                onClick={() => setPicked(null)}
              >
                追踪此 IP 的前后变化
              </a>
            )}
            {picked.old_slot_id && picked.old_slot_id !== picked.slot_id && (
              <a
                href={traceLink({ view: "ips", slot_id: picked.old_slot_id })}
                onClick={() => setPicked(null)}
              >
                旧出口席位
              </a>
            )}
            {picked.job_id && (
              <a
                href={traceLink({ view: "ips", job_id: picked.job_id })}
                onClick={() => setPicked(null)}
              >
                替换任务
              </a>
            )}
          </div>
        </Dialog>
      )}
    </section>
  );
}
