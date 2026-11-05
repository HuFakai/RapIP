import { useCallback, useEffect, useRef, useState } from "react";
import { randomId } from "./browser";

let csrf: string | null = null;
const reads = new Map<string, { at: number; promise: Promise<unknown> }>();
function readShared<T>(path: string): Promise<T> {
  const previous = reads.get(path);
  if (previous && Date.now() - previous.at < 1000)
    return previous.promise as Promise<T>;
  const promise = api<T>(path).catch((error) => {
    if (reads.get(path)?.promise === promise) reads.delete(path);
    throw error;
  });
  reads.set(path, { at: Date.now(), promise });
  if (reads.size > 200) reads.delete(reads.keys().next().value!);
  return promise;
}
const mutationKeys = new Map<string, string>();
export function setCsrf(value: string | null) {
  csrf = value;
  reads.clear();
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body) headers.set("Content-Type", "application/json");
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(`/api/v1${path}`, {
    signal: AbortSignal.timeout(15000),
    ...options,
    headers,
    credentials: "same-origin",
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/"))
      window.dispatchEvent(new Event("rapip:unauthorized"));
    throw new ApiError(
      response.status,
      data.message || data.detail || "请求失败",
    );
  }
  return data as T;
}

export async function mutate<T>(
  path: string,
  body: unknown = {},
  method = "POST",
): Promise<T> {
  const serialized = JSON.stringify(body);
  const fingerprint = path + serialized;
  const key = mutationKeys.get(fingerprint) ?? randomId();
  mutationKeys.set(fingerprint, key);
  try {
    const result = await api<T>(path, {
      method,
      body: serialized,
      headers: { "Idempotency-Key": key },
    });
    mutationKeys.delete(fingerprint);
    return result;
  } catch (error) {
    if (error instanceof ApiError && error.status < 500)
      mutationKeys.delete(fingerprint);
    throw error;
  }
}

export function useQuery<T>(path: string, enabled = true, interval = 4000) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState(0);
  const mounted = useRef(true);
  const sequence = useRef(0);
  const loading = useRef(false);
  const failures = useRef(0);
  const nextAttempt = useRef(0);
  const refresh = useCallback(async () => {
    if (
      !enabled ||
      loading.current ||
      document.hidden ||
      Date.now() < nextAttempt.current
    )
      return;
    loading.current = true;
    const seq = ++sequence.current;
    try {
      const value = await readShared<T>(path);
      if (mounted.current && seq === sequence.current) {
        setData(value);
        setError("");
        setUpdatedAt(Date.now());
        failures.current = 0;
        nextAttempt.current = 0;
      }
    } catch (err) {
      if (mounted.current && seq === sequence.current) {
        setError(err instanceof Error ? err.message : "连接失败");
        failures.current = Math.min(5, failures.current + 1);
        nextAttempt.current = Date.now() + 1000 * 2 ** failures.current;
      }
    } finally {
      if (seq === sequence.current) loading.current = false;
    }
  }, [path, enabled]);
  useEffect(() => {
    mounted.current = true;
    sequence.current++;
    loading.current = false;
    failures.current = 0;
    nextAttempt.current = 0;
    setData(undefined);
    setError("");
    const run = () => {
      void refresh();
    };
    run();
    const timer = interval > 0 ? window.setInterval(run, interval) : undefined;
    window.addEventListener("rapip:refresh", run);
    document.addEventListener("visibilitychange", run);
    return () => {
      mounted.current = false;
      sequence.current++;
      if (timer !== undefined) window.clearInterval(timer);
      window.removeEventListener("rapip:refresh", run);
      document.removeEventListener("visibilitychange", run);
    };
  }, [refresh, interval]);
  return { data, error, updatedAt, refresh };
}

export function refreshAll() {
  reads.clear();
  window.dispatchEvent(new Event("rapip:refresh"));
}

export interface Paged<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
export interface NodeData {
  group_id: string;
  slot_id: string;
  used: boolean;
  created_at: number;
  fail_count: number;
  connections: number;
  worker: string | null;
  latency_ms: number | null;
  quality: import("./v2").QualityResult;
  id: string;
  account_id: string;
  ip: string;
  port: number;
  country: string;
  state: string;
  reason: string;
  checked_at: number | null;
}
export interface Generation {
  node_id: string;
  ip: string;
  generation: number;
  state: string;
  connections: number;
  drain_until: number | null;
}
export interface BindingData {
  group_id: string;
  policy_version: number;
  id: string;
  worker: string | null;
  session: string;
  country: string | null;
  ip: string | null;
  generation: number;
  connections: number;
  status: string;
  last_active: number;
  created_at: number;
  failovers: number;
  generations: Generation[];
}
export interface Stats {
  probe_enabled: boolean;
  total_ips: number;
  fresh_count: number;
  in_use_count: number;
  draining_count: number;
  quarantined_count: number;
  replacing_count: number;
  blacklist_count: number;
  active_bindings: number;
  active_connections: number;
  binding_limit: number;
  waiters: number;
  bytes_up: number;
  bytes_down: number;
  ready: boolean;
  at: number;
  started_at: number;
  replacement_pending: number;
  countries: Record<string, { total: number; fresh: number; in_use: number }>;
}
export interface AccountData {
  account_id: string;
  name: string;
  enabled: boolean;
  node_count: number;
  fresh_count: number;
  replacement_budget: number | null;
  traffic_limit_gb: number | null;
  usage: { submitted: number; completed: number; redraws: number };
  health: { state: string; message: string; synced_at?: number };
  replacement_country: string;
}
export interface TaskData {
  id: string;
  kind: string;
  state: string;
  category: string;
  error: string | null;
  created_at: number;
  account_id?: string;
}
export interface EventData {
  id: string;
  message: string;
  kind: string;
  at: number;
  ip?: string;
  account_id?: string;
}
export interface SettingsData {
  values: Record<string, string | number | boolean>;
  source: string;
  deployment: {
    socks_host: string;
    socks_port: number;
    proxy_http_host: string;
    proxy_http_port: number;
    socks_user: string;
    public_proxy_host: string;
    public_socks_port: number;
    public_http_proxy_port: number;
    version: string;
  };
}
