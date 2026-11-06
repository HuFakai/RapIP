import {
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type FormEvent,
} from "react";
import {
  ArrowDown,
  ArrowDownUp,
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
import { useQuery, type Paged } from "@/lib/api";
import { queryString, type Group } from "@/lib/v2";
import { date } from "@/lib/utils";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { LoadingState } from "../ui/loading";
import { ErrorMessage, GroupLabel, Status } from "./Common";

const labels: Record<string, string> = {
  issued: "生成链接",
  reserved: "预留出口",
  assigned: "获配出口",
  connected: "首次连通",
  switched: "出口切换",
  draining: "旧连接排空",
  fault_retired: "故障退役",
  released: "释放绑定",
  admission: "资源准入",
  moved: "迁移分组",
  webshare: "Webshare",
  baseline: "已有绑定",
  in_place_started: "申请原位替换",
  in_place_join: "合并新 session",
  in_place_admission: "新 IP 质量准入",
  in_place_failed: "原位替换受阻",
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
}
interface Chain {
  id: string;
  status?: string;
  events: Entry[];
}
function traceLink(params: Record<string, string>) {
  return "#lifecycle?" + queryString(params);
}
function title(e: Entry) {
  return e.kind === "webshare"
    ? (statuses[e.status ?? ""] ?? "Webshare 替换")
    : (labels[e.kind] ?? e.kind);
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
              title="向前查看"
              aria-label={`${name}向前查看`}
              disabled={expanded ? position === 0 : edges.start}
              onClick={() => navigate(-1)}
            >
              <ChevronLeft size={14} />
            </button>
            <button
              type="button"
              title="向后查看"
              aria-label={`${name}向后查看`}
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
                      <ArrowLeft className="trace-arrow" size={14} />
                    ) : (
                      <ArrowRight className="trace-arrow" size={14} />
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
                      <ArrowDown className="trace-turn" size={14} />
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
  const supplierActive =
    !!supplierLatest && runningSupplier.has(supplierLatest.status ?? "");
  const businessActive = chain.status === "RUNNING";
  const state =
    chain.status === "CURRENT"
      ? "当前链路"
      : chain.status === "UNAVAILABLE"
        ? "当前链路 · 出口不可用"
        : chain.status === "RUNNING"
          ? "当前链路 · 切换中"
          : chain.status === "REVIEW"
            ? "当前链路 · 待处理"
            : latest.kind === "issued"
              ? "待首次连接"
              : events.some((e) => e.kind === "in_place_failed")
                ? "未完成"
                : "历史";
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
          {summary.old_ip && summary.ip && summary.old_ip !== summary.ip && (
            <span className="trace-ip-pair" title="旧出口 → 新出口">
              <code>{summary.old_ip}</code>
              <ArrowRight size={12} />
              <code>{summary.ip}</code>
            </span>
          )}
          <time title={date(latest.at)}>{date(latest.at)}</time>
          <span title="从首条事件到末条事件或当前时间">
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
      <Lane
        name="业务切换"
        items={business}
        expanded={expanded}
        onPick={onPick}
        running={businessActive}
        now={now}
      />
      <Lane
        name="IP / 补货"
        items={supplier}
        supplier
        expanded={expanded}
        onPick={onPick}
        running={supplierActive}
        now={now}
      />
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
  const query = useQuery<Paged<Chain>>(
    "/lifecycle/chains?" +
      queryString({
        view,
        group_id: group,
        binding_id: binding,
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
  function search(event: FormEvent) {
    event.preventDefault();
    location.hash = traceLink({ view, group_id: group, q: draft.trim() });
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
              链接轨迹
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
              IP 与补货
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
          <ArrowDownUp size={13} />
          最新在前 · 每条链路也从最新节点开始
        </span>
        {(binding || slot || job || q) && (
          <a href={traceLink({ view, group_id: group })}>清除筛选</a>
        )}
        {page > 1 && (
          <Button size="sm" variant="secondary" onClick={() => setPage(1)}>
            回到最新
          </Button>
        )}
      </div>
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
                工位链路
              </a>
            )}
            {picked.slot_id && (
              <a
                href={traceLink({ view: "ips", slot_id: picked.slot_id })}
                onClick={() => setPicked(null)}
              >
                资源席位
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
