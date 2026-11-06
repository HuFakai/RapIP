import { useState } from "react";
import { Copy, Loader2 } from "lucide-react";
import { mutate, refreshAll, useQuery } from "@/lib/api";
import { copyText } from "@/lib/browser";
import type { Group } from "@/lib/v2";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { ErrorMessage } from "./Common";

export interface ProxyLink {
  links: { socks5: string; socks5_named: string; http: string };
  remarks: string;
  reserved: boolean;
  worker_id: string;
  session_id: string;
}

export function ProxyLinksDialog({
  initialGroup,
  close,
  notify,
}: {
  initialGroup: string;
  close: () => void;
  notify: (value: string) => void;
}) {
  const groups = useQuery<{ items: Group[] }>("/groups", true, 5000);
  const [group, setGroup] = useState(initialGroup || "default");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [link, setLink] = useState<ProxyLink | null>(null);
  const selected = groups.data?.items.find((item) => item.id === group);
  async function copy(protocol: "socks5_named" | "socks5" | "http") {
    setBusy(true);
    setError("");
    try {
      const result = await mutate<ProxyLink>("/proxy-links", {
        group_id: group,
      });
      setLink(result);
      await copyText(result.links[protocol]);
      notify(
        `已复制普通待用链接 · 工位 ${result.worker_id}，首次连接才分配 IP`,
      );
      refreshAll();
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
        if (!open) close();
      }}
      title="复制代理链接"
      description="普通待用链接不预留 IP；首次连接从所选分组分配。"
    >
      <div className="proxy-links-content">
        <label className="field-label">
          资源分组
          <select
            value={group}
            onChange={(e) => {
              setGroup(e.target.value);
              setLink(null);
              setError("");
            }}
            disabled={busy}
          >
            {groups.data?.items
              .filter((item) => item.enabled)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
          </select>
        </label>
        <div className="quality-report-summary">
          <div>
            <span>工位范围</span>
            <strong>{selected?.workers?.range ?? "—"}</strong>
          </div>
          <div>
            <span>已绑定 / 已预留</span>
            <strong>{selected?.workers?.occupied ?? "—"}</strong>
          </div>
          <div>
            <span>待分配 IP</span>
            <strong>{selected?.fresh ?? "—"}</strong>
          </div>
        </div>
        <p className="field-help">
          待用链接未使用时重复复制同一份；使用后自动生成下一份。工位用尽时，请到
          IP 行内复制已绑定的链接。IP 不会因断连而释放。
        </p>
        <div className="ops-inline-actions">
          {(["socks5_named", "socks5", "http"] as const).map((protocol) => (
            <Button
              key={protocol}
              disabled={busy || !selected}
              onClick={() => void copy(protocol)}
            >
              {busy ? (
                <Loader2 size={15} className="spin" />
              ) : (
                <Copy size={15} />
              )}
              复制{" "}
              {protocol === "socks5_named"
                ? "SOCKS5（含别名）"
                : protocol === "socks5"
                  ? "纯 SOCKS5"
                  : "HTTP"}
            </Button>
          ))}
        </div>
        <ErrorMessage message={error || groups.error} />
        {link && (
          <div className="ops-detail">
            <p>
              {link.remarks}
              <br />
              最近生成：工位 <b>{link.worker_id}</b> · session{" "}
              <code>{link.session_id}</code>
            </p>
            <p className="field-help">
              完整链接包含代理密码，请妥善保存。若自动复制失败，可在下方选中复制。
            </p>
            <textarea
              aria-label="完整 HTTP 代理链接"
              readOnly
              value={link.links.http}
            />
          </div>
        )}
        <details className="quality-advanced">
          <summary>工位规则</summary>
          <div className="quality-advanced-body">
            <label className="report-method-switch">
              <input
                type="checkbox"
                checked={selected?.workers?.managed ?? false}
                disabled={!selected || busy}
                onChange={async (e) => {
                  setBusy(true);
                  setError("");
                  try {
                    await mutate(
                      `/groups/${group}/workers`,
                      { managed: e.target.checked },
                      "PATCH",
                    );
                    await groups.refresh();
                    notify("工位规则已保存，现有绑定保留");
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              />
              仅允许本组 01～N 的受管工位
            </label>
            <p className="field-help">
              N
              随资源数量扩展，缩容保留已存在的编号。旧分组默认兼容自定义工位；新分组默认启用规则。启用后已有自定义工位和匿名绑定继续可用，新请求必须使用有效工位。工位并不永久对应某个
              IP。
            </p>
          </div>
        </details>
      </div>
    </Dialog>
  );
}
