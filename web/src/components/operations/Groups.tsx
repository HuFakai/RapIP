import { useState, type FormEvent } from "react";
import {
  Check,
  ChevronRight,
  Layers3,
  Loader2,
  Plus,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import {
  api,
  refreshAll,
  useQuery,
  type NodeData,
  type Paged,
} from "@/lib/api";
import {
  basicPolicy,
  operation,
  queryString,
  type Group,
  type Policy,
  type QualityResult,
} from "@/lib/v2";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { CountryFlag } from "../CountryFlag";
import { ErrorMessage, Pages, Refresh, Status, Time } from "./Common";

function PolicyFields({
  policy,
  setPolicy,
}: {
  policy: Policy;
  setPolicy: (p: Policy) => void;
}) {
  const update = <K extends keyof Policy>(key: K, value: Policy[K]) =>
    setPolicy({ ...policy, [key]: value });
  function flag(name: string) {
    update(
      "reject_flags",
      policy.reject_flags.includes(name)
        ? policy.reject_flags.filter((x) => x !== name)
        : [...policy.reject_flags, name],
    );
  }
  return (
    <>
      <div className="ops-template-row">
        <Button
          variant="secondary"
          size="sm"
          type="button"
          onClick={() => setPolicy({ ...basicPolicy })}
        >
          基础网络模板
        </Button>
        <Button
          variant="secondary"
          size="sm"
          type="button"
          onClick={() =>
            setPolicy({
              ...basicPolicy,
              min_success: 3,
              max_latency_ms: 1500,
              ttl_hours: 6,
              require_ipinfo: true,
              network_types: ["isp"],
              reject_flags: ["hosting", "tor"],
              max_abuse_score: 10,
            })
          }
        >
          高要求模板
        </Button>
      </div>
      <p className="field-help">
        模板是初始调参建议。高要求模板需要 IPinfo 对应字段权限和 AbuseIPDB
        Key；缺失证据时保持未知，不自动换号。
      </p>
      <div className="form-two">
        <label>
          网络检测样本数
          <input
            type="number"
            min={1}
            max={10}
            value={policy.samples}
            onChange={(e) => update("samples", Number(e.target.value))}
          />
        </label>
        <label>
          最少成功样本
          <input
            type="number"
            min={1}
            max={policy.samples}
            value={policy.min_success}
            onChange={(e) => update("min_success", Number(e.target.value))}
          />
        </label>
        <label>
          成功样本最大耗时（ms）
          <input
            type="number"
            min={50}
            max={30000}
            value={policy.max_latency_ms}
            onChange={(e) => update("max_latency_ms", Number(e.target.value))}
          />
        </label>
        <label>
          证据有效期（小时）
          <input
            type="number"
            min={1}
            max={168}
            value={policy.ttl_hours}
            onChange={(e) => update("ttl_hours", Number(e.target.value))}
          />
        </label>
      </div>
      <label>
        国家允许列表
        <input
          placeholder="留空表示不限；例如 US,CA"
          value={policy.countries.join(",")}
          onChange={(e) =>
            update("countries", e.target.value.toUpperCase().split(","))
          }
        />
      </label>
      <div className="form-two">
        <label>
          ASN 允许列表
          <input
            placeholder="AS123,AS456；留空不限"
            value={policy.allowed_asns.join(",")}
            onChange={(e) => update("allowed_asns", e.target.value.split(","))}
          />
        </label>
        <label>
          ASN 拒绝列表
          <input
            placeholder="AS123；留空不拒绝"
            value={policy.denied_asns.join(",")}
            onChange={(e) => update("denied_asns", e.target.value.split(","))}
          />
        </label>
      </div>
      <div className="form-two">
        <label>
          网络类型要求
          <select
            value={policy.network_types[0] ?? ""}
            onChange={(e) =>
              update("network_types", e.target.value ? [e.target.value] : [])
            }
          >
            <option value="">不限 / 不要求属性检测</option>
            <option value="isp">已识别 ISP 归属</option>
            <option value="hosting">hosting</option>
            <option value="business">business</option>
            <option value="education">education</option>
            <option value="government">government</option>
          </select>
        </label>
        <label>
          举报置信分上限
          <input
            type="number"
            placeholder="留空不要求信誉评分"
            min={0}
            max={100}
            value={policy.max_abuse_score ?? ""}
            onChange={(e) =>
              update(
                "max_abuse_score",
                e.target.value === "" ? null : Number(e.target.value),
              )
            }
          />
        </label>
      </div>
      <div>
        <span className="ops-field-label">
          拒绝属性（需要检测源实际提供字段）
        </span>
        <div className="ops-checkboxes">
          {["hosting", "tor", "vpn", "proxy", "relay"].map((name) => (
            <label key={name}>
              <input
                type="checkbox"
                checked={policy.reject_flags.includes(name)}
                onChange={() => flag(name)}
              />
              {name}
            </label>
          ))}
        </div>
      </div>
      <label className="ops-check">
        <input
          type="checkbox"
          checked={policy.require_ipinfo}
          onChange={(e) => update("require_ipinfo", e.target.checked)}
        />
        必须具备 IPinfo 证据
      </label>
      <p className="field-help">
        proxy / VPN
        标签默认不拒绝。未要求的维度不显示为检测通过；住宅归属与业务成功不能仅靠一个标签保证。
      </p>
    </>
  );
}

export function Groups({ notify }: { notify: (v: string) => void }) {
  const query = useQuery<{ items: Group[] }>("/groups", true, 5000);
  const [editing, setEditing] = useState<Group | null | undefined>(undefined),
    [detail, setDetail] = useState<Group | null>(null),
    [busy, setBusy] = useState(""),
    [remove, setRemove] = useState<Group | null>(null),
    [checking, setChecking] = useState<Record<string, unknown> | null>(null);
  const [step, setStep] = useState(0),
    [name, setName] = useState(""),
    [id, setID] = useState(""),
    [description, setDescription] = useState(""),
    [policy, setPolicy] = useState<Policy>({ ...basicPolicy }),
    [selected, setSelected] = useState<string[]>([]),
    [auto, setAuto] = useState(false),
    [daily, setDaily] = useState(5),
    [round, setRound] = useState(10),
    [slots, setSlots] = useState(3),
    [cooldown, setCooldown] = useState(60),
    [error, setError] = useState("");
  const [page, setPage] = useState(1),
    [size, setSize] = useState(20),
    [memberPage, setMemberPage] = useState(1),
    [memberSize, setMemberSize] = useState(20),
    [candidateQ, setCandidateQ] = useState("");
  const candidates = useQuery<Paged<NodeData>>(
    "/nodes?" +
      queryString({
        group_id: "default",
        page,
        page_size: size,
        q: candidateQ,
      }),
    editing !== undefined,
    10000,
  );
  const members = useQuery<Paged<NodeData>>(
    "/nodes?" +
      queryString({
        group_id: detail?.id ?? "",
        page: memberPage,
        page_size: memberSize,
      }),
    !!detail,
    5000,
  );
  function edit(group: Group | null) {
    setEditing(group);
    setStep(group ? 1 : 0);
    setID(group?.id ?? "");
    setName(group?.name ?? "");
    setDescription(group?.description ?? "");
    setPolicy(group?.policy ?? { ...basicPolicy });
    setSelected([]);
    setAuto(group?.auto_replace ?? false);
    setDaily(group?.daily_limit ?? 5);
    setRound(group?.round_limit ?? 10);
    setSlots(group?.slot_limit ?? 3);
    setCooldown(group?.cooldown_seconds ?? 60);
    setError("");
    setChecking(null);
  }
  function normalize(): Policy {
    return {
      ...policy,
      countries: policy.countries.map((x) => x.trim()).filter(Boolean),
      allowed_asns: policy.allowed_asns.map((x) => x.trim()).filter(Boolean),
      denied_asns: policy.denied_asns.map((x) => x.trim()).filter(Boolean),
    };
  }
  async function execute(
    key: string,
    action: () => Promise<unknown>,
    message: string,
  ) {
    setBusy(key);
    try {
      const r = await action();
      notify(message);
      refreshAll();
      return r;
    } catch (e) {
      notify((e as Error).message);
      throw e;
    } finally {
      setBusy("");
    }
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    setError("");
    setBusy("save");
    try {
      const body = {
        name,
        description,
        node_ids: selected,
        policy: normalize(),
        auto_replace: auto,
        daily_limit: daily,
        round_limit: round,
        slot_limit: slots,
        cooldown_seconds: cooldown,
      };
      await operation(
        editing ? `/groups/${editing.id}` : "/groups",
        editing ? body : { ...body, group_id: id, node_ids: selected },
        editing ? "PATCH" : "POST",
      );
      setEditing(undefined);
      notify("分组已保存；未合格资源保持隔离");
      refreshAll();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function preview() {
    setBusy("preview");
    setError("");
    try {
      const response = await operation("/groups/preview", {
        node_ids: selected,
        policy: normalize(),
      });
      setChecking(response);
      notify("只读预览完成，没有提交 Replace");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function run(group: Group) {
    try {
      const r = await execute(
        group.id,
        () => operation(`/groups/${group.id}/check`),
        group.auto_replace
          ? "检测完成；请查看质量结论与替换任务"
          : "只读筛查和准入完成，自动替换关闭",
      );
      setChecking(r as Record<string, unknown>);
      setDetail(group);
    } catch {
      /* notified */
    }
  }
  const current = query.data?.items.find((g) => g.id === detail?.id) ?? detail;
  const results = checking?.items as QualityResult[] | undefined;
  return (
    <div className="page-enter">
      <div className="ops-toolbar">
        <p className="ops-summary-text">
          <ShieldCheck size={15} />
          按项目隔离资源，只有本组合格 IP 才可分配
        </p>
        <div className="ops-filter-row">
          <Refresh onClick={() => void query.refresh()} />
          <Button size="sm" onClick={() => edit(null)}>
            <Plus size={13} />
            创建质量组
          </Button>
        </div>
      </div>
      <ErrorMessage message={query.error} />
      <div className="surface ops-panel">
        <div className="ops-table-wrap">
          <table className="ops-table">
            <thead>
              <tr>
                <th>分组 / 用途</th>
                <th>合格 / 总席位</th>
                <th>质量分布</th>
                <th>今日替换</th>
                <th>自动更换</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {query.data?.items.map((g) => (
                <tr key={g.id}>
                  <td>
                    <button
                      className="ops-link"
                      onClick={() => {
                        setDetail(g);
                        setChecking(null);
                        setMemberPage(1);
                      }}
                    >
                      {g.name}
                    </button>
                    <small>
                      {g.id} · {g.description || "未填写用途"}
                    </small>
                  </td>
                  <td>
                    <b className="accent">{g.fresh}</b> / {g.total}
                    <small>在用 {g.in_use}</small>
                  </td>
                  <td>
                    <div className="ops-state-counts">
                      {Object.entries(g.quality_counts).map(([s, n]) => (
                        <span key={s}>
                          <Status state={s} />
                          <b>{n}</b>
                        </span>
                      ))}
                    </div>
                  </td>
                  <td>
                    {g.today_submitted} /{" "}
                    {g.id === "default" ? "账号预算" : g.daily_limit}
                    <small>
                      本轮 {g.round_submitted} / {g.round_limit}
                    </small>
                  </td>
                  <td>
                    {g.id === "default" ? (
                      <span className="muted">基础健康流程</span>
                    ) : (
                      <span
                        className={g.auto_replace ? "ops-warning" : "muted"}
                      >
                        {g.auto_replace
                          ? "已开启 · 消耗额度"
                          : "关闭 · 只读准入"}
                      </span>
                    )}
                    {!g.enabled && <small>分组已暂停</small>}
                  </td>
                  <td>
                    <div className="ops-inline-actions">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setDetail(g);
                          setChecking(null);
                        }}
                      >
                        详情
                      </Button>
                      {g.id !== "default" && (
                        <>
                          <Button
                            variant="secondary"
                            size="sm"
                            disabled={!!busy || g.running || !g.enabled}
                            onClick={() => void run(g)}
                          >
                            <Zap size={12} />
                            {busy === g.id || g.running
                              ? "筛查中"
                              : "检测并准入"}
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`编辑分组 ${g.name}`}
                            onClick={() => edit(g)}
                          >
                            <SlidersHorizontal size={13} />
                          </Button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="ops-footnote">
          没有选入质量组的资源继续默认健康准入。新 IP
          自动继承原组；未知或检测源异常不会触发质量换号。
        </p>
      </div>
      <Dialog
        open={editing !== undefined}
        onOpenChange={(open) => {
          if (!open && !busy) setEditing(undefined);
        }}
        title={editing ? "编辑质量分组" : "创建质量分组"}
        description="选择资源、设定质量要求，预览后配置可控的自动换号预算。"
      >
        <form className="form-stack ops-group-form" onSubmit={save}>
          <div className="ops-steps">
            {["选择资源", "质量要求", "预算与保存"].map((text, i) => (
              <button
                type="button"
                disabled={!!busy || false}
                className={step === i ? "selected" : ""}
                key={text}
                onClick={() => setStep(i)}
              >
                <span>{i + 1}</span>
                {text}
              </button>
            ))}
          </div>
          <ErrorMessage message={error} />
          {step === 0 && (
            <>
              <div className="form-two">
                <label>
                  分组 ID
                  <input
                    disabled={!!editing}
                    required
                    pattern="[A-Za-z0-9]{1,32}"
                    maxLength={32}
                    value={id}
                    onChange={(e) => setID(e.target.value)}
                    placeholder="register / collect"
                  />
                </label>
                <label>
                  分组名称
                  <input
                    required
                    maxLength={64}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="例如高要求业务"
                  />
                </label>
              </div>
              <label>
                用途说明
                <input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  maxLength={300}
                />
              </label>
              <div className="ops-filter-row">
                <input
                  aria-label="搜索待选 IP"
                  placeholder="筛选 IP / CIDR"
                  value={candidateQ}
                  onChange={(e) => {
                    setCandidateQ(e.target.value);
                    setPage(1);
                  }}
                />
                <span className="field-help">
                  已选 {selected.length} 个 · 每次最多 100 个
                </span>
              </div>
              <div className="ops-member-select">
                {candidates.data?.items.map((n) => (
                  <label key={n.id}>
                    <input
                      type="checkbox"
                      disabled={
                        n.used ||
                        !["FRESH", "QUARANTINED"].includes(n.state) ||
                        (!selected.includes(n.id) && selected.length >= 100)
                      }
                      checked={selected.includes(n.id)}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...selected, n.id]
                            : selected.filter((x) => x !== n.id),
                        )
                      }
                    />
                    <CountryFlag code={n.country} />
                    <span className="mono">{n.ip}</span>
                    <small>{n.account_id}</small>
                    <Status state={n.state} />
                  </label>
                ))}
              </div>
              <ErrorMessage message={candidates.error} />
              <Pages
                data={candidates.data}
                page={page}
                setPage={setPage}
                size={size}
                setSize={setSize}
              />
              <p className="field-help">
                仅选择默认组里尚未使用的
                IP；已有绑定、排空或替换中的资源不可迁移。允许先创建空组，之后再添加资源。
              </p>
              <Button type="button" onClick={() => setStep(1)}>
                设置质量要求 <ChevronRight size={13} />
              </Button>
            </>
          )}
          {step === 1 && (
            <>
              <PolicyFields policy={policy} setPolicy={setPolicy} />
              {!editing && (
                <Button
                  type="button"
                  variant="secondary"
                  disabled={!!busy || !selected.length}
                  onClick={() => void preview()}
                >
                  {busy === "preview" ? (
                    <Loader2 className="spin" size={13} />
                  ) : (
                    <Zap size={13} />
                  )}
                  只读预览选中资源
                </Button>
              )}
              {results && (
                <div className="ops-preview">
                  <strong>预览结果 · 不调用 Replace</strong>
                  {results.map((r, i) => (
                    <div key={i}>
                      <span className="mono">{r.ip}</span>
                      <Status state={r.state} />
                      <small>{r.reasons.join("；") || "满足所选条件"}</small>
                    </div>
                  ))}
                </div>
              )}
              <Button type="button" onClick={() => setStep(2)}>
                设置预算 <ChevronRight size={13} />
              </Button>
            </>
          )}
          {step === 2 && (
            <>
              {editing && (
                <div className="form-two">
                  <label>
                    分组名称
                    <input
                      required
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                    />
                  </label>
                  <label>
                    用途说明
                    <input
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                    />
                  </label>
                </div>
              )}
              <div className="ops-group-review">
                <Layers3 size={18} />
                <div>
                  <strong>{name || id || "尚未命名的分组"}</strong>
                  <p>
                    {editing
                      ? "策略变更后未分配库存会重新准入，已有长连接保持不变"
                      : `选中 ${selected.length} 个独立资源席位，其他组不参与筛查`}
                  </p>
                </div>
              </div>
              <div className="form-two">
                <label>
                  每日替换上限
                  <input
                    type="number"
                    min={0}
                    max={100000}
                    value={daily}
                    onChange={(e) => setDaily(Number(e.target.value))}
                  />
                </label>
                <label>
                  本轮替换上限
                  <input
                    type="number"
                    min={0}
                    max={100000}
                    value={round}
                    onChange={(e) => setRound(Number(e.target.value))}
                  />
                </label>
                <label>
                  每席位连续替换上限
                  <input
                    type="number"
                    min={1}
                    max={100}
                    value={slots}
                    onChange={(e) => setSlots(Number(e.target.value))}
                  />
                </label>
                <label>
                  替换间隔（秒）
                  <input
                    type="number"
                    min={0}
                    max={86400}
                    value={cooldown}
                    onChange={(e) => setCooldown(Number(e.target.value))}
                  />
                </label>
              </div>
              <label className="ops-check ops-auto-replace">
                <input
                  type="checkbox"
                  checked={auto}
                  onChange={(e) => setAuto(e.target.checked)}
                />
                <div>
                  <strong>自动更换明确不合格的 IP</strong>
                  <small>
                    开启后会按所选预算调用 Webshare
                    Replace；未知、检测异常或额度耗尽时停止。每日额度以
                    Asia/Shanghai 结算。
                  </small>
                </div>
              </label>
              <p className="field-help">
                账号总预算、全局分组预算及上方限额同时生效。不保证有限替换次数内一定获得合格
                IP。
              </p>
              <Button disabled={!!busy || !id || !name} type="submit">
                {busy === "save" ? (
                  <Loader2 size={14} className="spin" />
                ) : (
                  <Save size={14} />
                )}
                保存并应用
              </Button>
            </>
          )}
        </form>
      </Dialog>
      <Dialog
        open={!!detail && editing === undefined}
        onOpenChange={(open) => {
          if (!open) setDetail(null);
        }}
        title={current?.name ?? "分组详情"}
        description="查看成员与质量证据，管理分组准入与受控替换。"
      >
        {current && (
          <div className="form-stack ops-group-detail">
            <div className="ops-group-review">
              <ShieldCheck size={18} />
              <div>
                <strong>
                  {current.fresh} 个待分配 / {current.total} 个席位
                </strong>
                <p>
                  版本 {current.version} · 今日 {current.today_submitted} 次替换
                  · {current.auto_replace ? "自动更换已开启" : "自动更换关闭"}
                </p>
              </div>
            </div>
            {current.id !== "default" && (
              <div className="ops-filter-row">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={!!busy || current.running}
                  onClick={() => void run(current)}
                >
                  检测并准入
                </Button>
                <Button variant="ghost" size="sm" onClick={() => edit(current)}>
                  规则 / 添加资源
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!!busy}
                  onClick={() => {
                    void execute(
                      "pause",
                      () =>
                        operation(
                          `/groups/${current.id}`,
                          { enabled: !current.enabled },
                          "PATCH",
                        ),
                      "分组状态已更新",
                    ).catch(() => {});
                  }}
                >
                  {current.enabled ? "暂停新分配" : "恢复分组"}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!!busy}
                  onClick={() => {
                    void execute(
                      "round",
                      () => operation(`/groups/${current.id}/round`),
                      "已开启新轮次，每日与连续限制保留",
                    ).catch(() => {});
                  }}
                >
                  新筛选轮次
                </Button>
              </div>
            )}
            {current.auto_replace && (
              <p className="ops-warning">
                检测并准入可能触发额度内的真实替换；只查看证据不会调用 Replace。
              </p>
            )}
            <div className="ops-member-details">
              {members.data?.items.map((n) => (
                <div key={n.id}>
                  <div className="ops-filter-row">
                    <span className="mono">{n.ip}</span>
                    <Status
                      state={
                        n.quality.state ??
                        (n.group_id === "default" ? "BASIC" : "PENDING")
                      }
                    />
                    {current.id !== "default" && (
                      <button
                        className="ops-icon-button"
                        title="移回默认组"
                        disabled={!!busy || n.used}
                        onClick={() => {
                          void execute(
                            "remove-member",
                            () =>
                              operation(`/groups/${current.id}/members`, {
                                node_ids: [n.id],
                                remove: true,
                              }),
                            "已移回默认组，将重新健康准入",
                          ).catch(() => {});
                        }}
                      >
                        <X size={12} />
                      </button>
                    )}
                  </div>
                  <small>{n.reason}</small>
                  <Time value={n.quality.checked_at} />
                  {n.quality.evidence && (
                    <details>
                      <summary>检测证据</summary>
                      <pre>{JSON.stringify(n.quality.evidence, null, 2)}</pre>
                    </details>
                  )}
                </div>
              ))}
            </div>
            <ErrorMessage message={members.error} />
            <Pages
              data={members.data}
              page={memberPage}
              setPage={setMemberPage}
              size={memberSize}
              setSize={setMemberSize}
            />
            {results && (
              <details>
                <summary>本次筛查结果</summary>
                <pre>{JSON.stringify(checking, null, 2)}</pre>
              </details>
            )}
            {current.id !== "default" && (
              <Button
                variant="ghost"
                disabled={!!busy}
                onClick={() => setRemove(current)}
              >
                <Trash2 size={13} />
                删除分组并将空闲成员退回默认组
              </Button>
            )}
          </div>
        )}
      </Dialog>
      <Dialog
        open={!!remove}
        onOpenChange={(open) => {
          if (!open) setRemove(null);
        }}
        title="删除质量分组"
        description="不会删除供应商 IP；存在活跃绑定或在途任务时操作会被拒绝。"
      >
        <p className="field-help">
          未使用成员将回默认组，重新进行基础健康准入。分组规则将被删除。
        </p>
        <Button
          disabled={!!busy}
          onClick={() => {
            if (remove)
              void execute(
                "delete",
                () => operation(`/groups/${remove.id}`, {}, "DELETE"),
                "分组已删除",
              )
                .then(() => {
                  setRemove(null);
                  setDetail(null);
                })
                .catch(() => {});
          }}
        >
          确认删除
        </Button>
      </Dialog>
    </div>
  );
}

interface Source {
  id: string;
  configured: boolean;
  daily_limit: number;
  used_today: number;
  tier: string;
  capabilities: string;
  status?: { state: string; error?: string };
}
export function QualitySources({ notify }: { notify: (v: string) => void }) {
  const query = useQuery<{
    items: Source[];
    global_daily_limit: number;
    global_used_today: number;
  }>("/quality/sources", true, 15000);
  const [busy, setBusy] = useState("");
  const [testResults, setTestResults] = useState<
    Record<string, Record<string, unknown>>
  >({});
  async function save(e: FormEvent<HTMLFormElement>, source: Source) {
    e.preventDefault();
    const form = e.currentTarget;
    const data = new FormData(form);
    setBusy(source.id);
    try {
      const body: Record<string, unknown> = {
        daily_limit: Number(data.get("daily_limit")),
      };
      if (source.id === "ipinfo") body.tier = data.get("tier");
      if (data.get("remove")) body.key = "";
      else if (data.get("key")) body.key = String(data.get("key"));
      await api(`/quality/sources/${source.id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      form.reset();
      notify("检测源已保存；必需字段按实际权限验证");
      refreshAll();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function test(source: Source) {
    setBusy(source.id);
    try {
      const result = await api<{ result: Record<string, unknown> }>(
        `/quality/sources/${source.id}/test`,
        { method: "POST", body: JSON.stringify({ ip: "1.1.1.1" }) },
      );
      setTestResults((previous) => ({
        ...previous,
        [source.id]: result.result,
      }));
      notify(
        result.result.error
          ? String(result.result.error)
          : "检测源校验通过，可查看实际字段",
      );
      refreshAll();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  return (
    <section className="surface ops-panel ops-source-panel">
      <div className="ops-panel-head">
        <div>
          <span className="eyebrow">QUALITY PROVIDERS</span>
          <h2>质量检测源</h2>
        </div>
        <span>
          全局组替换今日 {query.data?.global_used_today ?? "—"} /{" "}
          {query.data?.global_daily_limit ?? "—"}
        </span>
      </div>
      <ErrorMessage message={query.error} />
      <div className="ops-source-grid">
        {query.data?.items.map((source) => (
          <form
            key={`${source.id}:${source.daily_limit}:${source.tier}`}
            className="form-stack"
            onSubmit={(e) => void save(e, source)}
          >
            <div className="ops-source-heading">
              <strong>
                {source.id === "ipinfo"
                  ? "IPinfo 网络属性"
                  : "AbuseIPDB 信誉查询"}
              </strong>
              <span
                className={
                  source.status?.state === "error" ? "ops-warning" : "muted"
                }
              >
                {!source.configured
                  ? "未配置"
                  : source.status?.state === "ok"
                    ? "最近查询正常"
                    : source.status?.state === "error"
                      ? "服务异常"
                      : "已配置 · 未校验"}
              </span>
            </div>
            <p className="field-help">
              {source.capabilities} · 今日请求 {source.used_today} /{" "}
              {source.daily_limit}。Key 不回显。
            </p>
            <label>
              API Key
              <input
                type="password"
                name="key"
                autoComplete="new-password"
                placeholder={
                  source.configured ? "已配置；留空保留原 Key" : "尚未配置"
                }
              />
            </label>
            <div className="form-two">
              <label>
                本地每日请求上限
                <input
                  name="daily_limit"
                  type="number"
                  min={0}
                  max={100000}
                  defaultValue={source.daily_limit}
                />
              </label>
              {source.id === "ipinfo" && (
                <label>
                  API 类型
                  <select name="tier" defaultValue={source.tier}>
                    <option value="lite">Lite（地区 / ASN）</option>
                    <option value="lookup">Lookup（按订阅字段）</option>
                  </select>
                </label>
              )}
            </div>
            <label className="ops-check">
              <input type="checkbox" name="remove" />
              删除已存 Key
            </label>
            <div className="ops-filter-row">
              <Button
                variant="secondary"
                size="sm"
                type="submit"
                disabled={!!busy}
              >
                <Check size={13} />
                保存检测源
              </Button>
              <Button
                variant="ghost"
                size="sm"
                type="button"
                disabled={!!busy || !source.configured}
                onClick={() => void test(source)}
              >
                校验服务
              </Button>
            </div>
            {testResults[source.id] && (
              <details open className="ops-source-result">
                <summary>最近校验返回的实际字段</summary>
                <pre>{JSON.stringify(testResults[source.id], null, 2)}</pre>
              </details>
            )}
          </form>
        ))}
      </div>
      <p className="ops-footnote">
        内置网络检测无需新
        Key。未配置的信誉或属性维度保持未知，不能显示为已通过；检测源故障不触发质量换号。
      </p>
    </section>
  );
}
