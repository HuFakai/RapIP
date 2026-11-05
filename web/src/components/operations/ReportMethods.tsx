import { useState } from "react";
import { mutate, refreshAll, useQuery } from "@/lib/api";
import { ErrorMessage } from "./Common";

export function ReportMethods() {
  const query = useQuery<{ ipquality: boolean; ping0: boolean }>(
    "/quality/report-methods",
    true,
    10000,
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <div className="report-methods">
      <div>
        <strong>单 IP 质量报告</strong>
        <p className="field-help">
          分别控制新检测，关闭后仍可查看历史报告；已开始的只读任务会完成。独立报告不改变质量分组准入。
        </p>
      </div>
      {(["ipquality", "ping0"] as const).map((method) => (
        <label className="report-method-switch" key={method}>
          <span>
            <b>{method === "ipquality" ? "xykt/IPQuality" : "Ping0"}</b>
            <small>
              {method === "ipquality"
                ? "通过出口交叉查询质量数据库"
                : "按 IP 查询公开网页，最少间隔 10 秒"}
            </small>
          </span>
          <input
            role="switch"
            type="checkbox"
            aria-label={`启用 ${method === "ipquality" ? "IPQuality" : "Ping0"} 检测`}
            checked={query.data?.[method] ?? false}
            disabled={busy || !query.data}
            onChange={async (e) => {
              setBusy(true);
              setError("");
              try {
                await mutate(
                  "/quality/report-methods",
                  { [method]: e.target.checked },
                  "PATCH",
                );
                await query.refresh();
                refreshAll();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          />
        </label>
      ))}
      <ErrorMessage message={error || query.error} />
    </div>
  );
}
