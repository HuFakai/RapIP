import { api, mutate, refreshAll } from "./api";
import type { AccountData, EventData, Stats } from "./api";

export interface Policy {
  samples: number;
  min_success: number;
  max_latency_ms: number;
  ttl_hours: number;
  countries: string[];
  allowed_asns: string[];
  denied_asns: string[];
  network_types: string[];
  reject_flags: string[];
  require_ipinfo: boolean;
  max_abuse_score: number | null;
}
export const basicPolicy: Policy = {
  samples: 3,
  min_success: 2,
  max_latency_ms: 5000,
  ttl_hours: 24,
  countries: [],
  allowed_asns: [],
  denied_asns: [],
  network_types: [],
  reject_flags: [],
  require_ipinfo: false,
  max_abuse_score: null,
};
export interface Group {
  id: string;
  name: string;
  description: string;
  policy: Policy;
  version: number;
  enabled: boolean;
  auto_replace: boolean;
  daily_limit: number;
  round_limit: number;
  slot_limit: number;
  cooldown_seconds: number;
  total: number;
  fresh: number;
  in_use: number;
  quality_counts: Record<string, number>;
  today_submitted: number;
  round_submitted: number;
  running: boolean;
}
export interface QualityResult {
  state: string;
  reasons: string[];
  checked_at?: number;
  expires_at?: number;
  version?: number;
  ip?: string;
  checks?: { name: string; value: unknown; latency_ms?: number }[];
  evidence?: Record<string, unknown>;
}
export interface MetricPoint {
  at: number;
  up: number;
  down: number;
  up_rate: number;
  down_rate: number;
  attempts: number;
  successes: number;
  failures: number;
  probes: number;
  probe_ok: number;
  peak: number;
  coverage: number;
  gap: boolean;
  instance: string;
  gauges: {
    active_connections: number;
    fresh_count: number;
    total_ips: number;
    in_use_count: number;
    quarantined_count: number;
    replacing_count: number;
  };
}
export interface Metrics {
  points: MetricPoint[];
  resolution: number;
  range: string;
  error?: string;
  summary?: {
    up: number;
    down: number;
    attempts: number;
    successes: number;
    failures: number;
    probes: number;
    probe_ok: number;
  };
  peak: number;
}
export interface OverviewData {
  stats: Stats;
  metrics: Metrics;
  accounts: (AccountData & { scoped_total: number; scoped_fresh: number })[];
  accounts_total: number;
  groups: Group[];
  events: EventData[];
  alerts: {
    id: string;
    kind: string;
    message: string;
    account_id?: string;
    group_id?: string;
  }[];
  alerts_total: number;
  health: { healthy: number; covered: number; total: number };
  quality: Record<string, number>;
  success_rate: number | null;
  probes: {
    sample_count: number;
    total_matched: number;
    histogram: number[];
    p50: number | null;
    p95: number | null;
    target: string;
  };
  observer: {
    mode: string;
    error: string | null;
    dropped_records: number;
    queued_records: number;
    retention_days: number;
    sample_interval: number;
  };
  address: { host: string; socks_port: number; http_port: number };
  at: number;
}
export interface ConnectionRecord {
  socks_reply?: number;
  socks_command?: number;
  error_message?: string;
  id: string;
  at: number;
  started_at: number;
  ended_at?: number;
  protocol: string;
  account_id: string;
  group_id: string;
  worker: string;
  session: string;
  ip: string;
  target: string;
  result: string;
  phase: string;
  established_ms?: number;
  duration_ms?: number;
  up: number;
  down: number;
  close_reason?: string;
  generation?: number;
}
export interface Operation {
  id: string;
  state: string;
  error?: string;
  result: Record<string, unknown>;
}
export async function operation(
  path: string,
  body: unknown = {},
  method = "POST",
  polls = 90,
): Promise<Record<string, unknown>> {
  const accepted = await mutate<Operation>(path, body, method);
  let current = accepted;
  for (let i = 0; i < polls; i++) {
    if (current.state === "COMPLETED") {
      refreshAll();
      return current.result;
    }
    if (current.state === "FAILED")
      throw new Error(current.error || "操作失败");
    await new Promise((resolve) => window.setTimeout(resolve, 1000));
    current = await api<Operation>(`/operations/${accepted.id}`);
  }
  throw new Error(
    `操作仍在后台执行，请在任务页查看 ${accepted.id.slice(0, 10)}；请勿重复提交`,
  );
}
export function queryString(
  values: Record<string, string | number | boolean | undefined>,
) {
  return new URLSearchParams(
    Object.entries(values)
      .filter(([, v]) => v !== "" && v !== undefined)
      .map(([k, v]) => [k, String(v)]),
  ).toString();
}
