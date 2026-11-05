import { useState } from "react";
import { ExternalLink, FlaskConical, Loader2 } from "lucide-react";
import { useQuery, type NodeData } from "@/lib/api";
import { operation } from "@/lib/v2";
import { date } from "@/lib/utils";
import { Button } from "../ui/button";
import { LoadingState } from "../ui/loading";
import { ErrorMessage, GroupLabel } from "./Common";

interface Ping0Response {
  enabled: boolean;
  busy: boolean;
  running: boolean;
  report: null | {
    ip: string;
    checked_at: number;
    source_url: string;
    scope: string;
    llm_note: string;
    fields: {
      location: string | null;
      asn: string | null;
      asn_owner: string | null;
      company: string | null;
      ip_type: string | null;
      risk: number | null;
      risk_label: string | null;
      native_ip: string | null;
      shared_users: string | null;
    };
    missing_fields: string[];
  };
}

export function Ping0Report({ node }: { node: NodeData }) {
  const query = useQuery<Ping0Response>(`/nodes/${node.id}/ping0`, true, 4000);
  const [running, setRunning] = useState(false),
    [error, setError] = useState("");
  const report = query.data?.report,
    fields = report?.fields;
  const busy = running || query.data?.busy;
  const risk = fields?.risk;
  async function run() {
    setRunning(true);
    setError("");
    try {
      await operation(`/nodes/${node.id}/ping0`, {}, "POST", 60);
      await query.refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }
  return (
    <div className="ipquality-content">
      <div className="quality-report-heading">
        <div>
          <strong className="mono">{node.ip}</strong>
          <GroupLabel id={node.group_id} />
        </div>
        <Button
          disabled={busy || !query.data?.enabled}
          onClick={() => void run()}
        >
          {busy ? (
            <Loader2 size={16} className="spin" />
          ) : (
            <FlaskConical size={16} />
          )}
          {busy ? "查询中…" : report ? "重新查询 Ping0" : "查询 Ping0"}
        </Button>
      </div>
      <p className="field-help">
        由服务器按 IP 查询 Ping0 公开页面，不建立业务连接、不预留工位、不更换
        IP。站点拒绝或字段缺失时保留未知，不会按不合格自动更换。
      </p>
      {query.isLoading && <LoadingState label="正在读取 Ping0 报告…" />}
      {query.data && !query.data.enabled && (
        <p className="ops-warning">
          Ping0 已关闭，可在网关设置 → 质量检测源启用；历史报告仍可查看。
        </p>
      )}
      <ErrorMessage message={error || query.error} />
      {report && fields ? (
        <>
          <div
            className={`assessment-hero assessment-${risk == null ? "unknown" : risk < 40 ? "good" : risk < 70 ? "review" : "caution"}`}
          >
            <div className="assessment-score">
              <span>Ping0 风控值 · 越高风险越大</span>
              <strong>
                {risk ?? "—"}
                <small>/ 100</small>
              </strong>
              <b>{fields.risk_label ?? "未取得风险结论"}</b>
            </div>
            <div className="assessment-identity">
              <span>Ping0 网络类型标签</span>
              <h3>{fields.ip_type ?? "未知"}</h3>
              <p>
                {fields.native_ip ?? "原生属性未知"} ·{" "}
                {fields.shared_users
                  ? `共享人数 ${fields.shared_users}`
                  : "共享人数未知"}
              </p>
            </div>
          </div>
          <div className="ops-detail">
            <dl>
              {(
                [
                  ["IP 位置", fields.location],
                  ["ASN", fields.asn],
                  ["ASN 所有者", fields.asn_owner],
                  ["企业", fields.company],
                  ["IP 类型", fields.ip_type],
                  ["原生 IP", fields.native_ip],
                  ["共享人数（过去一年估算）", fields.shared_users],
                  ["本次查询时间", date(report.checked_at)],
                ] as const
              ).map(([name, value]) => (
                <div className="ping0-field" key={name}>
                  <dt>{name}</dt>
                  <dd>{value || "未知 / 来源未提供"}</dd>
                </div>
              ))}
            </dl>
          </div>
          <p className="field-help">{report.scope}</p>
          <p className="field-help">{report.llm_note}</p>
        </>
      ) : (
        !query.isLoading && (
          <div className="quality-report-empty">
            <FlaskConical size={32} />
            <strong>{busy ? "正在读取公开查询页…" : "暂无 Ping0 报告"}</strong>
            <p>点击查询获取这个 IP 的风控、类型与归属信息。</p>
          </div>
        )
      )}
      <a
        className="ping0-source"
        href={`https://ping0.cc/ip/${encodeURIComponent(node.ip)}`}
        target="_blank"
        rel="noreferrer"
      >
        在 Ping0 查看原始页面及大模型检测 <ExternalLink size={14} />
      </a>
      <p className="field-help">
        报告独立按 IP
        保存，受“测活与质量报告”保留期限控制。住宅标签不等于已证明是真实家宽，也不能单凭该标签判断为伪家宽。
      </p>
    </div>
  );
}
