import { useState, type FormEvent } from "react";
import { Clock3, Save } from "lucide-react";
import { operation, type Group } from "@/lib/v2";
import { refreshAll } from "@/lib/api";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { ErrorMessage, Time } from "./Common";

export interface RotationPolicy {
  enabled: boolean;
  interval_seconds: number;
  idle_pause_seconds: number;
  daily_limit: number;
  total_limit: number;
  mode: "graceful" | "hard";
  fault_failover: boolean;
}
export interface RotationInfo extends RotationPolicy {
  today_used: number;
  total_used: number;
  bindings: {
    binding_id: string;
    session: string;
    worker?: string;
    status: string;
    next_at?: number;
    error?: string;
  }[];
}
export const rotationStates: Record<string, string> = {
  OFF: "固定 IP",
  WAITING_CONNECTION: "等待首次连接",
  RUNNING: "定时运行",
  IDLE_PAUSED: "无连接暂停",
  BUDGET_PAUSED: "额度暂停",
  WAITING_STOCK: "等待备用 IP",
  REFILLING: "等待补货/对账",
};
export function RotationDialog({
  group,
  close,
  notify,
}: {
  group: Group;
  close: () => void;
  notify: (v: string) => void;
}) {
  const r = group.rotation;
  const [policy, setPolicy] = useState<RotationPolicy>({
    enabled: r?.enabled ?? false,
    interval_seconds: r?.interval_seconds ?? 1800,
    idle_pause_seconds: r?.idle_pause_seconds ?? 600,
    daily_limit: r?.daily_limit ?? 0,
    total_limit: r?.total_limit ?? 0,
    mode: r?.mode ?? "graceful",
    fault_failover: r?.fault_failover ?? false,
  });
  const [reset, setReset] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const update = <K extends keyof RotationPolicy>(k: K, v: RotationPolicy[K]) =>
    setPolicy((p) => ({ ...p, [k]: v }));
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await operation(
        `/groups/${group.id}/rotation${reset ? "?reset=true" : ""}`,
        policy,
        "PUT",
      );
      refreshAll();
      notify("出口策略已保存；保存不会立即换 IP");
      close();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !busy) close();
      }}
      title={`${group.name} · 出口轮换`}
      description="固定配置长期有效；定时轮换仅在明确启用且预算允许时执行。"
      wide
    >
      <form onSubmit={save} className="form-stack rotation-form">
        <div className="rotation-mode-options">
          <button
            type="button"
            aria-pressed={!policy.enabled}
            onClick={() => update("enabled", false)}
          >
            <strong>固定 IP · 默认</strong>
            <span>断连不释放；更换 session 或主动操作才换 IP</span>
          </button>
          <button
            type="button"
            aria-pressed={policy.enabled}
            onClick={() => update("enabled", true)}
          >
            <strong>定时轮换</strong>
            <span>连接配置不变，按间隔与额度更新出口</span>
          </button>
        </div>
        <div className="rotation-budget-strip">
          <span>
            今日自动轮换{" "}
            <b>
              {r?.today_used ?? 0} / {policy.daily_limit}
            </b>
          </span>
          <span>
            本次授权已用{" "}
            <b>
              {r?.total_used ?? 0} / {policy.total_limit}
            </b>
          </span>
          <span>时区 Asia/Shanghai</span>
        </div>
        {policy.enabled && (
          <>
            <div className="form-two">
              <label>
                轮换间隔（分钟）
                <input
                  required
                  type="number"
                  min={1}
                  max={10080}
                  value={policy.interval_seconds / 60}
                  onChange={(e) =>
                    update(
                      "interval_seconds",
                      Math.round(Number(e.target.value) * 60),
                    )
                  }
                />
              </label>
              <label>
                无连接多久暂停（分钟）
                <input
                  required
                  type="number"
                  min={0}
                  max={10080}
                  value={policy.idle_pause_seconds / 60}
                  onChange={(e) =>
                    update(
                      "idle_pause_seconds",
                      Math.round(Number(e.target.value) * 60),
                    )
                  }
                />
              </label>
            </div>
            <p className="field-help">
              暂停只停止定时轮换，保留 IP 和 session；再次成功连接先用原
              IP，再计一个完整周期。0
              表示没有连接就暂停，存活的安静长连接仍算使用中。
            </p>
            <div className="form-two">
              <label>
                每日自动轮换上限（次）
                <input
                  required
                  type="number"
                  min={0}
                  max={100000}
                  value={policy.daily_limit}
                  onChange={(e) =>
                    update("daily_limit", Number(e.target.value))
                  }
                />
              </label>
              <label>
                本次启用总上限（次）
                <input
                  required
                  type="number"
                  min={0}
                  max={100000}
                  value={policy.total_limit}
                  onChange={(e) =>
                    update("total_limit", Number(e.target.value))
                  }
                />
              </label>
            </div>
            <p className="ops-warning">
              0
              次表示暂停，绝不表示无限。还受账号、分组替换预算和供应商余额限制；无库存时保留当前
              IP。
            </p>
            <label>
              连接切换方式
              <select
                value={policy.mode}
                onChange={(e) =>
                  update("mode", e.target.value as "graceful" | "hard")
                }
              >
                <option value="graceful">平滑切换 · 旧连接限时排空</option>
                <option value="hard">强制切换 · 立即关闭旧连接</option>
              </select>
            </label>
            <p className="field-help">
              TCP
              无法中途迁移。平滑期间新旧出口可能并存；强制切换需要客户端重连。
            </p>
            <label className="ops-checkbox">
              <input
                type="checkbox"
                checked={reset}
                onChange={(e) => setReset(e.target.checked)}
              />
              重新授权本次总额度（普通保存不会清零计数，每日计数仍保留）
            </label>
          </>
        )}
        <details className="quality-advanced">
          <summary>故障处理 · 默认保留原 IP</summary>
          <div className="quality-advanced-body">
            <label className="ops-checkbox">
              <input
                type="checkbox"
                checked={policy.fault_failover}
                onChange={(e) => update("fault_failover", e.target.checked)}
              />
              允许节点故障时同 session 自动更换 IP
            </label>
            <p className="field-help">
              开启后不再承诺配置不变就始终是同一
              IP。关闭时故障会保留原绑定并显示原因，等待恢复或人工处理。
            </p>
          </div>
        </details>
        {!!r?.bindings?.length && (
          <details className="quality-advanced">
            <summary>本组绑定计划（{r.bindings.length}）</summary>
            <div className="rotation-binding-list">
              {r.bindings.map((b) => (
                <div key={b.binding_id}>
                  <b>工位 {b.worker ?? "匿名"}</b>
                  <span className="mono">{b.session}</span>
                  <span>{rotationStates[b.status] ?? b.status}</span>
                  <Time value={b.next_at} />
                  {b.error && <small className="ops-warning">{b.error}</small>}
                </div>
              ))}
            </div>
          </details>
        )}
        <ErrorMessage message={error} />
        <Button type="submit" disabled={busy}>
          <Save size={14} />
          {busy ? "保存中…" : "保存出口策略"}
        </Button>
        <p className="field-help">
          <Clock3 size={12} /> session 永不过期；仅被明确替代或主动释放的旧
          session 不可继续使用。
        </p>
      </form>
    </Dialog>
  );
}
