import { useState } from "react";
import { mutate, refreshAll, useQuery } from "@/lib/api";
import { ErrorMessage } from "./Common";

export function ReportMethods() {
  const query = useQuery<{ ipquality: boolean }>(
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
          控制新的 IPQuality 检测。关闭后仍可查看历史报告；已开始的任务会完成。
        </p>
      </div>
      <label className="report-method-switch">
        <span>
          <b>xykt/IPQuality</b>
          <small>通过出口交叉查询质量数据库</small>
        </span>
        <input
          role="switch"
          type="checkbox"
          aria-label="启用 IPQuality 检测"
          checked={query.data?.ipquality ?? false}
          disabled={busy || !query.data}
          onChange={async (e) => {
            setBusy(true);
            setError("");
            try {
              await mutate(
                "/quality/report-methods",
                { ipquality: e.target.checked },
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
      <ErrorMessage message={error || query.error} />
    </div>
  );
}
