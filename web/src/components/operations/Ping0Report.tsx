import { useState } from "react";
import { ExternalLink, FlaskConical, Loader2 } from "lucide-react";
import { useQuery, type NodeData } from "@/lib/api";
import { operation } from "@/lib/v2";
import { date } from "@/lib/utils";
import { Button } from "../ui/button";
import { ErrorMessage } from "./Common";
interface Report {
  ip: string;
  state: string;
  site_access: string;
  checked_at: number;
  note: string;
  risk_percent?: number;
  fields: Record<string, string>;
  http_status?: number;
}
const names: Record<string, string> = {
  location: "IP 位置",
  asn: "ASN",
  asn_owner: "ASN 所有者",
  company: "企业",
  ip_type: "IP 类型",
  risk: "Ping0 风控值",
  native: "原生 IP",
  sharing: "共享人数",
};
const access: Record<string, string> = {
  DIRECT: "无需验证，取得报告",
  CHALLENGE: "触发站点验证",
  RATE_LIMITED: "站点限流",
  HTTP_ERROR: "站点返回错误",
  NETWORK_ERROR: "网络查询失败",
  UNVERIFIED: "未确认出口，未采纳",
  PARSE_CHANGED: "页面结构变化",
  UNKNOWN: "暂未确认",
};
export function Ping0Report({ node }: { node: NodeData | null }) {
  const query = useQuery<{
    enabled: boolean;
    running: boolean;
    busy: boolean;
    report: Report | null;
  }>(`/nodes/${node?.id ?? "none"}/ping0`, !!node, 3000);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function run() {
    if (!node) return;
    setBusy(true);
    setError("");
    try {
      await operation(`/nodes/${node.id}/ping0`, {}, "POST", 60);
      await query.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const report = query.data?.report;
  return (
    <div className="ping0-report">
      <div className="quality-report-heading">
        <strong className="mono">{node?.ip}</strong>
        <Button
          disabled={busy || query.data?.busy || !query.data?.enabled}
          onClick={() => void run()}
        >
          {busy || query.data?.running ? (
            <Loader2 className="spin" size={15} />
          ) : (
            <FlaskConical size={15} />
          )}
          查询这个 IP
        </Button>
      </div>
      <p className="field-help">
        通过这个 IP 自身访问 Ping0 首页，不占用工位、不换号。报告缓存 10
        分钟；全局低频单次查询。遇到验证页或限流会停止，不自动解验证或换出口重试。
      </p>
      {query.data && !query.data.enabled && (
        <p className="ops-warning">
          Ping0 自查询已关闭，可在设置 → 质量检测源开启。历史报告仍可查看。
        </p>
      )}
      <ErrorMessage message={error || query.error} />
      {report ? (
        <>
          <div
            className={`ping0-access ${report.site_access === "DIRECT" ? "direct" : "unknown"}`}
          >
            <strong>{access[report.site_access] ?? report.site_access}</strong>
            <time>{date(report.checked_at)}</time>
            <p>{report.note}</p>
          </div>
          {!!Object.keys(report.fields).length && (
            <dl className="ping0-fields">
              {Object.entries(names).map(([key, label]) => (
                <div key={key}>
                  <dt>{label}</dt>
                  <dd>{report.fields[key] || "未提供"}</dd>
                </div>
              ))}
            </dl>
          )}
        </>
      ) : (
        <p className="ops-empty">尚无 Ping0 报告。启用后可以查询这个出口。</p>
      )}
      {node && (
        <Button asChild variant="secondary">
          <a
            href={`https://ping0.cc/ip/${node.ip}`}
            target="_blank"
            rel="noreferrer"
          >
            在 Ping0 查看原始页面 <ExternalLink size={14} />
          </a>
        </Button>
      )}
      <p className="field-help">
        是否触发验证只表示这次出口与浏览器指纹在 Ping0 的访问结果，不等于通用 IP
        信誉，也不能据此直接断言家宽或自动更换。
      </p>
    </div>
  );
}
