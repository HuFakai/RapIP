import { useState } from "react";
import { FlaskConical, Loader2 } from "lucide-react";
import { useQuery, type NodeData } from "@/lib/api";
import { operation } from "@/lib/v2";
import { date } from "@/lib/utils";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { ErrorMessage, GroupLabel } from "./Common";
interface Report {
  ip: string;
  state: string;
  checked_at: number;
  revision: string;
  scope: string;
  scores_available: string[];
  missing_scores: string[];
  Info: {
    ASN?: string;
    Organization?: string;
    Region?: { Code?: string; Name?: string };
  };
  Type: { Usage?: Record<string, string | null> };
  Score: Record<string, string | number | null>;
  Factor: Record<string, Record<string, boolean | string | null>>;
}
interface Response {
  report: Report | null;
  running: boolean;
  busy: boolean;
  node_ip: string;
}
const providers = [
  "IPinfo",
  "SCAMALYTICS",
  "ipapi",
  "AbuseIPDB",
  "IP2LOCATION",
  "IPQS",
  "ipdata",
];
const flags = [
  ["Proxy", "代理"],
  ["VPN", "VPN"],
  ["Tor", "Tor"],
  ["Server", "机房"],
  ["Abuser", "滥用"],
  ["Robot", "机器人"],
] as const;
function Flag({ value }: { value?: boolean | string | null }) {
  return (
    <span
      className={`quality-flag ${value === true ? "is-flagged" : value === false ? "is-clear" : ""}`}
    >
      {value === true ? "是" : value === false ? "否" : "—"}
    </span>
  );
}
export function IPQualityDialog({
  node,
  onClose,
}: {
  node: NodeData | null;
  onClose: () => void;
}) {
  const query = useQuery<Response>(
    `/nodes/${node?.id ?? "none"}/ip-quality`,
    !!node,
    3000,
  );
  const [running, setRunning] = useState(false),
    [error, setError] = useState("");
  async function run() {
    if (!node) return;
    setError("");
    setRunning(true);
    try {
      await operation(`/nodes/${node.id}/ip-quality`, {}, "POST", 240);
      await query.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }
  const report = query.data?.report,
    busy = running || query.data?.running;
  return (
    <Dialog
      wide
      open={!!node}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="IP 质量报告"
      description="使用固定版本 xykt/IPQuality 的数据库检测模块，生成当前出口的参考报告。"
    >
      <div className="ipquality-content">
        <div className="quality-report-heading">
          <div>
            <strong className="mono">{node?.ip}</strong>
            <GroupLabel id={node?.group_id} />
          </div>
          <Button
            disabled={busy || query.data?.busy}
            onClick={() => void run()}
          >
            {busy ? (
              <Loader2 size={16} className="spin" />
            ) : (
              <FlaskConical size={16} />
            )}
            {busy
              ? "检测中，最长约 3 分钟"
              : report
                ? "重新检测"
                : "开始质量检测"}
          </Button>
        </div>
        <p className="field-help">
          只检测这个出口，不创建工位、不轮换
          IP，也不更改分组准入。会访问第三方查询服务并消耗少量代理流量；流媒体、邮件和
          DNSBL 不在本次检测范围。不同来源的分值含义不同，不合并成万能评分。
        </p>
        <ErrorMessage message={error || query.error} />
        {query.data?.busy && !query.data.running && (
          <p className="ops-warning">
            另一个 IP 正在检测；全局一次执行一个任务，避免密集访问检测源。
          </p>
        )}
        {!report && (
          <div className="quality-report-empty">
            <FlaskConical size={32} />
            <strong>
              {busy
                ? "正在核验出口并查询质量数据库…"
                : "这个出口还没有检测报告"}
            </strong>
            <p>第三方服务拒绝、超时或缺失字段将显示未知，不会判断为合格。</p>
          </div>
        )}
        {report && (
          <>
            <div className="quality-report-summary">
              <div>
                <span>评分来源</span>
                <strong>
                  {report.scores_available.length} /{" "}
                  {report.scores_available.length +
                    report.missing_scores.length}
                </strong>
              </div>
              <div>
                <span>ASN</span>
                <strong>{report.Info?.ASN || "未知"}</strong>
              </div>
              <div>
                <span>网络组织</span>
                <strong>{report.Info?.Organization || "未知"}</strong>
              </div>
              <div>
                <span>报告时间</span>
                <strong>{date(report.checked_at)}</strong>
              </div>
            </div>
            {report.state === "UNKNOWN" && (
              <p className="ops-warning">
                查询未取得可用的质量证据，请稍后重试。此结果不代表 IP 质量差。
              </p>
            )}
            <div className="ops-table-wrap">
              <table className="ops-table">
                <thead>
                  <tr>
                    <th>检测来源</th>
                    <th>原始评分</th>
                    <th>网络类型</th>
                    {flags.map(([, name]) => (
                      <th key={name}>{name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {providers.map((source) => {
                    const factorName =
                      source === "SCAMALYTICS"
                        ? "SCAMALYTICS"
                        : source === "IP2LOCATION"
                          ? "IP2LOCATION"
                          : source;
                    return (
                      <tr key={source}>
                        <td>{source}</td>
                        <td className="mono">
                          {report.Score?.[source] ?? "—"}
                        </td>
                        <td>{report.Type?.Usage?.[source] ?? "—"}</td>
                        {flags.map(([key]) => (
                          <td key={key}>
                            <Flag value={report.Factor?.[key]?.[factorName]} />
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="field-help">
              “—”表示缺少数据或该来源不提供此指标；“否”仅代表该来源当前未标记此属性，不能证明目标业务一定可用。当前分组的质量等级仍按网络样本及已配置的官方检测源判定。
            </p>
            <details className="quality-advanced">
              <summary>原始参考数据与模块来源</summary>
              <div className="quality-advanced-body">
                <p className="field-help">{report.scope}</p>
                <a
                  href="https://github.com/HuFakai/RapIP/tree/main/src/rapip/vendor/ipquality"
                  target="_blank"
                  rel="noreferrer"
                >
                  检测模块源码与 AGPL-3.0 许可 ↗
                </a>
                <pre>{JSON.stringify(report, null, 2)}</pre>
              </div>
            </details>
          </>
        )}
        <p className="field-help">
          报告按“测活与质量报告”保留策略清理。可关闭弹窗，任务会继续执行，稍后再次打开查看。
        </p>
      </div>
    </Dialog>
  );
}
