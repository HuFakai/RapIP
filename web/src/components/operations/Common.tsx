import { ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { useQuery, type AccountData, type Paged } from "@/lib/api";
import type { Group } from "@/lib/v2";
import { Button } from "../ui/button";
import { date } from "@/lib/utils";

const text: Record<string, string> = {
  healthy: "正常",
  PASS: "质量通过",
  FAIL: "质量不合格",
  UNKNOWN: "证据不足",
  ERROR: "检测异常",
  EXPIRED: "已过期",
  PENDING: "待检测",
  BASIC: "基础准入",
  FRESH: "待分配",
  IN_USE: "使用中",
  DRAINING: "排空中",
  QUARANTINED: "隔离",
  REPLACING: "替换中",
  success: "建立成功",
  authentication_rejected: "认证拒绝",
  protocol_error: "协议错误",
  POOL_EXHAUSTED: "本组库存不足",
  GROUP_DISABLED: "分组不可用",
  upstream_transport: "上游网络失败",
  upstream_target: "目标连接失败",
  upstream_authentication: "上游认证失败",
};
export function Status({ state }: { state: string }) {
  return (
    <span
      className={`ops-status tone-${state === "healthy" || state === "PASS" || state === "FRESH" || state === "success" ? "good" : state === "FAIL" || state === "ERROR" || state === "upstream_transport" ? "bad" : "neutral"}`}
    >
      {text[state] || state}
    </span>
  );
}
export function ErrorMessage({ message }: { message?: string | null }) {
  return message ? (
    <div className="ops-error" role="alert">
      {message}
    </div>
  ) : null;
}
export function Scope({
  account,
  group,
  onAccount,
  onGroup,
}: {
  account: string;
  group: string;
  onAccount: (v: string) => void;
  onGroup: (v: string) => void;
}) {
  const accounts = useQuery<{ items: AccountData[] }>("/accounts", true, 30000);
  const groups = useQuery<{ items: Group[] }>("/groups", true, 15000);
  return (
    <>
      <select
        aria-label="筛选账号"
        value={account}
        onChange={(e) => onAccount(e.target.value)}
      >
        <option value="">全部账号</option>
        {accounts.data?.items.map((a) => (
          <option value={a.account_id} key={a.account_id}>
            {a.name}
          </option>
        ))}
      </select>
      <select
        aria-label="筛选分组"
        value={group}
        onChange={(e) => onGroup(e.target.value)}
      >
        <option value="">全部分组</option>
        {groups.data?.items.map((g) => (
          <option key={g.id} value={g.id}>
            {g.name}
          </option>
        ))}
      </select>
    </>
  );
}
export function Pages({
  data,
  page,
  setPage,
  size,
  setSize,
}: {
  data?: Paged<unknown>;
  page: number;
  setPage: (v: number) => void;
  size: number;
  setSize: (v: number) => void;
}) {
  return (
    <div className="ops-pager">
      <span>共 {data?.total ?? "—"} 条</span>
      <select
        aria-label="每页条数"
        value={size}
        onChange={(e) => {
          setSize(Number(e.target.value));
          setPage(1);
        }}
      >
        {[20, 50, 100].map((n) => (
          <option key={n} value={n}>
            {n} 条/页
          </option>
        ))}
      </select>
      <Button
        variant="ghost"
        size="sm"
        aria-label="上一页"
        disabled={page === 1}
        onClick={() => setPage(page - 1)}
      >
        <ChevronLeft size={14} />
      </Button>
      <span>
        {page} / {Math.max(1, Math.ceil((data?.total ?? 0) / size))}
      </span>
      <Button
        variant="ghost"
        size="sm"
        aria-label="下一页"
        disabled={!data || page * size >= data.total}
        onClick={() => setPage(page + 1)}
      >
        <ChevronRight size={14} />
      </Button>
    </div>
  );
}
export function Refresh({ onClick }: { onClick: () => void }) {
  return (
    <Button variant="secondary" size="sm" onClick={onClick}>
      <RefreshCw size={13} />
      刷新
    </Button>
  );
}
export function Time({ value }: { value?: number | null }) {
  return <span className="ops-time">{value ? date(value) : "—"}</span>;
}
