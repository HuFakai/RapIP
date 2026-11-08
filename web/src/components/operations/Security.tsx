import { useState, type FormEvent } from "react";
import { KeyRound, Loader2, ShieldCheck } from "lucide-react";
import { api, refreshAll, setCsrf, useQuery } from "@/lib/api";
import { Button } from "../ui/button";
import { LoadingState } from "../ui/loading";
import { ErrorMessage } from "./Common";

type Names = { admin_username: string; proxy_username: string };
function CredentialForm({
  scope,
  username,
  notify,
}: {
  scope: "admin" | "proxy";
  username: string;
  notify: (text: string) => void;
}) {
  const admin = scope === "admin";
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const values = new FormData(form);
    const password = String(values.get("new_password") || "");
    if (password !== values.get("confirmation")) {
      setError("两次输入的新密码不一致");
      return;
    }
    setBusy(true);
    setError("");
    try {
      // Credentials never enter the generic mutation retry cache.
      const result = await api<{ requires_login: boolean }>(
        "/settings/credentials",
        {
          method: "PATCH",
          body: JSON.stringify({
            scope,
            username: values.get("username"),
            current_password: values.get("current_password"),
            ...(password ? { new_password: password } : {}),
          }),
        },
      );
      form.reset();
      if (result.requires_login) {
        notify("管理登录已更新，请使用新凭据重新登录");
        setCsrf(null);
        window.dispatchEvent(new Event("rapip:unauthorized"));
      } else {
        notify("代理凭据已更新，请重新复制链接并更新客户端；原 IP 绑定保留");
        refreshAll();
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="surface credential-card">
      <header>
        {admin ? <ShieldCheck size={20} /> : <KeyRound size={20} />}
        <div>
          <h2>{admin ? "管理页面登录" : "SOCKS5 / HTTP 代理入口"}</h2>
          <p>
            {admin
              ? "管理工作台的用户名与密码"
              : "客户端代理链接中的用户名前缀与密码"}
          </p>
        </div>
      </header>
      <form className="form-stack" onSubmit={save} key={username}>
        <ErrorMessage message={error} />
        <label>
          {admin ? "管理用户名" : "代理用户名前缀"}
          <input
            aria-label={admin ? "管理用户名" : "代理用户名前缀"}
            name="username"
            autoComplete="username"
            defaultValue={username}
            required
            maxLength={64}
            pattern={admin ? "[A-Za-z0-9_.@\\-]{1,64}" : "[A-Za-z0-9]{1,64}"}
          />
        </label>
        {!admin && (
          <p className="field-help">
            仅支持字母、数字；worker、session、分组等路由后缀保持原规则。
          </p>
        )}
        <label>
          当前管理密码
          <input
            name="current_password"
            aria-label={
              admin
                ? "修改管理登录：当前管理密码"
                : "修改代理入口：当前管理密码"
            }
            type="password"
            required
            autoComplete="current-password"
            maxLength={256}
          />
        </label>
        <div className="credential-passwords">
          <label>
            新密码
            <input
              name="new_password"
              aria-label={admin ? "新管理密码" : "新代理密码"}
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              placeholder="留空保留当前密码"
            />
          </label>
          <label>
            确认新密码
            <input
              name="confirmation"
              aria-label={admin ? "确认新管理密码" : "确认新代理密码"}
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={128}
              placeholder="再次输入新密码"
            />
          </label>
        </div>
        <p className="credential-impact">
          {admin
            ? "保存后所有浏览器登录会话失效，需要重新登录。管理 API Token 保持有效。"
            : "保存后新连接须使用新凭据。已建立的连接继续，工位、session 与出口 IP 绑定保留；请重新复制链接更新客户端。"}
        </p>
        <Button type="submit" disabled={busy}>
          {busy && <Loader2 size={14} className="spin" />}
          {admin ? "保存管理登录" : "保存代理凭据"}
        </Button>
      </form>
    </section>
  );
}
export function Security({ notify }: { notify: (text: string) => void }) {
  const query = useQuery<Names>("/settings/credentials");
  return (
    <div className="credential-settings">
      <ErrorMessage message={query.error} />
      {query.isLoading && <LoadingState label="正在加载账号设置…" />}
      {query.data && (
        <div className="credential-grid">
          <CredentialForm
            scope="admin"
            username={query.data.admin_username}
            notify={notify}
          />
          <CredentialForm
            scope="proxy"
            username={query.data.proxy_username}
            notify={notify}
          />
        </div>
      )}
    </div>
  );
}
