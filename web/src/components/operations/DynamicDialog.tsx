import { useState, type FormEvent } from "react";
import { ArrowRightLeft, Info, Layers3, Replace } from "lucide-react";
import { refreshAll } from "@/lib/api";
import { operation, type Group, type DynamicPolicy } from "@/lib/v2";
import { Button } from "../ui/button";
import { Dialog } from "../ui/dialog";
import { ErrorMessage } from "./Common";

const modes = [
  {
    id: "pool",
    title: "仅从池内切换",
    detail: "新 session 先拿合格备用 IP，旧 IP 再补货。没库存则保留原绑定。",
    icon: Layers3,
  },
  {
    id: "pool_then_in_place",
    title: "池内优先 · 缺库存时原位替换",
    detail: "有合格库存时走原流程；缺库存时直接替换当前 IP。",
    icon: ArrowRightLeft,
  },
  {
    id: "in_place",
    title: "始终原位替换",
    detail:
      "替换当前 IP，不使用池内备用 IP。合格后才把新出口交给最新 session。",
    icon: Replace,
  },
] as const;

export function DynamicDialog({
  group,
  close,
  notify,
}: {
  group: Group;
  close: () => void;
  notify: (value: string) => void;
}) {
  const [policy, setPolicy] = useState<DynamicPolicy>(
    group.dynamic_switch ?? {
      mode: "pool",
      wait_seconds: 25,
      cooldown_seconds: 60,
    },
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await operation(`/groups/${group.id}/dynamic-switch`, policy, "PUT");
      refreshAll();
      notify("IP 动态切换规则已保存");
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
      onOpenChange={(open) => {
        if (!open && !busy) close();
      }}
      title={`${group.name} · IP 动态切换`}
      description="仅影响已使用工位换 session；首次分配、同 session 重连和定时轮换各按原规则执行。"
    >
      <form className="form-stack policy-form dynamic-form" onSubmit={save}>
        <div className="policy-scroll">
          <fieldset className="dynamic-modes">
            <legend className="sr-only">切换策略</legend>
            {modes.map(({ id, title, detail, icon: Icon }) => (
              <label key={id} className="dynamic-choice">
                <input
                  type="radio"
                  name="dynamic-mode"
                  checked={policy.mode === id}
                  onChange={() => setPolicy({ ...policy, mode: id })}
                />
                <span className="policy-mode-icon">
                  <Icon size={17} />
                </span>
                <span>
                  <strong>{title}</strong>
                  <small>{detail}</small>
                </span>
              </label>
            ))}
          </fieldset>
          {policy.mode !== "pool" && (
            <>
              <div className="dynamic-fields">
                <label>
                  单次连接最多等待（秒）
                  <input
                    type="number"
                    min={5}
                    max={90}
                    required
                    value={policy.wait_seconds}
                    onChange={(e) =>
                      setPolicy({
                        ...policy,
                        wait_seconds: e.target.valueAsNumber || 0,
                      })
                    }
                  />
                </label>
                <label>
                  成功后原位冷却（秒）
                  <input
                    type="number"
                    min={10}
                    max={86400}
                    required
                    value={policy.cooldown_seconds}
                    onChange={(e) =>
                      setPolicy({
                        ...policy,
                        cooldown_seconds: e.target.valueAsNumber || 0,
                      })
                    }
                  />
                </label>
              </div>
              <p className="policy-hint ops-warning">
                <Info size={15} />{" "}
                <span>
                  原位替换会消耗 Webshare 额度，提交时旧 TCP
                  会断开。连接超时后使用同一个 session
                  重试；任务在后台继续。指定国家的连接不会随机换到其他国家。
                </span>
              </p>
            </>
          )}
          <ErrorMessage message={error} />
        </div>
        <div className="policy-actions">
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={close}
          >
            取消
          </Button>
          <Button disabled={busy} type="submit">
            {busy ? "正在保存…" : "保存切换规则"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
