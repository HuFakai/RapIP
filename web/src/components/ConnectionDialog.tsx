import { useEffect, useState } from "react";
import {
  Check,
  Clipboard,
  Code2,
  Loader2,
  RefreshCw,
  Terminal,
} from "lucide-react";
import { api, useQuery, type SettingsData } from "@/lib/api";
import { copyText, randomId } from "@/lib/browser";
import type { Group } from "@/lib/v2";
import { Button } from "./ui/button";
import { Dialog } from "./ui/dialog";

interface ConnectionData {
  username: string;
  host: string;
  socks_port: number;
  http_port: number;
  links: {
    socks5: string;
    socks5_named: string;
    socks5h: string;
    http: string;
  };
  ai_guide: string;
  structured: {
    http: { server: string; username: string; password: string };
    socks5: { server: string; username: string; password: string };
  };
}

function CopyField({
  title,
  value,
  onCopy,
}: {
  title: string;
  value: string;
  onCopy: () => void;
}) {
  return (
    <div className="connection-link">
      <div className="connection-link-label">
        <span>{title}</span>
        <Button
          variant="ghost"
          size="sm"
          type="button"
          onClick={onCopy}
          aria-label={`复制${title}`}
        >
          <Clipboard size={14} />
          复制
        </Button>
      </div>
      <code tabIndex={0}>{value}</code>
    </div>
  );
}

export function ConnectionDialog({
  open,
  onOpenChange,
  settings,
  notify,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  settings?: SettingsData;
  notify: (text: string) => void;
}) {
  const [group, setGroup] = useState("default");
  const [mode, setMode] = useState<"direct" | "tunnel">("direct");
  const groups = useQuery<{ items: Group[] }>("/groups", open, 15000);
  const [worker, setWorker] = useState("01");
  const [session, setSession] = useState(() =>
    randomId().replaceAll("-", "").slice(0, 10),
  );
  const [country, setCountry] = useState("");
  const [hostOverride, setHostOverride] = useState<string | null>(null);
  const host =
    hostOverride ??
    (mode === "tunnel"
      ? "127.0.0.1"
      : settings?.deployment.public_proxy_host || window.location.hostname);
  const effectiveHost = host || window.location.hostname;
  const [tab, setTab] = useState<"links" | "guide">("links");
  const [data, setData] = useState<ConnectionData | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState("");
  useEffect(() => {
    if (!open) {
      setData(null);
      setError("");
      return;
    }
    setData(null);
    setError("");
    if (
      !/^[A-Za-z0-9]{1,64}$/.test(worker) ||
      !/^[A-Za-z0-9]{1,64}$/.test(session)
    ) {
      setError("工位和 session 需填写 1–64 位字母或数字");
      return;
    }
    let active = true;
    const abort = new AbortController();
    const timer = window.setTimeout(() => {
      void api<ConnectionData>("/connection-config", {
        method: "POST",
        signal: abort.signal,
        body: JSON.stringify({
          host: effectiveHost,
          worker_id: worker,
          session_id: session,
          country_code: country || null,
          group_id: group,
          tunnel: mode === "tunnel",
        }),
      })
        .then((value) => {
          if (active) setData(value);
        })
        .catch((err) => {
          if (active && !abort.signal.aborted) setError((err as Error).message);
        });
    }, 200);
    return () => {
      active = false;
      abort.abort();
      window.clearTimeout(timer);
    };
  }, [open, effectiveHost, worker, session, country, group, mode]);
  useEffect(() => {
    if (copied) {
      const timer = window.setTimeout(() => setCopied(""), 2000);
      return () => window.clearTimeout(timer);
    }
  }, [copied]);
  async function copy(value: string, name: string) {
    try {
      await copyText(value);
      setCopied(name);
      notify(`已复制${name}`);
    } catch {
      notify("请选中内容后手动复制");
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="客户端连接配置"
      description="生成可直接使用的代理链接，不会占用 IP 或创建业务连接。"
    >
      <div className="connection-tabs" role="tablist" aria-label="连接配置内容">
        <button
          role="tab"
          aria-selected={tab === "links"}
          aria-controls="connection-links"
          id="connection-links-tab"
          onClick={() => setTab("links")}
        >
          <Terminal size={15} />
          连接链接
        </button>
        <button
          role="tab"
          aria-selected={tab === "guide"}
          aria-controls="connection-guide"
          id="connection-guide-tab"
          onClick={() => setTab("guide")}
        >
          <Code2 size={15} />
          AI 接入说明
        </button>
      </div>
      {tab === "links" ? (
        <div
          className="form-stack"
          role="tabpanel"
          id="connection-links"
          aria-labelledby="connection-links-tab"
        >
          <div className="form-two">
            <label>
              连接方式
              <select
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value as "direct" | "tunnel");
                  setHostOverride(null);
                }}
              >
                <option value="direct">公网直连</option>
                <option value="tunnel">SSH 隧道</option>
              </select>
            </label>
            <label>
              项目资源组
              <select
                aria-label="项目资源组"
                value={group}
                onChange={(e) => setGroup(e.target.value)}
              >
                {groups.data?.items.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.name} · 可分配 {g.fresh}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label>
            服务器地址
            <input
              aria-label="服务器地址"
              value={host}
              placeholder={window.location.hostname}
              onChange={(event) => setHostOverride(event.target.value)}
              autoComplete="off"
            />
            <small className="field-help">
              {mode === "direct"
                ? "使用服务器公网 IP / 可直达代理域名，管理域名不一定支持代理端口。"
                : "需要先建立 SSH 转发；此处使用本机地址，端口按服务器监听值生成。"}
            </small>
          </label>
          <div className="form-two">
            <label>
              工位 ID
              <input
                value={worker}
                onChange={(event) => setWorker(event.target.value)}
                maxLength={64}
              />
            </label>
            <label>
              Session ID
              <div className="session-input">
                <input
                  value={session}
                  onChange={(event) => setSession(event.target.value)}
                  maxLength={64}
                />
                <Button
                  variant="ghost"
                  size="icon"
                  type="button"
                  aria-label="生成新 session"
                  title="生成新 session（仅生成配置）"
                  onClick={() =>
                    setSession(randomId().replaceAll("-", "").slice(0, 10))
                  }
                >
                  <RefreshCw size={14} />
                </Button>
              </div>
            </label>
          </div>
          <label>
            国家
            <select
              value={country}
              onChange={(event) => setCountry(event.target.value)}
            >
              <option value="">任意可用国家</option>
              {["US", "GB", "CA", "DE", "FR", "JP", "SG", "AU"].map((code) => (
                <option key={code} value={code}>
                  {new Intl.DisplayNames(["zh-CN"], { type: "region" }).of(
                    code,
                  )}{" "}
                  · {code}
                </option>
              ))}
            </select>
          </label>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          {!data && !error && (
            <p className="connection-loading">
              <Loader2 size={15} className="spin" />
              正在生成连接配置…
            </p>
          )}
          {data && (
            <div className="connection-links">
              <CopyField
                title="SOCKS5 导入链接（含别名）"
                value={data.links.socks5_named}
                onCopy={() =>
                  void copy(data.links.socks5_named, "SOCKS5 含别名")
                }
              />
              <CopyField
                title="HTTP 完整链接"
                value={data.links.http}
                onCopy={() => void copy(data.links.http, "HTTP 完整链接")}
              />
              <details className="dns-link">
                <summary>脚本 / 远程 DNS 专用链接</summary>
                <CopyField
                  title="纯 SOCKS5 链接"
                  value={data.links.socks5}
                  onCopy={() => void copy(data.links.socks5, "纯 SOCKS5 链接")}
                />
                <CopyField
                  title="SOCKS5h 链接"
                  value={data.links.socks5h}
                  onCopy={() => void copy(data.links.socks5h, "SOCKS5h 链接")}
                />
              </details>
              <details className="dns-link">
                <summary>结构化配置与 curl 示例</summary>
                <div className="connection-links">
                  <CopyField
                    title="HTTP 结构化配置（含密码）"
                    value={JSON.stringify(data.structured.http, null, 2)}
                    onCopy={() =>
                      void copy(
                        JSON.stringify(data.structured.http, null, 2),
                        "HTTP 结构化配置",
                      )
                    }
                  />
                  <CopyField
                    title="curl 示例"
                    value={`curl --proxy '${data.links.socks5h}' https://api.ipify.org`}
                    onCopy={() =>
                      void copy(
                        `curl --proxy '${data.links.socks5h}' https://api.ipify.org`,
                        "curl 示例",
                      )
                    }
                  />
                </div>
              </details>
              <p className="field-help">
                以上链接已包含代理密码，可直接粘贴到客户端。HTTPS 目标也使用
                HTTP 代理链接。
              </p>
            </div>
          )}
        </div>
      ) : (
        <div
          role="tabpanel"
          id="connection-guide"
          aria-labelledby="connection-guide-tab"
          className="guide-panel"
        >
          <p>
            包含用户名规则、会话语义、连接示例和管理 API 用法，可直接粘贴给
            AI。真实密码与管理员 Token 使用占位符。
          </p>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <pre tabIndex={0}>{data?.ai_guide || "正在生成接入说明…"}</pre>
          <Button
            type="button"
            disabled={!data}
            onClick={() => data && void copy(data.ai_guide, "AI 接入说明")}
          >
            {copied === "AI 接入说明" ? (
              <Check size={16} />
            ) : (
              <Clipboard size={16} />
            )}
            复制完整 AI 接入说明
          </Button>
        </div>
      )}
      <div className="connection-caption">
        同一工位与 session 保持出口；切换 session 使旧 session 失效。已建立的
        TCP 需要排空或重连。
      </div>
    </Dialog>
  );
}
