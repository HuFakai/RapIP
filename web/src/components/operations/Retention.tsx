import { LoadingState } from "../ui/loading";
import { useState, type FormEvent } from "react";
import { Clock3, Database, Loader2, Trash2 } from "lucide-react";
import { mutate, refreshAll, useQuery } from "@/lib/api";
import { bytes, date } from "@/lib/utils";
import { operation } from "@/lib/v2";
import { Button } from "../ui/button";
import { ErrorMessage } from "./Common";

type Policy = Record<string, number>;
interface Storage {
  policy: Policy;
  database_bytes: number;
  reusable_bytes: number;
  observation_database_bytes: number;
  state_database_bytes: number;
  trace_records: number;
  record_kinds?: Record<string, number>;
  state_reusable_bytes: number;
  records: number;
  samples: number;
  operations: number;
  events: number;
  last_cleanup: number | null;
  protected: string;
  error?: string;
}
const fields = [
  ["success_log_hours", "成功连接日志", "小时", 1, 2160],
  ["error_log_days", "异常连接日志", "天", 1, 365],
  ["probe_log_days", "测活与质量报告", "天", 1, 365],
  ["metrics_days", "趋势统计", "天", 7, 365],
  ["operation_days", "已结束管理操作", "天", 1, 365],
  ["event_days", "运行事件", "天", 1, 365],
  ["trace_days", "代理追踪", "天", 1, 365],
  ["max_trace_records", "代理追踪条数上限", "条", 1000, 200000],
  ["max_records", "明细总条数上限", "条", 1000, 1000000],
  ["max_database_mb", "观测库保护阈值", "MiB", 32, 4096],
] as const;
export function Retention({ notify }: { notify: (text: string) => void }) {
  const query = useQuery<Storage>("/settings/storage", true, 15000);
  const [busy, setBusy] = useState("");
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const policy = Object.fromEntries(
      [...new FormData(e.currentTarget)].map(([key, value]) => [
        key,
        Number(value),
      ]),
    );
    setBusy("save");
    try {
      await mutate("/settings", { retention: policy }, "PATCH");
      notify("保留策略已保存，后台每小时清理一次");
      refreshAll();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  async function cleanup() {
    setBusy("cleanup");
    try {
      await operation("/settings/storage/cleanup");
      notify("已按当前保存的策略清理历史数据");
      refreshAll();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy("");
    }
  }
  const d = query.data;
  return (
    <section className="surface settings-surface retention-settings">
      <div className="section-heading">
        <div>
          <span className="eyebrow">DATA LIFECYCLE</span>
          <h2>
            <Clock3 size={20} /> 数据生命周期
          </h2>
        </div>
        <span className="small muted">定时清理 · 保护运行状态</span>
      </div>
      <ErrorMessage message={query.error || d?.error} />
      {query.isLoading && (
        <LoadingState
          label="正在加载数据保留配置…"
          variant="settings"
          rows={4}
        />
      )}
      {d && (
        <>
          <div className="retention-stats">
            <div>
              <Database size={17} />
              <strong>{bytes(d.database_bytes)}</strong>
              <span>两份数据库及 WAL 总量</span>
            </div>
            <div>
              <strong>{d.records.toLocaleString()}</strong>
              <span>连接 / 测活明细</span>
            </div>
            <div>
              <strong>{d.samples.toLocaleString()}</strong>
              <span>统计采样</span>
            </div>
            <div>
              <strong>{d.trace_records.toLocaleString()}</strong>
              <span>代理追踪事件 · 独立记录</span>
            </div>
          </div>
          <div className="retention-breakdown">
            <span>
              观测库 <b>{bytes(d.observation_database_bytes)}</b>
            </span>
            <span>
              调度与追踪库 <b>{bytes(d.state_database_bytes)}</b>
            </span>
            <span>
              管理操作 <b>{d.operations.toLocaleString()}</b>
            </span>
            <span>
              运行事件 <b>{d.events.toLocaleString()}</b>
            </span>
          </div>
          <form onSubmit={save} key={JSON.stringify(d.policy)}>
            <div className="retention-fields">
              <label>
                <span>趋势存储精度</span>
                <select
                  name="metrics_interval_seconds"
                  aria-label="趋势存储精度"
                  defaultValue={d.policy.metrics_interval_seconds}
                >
                  <option value="60">每分钟 · 推荐，节省存储</option>
                  <option value="30">每 30 秒</option>
                  <option value="10">每 10 秒 · 更多细节</option>
                </select>
              </label>
              {fields.map(([key, label, unit, min, max]) => (
                <label key={key}>
                  <span>{label}</span>
                  <div>
                    <input
                      name={key}
                      aria-label={`${label}保留设置`}
                      type="number"
                      required
                      min={min}
                      max={max}
                      step={1}
                      defaultValue={d.policy[key]}
                    />
                    <span>{unit}</span>
                  </div>
                </label>
              ))}
            </div>
            <p className="field-help">
              超过保留时间或条数上限的历史记录会被删除。趋势默认按分钟存储，实时指标仍每
              5 秒更新；旧的细粒度采样在 24 小时后合并到 1 分钟、7 天后合并到 15
              分钟。未认证的重复请求按协议、结果每分钟合并，失败次数仍完整统计。缩短期限将影响已有历史。
            </p>
            <div className="quality-wizard-footer">
              <span>
                上次清理：{d.last_cleanup ? date(d.last_cleanup) : "尚未执行"}
              </span>
              <Button disabled={!!busy} type="submit">
                {busy === "save" && <Loader2 size={14} className="spin" />}
                保存保留策略
              </Button>
            </div>
          </form>
          <details className="quality-advanced">
            <summary>清理与存储说明</summary>
            <div className="quality-advanced-body">
              <p className="field-help">
                {d.protected}
                。进行中的操作和仍受幂等键保护的结果不会清理。当前有{" "}
                {bytes(d.reusable_bytes + d.state_reusable_bytes)}{" "}
                页空间可复用；删除记录后文件不一定立即缩小。达到观测库有效数据保护阈值时暂停明细采集，清理后自动恢复原有采集设置。
              </p>
              <Button
                variant="secondary"
                disabled={!!busy}
                onClick={() => void cleanup()}
              >
                <Trash2 size={14} />
                {busy === "cleanup" ? "正在清理…" : "按已保存策略立即清理"}
              </Button>
            </div>
          </details>
        </>
      )}
    </section>
  );
}
