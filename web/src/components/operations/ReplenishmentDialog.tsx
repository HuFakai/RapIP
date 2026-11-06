import { useState, type FormEvent } from "react";
import { Check, Info, RefreshCw, Search, Shuffle, X } from "lucide-react";
import { mutate, refreshAll, useQuery } from "@/lib/api";
import type { Group, ReplenishmentRule } from "@/lib/v2";
import { Dialog } from "../ui/dialog";
import { Button } from "../ui/button";
import { ErrorMessage } from "./Common";

type Kind = "country" | "asn" | "ip_range";
type StockItem = { value: string; count: number; name: string };
type Stock = Record<Kind, StockItem[]> & {
  account_count: number;
  error?: string;
};
const labels = { country: "国家", asn: "ASN", ip_range: "IPv4 号段" };
const countries = new Intl.DisplayNames(["zh-CN"], { type: "region" });
const limit = 20;

function optionLabel(kind: Kind, item: StockItem) {
  if (kind === "country") return `${countries.of(item.value)} · ${item.value}`;
  if (kind === "asn")
    return `AS${item.value}${item.name ? ` · ${item.name}` : ""}`;
  return item.value;
}

function StockPicker({
  kind,
  stock,
  selected,
  onChange,
  busy,
}: {
  kind: Kind;
  stock: StockItem[];
  selected: string[];
  onChange: (items: string[]) => void;
  busy: boolean;
}) {
  const [search, setSearch] = useState("");
  const [visible, setVisible] = useState(60);
  const found = stock.filter((item) =>
    optionLabel(kind, item).toLowerCase().includes(search.trim().toLowerCase()),
  );
  const options = new Map(stock.map((item) => [item.value, item]));
  const multi = kind !== "country";
  return (
    <div className="replenish-picker">
      <div className="replenish-picker-head">
        <strong>{labels[kind]}库存</strong>
        <span className="muted">
          {multi ? `已选 ${selected.length} / ${limit}` : "单选"}
        </span>
      </div>
      {selected.length > 0 && (
        <div className="replenish-tags" aria-label="已选目标">
          {selected.map((value) => (
            <Button
              key={value}
              type="button"
              size="sm"
              variant="secondary"
              disabled={busy}
              onClick={() =>
                onChange(selected.filter((item) => item !== value))
              }
            >
              {options.has(value)
                ? optionLabel(kind, options.get(value)!)
                : `${value}（当前不可选）`}{" "}
              <X size={12} />
            </Button>
          ))}
        </div>
      )}
      <label className="replenish-search">
        <Search size={15} />
        <input
          aria-label={`筛选${labels[kind]}库存`}
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setVisible(60);
          }}
          placeholder={`筛选${labels[kind]}，只能从列表选择`}
        />
      </label>
      <div
        className="replenish-options"
        role="group"
        aria-label={`可选${labels[kind]}`}
      >
        {found.slice(0, visible).map((item) => {
          const active = selected.includes(item.value);
          return (
            <button
              type="button"
              key={item.value}
              aria-pressed={active}
              disabled={busy || (multi && !active && selected.length >= limit)}
              onClick={() =>
                onChange(
                  active
                    ? selected.filter((value) => value !== item.value)
                    : multi
                      ? [...selected, item.value]
                      : [item.value],
                )
              }
            >
              <span>
                {active && <Check size={14} />} {optionLabel(kind, item)}
              </span>
              <small>{item.count.toLocaleString()} 可用</small>
            </button>
          );
        })}
        {!found.length && <p className="field-help">没有匹配的库存选项。</p>}
      </div>
      {found.length > visible && (
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={() => setVisible(visible + 60)}
        >
          显示更多（还剩 {found.length - visible} 项）
        </Button>
      )}
    </div>
  );
}

export function ReplenishmentDialog({
  group,
  close,
  notify,
}: {
  group: Group;
  close: () => void;
  notify: (text: string) => void;
}) {
  const [mode, setMode] = useState<ReplenishmentRule["mode"]>(
    group.replenishment?.mode ?? "any",
  );
  const [country, setCountry] = useState(group.replenishment?.country ?? "");
  const [asns, setAsns] = useState(group.replenishment?.asns ?? []);
  const [ranges, setRanges] = useState(group.replenishment?.ranges ?? []);
  const [fallback, setFallback] = useState(
    group.replenishment?.fallback ?? true,
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const kind = mode === "any" || mode === "original" ? null : mode;
  const query = useQuery<Stock>(
    `/groups/${group.id}/replenishment-options`,
    !!kind,
    60000,
  );
  const selected =
    kind === "country"
      ? country
        ? [country]
        : []
      : kind === "asn"
        ? asns
        : ranges;
  const stock = kind ? (query.data?.[kind] ?? []) : [];
  const canSave =
    !kind ||
    (!query.error &&
      !query.data?.error &&
      selected.length > 0 &&
      selected.length <= limit &&
      selected.every((value) => stock.some((item) => item.value === value)));
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!canSave || busy) return;
    setBusy(true);
    setError("");
    try {
      await mutate(
        `/groups/${group.id}/replenishment`,
        {
          mode,
          country: mode === "country" ? country : "",
          asns: mode === "asn" ? asns : [],
          ranges: mode === "ip_range" ? ranges : [],
          fallback,
        },
        "PATCH",
      );
      refreshAll();
      notify("已保存分组补货规则，对新任务生效");
      close();
    } catch (e) {
      setError((e as Error).message);
      void query.refresh();
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      open
      wide={!!kind}
      title={`${group.name} · Webshare 补货规则`}
      description="保存只设置后续补货目标，不发起更换。"
      onOpenChange={(open) => {
        if (!open && !busy) close();
      }}
    >
      <form className="form-stack policy-form replenish-form" onSubmit={save}>
        <div className="policy-scroll">
          <label className="policy-select">
            <strong>补货方式</strong>
            <select
              value={mode}
              disabled={busy}
              onChange={(e) => {
                setMode(e.target.value as ReplenishmentRule["mode"]);
                setError("");
              }}
            >
              <option value="any">随机可用 IP（默认）</option>
              <option value="original">保持各 IP 原国家</option>
              <option value="country">指定国家</option>
              <option value="asn">指定 ASN</option>
              <option value="ip_range">指定 IPv4 CIDR 号段</option>
            </select>
          </label>
          {mode !== "any" && (
            <label className="policy-fallback">
              <Shuffle size={16} />
              <span>
                <strong>缺货时随机补货</strong>
                <small>
                  仅在 Webshare 确认缺货时兜底；补回的 IP 仍须通过本组准入。
                </small>
              </span>
              <input
                type="checkbox"
                role="switch"
                aria-label="指定库存不足时随机补货"
                checked={fallback}
                disabled={busy}
                onChange={(e) => setFallback(e.target.checked)}
              />
            </label>
          )}
          {kind && (
            <>
              <div className="replenish-picker-head stock-caption">
                <span className="field-help">
                  {query.isFetching && !query.data
                    ? "正在读取 Webshare 库存…"
                    : query.data
                      ? query.data.account_count === 1
                        ? "当前账号可用库存 · 执行前还会重新校验"
                        : `本组 ${query.data.account_count} 个上游账号的共同可用选项；数量取各账号最小值。`
                      : "读取本组 Webshare 库存"}
                </span>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={query.isFetching || busy}
                  onClick={() => void query.refresh()}
                >
                  <RefreshCw size={13} />
                  刷新
                </Button>
              </div>
              <ErrorMessage message={query.error || query.data?.error} />
              {query.data && (
                <StockPicker
                  key={kind}
                  kind={kind}
                  stock={stock}
                  selected={selected}
                  busy={busy}
                  onChange={
                    kind === "country"
                      ? (items) => setCountry(items[0] ?? "")
                      : kind === "asn"
                        ? setAsns
                        : setRanges
                  }
                />
              )}
            </>
          )}
          {mode === "any" && (
            <p className="policy-hint">
              <Info size={15} />从 Webshare 可用库存随机选择，补回的 IP
              仍要通过分组准入。
            </p>
          )}
          <ErrorMessage message={error} />
        </div>
        <div className="policy-actions">
          <Button
            type="button"
            variant="secondary"
            disabled={busy}
            onClick={close}
          >
            取消
          </Button>
          <Button type="submit" disabled={busy || !canSave}>
            {busy ? "保存中…" : "保存规则"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
