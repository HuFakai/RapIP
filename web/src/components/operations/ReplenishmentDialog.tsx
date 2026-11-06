import { useState, type FormEvent } from "react";
import { mutate, refreshAll } from "@/lib/api";
import type { Group, ReplenishmentRule } from "@/lib/v2";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { ErrorMessage } from "./Common";

export function ReplenishmentDialog({
  group,
  close,
  notify,
}: {
  group: Group;
  close: () => void;
  notify: (text: string) => void;
}) {
  const [mode, setMode] = useState<ReplenishmentRule["mode"]>(
    group.replenishment?.mode ?? "any",
  );
  const [country, setCountry] = useState(group.replenishment?.country ?? "");
  const [ranges, setRanges] = useState(
    group.replenishment?.ranges?.join("\n") ?? "",
  );
  const [fallback, setFallback] = useState(
    group.replenishment?.fallback ?? true,
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await mutate(
        `/groups/${group.id}/replenishment`,
        {
          mode,
          country: mode === "country" ? country.trim().toUpperCase() : "",
          ranges:
            mode === "ip_range"
              ? ranges
                  .trim()
                  .split(/[\s,，]+/)
                  .filter(Boolean)
              : [],
          fallback,
        },
        "PATCH",
      );
      refreshAll();
      notify("已保存分组补货规则，对新任务生效");
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
      title={`${group.name} · Webshare 补货规则`}
      description="控制退役后向 Webshare 申请什么 IP。保存规则不会发起替换。"
      onOpenChange={(open) => {
        if (!open && !busy) close();
      }}
    >
      <form className="form-stack" onSubmit={save}>
        <label>
          补货方式
          <select
            value={mode}
            onChange={(e) =>
              setMode(e.target.value as ReplenishmentRule["mode"])
            }
          >
            <option value="any">随机（默认）</option>
            <option value="country">指定国家</option>
            <option value="ip_range">指定 IPv4 号段</option>
          </select>
        </label>
        {mode === "country" && (
          <label>
            国家代码
            <input
              value={country}
              onChange={(e) => setCountry(e.target.value.toUpperCase())}
              maxLength={2}
              pattern="[A-Z]{2}"
              placeholder="例如 US"
              required
            />
          </label>
        )}
        {mode === "ip_range" && (
          <label>
            IPv4 CIDR（最多 20 项）
            <textarea
              value={ranges}
              onChange={(e) => setRanges(e.target.value)}
              rows={4}
              placeholder="例如 203.0.113.0/24，每行一项"
              required
            />
          </label>
        )}
        {mode !== "any" && (
          <label className="report-method-switch">
            <span>指定库存不足时随机补货</span>
            <input
              type="checkbox"
              checked={fallback}
              onChange={(e) => setFallback(e.target.checked)}
            />
          </label>
        )}
        <p className="field-help">
          只有 Webshare
          明确确认缺货，才尝试随机补货。随机结果仍须满足本组准入；替换沿用现有额度限制。手动更换仍使用你在该次操作中选择的参数。
        </p>
        <ErrorMessage message={error} />
        <Button type="submit" disabled={busy}>
          {busy ? "保存中…" : "保存规则"}
        </Button>
      </form>
    </Dialog>
  );
}
