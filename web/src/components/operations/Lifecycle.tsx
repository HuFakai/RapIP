import { useState, type FormEvent } from "react";
import { ArrowRight, Copy, GitBranch, Layers3, Search } from "lucide-react";
import { api, useQuery, type Paged } from "@/lib/api";
import { copyText } from "@/lib/browser";
import { queryString, type Group } from "@/lib/v2";
import { date } from "@/lib/utils";
import { Button } from "../ui/button";
import { LoadingState } from "../ui/loading";
import { ErrorMessage, GroupLabel, Pages, Status } from "./Common";

const labels: Record<string, string> = {
  issued: "生成待用链接",
  reserved: "预留指定 IP",
  assigned: "链接获配出口",
  connected: "本代首次记录连通",
  switched: "链接切换出口",
  draining: "旧连接排空",
  fault_retired: "故障出口退役",
  released: "释放绑定 / 取消预留",
  admission: "资源准入",
  moved: "迁移分组",
  webshare: "Webshare 补货",
  baseline: "升级时已有绑定",
};
const sources: Record<string, string> = {
  client_session: "客户端更换 session",
  scheduled: "定时轮换",
  manual: "手动切换",
  manual_supplier: "手动供应商替换",
};
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
export function Lifecycle({ notify }: { notify: (message: string) => void }) {
  const initial = new URLSearchParams(location.hash.split("?")[1]);
  const view = initial.get("view") === "ips" ? "ips" : "links";
  const group = initial.get("group_id") ?? "";
  const binding = initial.get("binding_id") ?? "";
  const slot = initial.get("slot_id") ?? "";
  const job = initial.get("job_id") ?? "";
  const q = initial.get("q") ?? "";
  const [draft, setDraft] = useState(q),
    [page, setPage] = useState(1),
    [size, setSize] = useState(20);
  const [copying, setCopying] = useState("");
  const groups = useQuery<{ items: Group[] }>("/groups", true, 15000);
  const query = useQuery<Paged<Entry>>(
    "/lifecycle?" +
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
    5000,
  );
  const link = (params: Record<string, string>) =>
    "#lifecycle?" + queryString(params);
  function search(event: FormEvent) {
    event.preventDefault();
    location.hash = link({ view, group_id: group, q: draft.trim() });
  }
  async function copy(bindingId: string) {
    setCopying(bindingId);
    try {
      const result = await api<{ links: { socks5_named: string } }>(
        "/bindings/" + bindingId + "/proxy-link",
      );
      await copyText(result.links.socks5_named);
      notify("已复制当前 SOCKS 配置，含出口别名");
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setCopying("");
    }
  }
  return (
    <div className="surface ops-panel lifecycle-page">
      <div className="lifecycle-head">
        <div className="lifecycle-views" aria-label="追踪视图">
          <Button asChild variant={view === "links" ? "default" : "secondary"}>
            <a
              href={link({ view: "links", group_id: group })}
              aria-current={view === "links" ? "page" : undefined}
            >
              <GitBranch size={16} /> 链接轨迹
            </a>
          </Button>
          <Button asChild variant={view === "ips" ? "default" : "secondary"}>
            <a
              href={link({ view: "ips", group_id: group })}
              aria-current={view === "ips" ? "page" : undefined}
            >
              <Layers3 size={16} /> IP 与补货轨迹
            </a>
          </Button>
        </div>
        <form className="ops-filter-row" onSubmit={search}>
          <select
            aria-label="追踪分组"
            value={group}
            onChange={(e) => {
              location.hash = link({ view, group_id: e.target.value, q });
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
            placeholder="工位 / session / IP / 任务 ID"
            value={draft}
            maxLength={128}
            onChange={(e) => setDraft(e.target.value)}
          />
          <Button size="sm" variant="secondary" type="submit">
            <Search size={14} />
            查询
          </Button>
        </form>
      </div>
      <p className="lifecycle-note">
        {view === "links"
          ? "先分配池内出口，再验证连接成功。供应商补货单独记录。"
          : "按资源席位串起旧 IP 与新 IP。补货成功后还需通过准入。"}
      </p>
      {(binding || slot || job || q) && (
        <div className="lifecycle-filter">
          <span>
            {binding
              ? "当前绑定"
              : slot
                ? "当前资源席位"
                : job
                  ? "当前替换任务"
                  : q}
          </span>
          <a href={link({ view, group_id: group })}>清除筛选</a>
        </div>
      )}
      <ErrorMessage message={query.error} />
      {query.isLoading && <LoadingState label="正在读取轨迹…" />}
      {query.data?.items.length === 0 && (
        <p className="ops-empty">
          暂无符合条件的记录。旧版本无法还原的事件不会补造。
        </p>
      )}
      <ol className="lifecycle-list">
        {query.data?.items.map((e) => (
          <li key={e.id} className={`lifecycle-event lifecycle-${e.kind}`}>
            <time>
              {date(e.at)}
              {e.historical && <small> · 旧版记录</small>}
            </time>
            <div className="lifecycle-event-body">
              <div className="lifecycle-event-title">
                <strong>{labels[e.kind] ?? e.kind}</strong>
                {e.status && e.status !== "EXISTING" && (
                  <Status state={e.status} />
                )}
                <GroupLabel id={e.group_id} />
              </div>
              {(e.session || e.worker || e.generation) && (
                <div className="lifecycle-context">
                  {e.worker && (
                    <span>
                      工位 <b>{e.worker}</b>
                    </span>
                  )}
                  {e.old_session && e.old_session !== e.session && (
                    <>
                      <code>{e.old_session}</code>
                      <ArrowRight size={13} />
                    </>
                  )}
                  {e.session && <code>{e.session}</code>}
                  {!!e.generation && <span>第 {e.generation} 代</span>}
                </div>
              )}
              {(e.ip || e.old_ip || e.new_ip) && (
                <div className="lifecycle-path">
                  {e.old_ip && (
                    <>
                      <a href={link({ view: "ips", q: e.old_ip })}>
                        {e.old_ip}
                      </a>
                      <ArrowRight size={14} />
                    </>
                  )}
                  {e.ip && <a href={link({ view: "ips", q: e.ip })}>{e.ip}</a>}
                  {e.new_ip && (
                    <>
                      <ArrowRight size={14} />
                      <a href={link({ view: "ips", q: e.new_ip })}>
                        {e.new_ip}
                      </a>
                      <small>补回库存</small>
                    </>
                  )}
                </div>
              )}
              {e.selection && (
                <p>
                  Webshare 规则：
                  {e.fallback_reason && e.preferred_selection
                    ? `${selectionLabel(e.preferred_selection)} → `
                    : ""}
                  {selectionLabel(e.selection)}
                  {e.fallback_reason ? "（指定库存不足，随机兜底）" : ""}
                </p>
              )}
              {(e.reason ||
                e.source ||
                e.baseline ||
                e.kind === "baseline") && (
                <p>
                  {e.baseline || e.kind === "baseline"
                    ? "升级时观察到的状态；早期历史可能缺失。"
                    : e.reason || sources[e.source ?? ""] || e.source}
                </p>
              )}
              <div className="lifecycle-event-actions">
                {e.binding_id && (
                  <a href={link({ view: "links", binding_id: e.binding_id })}>
                    查看绑定
                  </a>
                )}
                {e.slot_id && (
                  <a href={link({ view: "ips", slot_id: e.slot_id })}>
                    资源席位
                  </a>
                )}
                {e.old_slot_id && e.old_slot_id !== e.slot_id && (
                  <a href={link({ view: "ips", slot_id: e.old_slot_id })}>
                    旧 IP 补货链
                  </a>
                )}
                {e.job_id && (
                  <a href={link({ view: "ips", job_id: e.job_id })}>
                    替换任务 <code>{e.job_id.slice(0, 8)}</code>
                  </a>
                )}
                {e.supplier_id && <span>Webshare #{e.supplier_id}</span>}
                {e.attempt ? <span>第 {e.attempt} 次提交</span> : null}
              </div>
            </div>
          </li>
        ))}
      </ol>
      {binding && (
        <div className="lifecycle-filter">
          <Button
            variant="secondary"
            disabled={!!copying}
            onClick={() => void copy(binding)}
          >
            <Copy size={14} />
            复制当前 SOCKS 链接
          </Button>
          <span>历史出口仅用于追踪，复制的是当前有效配置。</span>
        </div>
      )}
      <Pages
        data={query.data}
        page={page}
        setPage={setPage}
        size={size}
        setSize={setSize}
      />
    </div>
  );
}
