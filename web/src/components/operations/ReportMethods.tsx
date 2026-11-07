import { useState } from "react";
import { mutate, refreshAll, useQuery } from "@/lib/api";
import { ErrorMessage } from "./Common";

export function ReportMethods() {
  const query = useQuery<{ ipquality: boolean; ping0: boolean }>(
    "/quality/report-methods",
    true,
    10000,
  );
  const [pending, setPending] = useState<{
    key: "ipquality" | "ping0";
    value: boolean;
  } | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function save(method: "ipquality" | "ping0", value: boolean) {
    setBusy(true);
    setPending({ key: method, value });
    setError("");
    try {
      await mutate("/quality/report-methods", { [method]: value }, "PATCH");
      await query.refresh();
      refreshAll();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setPending(null);
    }
  }
  return (
    <div className="report-methods">
      <strong>单 IP 质量报告</strong>
      <p className="field-help">
        分别控制检测方法；关闭后保留历史报告，已开始的任务会完成。报告不改变分组准入，也不更换
        IP。
      </p>
      {(
        [
          ["ipquality", "xykt/IPQuality", "通过出口交叉查询质量数据库"],
          [
            "ping0",
            "Ping0 自查询",
            "使用被检测 IP 本身访问 Ping0 首页；低频请求，遇到验证或限流就停止。可访问性仅作站点参考",
          ],
        ] as const
      ).map(([key, name, note]) => (
        <label className="report-method-switch" key={key}>
          <span>
            <b>{name}</b>
            <small>{note}</small>
          </span>
          <input
            role="switch"
            type="checkbox"
            aria-label={`启用 ${name} 检测`}
            checked={
              pending?.key === key
                ? pending.value
                : (query.data?.[key] ?? false)
            }
            disabled={busy || !query.data}
            onChange={(e) => void save(key, e.target.checked)}
          />
        </label>
      ))}
      <ErrorMessage message={error || query.error} />
    </div>
  );
}
