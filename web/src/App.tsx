import { LoadingState } from "./components/ui/loading";
import {
  useCallback,
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  Globe2,
  Layers3,
  LayoutDashboard,
  Loader2,
  LogOut,
  Menu,
  Network,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Terminal,
  Trash2,
  Unplug,
  Workflow,
  X,
} from "lucide-react";
import {
  api,
  mutate,
  refreshAll,
  setCsrf,
  useQuery,
  type EventData,
  type AccountData,
  type BindingData,
  type Paged,
  type SettingsData,
  type Stats,
  type TaskData,
} from "./lib/api";
import { rotationStates } from "./components/operations/RotationDialog";
import { quotaPair, trafficPair, packageBytes } from "./lib/quotas";
import { date, labels } from "./lib/utils";
import { Button } from "./components/ui/button";
import { Dialog } from "./components/ui/dialog";
import { ConnectionDialog } from "./components/ConnectionDialog";
import { Nodes } from "./components/operations/Nodes";
import { GroupLabel, GroupNamesProvider } from "./components/operations/Common";
import { Retention } from "./components/operations/Retention";
import { Groups, QualitySources } from "./components/operations/Groups";
import { Connections } from "./components/operations/Connections";
import { Tasks } from "./components/operations/Tasks";
import { operation, queryString, type Group } from "./lib/v2";
import { AppearanceSettings } from "./components/AppearanceSettings";

const Overview = lazy(() =>
  import("./components/operations/Overview").then((m) => ({
    default: m.Overview,
  })),
);

type PageId =
  | "overview"
  | "bindings"
  | "nodes"
  | "accounts"
  | "tasks"
  | "settings"
  | "groups"
  | "connections";
const navigation = [
  {
    id: "overview",
    label: "代理总览",
    icon: LayoutDashboard,
    caption: "库存、质量、连接与补货，一屏掌握",
  },
  {
    id: "bindings",
    label: "工位与会话",
    icon: Workflow,
    caption: "管理出口绑定与长连接",
  },
  {
    id: "nodes",
    label: "IP 资源池",
    icon: Layers3,
    caption: "查看每一个节点的生命周期",
  },
  {
    id: "groups",
    label: "质量分组",
    icon: ShieldCheck,
    caption: "按项目筛选资源与质量准入",
  },
  {
    id: "connections",
    label: "连接记录",
    icon: Network,
    caption: "建立结果、出口与连接生命周期",
  },
  {
    id: "accounts",
    label: "上游账号",
    icon: Globe2,
    caption: "Webshare 账号、备注与套餐用量",
  },
  {
    id: "tasks",
    label: "任务与事件",
    icon: Activity,
    caption: "追踪换号、补货与状态变化",
  },
  {
    id: "settings",
    label: "网关设置",
    icon: Settings2,
    caption: "调整调度与连接处理策略",
  },
] as const;

function Brand({ small = false }: { small?: boolean }) {
  return (
    <div className={`brand ${small ? "brand-small" : ""}`}>
      <svg viewBox="0 0 32 32" aria-hidden="true">
        <path d="M7 24V8h10a5.5 5.5 0 0 1 0 11h-5m8 0 6 5" />
      </svg>
      <span>
        Rap<span className="brand-ip">IP</span>
      </span>
    </div>
  );
}
function Badge({ state }: { state: string }) {
  return (
    <span className={`badge state-${state.toLowerCase()}`}>
      <i />
      {labels[state] || state}
    </span>
  );
}
function Empty({
  icon,
  title,
  children,
}: {
  icon?: ReactNode;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon || <Network size={25} />}</div>
      <h3>{title}</h3>
      {children && <p>{children}</p>}
    </div>
  );
}
function ErrorNote({ message }: { message: string }) {
  return message ? (
    <div className="error-note" role="alert">
      {message} · 保留上次数据，等待重新连接
    </div>
  ) : null;
}
function Pager({
  current,
  total,
  setPage,
}: {
  current: number;
  total: number;
  setPage: (value: number) => void;
}) {
  return (
    <div className="pager">
      <span>共 {total} 条记录</span>
      <div>
        <Button
          variant="ghost"
          size="icon"
          aria-label="上一页"
          disabled={current === 1}
          onClick={() => setPage(current - 1)}
        >
          <ChevronLeft size={16} />
        </Button>
        <span>{current}</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="下一页"
          disabled={current * 20 >= total}
          onClick={() => setPage(current + 1)}
        >
          <ChevronRight size={16} />
        </Button>
      </div>
    </div>
  );
}

function Login({ onLogin }: { onLogin: () => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const values = new FormData(event.currentTarget);
    try {
      const result = await api<{ csrf: string }>("/auth/login", {
        method: "POST",
        body: JSON.stringify(Object.fromEntries(values)),
      });
      setCsrf(result.csrf);
      onLogin();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="login-screen">
      <section className="login-art">
        <Brand />
        <div className="login-grid" />
        <div className="orbit orbit-one" />
        <div className="orbit orbit-two" />
        <div className="login-node node-a" />
        <div className="login-node node-b" />
        <div className="login-node node-c" />
        <div className="login-mark">
          <Network strokeWidth={1} />
        </div>
        <div className="login-copy">
          <span className="eyebrow">PRIVATE PROXY WORKSPACE</span>
          <h1>
            每一个出口，
            <br />
            清晰可控。
          </h1>
          <p>代理资源、连接与会话，集中管理。</p>
        </div>
        <div className="login-footer">
          <span>RAPIP GATEWAY</span>
          <span>01 / PRIVATE ACCESS</span>
        </div>
      </section>
      <section className="login-form-area">
        <div className="login-form-wrap">
          <div className="login-access">
            <ShieldCheck size={18} />
            <span>管理员入口</span>
          </div>
          <h2>登录工作台</h2>
          <p className="muted">使用本地配置的管理员凭据继续。</p>
          <form onSubmit={submit}>
            <label>
              用户名
              <input
                name="username"
                autoComplete="username"
                defaultValue="admin"
                required
              />
            </label>
            <label>
              密码
              <input
                name="password"
                type="password"
                autoComplete="current-password"
                placeholder="输入管理员密码"
                required
              />
            </label>
            {error && (
              <div className="form-error" role="alert">
                {error}
              </div>
            )}
            <Button disabled={busy} type="submit">
              {busy ? <Loader2 className="spin" size={17} /> : "登录"}
              <ArrowRight size={17} />
            </Button>
          </form>
          <div className="login-note">
            <ShieldCheck size={15} /> 凭据仅用于连接你的私有网关
          </div>
        </div>
      </section>
    </main>
  );
}

function BindingsPage({
  onRotate,
  onRelease,
  onConnect,
}: {
  onRotate: (item: BindingData) => void;
  onRelease: (item: BindingData) => void;
  onConnect: () => void;
}) {
  const [group, setGroup] = useState(
    new URLSearchParams(location.hash.split("?")[1]).get("group_id") ?? "",
  );
  const groups = useQuery<{ items: Group[] }>("/groups", true, 15000);
  const [current, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const history = useQuery<Paged<EventData>>(
    `/events?binding_id=${expanded ?? "none"}&page_size=20`,
    !!expanded,
    5000,
  );
  const query = useQuery<Paged<BindingData>>(
    `/bindings?page=${current}&page_size=20&q=${encodeURIComponent(search)}&group_id=${encodeURIComponent(group)}`,
  );
  return (
    <section className="surface page-enter">
      <div className="table-toolbar">
        <div className="search-input">
          <Search size={16} />
          <input
            aria-label="搜索工位或会话"
            placeholder="搜索工位或 session"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
        </div>
        <select
          aria-label="筛选会话分组"
          value={group}
          onChange={(e) => {
            setGroup(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部分组</option>
          {groups.data?.items.map((g) => (
            <option key={g.id} value={g.id}>
              {g.name}
            </option>
          ))}
        </select>
        <Button variant="secondary" size="sm" onClick={onConnect}>
          <Plus size={15} />
          连接配置
        </Button>
      </div>
      <ErrorNote message={query.error} />
      {query.isLoading && <LoadingState label="正在加载会话绑定…" />}
      <div className="binding-list">
        {query.data?.items.map((item) => (
          <div className="binding-item" key={item.id}>
            <div className="binding-main">
              <button
                className="binding-title"
                onClick={() =>
                  setExpanded(expanded === item.id ? null : item.id)
                }
              >
                <span className="worker-icon">
                  <Workflow size={19} />
                </span>
                <span>
                  <strong>
                    {item.worker ? `工位 ${item.worker}` : "匿名会话"}
                  </strong>
                  <small className="mono">{item.session}</small>
                </span>
              </button>
              <div className="binding-ip">
                <span className="mono strong">{item.ip || "等待可用出口"}</span>
                <span className="small muted">
                  <GroupLabel id={item.group_id} /> ·{" "}
                  {item.country || "任意国家"} · 第 {item.generation} 代
                </span>
              </div>
              <div>
                <Badge state={item.status} />
                <div className="small muted binding-count">
                  {item.connections} 条连接 ·{" "}
                  {item.connections === 0 ? "IP 保留中" : "传输中"} ·{" "}
                  {item.failovers} 次故障转移
                </div>
              </div>
              <div className="row-actions">
                <Button
                  variant="secondary"
                  size="sm"
                  disabled={item.status === "RESERVED"}
                  onClick={() => onRotate(item)}
                >
                  <RefreshCw size={14} />换 IP
                </Button>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`${item.status === "RESERVED" ? "取消预留工位" : "释放工位"} ${item.worker || item.session}`}
                  onClick={() => onRelease(item)}
                >
                  <Unplug size={16} />
                </Button>
              </div>
            </div>
            {expanded === item.id && (
              <div className="binding-detail">
                <p className="rotation-permanent-note">
                  {item.unavailable || "绑定长期保留；断连不会释放 IP"} ·{" "}
                  {rotationStates[item.rotation_status?.status ?? "OFF"] ??
                    item.rotation_status?.status}{" "}
                  {item.rotation_status?.next_at
                    ? `· 下次轮换 ${date(item.rotation_status.next_at)}`
                    : ""}
                </p>
                {item.generations.map((generation) => (
                  <div key={generation.node_id}>
                    <span className="mono">{generation.ip}</span>
                    <span>第 {generation.generation} 代</span>
                    <Badge state={generation.state} />
                    <span>{generation.connections} 条连接</span>
                    {generation.drain_until && (
                      <span className="small muted">
                        排空截止 {date(generation.drain_until)}
                      </span>
                    )}
                  </div>
                ))}
                <div className="binding-timeline">
                  <strong>绑定事件</strong>
                  {history.data?.items.map((e) => (
                    <p key={e.id}>
                      <time>{date(e.at)}</time> · {e.message}
                    </p>
                  ))}
                  {!history.data?.items.length && (
                    <p>暂无保留事件，历史日志可能已清理</p>
                  )}
                </div>
                <p className="small muted">
                  最近活动 {date(item.last_active)} · 创建于{" "}
                  {date(item.created_at)}
                </p>
              </div>
            )}
          </div>
        ))}
      </div>
      {query.data?.items.length === 0 && (
        <div className="binding-empty">
          <Empty title="等待第一个会话">
            将代理配置到客户端，建立连接后会自动出现在这里。
          </Empty>
          <Button variant="secondary" onClick={onConnect}>
            <Terminal size={16} />
            获取连接配置
          </Button>
        </div>
      )}
      <Pager
        current={current}
        total={query.data?.total ?? 0}
        setPage={setPage}
      />
    </section>
  );
}

function AccountsPage({ notify }: { notify: (message: string) => void }) {
  const query = useQuery<{ items: AccountData[] }>("/accounts");
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<AccountData | null>(null);
  const [removing, setRemoving] = useState<AccountData | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const deletion = useQuery<{
    allowed: boolean;
    reason: string | null;
    node_count: number;
  }>(
    `/accounts/${removing?.account_id ?? "none"}/deletion-check`,
    !!removing,
    3000,
  );
  const currentRemoval =
    query.data?.items.find(
      (item) => item.account_id === removing?.account_id,
    ) ?? removing;
  async function add(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const body = {
      name: form.get("name"),
      notes: String(form.get("notes") || "").trim(),
      api_key: form.get("api_key"),
      replacement_budget: form.get("replacement_budget")
        ? Number(form.get("replacement_budget"))
        : null,
    };
    try {
      await mutate("/accounts", body);
      setAdding(false);
      refreshAll();
      notify("账号已添加，正在同步节点");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const [syncing, setSyncing] = useState("");
  async function refreshUsage(item: AccountData) {
    setSyncing(item.account_id);
    try {
      const result = await operation(
        `/accounts/${item.account_id}/refresh-usage`,
      );
      refreshAll();
      notify(
        result.error ? String(result.error) : "供应商套餐额度与流量已同步",
      );
    } catch (err) {
      notify((err as Error).message);
    } finally {
      setSyncing("");
    }
  }
  async function toggle(item: AccountData) {
    try {
      await mutate(
        `/accounts/${item.account_id}`,
        { enabled: !item.enabled },
        "PATCH",
      );
      refreshAll();
      notify(
        item.enabled ? "已暂停该账号的新分配，现有连接保留" : "已恢复新分配",
      );
    } catch (err) {
      notify((err as Error).message);
    }
  }
  async function saveAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editing) return;
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    try {
      await mutate(
        `/accounts/${editing.account_id}`,
        {
          name: String(form.get("name")).trim(),
          notes: String(form.get("notes") || "").trim(),
          traffic_limit_gb: form.get("traffic_limit_gb")
            ? Number(form.get("traffic_limit_gb"))
            : null,
          ...(String(form.get("api_key") || "").trim()
            ? { api_key: String(form.get("api_key")).trim() }
            : {}),
          replacement_budget: form.get("replacement_budget")
            ? Number(form.get("replacement_budget"))
            : null,
          replacement_country: form.get("replacement_country"),
        },
        "PATCH",
      );
      setEditing(null);
      refreshAll();
      notify("账号资料与补货策略已保存");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function removeAccount(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!removing || confirmation !== removing.account_id) return;
    setBusy(true);
    setError("");
    try {
      const result = await operation(
        `/accounts/${removing.account_id}`,
        {},
        "DELETE",
      );
      setRemoving(null);
      setConfirmation("");
      refreshAll();
      notify(
        String(result.warning || "账号已从本地移除，没有调用供应商更换或退订"),
      );
    } catch (err) {
      setError((err as Error).message);
      refreshAll();
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="page-enter">
      <div className="page-subtoolbar">
        <p className="muted small">
          账号暂停只影响新分配，保留正在使用的连接。
        </p>
        <Button
          onClick={() => {
            setError("");
            setAdding(true);
          }}
        >
          <Plus size={16} />
          添加账号
        </Button>
      </div>
      <ErrorNote message={query.error} />
      {query.isLoading && <LoadingState label="正在加载上游账号…" />}
      <div className="account-list">
        {query.data?.items.map((item) => (
          <section className="surface account-surface" key={item.account_id}>
            <div className="account-heading">
              <div className="account-emblem">
                <Globe2 size={22} />
              </div>
              <div>
                <h2>{item.name}</h2>
                <span className="small muted mono">
                  {item.account_id} / WEBSHARE
                </span>
              </div>
              <Badge state={item.health.state} />
              <div className="account-actions">
                {" "}
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    setError("");
                    setEditing(item);
                  }}
                >
                  <Settings2 size={15} />
                  编辑
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  aria-label={`删除账号 ${item.name}`}
                  onClick={() => {
                    setRemoving(item);
                    setConfirmation("");
                    setError("");
                  }}
                >
                  <Trash2 size={14} />
                  删除
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void toggle(item)}
                >
                  {item.enabled ? <Pause size={14} /> : <Play size={14} />}
                  {item.enabled ? "暂停新分配" : "恢复分配"}
                </Button>
              </div>
            </div>
            {item.notes && (
              <p className="account-notes">
                <span>备注</span>
                {item.notes}
              </p>
            )}
            <div className="account-metrics">
              <div>
                <span>受管 IP</span>
                <strong>
                  {item.node_count}
                  <small>个</small>
                </strong>
              </div>
              <div>
                <span>可分配</span>
                <strong className="accent">
                  {item.fresh_count}
                  <small>个</small>
                </strong>
              </div>
              <div>
                <span>供应商更换额度 · 剩余 / 总额</span>
                <strong className="account-quota-value">
                  {quotaPair(
                    item.supplier_usage?.replacement?.remaining,
                    item.supplier_usage?.replacement?.total,
                  )}
                  <small>次</small>
                </strong>
                <p className="quota-metric-help">
                  已用 {item.supplier_usage?.replacement?.used ?? "—"} 次
                  {item.supplier_usage?.stale ? " · 待更新" : ""}
                </p>
              </div>
              <div>
                <span>套餐流量 · 剩余 / 总量</span>
                <strong className="account-quota-value">
                  {item.supplier_usage?.traffic?.unlimited
                    ? "不限量"
                    : trafficPair(
                        item.supplier_usage?.traffic?.remaining_bytes,
                        item.supplier_usage?.traffic?.total_bytes,
                      )}
                </strong>
                <p className="quota-metric-help">
                  本期已用{" "}
                  {packageBytes(item.supplier_usage?.traffic?.used_bytes)}
                </p>
              </div>
            </div>
            <div className="supplier-sync-line">
              <div>
                <span
                  className={`ops-status tone-${item.supplier_usage?.stale ? "warn" : "good"}`}
                >
                  {item.supplier_usage?.stale
                    ? "套餐数据待更新"
                    : "Webshare API"}
                </span>
                <span>
                  {item.supplier_usage?.checked_at
                    ? `同步 ${date(item.supplier_usage.checked_at)}`
                    : "尚未同步套餐"}{" "}
                  · 供应商用量按小时聚合
                </span>
                {item.supplier_usage?.error && (
                  <span className="ops-warning">
                    {item.supplier_usage.error}
                  </span>
                )}
              </div>
              <Button
                variant="secondary"
                size="sm"
                disabled={!!syncing}
                onClick={() => void refreshUsage(item)}
              >
                <RefreshCw
                  size={13}
                  className={syncing === item.account_id ? "spin" : ""}
                />
                同步套餐
              </Button>
            </div>
            <details className="account-local-budget">
              <summary>
                本地安全预算与网关计数{" "}
                <span>
                  {item.usage.submitted} / {item.replacement_budget ?? "不限"}{" "}
                  次
                </span>
              </summary>
              <div className="account-budget">
                <div>
                  <span>本地替换预算</span>
                  <span>
                    {item.replacement_budget !== null
                      ? `${Math.max(0, item.replacement_budget - item.usage.submitted).toLocaleString()} 次可用`
                      : "未设上限"}
                  </span>
                </div>
                <div className="budget-track">
                  <i
                    style={{
                      width: `${item.replacement_budget ? Math.max(0, 1 - item.usage.submitted / item.replacement_budget) * 100 : 100}%`,
                    }}
                  />
                </div>
                <p>
                  成功补货 {item.usage.completed} 次 · 碰撞重抽{" "}
                  {item.usage.redraws} 次 · 非供应商实时余额
                </p>
              </div>
            </details>
            <div className="account-footer">
              <span>{item.health.message}</span>
              <span>最近同步 {date(item.health.synced_at)}</span>
            </div>
          </section>
        ))}
      </div>
      {query.data?.items.length === 0 && (
        <section className="surface">
          <Empty
            icon={<Globe2 size={25} />}
            title="添加你的第一个 Webshare 账号"
          >
            API Key 仅写入后台私有存储。
          </Empty>
        </section>
      )}
      <Dialog
        open={adding}
        onOpenChange={(open) => {
          if (!busy) setAdding(open);
        }}
        title="添加上游账号"
        description="验证 API Key 后同步资源，密钥保存后不再回显。"
      >
        <form onSubmit={add} className="form-stack">
          <p className="field-help">
            账号 ID 自动生成，列表优先显示名称。套餐额度和流量会从 API 同步。
          </p>
          <label>
            显示名称
            <input name="name" placeholder="例如 备用账号" required />
          </label>
          <label>
            备注（选填）
            <textarea
              name="notes"
              maxLength={500}
              rows={3}
              placeholder="例如：所属项目、采购渠道或使用说明"
            />
          </label>
          <label>
            API Key
            <input
              name="api_key"
              type="password"
              autoComplete="off"
              minLength={16}
              required
            />
          </label>
          <details className="quality-advanced">
            <summary>本地安全限制（选填）</summary>
            <div className="quality-advanced-body">
              {" "}
              <label>
                本地替换预算
                <input
                  name="replacement_budget"
                  type="number"
                  min="0"
                  placeholder="留空不限制"
                />
              </label>
              <p className="field-help">
                这是本网关允许消耗的上限，独立于供应商剩余额度；留空不额外限制。
              </p>
            </div>
          </details>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <Button disabled={busy} type="submit">
            {busy && <Loader2 className="spin" size={16} />}验证并添加
          </Button>
        </form>
      </Dialog>
      <Dialog
        open={!!editing}
        onOpenChange={(open) => {
          if (!open && !busy) setEditing(null);
        }}
        title="编辑上游账号"
        description="账号 ID 保持不变。名称与套餐记录可编辑；本地计数不会因编辑而重置。"
      >
        {editing && (
          <form className="form-stack" onSubmit={saveAccount}>
            <div className="account-id-note">
              账号 ID：<code>{editing.account_id}</code>
            </div>
            <label>
              显示名称
              <input
                name="name"
                required
                maxLength={64}
                defaultValue={editing.name}
              />
            </label>
            <label>
              备注（选填）
              <textarea
                name="notes"
                maxLength={500}
                rows={3}
                defaultValue={editing.notes ?? ""}
              />
            </label>
            <label>
              套餐流量备用记录（GB）
              <input
                name="traffic_limit_gb"
                type="number"
                min={0}
                step={1}
                defaultValue={editing.traffic_limit_gb ?? ""}
                placeholder="留空表示未记录"
              />
            </label>
            <p className="field-help">
              套餐真实额度与已用量由 API
              同步；下方预算和流量备用记录仅作为本地限制/说明，不覆盖供应商数据。
            </p>
            <label>
              本地替换预算
              <input
                name="replacement_budget"
                type="number"
                min="0"
                defaultValue={editing.replacement_budget ?? ""}
              />
            </label>
            <label>
              补货国家
              <select
                name="replacement_country"
                defaultValue={editing.replacement_country}
              >
                {!["original", "any", "US", "GB", "DE", "JP"].includes(
                  editing.replacement_country,
                ) && (
                  <option value={editing.replacement_country}>
                    {editing.replacement_country}
                  </option>
                )}
                <option value="original">保持原国家</option>
                <option value="any">任意国家</option>
                <option value="US">美国 US</option>
                <option value="GB">英国 GB</option>
                <option value="DE">德国 DE</option>
                <option value="JP">日本 JP</option>
              </select>
            </label>
            <details className="quality-advanced">
              <summary>更新 API Key（选填）</summary>
              <div className="quality-advanced-body">
                <p className="field-help">
                  留空保留当前密钥。更新前请暂停账号并结束业务；后台会只读验证密钥和资源一致性，校验失败保留原配置。
                </p>
                <label>
                  新 API Key
                  <input
                    name="api_key"
                    type="password"
                    autoComplete="new-password"
                    maxLength={256}
                    minLength={16}
                    placeholder="留空不修改"
                  />
                </label>
              </div>
            </details>
            {error && (
              <p className="form-error" role="alert">
                {error}
              </p>
            )}
            <Button disabled={busy} type="submit">
              保存配置
            </Button>
          </form>
        )}
      </Dialog>
      <Dialog
        open={!!removing}
        onOpenChange={(open) => {
          if (!open && !busy) setRemoving(null);
        }}
        title="移除上游账号"
        description="仅删除本网关的账号接入，不注销 Webshare 账号、不退订套餐、不撤销供应商密钥，也不调用 Replace。"
      >
        {currentRemoval && (
          <form className="form-stack" onSubmit={removeAccount}>
            <div className="account-removal-note">
              <strong>{currentRemoval.name}</strong>
              <p>
                {currentRemoval.account_id} ·{" "}
                {deletion.data?.node_count ?? currentRemoval.node_count}{" "}
                个受管出口将归档，不再参与分配；本地私有存储中的 API Key
                和节点凭据会清理，历史计数与审计标识保留。
              </p>
            </div>
            {currentRemoval.enabled && (
              <Button
                variant="secondary"
                type="button"
                disabled={busy}
                onClick={() => void toggle(currentRemoval)}
              >
                先暂停该账号的新分配
              </Button>
            )}
            {deletion.data?.reason && (
              <p className="ops-warning">{deletion.data.reason}</p>
            )}
            <ErrorNote message={error || deletion.error} />
            <label>
              输入账号 ID 确认移除
              <input
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
                placeholder={currentRemoval.account_id}
                autoComplete="off"
              />
            </label>
            <Button
              type="submit"
              disabled={
                busy ||
                !deletion.data?.allowed ||
                confirmation !== currentRemoval.account_id
              }
            >
              {busy ? (
                <Loader2 className="spin" size={14} />
              ) : (
                <Trash2 size={14} />
              )}
              确认本地移除
            </Button>
          </form>
        )}
      </Dialog>
    </div>
  );
}

function SettingsPage({ notify }: { notify: (message: string) => void }) {
  const sections = [
    ["scheduler", "调度与连接"],
    ["quality", "质量检测源"],
    ["retention", "数据生命周期"],
    ["appearance", "外观主题"],
    ["runtime", "运行配置"],
  ] as const;
  const initial =
    new URLSearchParams(location.hash.split("?")[1]).get("tab") ?? "scheduler";
  const [section, setSection] = useState(
    sections.some(([id]) => id === initial) ? initial : "scheduler",
  );
  const [visited, setVisited] = useState<string[]>([section]);
  const query = useQuery<SettingsData>("/settings", true, 15000);
  useEffect(() => {
    history.replaceState(null, "", "#settings?tab=" + section);
  }, [section]);
  function chooseSection(id: string, focus = false) {
    setSection(id);
    setVisited((old) => (old.includes(id) ? old : [...old, id]));
    if (focus) document.getElementById("settings-tab-" + id)?.focus();
  }
  const [busy, setBusy] = useState(false);
  const [probeSaving, setProbeSaving] = useState(false);
  async function toggleProbe() {
    if (!query.data || probeSaving) return;
    setProbeSaving(true);
    const enabled = !query.data.values.probe_enabled;
    try {
      await mutate("/settings", { probe_enabled: enabled }, "PATCH");
      notify(
        enabled
          ? "主动测活已开启，正在验证未使用节点"
          : "主动测活已关闭，已有业务连接保持不变",
      );
      await query.refresh();
      refreshAll();
    } catch (err) {
      notify((err as Error).message);
    } finally {
      setProbeSaving(false);
    }
  }
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const form = new FormData(event.currentTarget);
    const values: Record<string, number | string> = {};
    for (const [key, value] of form)
      values[key] = ["session_switch_mode", "connection_log_mode"].includes(key)
        ? String(value)
        : Number(value);
    try {
      await mutate("/settings", values, "PATCH");
      notify("设置已保存并生效");
      refreshAll();
    } catch (err) {
      notify((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const fields = [
    {
      key: "max_connections",
      label: "网关并发连接上限",
      detail: "限制新接入连接；调整不会主动断开现有连接",
      min: 1,
      max: 100000,
    },
    {
      key: "max_connections_per_binding",
      label: "单绑定 TCP 连接上限",
      detail:
        "同一绑定所有 TCP 连接的总和，包含排空中的旧连接；不是浏览器数量。超限拒绝新连接，不让 session 失效",
      min: 1,
      max: 100000,
    },
    {
      key: "quality_daily_limit",
      label: "分组每日全局替换预算",
      detail:
        "次/天，所有非默认组共用，并叠加账号及单组限制；包含已预留任务，0 暂停分组替换，默认组不受此项约束",
      min: 0,
      max: 100000,
    },
    {
      key: "max_active_bindings",
      label: "最大保留绑定数",
      detail:
        "包含无连接但保留 IP 的绑定。0 按受管资源数减备用数计算，不再隐式保留 40%；不会抢占已有绑定",
      min: 0,
      max: 100000,
    },
    {
      key: "pool_wait_timeout",
      label: "热备等待上限",
      detail:
        "秒，申请或切换时等待符合分组、国家、质量条件的备用 IP；超时保留原绑定并报错，不消耗新 IP",
      min: 0.1,
      max: 30,
    },
    {
      key: "drain_timeout",
      label: "平滑排空上限",
      detail:
        "秒，平滑换 IP 后旧连接继续运行的最长时间；结束或到期后旧 IP 退役并补货，可能消耗更换额度",
      min: 1,
      max: 3600,
    },
    {
      key: "min_spare_ips",
      label: "最少备用 IP 数",
      detail:
        "限制新绑定使用的备用余量，默认 0；留 0 个可全部绑定，但换 session 或平滑轮换可能因缺货失败。已有绑定不受挤出",
      min: 0,
      max: 100000,
    },
    {
      key: "probe_interval",
      label: "热备巡检间隔",
      detail:
        "秒，主动测活开启时检查未使用的候选节点，产生少量代理流量，不回收长期绑定",
      min: 60,
      max: 86400,
    },
    {
      key: "sync_interval",
      label: "账号同步间隔",
      detail:
        "秒，核对供应商 IP 列表与套餐快照，不代表每次同步都更换 IP；原出口被供应商移除会明确提示",
      min: 30,
      max: 86400,
    },
  ];
  return (
    <div className="page-enter">
      <div
        className="settings-tabs surface"
        role="tablist"
        aria-label="网关设置板块"
      >
        {sections.map(([id, label], index) => (
          <button
            type="button"
            role="tab"
            id={"settings-tab-" + id}
            aria-controls={"settings-panel-" + id}
            aria-selected={section === id}
            tabIndex={section === id ? 0 : -1}
            key={id}
            onClick={() => chooseSection(id)}
            onKeyDown={(event) => {
              const target =
                event.key === "ArrowRight"
                  ? (index + 1) % sections.length
                  : event.key === "ArrowLeft"
                    ? (index + sections.length - 1) % sections.length
                    : event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? sections.length - 1
                        : -1;
              if (target >= 0) {
                event.preventDefault();
                chooseSection(sections[target][0], true);
              }
            }}
          >
            {label}
          </button>
        ))}
      </div>
      <div
        role="tabpanel"
        id="settings-panel-appearance"
        aria-labelledby="settings-tab-appearance"
        hidden={section !== "appearance"}
      >
        {visited.includes("appearance") && <AppearanceSettings />}
      </div>
      <div
        role="tabpanel"
        id="settings-panel-quality"
        aria-labelledby="settings-tab-quality"
        hidden={section !== "quality"}
      >
        {visited.includes("quality") && <QualitySources notify={notify} />}
      </div>
      <div
        role="tabpanel"
        id="settings-panel-retention"
        aria-labelledby="settings-tab-retention"
        hidden={section !== "retention"}
      >
        {visited.includes("retention") && <Retention notify={notify} />}
      </div>
      <ErrorNote message={query.error} />
      {query.isLoading && section !== "appearance" && (
        <LoadingState label="正在加载网关设置…" variant="settings" />
      )}
      {query.data && (
        <>
          <div
            role="tabpanel"
            id="settings-panel-scheduler"
            aria-labelledby="settings-tab-scheduler"
            hidden={section !== "scheduler"}
          >
            {visited.includes("scheduler") && (
              <section className="surface settings-surface">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">SCHEDULER</span>
                    <h2>容量、连接与调试</h2>
                  </div>
                  <span className="small muted">修改后即时生效</span>
                </div>
                <p className="rotation-permanent-note">
                  固定配置长期有效：关闭连接不会释放 IP，session
                  不因空闲过期。定时轮换与无连接暂停在质量组中独立配置，默认关闭。
                </p>
                <form onSubmit={save}>
                  <div className="settings-fields">
                    <div className="setting-row probe-setting">
                      <div>
                        <label id="probe-switch-label">主动测活</label>
                        <p>
                          开启后立即检查未使用节点；关闭停止新测活，不影响已有业务连接。
                        </p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-labelledby="probe-switch-label"
                        aria-checked={Boolean(query.data.values.probe_enabled)}
                        disabled={busy || probeSaving}
                        onClick={() => void toggleProbe()}
                        className="probe-switch"
                      >
                        <span className="switch-track">
                          <i />
                        </span>
                        <span>
                          {probeSaving
                            ? "保存中…"
                            : query.data.values.probe_enabled
                              ? "已开启"
                              : "已关闭"}
                        </span>
                      </button>
                    </div>
                    <div className="setting-row">
                      <div>
                        <label htmlFor="connection_log_mode">
                          连接明细记录
                        </label>
                        <p>
                          聚合指标始终采集，明细可关闭；保留期限在数据生命周期中设置
                        </p>
                      </div>
                      <select
                        id="connection_log_mode"
                        name="connection_log_mode"
                        defaultValue={String(
                          query.data.values.connection_log_mode,
                        )}
                      >
                        <option value="off">关闭</option>
                        <option value="errors">仅失败</option>
                        <option value="all">全量</option>
                      </select>
                    </div>
                    {fields.map((field) => (
                      <div className="setting-row" key={field.key}>
                        <div>
                          <label htmlFor={field.key}>{field.label}</label>
                          <p>{field.detail}</p>
                        </div>
                        <input
                          id={field.key}
                          name={field.key}
                          type="number"
                          min={field.min}
                          max={field.max}
                          step="any"
                          defaultValue={Number(query.data!.values[field.key])}
                          required
                        />
                      </div>
                    ))}
                    <div className="setting-row">
                      <div>
                        <label htmlFor="session_switch_mode">
                          默认切换方式
                        </label>
                        <p>风控等立即停用场景可单次选择强制切换</p>
                      </div>
                      <select
                        id="session_switch_mode"
                        name="session_switch_mode"
                        defaultValue={String(
                          query.data.values.session_switch_mode,
                        )}
                      >
                        <option value="graceful">平滑切换</option>
                        <option value="hard">强制切换</option>
                      </select>
                    </div>
                  </div>
                  <div className="settings-footer">
                    <span>
                      <ShieldCheck size={15} /> 持久化后生效，重启保留
                    </span>
                    <Button disabled={busy || probeSaving} type="submit">
                      {busy && <Loader2 size={16} className="spin" />}保存设置
                    </Button>
                  </div>
                </form>
              </section>
            )}
          </div>
          <div
            role="tabpanel"
            id="settings-panel-runtime"
            aria-labelledby="settings-tab-runtime"
            hidden={section !== "runtime"}
          >
            {visited.includes("runtime") && (
              <section className="surface deployment-surface">
                <div className="section-heading">
                  <div>
                    <span className="eyebrow">RUNTIME</span>
                    <h2>运行配置</h2>
                  </div>
                  <span className="small muted">部署参数 · 只读</span>
                </div>
                <div className="deployment-grid">
                  <div>
                    <span>服务器公网 IP / 代理主机</span>
                    <code>
                      {query.data.deployment.public_proxy_host || "未配置"}
                    </code>
                  </div>
                  <div>
                    <span>session 生命周期</span>
                    <strong>
                      {query.data.deployment.session_expires
                        ? "按会话策略"
                        : "长期保留 · 空闲不过期"}
                    </strong>
                  </div>
                  <div>
                    <span>SOCKS5 入口</span>
                    <code>
                      {query.data.deployment.public_proxy_host ||
                        "未配置公网地址"}
                      :{query.data.deployment.public_socks_port}
                    </code>
                  </div>
                  <div>
                    <span>HTTP 代理入口</span>
                    <code>
                      {query.data.deployment.public_proxy_host ||
                        "未配置公网地址"}
                      :{query.data.deployment.public_http_proxy_port}
                    </code>
                  </div>
                  <div>
                    <span>长连接无流量超时</span>
                    <strong>
                      {query.data.deployment.tunnel_idle_timeout === 0
                        ? "已禁用 · 静默 TCP 不因无流量关闭"
                        : `${query.data.deployment.tunnel_idle_timeout} 秒`}
                    </strong>
                  </div>
                  <div>
                    <span>同 session 故障转移</span>
                    <strong>{query.data.deployment.fault_failover}</strong>
                  </div>
                </div>
                <details className="quality-advanced">
                  <summary>高级监听信息</summary>
                  <p className="field-help">
                    0.0.0.0
                    表示监听所有接口，不能用作客户端连接地址。公网端口可通过部署环境变量映射。
                  </p>
                  <code>
                    SOCKS5 {query.data.deployment.socks_host}:
                    {query.data.deployment.socks_port} · HTTP{" "}
                    {query.data.deployment.proxy_http_host}:
                    {query.data.deployment.proxy_http_port}
                  </code>
                </details>
              </section>
            )}
          </div>
        </>
      )}
    </div>
  );
}

export default function App() {
  const [routeKey, setRouteKey] = useState(() => window.location.hash);
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [pageId, setPageId] = useState<PageId>(() => {
    const initial = window.location.hash.slice(1).split("?")[0];
    return navigation.some((item) => item.id === initial)
      ? (initial as PageId)
      : "overview";
  });
  const [toast, setToast] = useState("");
  const [connectionOpen, setConnectionOpen] = useState(false);
  const [target, setTarget] = useState<BindingData | null>(null);
  const [action, setAction] = useState<"rotate" | "release">("rotate");
  const [mode, setMode] = useState<"graceful" | "hard">("graceful");
  const [sessionPolicy, setSessionPolicy] = useState("keep");
  const [newSession, setNewSession] = useState("");
  const [busy, setBusy] = useState(false);
  const [operationError, setOperationError] = useState("");
  const busyRef = useRef(false);
  const statsQuery = useQuery<Stats>("/stats", authenticated === true);
  const settings = useQuery<SettingsData>(
    "/settings",
    authenticated === true,
    30000,
  );
  const active = navigation.find((item) => item.id === pageId)!;
  const notify = useCallback((message: string) => setToast(message), []);
  const navigate = useCallback(
    (value: PageId, filters?: Record<string, string>) => {
      setPageId(value);
      setRouteKey("#" + value + (filters ? "?" + queryString(filters) : ""));
      window.location.hash =
        value + (filters ? "?" + queryString(filters) : "");
    },
    [],
  );
  useEffect(() => {
    void api<{ csrf: string }>("/auth/me")
      .then((result) => {
        setCsrf(result.csrf);
        setAuthenticated(true);
      })
      .catch(() => setAuthenticated(false));
    const unauthorized = () => {
      setCsrf(null);
      setAuthenticated(false);
    };
    const hashChange = () => {
      setRouteKey(window.location.hash);
      const id = window.location.hash.slice(1).split("?")[0];
      if (navigation.some((item) => item.id === id)) setPageId(id as PageId);
    };
    window.addEventListener("rapip:unauthorized", unauthorized);
    window.addEventListener("hashchange", hashChange);
    return () => {
      window.removeEventListener("rapip:unauthorized", unauthorized);
      window.removeEventListener("hashchange", hashChange);
    };
  }, []);
  useEffect(() => {
    if (toast) {
      const timer = window.setTimeout(() => setToast(""), 6500);
      return () => window.clearTimeout(timer);
    }
  }, [toast]);
  async function watchOperation(operation: TaskData) {
    for (let count = 0; count < 90; count++) {
      await new Promise((resolve) => window.setTimeout(resolve, 1000));
      try {
        const result = await api<TaskData>(`/operations/${operation.id}`);
        if (result.state === "COMPLETED") {
          refreshAll();
          notify("操作已完成");
          return;
        }
        if (result.state === "FAILED") {
          refreshAll();
          notify(result.error || "操作未完成");
          return;
        }
      } catch {
        notify("结果暂不可用，请到任务页查看原操作");
        return;
      }
    }
    notify("任务仍在进行，可在任务页追踪");
  }
  async function sync() {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      const op = await mutate<TaskData>("/sync");
      notify("同步已受理");
      void watchOperation(op);
    } catch (err) {
      notify((err as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  function choose(item: BindingData, kind: "rotate" | "release") {
    setTarget(item);
    setAction(kind);
    setMode("graceful");
    setSessionPolicy("keep");
    setOperationError("");
    setNewSession("");
  }
  async function control(event: FormEvent) {
    event.preventDefault();
    if (!target || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setOperationError("");
    const body =
      action === "release"
        ? { mode, expected_generation: target.generation }
        : {
            mode,
            session_policy: sessionPolicy,
            ...(sessionPolicy === "new" ? { new_session_id: newSession } : {}),
            expected_generation: target.generation,
          };
    try {
      const op = await mutate<TaskData>(
        `/bindings/${target.id}/${action === "release" && target.status === "RESERVED" ? "cancel-reservation" : action}`,
        action === "release" && target.status === "RESERVED" ? {} : body,
      );
      setTarget(null);
      notify("操作已受理，正在等待执行结果");
      void watchOperation(op);
      refreshAll();
    } catch (err) {
      setOperationError((err as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
      setCsrf(null);
      setAuthenticated(false);
    } catch (err) {
      notify((err as Error).message);
    }
  }
  if (authenticated === null)
    return (
      <div className="boot-screen">
        <Brand />
        <Loader2 size={22} className="spin" />
      </div>
    );
  if (!authenticated)
    return (
      <Login
        onLogin={() => {
          setAuthenticated(true);
          refreshAll();
        }}
      />
    );
  return (
    <GroupNamesProvider>
      <div className="app-shell">
        <aside className="sidebar">
          <div className="sidebar-brand">
            <Brand />
            <span className="version-tag">v0.2.6</span>
          </div>
          <div className="workspace-label">WORKSPACE</div>
          <nav aria-label="主导航">
            {navigation.map((item) => (
              <button
                key={item.id}
                className={`nav-item ${pageId === item.id ? "active" : ""}`}
                aria-label={item.label}
                title={item.label}
                onClick={() => navigate(item.id)}
                aria-current={pageId === item.id ? "page" : undefined}
              >
                <item.icon size={18} strokeWidth={1.7} />
                <span>{item.label}</span>
                {item.id === "bindings" &&
                  !!statsQuery.data?.active_bindings && (
                    <small>{statsQuery.data.active_bindings}</small>
                  )}
              </button>
            ))}
          </nav>
          <div className="sidebar-bottom">
            <div className="private-badge">
              <ShieldCheck size={17} />
              <div>
                <strong>私有工作空间</strong>
                <span>单管理员 · 私有网关</span>
              </div>
            </div>
            <button
              className="logout-button"
              aria-label="退出管理员登录"
              onClick={() => void logout()}
            >
              <span className="avatar">A</span>
              <span>
                <strong>管理员</strong>
                <small>admin</small>
              </span>
              <LogOut size={16} />
            </button>
          </div>
        </aside>
        <main className="workspace">
          <div className="topbar">
            <div className="breadcrumb">
              <Menu size={15} />
              <span>工作空间</span>
              <ChevronRight size={13} />
              <strong>{active.label}</strong>
            </div>
            <div className="topbar-status">
              <span
                className={`pulse-dot ${statsQuery.error ? "offline-dot" : ""}`}
              />
              <span>
                {statsQuery.error
                  ? "连接中断"
                  : statsQuery.data?.ready
                    ? "服务正常"
                    : "等待就绪"}
              </span>
              <span className="topbar-divider" />
              <span className="mono">
                {settings.data?.deployment.version
                  ? `v${settings.data.deployment.version}`
                  : "RAPIP"}{" "}
                / GATEWAY
              </span>
            </div>
          </div>
          <div className="content content-compact">
            <header className="page-heading">
              <div>
                <span className="eyebrow">
                  {pageId === "overview"
                    ? "GATEWAY OVERVIEW"
                    : active.id.toUpperCase()}
                </span>
                <h1>
                  {active.label}
                  <span className="heading-dot">.</span>
                </h1>
                <p>{active.caption}</p>
              </div>
              <div className="heading-actions">
                <span className="updated-label">
                  {statsQuery.updatedAt
                    ? `更新于 ${new Date(statsQuery.updatedAt).toLocaleTimeString("zh-CN", { hour12: false })}`
                    : "正在获取状态"}
                </span>
                <Button
                  variant="secondary"
                  onClick={() => void sync()}
                  disabled={busy}
                >
                  <RefreshCw size={15} className={busy ? "spin" : ""} />
                  同步资源
                </Button>
              </div>
            </header>
            <ErrorNote message={statsQuery.error} />
            {statsQuery.data?.probe_enabled === false && (
              <div className="environment-note">
                <CircleHelp size={16} />
                <span>
                  主动测活已关闭；已验证资源仍可使用，新节点保持隔离。
                </span>
              </div>
            )}
            {pageId === "overview" && (
              <Suspense
                fallback={
                  <LoadingState variant="overview" label="正在加载运行看板…" />
                }
              >
                <Overview
                  key={routeKey}
                  onNavigate={navigate}
                  onConnect={() => setConnectionOpen(true)}
                />
              </Suspense>
            )}
            {pageId === "nodes" && <Nodes key={routeKey} notify={notify} />}
            {pageId === "groups" && <Groups notify={notify} />}
            {pageId === "connections" && (
              <Connections key={routeKey} notify={notify} />
            )}
            {pageId === "bindings" && (
              <BindingsPage
                key={routeKey}
                onRotate={(item) => choose(item, "rotate")}
                onRelease={(item) => choose(item, "release")}
                onConnect={() => setConnectionOpen(true)}
              />
            )}
            {pageId === "accounts" && <AccountsPage notify={notify} />}
            {pageId === "tasks" && <Tasks key={routeKey} />}
            {pageId === "settings" && <SettingsPage notify={notify} />}
            <footer className="workspace-footer">
              <span>
                <Network size={13} /> RapIP Gateway
              </span>
              <span>独占会话 · 受控轮换 · 异步补货</span>
            </footer>
          </div>
        </main>
        <ConnectionDialog
          open={connectionOpen}
          onOpenChange={setConnectionOpen}
          settings={settings.data}
          notify={notify}
        />
        <Dialog
          open={!!target}
          onOpenChange={(open) => {
            if (!open && !busy) setTarget(null);
          }}
          title={
            action === "rotate"
              ? "轮换出口 IP"
              : target?.status === "RESERVED"
                ? "取消未使用预留"
                : "释放会话绑定"
          }
          description={`工位 ${target?.worker || "匿名"} · 当前 ${target?.connections ?? 0} 条连接`}
        >
          <form className="form-stack" onSubmit={control}>
            {target?.status !== "RESERVED" && (
              <div className="mode-options">
                <button
                  type="button"
                  className={mode === "graceful" ? "selected" : ""}
                  onClick={() => setMode("graceful")}
                >
                  <Workflow size={20} />
                  <strong>平滑{action === "rotate" ? "切换" : "释放"}</strong>
                  <span>已有连接继续排空</span>
                  {mode === "graceful" && (
                    <Check size={15} className="mode-check" />
                  )}
                </button>
                <button
                  type="button"
                  className={mode === "hard" ? "selected hard-selected" : ""}
                  onClick={() => setMode("hard")}
                >
                  <Unplug size={20} />
                  <strong>强制{action === "rotate" ? "切换" : "释放"}</strong>
                  <span>立即关闭已有连接</span>
                  {mode === "hard" && (
                    <Check size={15} className="mode-check" />
                  )}
                </button>
              </div>
            )}
            {action === "rotate" && (
              <>
                <label>
                  Session 策略
                  <select
                    value={sessionPolicy}
                    onChange={(event) => setSessionPolicy(event.target.value)}
                  >
                    <option value="keep">
                      保留当前 session，客户端无需改用户名
                    </option>
                    <option value="new">创建新的 session</option>
                  </select>
                </label>
                {sessionPolicy === "new" && (
                  <label>
                    新 Session ID
                    <input
                      value={newSession}
                      onChange={(event) => setNewSession(event.target.value)}
                      pattern="[A-Za-z0-9]{1,64}"
                      required
                    />
                  </label>
                )}
              </>
            )}
            <div className="connection-hint">
              <CircleHelp size={17} />
              <p>
                {action === "release"
                  ? target?.status === "RESERVED"
                    ? "取消后旧链接失效，原 IP 返回待分配池，不调用供应商更换 API。若已被使用会拒绝取消，请刷新。"
                    : "释放后当前 session 失效，业务需创建新 session。"
                  : "换号后新连接使用新出口。已有 TCP 不会迁移，强制切换后由客户端重连。"}
              </p>
            </div>
            {operationError && (
              <p className="form-error" role="alert">
                {operationError}
              </p>
            )}
            <Button
              type="submit"
              variant={mode === "hard" ? "destructive" : "default"}
              disabled={busy}
            >
              {busy && <Loader2 size={16} className="spin" />}
              {action === "rotate" ? "执行轮换" : "释放绑定"}
            </Button>
          </form>
        </Dialog>
        {toast && (
          <div className="toast" role="status">
            <Check size={17} />
            <span>{toast}</span>
            <button aria-label="关闭通知" onClick={() => setToast("")}>
              <X size={15} />
            </button>
          </div>
        )}
      </div>
    </GroupNamesProvider>
  );
}
