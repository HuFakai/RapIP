import { useState, type FormEvent } from "react";
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  Check,
  CircleDot,
  Clock3,
  GitBranch,
  Layers3,
  Radio,
  Search,
} from "lucide-react";
import { useQuery, type Paged } from "@/lib/api";
import { queryString, type Group } from "@/lib/v2";
import { date } from "@/lib/utils";
import { Button } from "../ui/button";
import { CountryFlag } from "../CountryFlag";
import { LoadingState } from "../ui/loading";
import { ErrorMessage, GroupLabel } from "./Common";

interface Entry {
  id: string;
  at: number;
  kind: string;
  group_id?: string;
  group_name?: string;
  worker?: string;
  session?: string;
  old_session?: string;
  ip?: string;
  old_ip?: string;
  new_ip?: string;
  country?: string;
  old_country?: string;
  new_country?: string;
  status?: string;
  reason?: string;
  source?: string;
  cause?: string;
  job_id?: string;
  supplier_id?: string;
  binding_id?: string;
  slot_id?: string;
  generation?: number;
  historical?: boolean;
  baseline?: boolean;
  release_action?: string;
  resource_action?: string;
  quality_state?: string;
  successes?: number;
  samples?: number;
  fallback_reason?: string;
  selection?: {
    type: string;
    country_code?: string;
    asn_numbers?: string[];
    ip_ranges?: string[];
  };
  context?: {
    old_session?: string;
    session?: string;
    old_ip?: string;
    ip?: string;
    source?: string;
  };
}
interface Path {
  slot_id: string;
  current: {
    ip: string;
    state: string;
    group_id: string;
    country?: string;
  } | null;
  changes: { ip: string; new_ip: string; at: number }[];
}
interface Focus {
  exit_country?: string;
  worker?: string;
  session: string;
  ip?: string;
  generation: number;
  status: string;
  group_id: string;
}
type Result = Paged<Entry> & { paths?: Path[]; focus?: Focus | null };
const stateNames: Record<string, string> = {
  FRESH: "待分配",
  IN_USE: "使用中",
  RESERVED: "已预留",
  QUARANTINED: "等待复检",
  REPLACING: "更换中",
  DRAINING: "等待旧连接结束",
};
function href(params: Record<string, string>) {
  return "#lifecycle?" + queryString(params);
}
function IP({
  ip,
  country,
  slot,
}: {
  ip?: string;
  country?: string;
  slot?: string;
}) {
  return (
    <span className="journey-ip">
      <CountryFlag code={country ?? null} />
      <b>{country || "国家未记录"}</b>
      {ip ? (
        <a href={href({ view: "ips", q: ip, slot_id: slot ?? "" })}>
          <code>{ip}</code>
        </a>
      ) : (
        <span>IP 待分配</span>
      )}
    </span>
  );
}
function describe(e: Entry): [string, string, string] {
  const source =
    e.source === "client_session"
      ? "客户端"
      : e.source === "scheduled"
        ? "定时任务"
        : "管理员";
  switch (e.kind) {
    case "issued":
      return [
        "生成了待用链接",
        "这份链接还没有使用，也没有占用 IP；首次连接时再分配。",
        "info",
      ];
    case "reserved":
      return [
        "链接预留了这个 IP",
        "你复制了指定 IP 的代理链接。系统已把它留给对应工位，等待首次连接。",
        "info",
      ];
    case "assigned":
      return [
        "代理链接获配了 IP",
        e.reason?.includes("预留")
          ? "客户端开始使用之前预留的链接；此 IP 现由该工位和 session 独占。"
          : "系统从这个分组的待分配池中选出 IP，绑定给下面的工位和 session。",
        "good",
      ];
    case "connected":
      return [
        "这份链接首次连通",
        "已成功通过该出口建立代理隧道。这说明网络连通，不代表目标网站的业务操作已经成功。",
        "good",
      ];
    case "session_requested":
      return [
        "客户端提交了新 session",
        `同一工位从 ${e.old_session ?? "原 session"} 改为 ${e.session ?? "新 session"}，开始寻找下一个可用出口；此时还未完成切换。`,
        "info",
      ];
    case "rotation_requested":
      return [
        "收到轮换出口请求",
        `${source}要求换出口；接下来先寻找合格的备用 IP，当前请求本身还没有完成换号。`,
        "info",
      ];
    case "switched":
      return [
        "链接已切换到新出口",
        e.source === "client_in_place"
          ? "Webshare 原位替换及新 IP 准入已完成，最新接收的 session 开始使用新出口。"
          : "系统已从待分配池取得新 IP，并交给这个链接。旧 IP 的后续补货会作为下面的独立事件显示。",
        "good",
      ];
    case "draining":
      return [
        "旧 IP 正在等待连接结束",
        "新连接已改用新出口；旧 TCP 暂时保留，排空或达到时限后再处理旧 IP。",
        "info",
      ];
    case "released":
      return [
        "会话绑定已释放",
        e.release_action === "unused_reservation"
          ? "尚未使用的预留已取消。旧链接失效，未调用 Webshare。"
          : e.resource_action !== "return_after_drain"
            ? "旧版本留下的释放记录；当时的后续处理请以下面的补货事件为准。"
            : "旧 session 已失效。当前版本保留原 IP，等连接结束后重新按本组准入参与分配，不调用 Webshare。",
        "info",
      ];
    case "returned":
      return [
        "原 IP 已返回待分配池",
        "IP 没有更换，也没有消耗 Webshare 额度；其他符合条件的链接现在可以获配它。若准入已过期，则先等待复检。",
        "good",
      ];
    case "request_failed":
      return [
        "这次切换没有完成",
        e.reason ?? "没有拿到可用的新出口；请保留原绑定并查看原因。",
        "warn",
      ];
    case "fault_retired":
      return [
        "旧出口因连接故障退役",
        e.reason ?? "触发了本组已开启的故障转移规则。",
        "warn",
      ];
    case "in_place_started":
      return [
        "收到新 session，开始原位换号",
        `新候选是 ${e.session ?? "未记录"}。这一模式直接替换原 IP，等待供应商结果和新 IP 准入。`,
        "info",
      ];
    case "in_place_join":
      return [
        "更新了本次换号的接收 session",
        `候选更新为 ${e.session ?? "未记录"}，仍复用同一项 Webshare 任务，没有再启动一笔换号。`,
        "info",
      ];
    case "in_place_failed":
      return [
        "原位换号遇到问题",
        e.reason ?? "暂未能把合格的新出口交给客户端，请先核对当前任务。",
        "warn",
      ];
    case "in_place_admission":
      return e.status === "PASS"
        ? [
            "新 IP 通过质量检查",
            "新出口已符合分组要求，接下来交给本次任务最新接收的 session。",
            "good",
          ]
        : [
            "正在检查换回来的新 IP",
            e.reason ??
              "供应商已经返回新 IP；在确认连通和符合分组要求之前，不会交给客户端。",
            "info",
          ];
    case "admission":
      return e.status === "FRESH" || e.status === "IN_USE"
        ? [
            "IP 检查通过，可以分配",
            `${e.successes != null ? `网络样本 ${e.successes}/${e.samples ?? "—"} 成功。` : ""}${e.reason ?? "这个 IP 已满足当前分组的准入要求。"}`,
            "good",
          ]
        : [
            "IP 暂停分配，等待检查",
            e.reason ??
              "当前没有足够的有效证据确认它符合分组要求；暂时隔离，不等于 IP 已经被更换。",
            "warn",
          ];
    case "moved":
      return [
        "IP 加入了这个分组",
        "之后按这个分组的质量要求和分配规则使用；迁移分组本身不更换 IP。",
        "info",
      ];
    case "baseline":
      return [
        "从已有绑定开始记录",
        "这是升级时观察到的状态；更早没有记录的操作无法还原。",
        "info",
      ];
    case "webshare": {
      const descriptions: Record<string, [string, string, string]> = {
        QUEUED: [
          "旧 IP 进入 Webshare 更换队列",
          "系统准备为旧 IP 补货；排队还不表示供应商已完成换号。",
          "info",
        ],
        SUBMITTING: [
          "正在向 Webshare 提交换号",
          "这一步会提交供应商更换请求并占用相应额度。",
          "info",
        ],
        PROCESSING: [
          "Webshare 正在处理换号",
          "供应商已受理任务；系统正在等待结果，没有因为客户端重试重复提交。",
          "info",
        ],
        RECONCILING: [
          "正在确认 Webshare 换回的 IP",
          "供应商报告已处理，系统正在比对资源列表，确认旧 IP 与新 IP 的对应关系。",
          "info",
        ],
        COMPLETED: [
          "Webshare 已返回新 IP",
          e.new_ip
            ? "旧 IP 已换成下方的新 IP；新 IP 仍要通过本组质量检查，之后才进入待分配池。"
            : "旧 IP 已移除，但这次记录没有确认新 IP；不能据此认定已补回可用库存。",
          "good",
        ],
        FAILED: [
          "Webshare 更换失败",
          e.reason ??
            "供应商未完成更换，请查看原任务；不会把未知结果当作成功。",
          "warn",
        ],
        UNCERTAIN: [
          "Webshare 结果暂时无法确认",
          e.reason ??
            "自动重复提交已暂停，避免重复消耗额度，需要先核对原任务。",
          "warn",
        ],
        BUDGET_BLOCKED: [
          "更换因额度不足暂停",
          e.reason ?? "当前预算不允许继续提交供应商请求。",
          "warn",
        ],
      };
      return (
        descriptions[e.status ?? ""] ?? [
          "Webshare 任务状态更新",
          e.reason ?? e.status ?? "状态未记录",
          "info",
        ]
      );
    }
    default:
      return ["资源状态有更新", e.reason ?? e.kind, "info"];
  }
}
function EventRow({ entry: e, number }: { entry: Entry; number: number }) {
  const [open, setOpen] = useState(false);
  const [title, body, tone] = describe(e);
  const worker = e.worker;
  const context = e.context;
  const future =
    context?.session && context.old_session && context.session !== e.session
      ? context.session
      : "";
  const origin = e.old_ip && e.old_ip !== e.ip ? e.old_ip : "";
  return (
    <li className={`journey-row tone-${tone}`}>
      <div className="journey-time">
        <time>{date(e.at)}</time>
        <span>事件 {number}</span>
      </div>
      <div className="journey-marker" aria-hidden="true">
        {tone === "good" ? <Check size={13} /> : <CircleDot size={13} />}
      </div>
      <article className="journey-card">
        <header>
          <strong>{title}</strong>
          <GroupLabel
            id={e.group_id}
            historicalName={e.group_name}
            showId={false}
          />
          <button
            type="button"
            className="journey-details-button"
            aria-expanded={open}
            onClick={() => setOpen(!open)}
          >
            {open ? "收起" : "详情"}
          </button>
        </header>
        <p>{body}</p>
        <div className="journey-context">
          <div className="journey-route">
            {origin && (
              <>
                <IP ip={origin} country={e.old_country} />
                <ArrowRight size={13} />
              </>
            )}
            <IP ip={e.ip} country={e.country} slot={e.slot_id} />
            {e.new_ip && (
              <>
                <ArrowRight size={13} />
                <IP ip={e.new_ip} country={e.new_country} slot={e.slot_id} />
              </>
            )}
          </div>
          <span className="journey-chip chip-worker">
            {e.kind === "webshare" ? "原工位" : "工位"}{" "}
            <code>{worker ?? "未绑定"}</code>
          </span>
          <span className="journey-chip chip-session">
            {e.kind === "webshare" ? "原 session" : "session"}{" "}
            <code>{e.session ?? "未绑定"}</code>
          </span>
          {future && (
            <span className="journey-chip chip-session">
              关联的新 session <code>{future}</code>
            </span>
          )}
        </div>
        {open && (
          <div className="journey-expanded">
            <dl>
              <dt>触发来源</dt>
              <dd>{e.source ?? e.cause ?? "系统记录"}</dd>
              {e.reason && (
                <>
                  <dt>原始原因</dt>
                  <dd>{e.reason}</dd>
                </>
              )}
              {e.supplier_id && (
                <>
                  <dt>Webshare 单号</dt>
                  <dd>{e.supplier_id}</dd>
                </>
              )}
              {e.job_id && (
                <>
                  <dt>替换任务</dt>
                  <dd>
                    <code>{e.job_id}</code>
                  </dd>
                </>
              )}
              {e.generation != null && (
                <>
                  <dt>代次</dt>
                  <dd>{e.generation}</dd>
                </>
              )}
              {e.selection && (
                <>
                  <dt>补货条件</dt>
                  <dd>
                    {e.selection.type === "country"
                      ? `国家 ${e.selection.country_code}`
                      : e.selection.type === "asn"
                        ? `ASN ${e.selection.asn_numbers?.join("、")}`
                        : e.selection.type === "ip_range"
                          ? e.selection.ip_ranges?.join("、")
                          : "随机"}
                  </dd>
                </>
              )}
              {e.fallback_reason && (
                <>
                  <dt>兜底原因</dt>
                  <dd>{e.fallback_reason}</dd>
                </>
              )}
            </dl>
            <div className="journey-links">
              {worker && (
                <a
                  href={href({
                    view: "links",
                    worker,
                    group_id: e.group_id ?? "",
                  })}
                >
                  看这个工位的完整过程
                </a>
              )}
              {e.job_id && (
                <a href={href({ view: "ips", job_id: e.job_id })}>
                  只看这项 Webshare 任务
                </a>
              )}
              {e.binding_id && (
                <a href={href({ view: "links", binding_id: e.binding_id })}>
                  只看这次绑定
                </a>
              )}
            </div>
          </div>
        )}
      </article>
    </li>
  );
}
export function Lifecycle() {
  const params = new URLSearchParams(location.hash.split("?")[1]);
  const view = params.get("view") === "ips" ? "ips" : "links";
  const group = params.get("group_id") ?? "",
    binding = params.get("binding_id") ?? "";
  const worker = params.get("worker") ?? "",
    q = params.get("q") ?? "",
    slot = params.get("slot_id") ?? "",
    job = params.get("job_id") ?? "";
  const [draft, setDraft] = useState(q),
    [page, setPage] = useState(1);
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const size = 25;
  const groups = useQuery<{ items: Group[] }>("/groups", true, 15000);
  const query = useQuery<Result>(
    "/lifecycle/chains?" +
      queryString({
        event_view: true,
        view,
        group_id: group,
        binding_id: binding,
        worker,
        q,
        slot_id: slot,
        job_id: job,
        page,
        page_size: size,
        order,
      }),
    true,
    3000,
  );
  function search(e: FormEvent) {
    e.preventDefault();
    const value = draft.trim();
    location.hash = href({
      view: /^\d{1,3}(\.\d{1,3}){3}$/.test(value) ? "ips" : view,
      group_id: group,
      q: value,
    });
  }
  const paths = query.data?.paths ?? [],
    focus = query.data?.focus;
  return (
    <section className="journey-page">
      <div className="surface trace-toolbar">
        <nav aria-label="追踪对象">
          <Button
            asChild
            size="sm"
            variant={view === "links" ? "default" : "secondary"}
          >
            <a href={href({ view: "links", group_id: group })}>
              <GitBranch size={14} />
              链接使用过程
            </a>
          </Button>
          <Button
            asChild
            size="sm"
            variant={view === "ips" ? "default" : "secondary"}
          >
            <a href={href({ view: "ips", group_id: group })}>
              <Layers3 size={14} />
              IP 使用与更换
            </a>
          </Button>
        </nav>
        <form onSubmit={search}>
          <select
            aria-label="追踪分组"
            value={group}
            onChange={(e) => {
              location.hash = href({ view, group_id: e.target.value, q });
            }}
          >
            <option value="">全部分组</option>
            {groups.data?.items.map((g) => (
              <option value={g.id} key={g.id}>
                {g.name}
              </option>
            ))}
          </select>
          <input
            aria-label="搜索 IP、工位或 session"
            placeholder="输入 IP、工位或 session"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            maxLength={128}
          />
          <Button size="sm" variant="secondary" type="submit" aria-label="查询">
            <Search size={14} />
          </Button>
        </form>
        <span className="trace-live">
          <Radio size={13} />
          {query.error ? "更新暂停" : "实时更新"}
        </span>
      </div>
      {(q || slot || binding || worker || job) && (
        <aside className="surface journey-focus">
          <header>
            <Layers3 size={15} />
            <strong>
              {q
                ? `IP / 关键词：${q}`
                : worker
                  ? `工位 ${worker} 的使用过程`
                  : binding
                    ? "这次链接绑定的完整过程"
                    : job
                      ? "这项 Webshare 更换任务"
                      : "该 IP 席位的完整过程"}
            </strong>
            <a href={href({ view, group_id: group })}>清除筛选</a>
          </header>
          {focus && (
            <p>
              当前{focus.status === "RELEASED" ? "已释放" : "绑定"} · session{" "}
              <code>{focus.session}</code>
              {focus.ip && (
                <>
                  {" "}
                  · 出口 <IP ip={focus.ip} country={focus.exit_country} />
                </>
              )}
            </p>
          )}
          {paths.map((p) => (
            <div className="journey-focus-path" key={p.slot_id}>
              <b>当前资源</b>
              {p.current ? (
                <>
                  <IP
                    ip={p.current.ip}
                    country={p.current.country}
                    slot={p.slot_id}
                  />
                  <span>{stateNames[p.current.state] ?? p.current.state}</span>
                </>
              ) : (
                <span>原 IP 已退役，未确认当前资源</span>
              )}
              {!!p.changes.length && (
                <details>
                  <summary>
                    已确认的 Webshare 换号（{p.changes.length} 次）
                  </summary>
                  <ul>
                    {p.changes.map((c) => (
                      <li key={c.at}>
                        <time>{date(c.at)}</time>
                        <code>{c.ip}</code>
                        <ArrowRight size={12} />
                        <code>{c.new_ip}</code>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          ))}
          <small>
            下面按实际记录讲述发生的事；新出口获配和旧 IP
            补货会分别说明。没有记录的信息显示“未记录”，不会补造。
          </small>
        </aside>
      )}
      <div className="journey-controls">
        <span>
          <Clock3 size={13} />
          {query.data?.total ?? "—"} 个事件
        </span>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            setOrder(order === "asc" ? "desc" : "asc");
            setPage(1);
          }}
        >
          {order === "asc" ? <ArrowDown size={13} /> : <ArrowUp size={13} />}{" "}
          {order === "asc" ? "从头看 · 按发生顺序" : "看最新 · 新事件在上"}
        </Button>
      </div>
      <ErrorMessage message={query.error} />
      {query.isLoading && <LoadingState label="正在还原使用过程…" />}
      {!query.isLoading && query.data?.items.length === 0 && (
        <p className="surface ops-empty">还没有符合条件的事件。</p>
      )}
      <ol className="journey-timeline">
        {query.data?.items.map((e, i) => (
          <EventRow
            key={e.id}
            entry={e}
            number={
              order === "asc"
                ? (page - 1) * size + i + 1
                : (query.data?.total ?? 0) - (page - 1) * size - i
            }
          />
        ))}
      </ol>
      <div className="ops-pager">
        <span>每页 {size} 个事件</span>
        <Button
          size="sm"
          variant="secondary"
          disabled={page === 1}
          onClick={() => setPage(page - 1)}
        >
          上一页
        </Button>
        <span>
          {page} / {Math.max(1, Math.ceil((query.data?.total ?? 0) / size))}
        </span>
        <Button
          size="sm"
          variant="secondary"
          disabled={!query.data || page * size >= query.data.total}
          onClick={() => setPage(page + 1)}
        >
          下一页
        </Button>
      </div>
    </section>
  );
}
