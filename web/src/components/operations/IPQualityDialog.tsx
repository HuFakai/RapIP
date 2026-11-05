import { Ping0Report } from "./Ping0Report";
import { useState } from "react";
import { FlaskConical, Loader2 } from "lucide-react";
import { useQuery, type NodeData } from "@/lib/api";
import { operation } from "@/lib/v2";
import { date } from "@/lib/utils";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { ErrorMessage, GroupLabel } from "./Common";
interface Assessment {
  summary: string;
  version: string;
  score: number | null;
  grade: string;
  profile: string;
  network_type: string;
  network_label: string;
  type_confidence: string;
  type_reason: string;
  breakdown: {
    reputation: number | null;
    residential_fit: number | null;
    privacy: number | null;
  };
  reasons: string[];
  missing_dimensions: string[];
  formula: string;
  note: string;
}
interface Report {
  assessment?: Assessment;
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
  enabled: boolean;
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
function XYReport({ node }: { node: NodeData | null }) {
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
    <div>
      <div className="ipquality-content">
        <div className="quality-report-heading">
          <div>
            <strong className="mono">{node?.ip}</strong>
            <GroupLabel id={node?.group_id} />
          </div>
          <Button
            disabled={busy || query.data?.busy || !query.data?.enabled}
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
          DNSBL 不在本次检测范围。综合分采用公开的 RapIP
          家宽场景规则，不代表业务成功率。
        </p>
        {!query.data?.enabled && query.data && (
          <p className="ops-warning">
            IPQuality 已关闭；可在网关设置 → 质量检测源启用。历史报告仍可查看。
          </p>
        )}
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
            {report.assessment && (
              <div className="quality-assessment">
                <div
                  className={`assessment-hero assessment-${report.assessment.score === null ? "unknown" : report.assessment.score >= 75 ? "good" : report.assessment.score >= 50 ? "review" : "caution"}`}
                >
                  <div className="assessment-score">
                    <span>{report.assessment.profile}</span>
                    <strong>
                      {report.assessment.score ?? "—"}
                      <small>/ 100</small>
                    </strong>
                    <b>{report.assessment.grade}</b>
                  </div>
                  <div className="assessment-identity">
                    <span>
                      IP 类型 · 证据一致度{report.assessment.type_confidence}
                    </span>
                    <h3>{report.assessment.network_label}</h3>
                    <p>{report.assessment.type_reason}</p>
                  </div>
                </div>
                <p className="assessment-verdict">
                  {report.assessment.summary}
                </p>
                <div className="assessment-dimensions">
                  {[
                    ["信誉参考", report.assessment.breakdown.reputation],
                    ["家宽适配", report.assessment.breakdown.residential_fit],
                    ["风险标签", report.assessment.breakdown.privacy],
                  ].map(([title, value]) => (
                    <div key={String(title)}>
                      <span>{title}</span>
                      <strong>
                        {value ?? "—"}
                        <small>/100</small>
                      </strong>
                    </div>
                  ))}
                </div>
                <div className="assessment-explanation">
                  <strong>为什么这样判断</strong>
                  {report.assessment.missing_dimensions.length > 0 && (
                    <p>
                      缺少{report.assessment.missing_dimensions.join("、")}
                      证据，暂不能给出综合分。
                    </p>
                  )}
                  <ul>
                    {report.assessment.reasons.map((reason) => (
                      <li key={reason}>{reason}</li>
                    ))}
                  </ul>
                </div>
                <details className="quality-advanced">
                  <summary>评分规则与适用范围</summary>
                  <div className="quality-advanced-body">
                    <p>{report.assessment.formula}</p>
                    <p className="field-help">
                      机房最高 49、来源冲突最高 64；信誉来源不足 3 个时最高
                      85，只有 1 个时最高 59。类型证据不足 2 个来源时最高
                      69。风险标签覆盖不足 3 类时，风险标签分最高
                      70。未知维度不按零分或满分处理。
                    </p>
                    <p className="field-help">
                      {report.assessment.note}{" "}
                      分高仅表示现有证据更符合家宽要求；低举报也不能证实家宽。版本：
                      {report.assessment.version}
                    </p>
                  </div>
                </details>
              </div>
            )}
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
            <details className="quality-advanced">
              <summary>查看检测源原始评分与标签</summary>
              <div className="quality-advanced-body">
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
                                <Flag
                                  value={report.Factor?.[key]?.[factorName]}
                                />
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
              </div>
            </details>
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
    </div>
  );
}

export function IPQualityDialog({
  node,
  onClose,
}: {
  node: NodeData | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"ipquality" | "ping0">("ipquality");
  return (
    <Dialog
      wide
      open={!!node}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title="IP 质量报告"
      description="两种独立检测方式交叉参考，保留各自的评分含义，不改变分组准入。"
    >
      <div className="report-tabs" role="tablist" aria-label="质量报告来源">
        {(["ipquality", "ping0"] as const).map((name) => (
          <button
            id={`report-tab-${name}`}
            key={name}
            role="tab"
            aria-selected={tab === name}
            aria-controls="quality-report-panel"
            tabIndex={tab === name ? 0 : -1}
            onKeyDown={(event) => {
              if (["ArrowLeft", "ArrowRight"].includes(event.key)) {
                event.preventDefault();
                const next = tab === "ping0" ? "ipquality" : "ping0";
                setTab(next);
                document.getElementById(`report-tab-${next}`)?.focus();
              }
            }}
            onClick={() => setTab(name)}
          >
            {name === "ipquality"
              ? "IPQuality · 综合参考"
              : "Ping0 · 风控与类型"}
          </button>
        ))}
      </div>
      <div
        id="quality-report-panel"
        role="tabpanel"
        aria-labelledby={`report-tab-${tab}`}
      >
        {tab === "ipquality" ? (
          <XYReport node={node} />
        ) : (
          node && <Ping0Report node={node} />
        )}
      </div>
    </Dialog>
  );
}
