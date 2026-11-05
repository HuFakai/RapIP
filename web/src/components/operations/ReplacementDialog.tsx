import { useState } from "react";
import { FlaskConical, RefreshCw } from "lucide-react";
import { useQuery, refreshAll, type NodeData } from "@/lib/api";
import { operation } from "@/lib/v2";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { AccountLabel, GroupLabel, ErrorMessage } from "./Common";
import { quotaPair } from "@/lib/quotas";
import type { AccountData } from "@/lib/api";

interface Preview {
  id: string;
  expires_at: number;
  items: { ip: string; account_id: string; supplier_preview_id: string }[];
}
export function ReplacementDialog({
  nodes,
  close,
  notify,
}: {
  nodes: NodeData[];
  close: () => void;
  notify: (v: string) => void;
}) {
  const [type, setType] = useState("original"),
    [country, setCountry] = useState(""),
    [asn, setAsn] = useState(""),
    [cidr, setCidr] = useState(""),
    [bound, setBound] = useState("reject"),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [preview, setPreview] = useState<Preview | null>(null);
  const accounts = useQuery<{ items: AccountData[] }>("/accounts");
  const firstAccount = nodes[0]?.account_id;
  const options = useQuery<{
    available_countries: Record<string, number>;
    available_asns: Record<string, number>;
    available_ip_ranges_24: Record<string, number>;
    error?: string;
  }>(`/accounts/${firstAccount}/replacement-options`, !!firstAccount, 60000);
  const invalidate = (fn: () => void) => {
    fn();
    setPreview(null);
    setError("");
  };
  async function preflight() {
    setBusy(true);
    setError("");
    try {
      const data = await operation("/replacements/preview", {
        node_ids: nodes.map((n) => n.id),
        bound_action: bound,
        selection: {
          type,
          country_code: country,
          asn_numbers: asn
            .split(/[\s,]+/)
            .filter(Boolean)
            .map((x) => x.toUpperCase().replace(/^AS/, "")),
          ip_ranges: cidr.split(/[\s,]+/).filter(Boolean),
        },
      });
      setPreview(data as unknown as Preview);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function commit() {
    if (!preview) return;
    setBusy(true);
    setError("");
    try {
      await operation("/replacements/commit", { preview_id: preview.id });
      refreshAll();
      notify("手动更换已受理；请在任务与事件中追踪，可取消尚未执行的等待任务");
      close();
    } catch (e) {
      setError((e as Error).message);
      setPreview(null);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      onOpenChange={(v) => {
        if (!v && !busy) close();
      }}
      title={`更换 IP · ${nodes.length} 个`}
      description="调用 Webshare 更换所选资源，会消耗更换额度；先预检，再确认提交。"
      wide
    >
      <div className="form-stack">
        <div className="replacement-selection">
          {nodes.map((n) => (
            <div key={n.id}>
              <b className="mono">{n.ip}</b>
              <AccountLabel id={n.account_id} />
              <GroupLabel id={n.group_id} />
              <span>
                {n.worker || n.binding_id ? "已绑定（包括空闲保留）" : "未绑定"}
              </span>
            </div>
          ))}
        </div>
        <div className="rotation-budget-strip">
          {accounts.data?.items
            .filter((a) => nodes.some((n) => n.account_id === a.account_id))
            .map((a) => (
              <span key={a.account_id}>
                {a.name} · 剩余额度{" "}
                <b>
                  {quotaPair(
                    a.supplier_usage?.replacement?.remaining,
                    a.supplier_usage?.replacement?.total,
                  )}
                </b>
              </span>
            ))}
        </div>
        <label>
          更换目标
          <select
            value={type}
            onChange={(e) => invalidate(() => setType(e.target.value))}
          >
            <option value="original">保持各 IP 原国家</option>
            <option value="country">指定国家</option>
            <option value="any">随机可用 IP</option>
            <option value="asn">指定 ASN</option>
            <option value="ip_range">指定 CIDR 网段</option>
          </select>
        </label>
        {type === "country" && (
          <label>
            国家代码
            <input
              list="replacement-countries"
              placeholder="例如 US"
              maxLength={2}
              value={country}
              onChange={(e) =>
                invalidate(() => setCountry(e.target.value.toUpperCase()))
              }
            />
            <datalist id="replacement-countries">
              {Object.entries(options.data?.available_countries ?? {})
                .filter(([, n]) => n > 0)
                .map(([v, n]) => (
                  <option key={v} value={v}>
                    {n} 个可用
                  </option>
                ))}
            </datalist>
          </label>
        )}
        {type === "asn" && (
          <label>
            ASN（逗号分隔）
            <input
              list="replacement-asns"
              placeholder="例如 2914 或 AS2914"
              value={asn}
              onChange={(e) => invalidate(() => setAsn(e.target.value))}
            />
            <datalist id="replacement-asns">
              {Object.keys(options.data?.available_asns ?? {}).map((v) => (
                <option key={v} value={v} />
              ))}
            </datalist>
          </label>
        )}
        {type === "ip_range" && (
          <label>
            IPv4 CIDR（逗号分隔）
            <input
              list="replacement-ranges"
              placeholder="例如 203.0.113.0/24；需供应商有库存"
              value={cidr}
              onChange={(e) => invalidate(() => setCidr(e.target.value))}
            />
            <datalist id="replacement-ranges">
              {Object.keys(options.data?.available_ip_ranges_24 ?? {})
                .slice(0, 1000)
                .map((v) => (
                  <option key={v} value={v} />
                ))}
            </datalist>
          </label>
        )}
        <p className="field-help">
          库存建议来自首个账号；多账号会分别预检。最终以各套餐预检结果为准，不保证指定风险分或家宽属性。新
          IP 必须重新通过所属质量组准入。
        </p>
        {options.data?.error && (
          <p className="ops-warning">{options.data.error}</p>
        )}
        <details className="quality-advanced">
          <summary>已绑定资源的处理方式</summary>
          <div className="quality-advanced-body">
            <label>
              操作影响
              <select
                value={bound}
                onChange={(e) => invalidate(() => setBound(e.target.value))}
              >
                <option value="reject">仅更换未绑定资源（默认）</option>
                <option value="wait">已绑定资源等待全部连接断开后更换</option>
                <option value="replace">
                  明确更换已绑定 IP · 保留 session 并关闭旧连接
                </option>
              </select>
            </label>
            <p className="ops-warning">
              已绑定 IP 即使没有连接也属于长期保留资源。显式更换后配置不变，原
              IP 会变；更换及检测期间可能暂时无法连接。等待任务可在任务页取消。
            </p>
          </div>
        </details>
        <ErrorMessage message={error} />
        {preview && (
          <div className="replacement-preview">
            <b>预检通过 · 共 {preview.items.length} 个，一换一</b>
            <p>
              预检不修改代理列表。账号、绑定或资源状态变化后必须重新预检；有效至{" "}
              {new Date(preview.expires_at * 1000).toLocaleTimeString()}。
            </p>
          </div>
        )}
        <div className="ops-inline-actions">
          <Button
            variant="secondary"
            disabled={busy}
            onClick={() => void preflight()}
          >
            <FlaskConical size={14} />
            {busy ? "处理中…" : "供应商预检"}
          </Button>
          <Button
            variant="destructive"
            disabled={
              busy || !preview || preview.expires_at * 1000 < Date.now()
            }
            onClick={() => void commit()}
          >
            <RefreshCw size={14} />
            确认更换 {nodes.length} 个 IP
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
