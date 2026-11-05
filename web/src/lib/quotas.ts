export interface SupplierUsage {
  source?: string;
  plan_id?: string;
  checked_at?: number;
  stale: boolean;
  period_expired?: boolean;
  period_start?: string;
  period_end?: string;
  error?: string;
  replacement?: {
    total: number | null;
    used: number | null;
    remaining: number | null;
  };
  traffic?: {
    total_bytes: number | null;
    used_bytes: number | null;
    remaining_bytes: number | null;
    unlimited: boolean;
    limit_gb: number | null;
  };
}
export interface SupplierTotals {
  account_count: number;
  unique_plans: number;
  duplicate_accounts: number;
  missing_accounts: number;
  oldest_checked_at: number | null;
  replacement: {
    total: number | null;
    remaining: number | null;
    complete: boolean;
    covered_plans: number;
  };
  traffic: {
    total_bytes: number | null;
    used_bytes: number | null;
    remaining_bytes: number | null;
    unlimited_plans: number;
    complete: boolean;
    covered_plans: number;
  };
}
export function quotaPair(
  remaining: number | null | undefined,
  total: number | null | undefined,
) {
  return remaining == null || total == null
    ? "待同步"
    : `${remaining.toLocaleString("zh-CN")} / ${total.toLocaleString("zh-CN")}`;
}
// Supplier package GB and per-cycle byte counts use decimal GB; tunnel charts retain binary units.
export function packageBytes(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index =
    value < 1 ? 0 : Math.min(4, Math.floor(Math.log(value) / Math.log(1000)));
  return `${(value / 1000 ** index).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} ${units[index]}`;
}
export function trafficPair(
  remaining: number | null | undefined,
  total: number | null | undefined,
) {
  if (remaining == null || total == null) return "待同步";
  if (total >= 1e9)
    return `${(remaining / 1e9).toLocaleString("zh-CN", { maximumFractionDigits: 3 })} / ${(total / 1e9).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} GB`;
  return `${packageBytes(remaining)} / ${packageBytes(total)}`;
}
