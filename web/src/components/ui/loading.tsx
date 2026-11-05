import { Loader2 } from "lucide-react";

export function LoadingState({
  label = "正在加载数据…",
  variant = "list",
  rows = 4,
}: {
  label?: string;
  variant?: "list" | "overview" | "settings";
  rows?: number;
}) {
  return (
    <div
      className={`loading-state loading-${variant}`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <p className="loading-caption">
        <Loader2 size={15} className="spin" />
        {label}
      </p>
      <div className="loading-shapes" aria-hidden="true">
        {variant === "overview" && (
          <div className="loading-metrics">
            {Array.from({ length: 8 }, (_, i) => (
              <div className="loading-metric" key={i}>
                <span className="skeleton-line" />
                <span className="skeleton-number" />
                <span className="skeleton-line short" />
              </div>
            ))}
          </div>
        )}
        {Array.from({ length: rows }, (_, i) => (
          <div className="loading-row" key={i}>
            <span className="skeleton-block" />
            <span className="skeleton-line" />
            <span className={`skeleton-line ${i % 2 ? "short" : "medium"}`} />
          </div>
        ))}
      </div>
    </div>
  );
}

export function TableLoading({
  columns,
  label,
}: {
  columns: number;
  label?: string;
}) {
  return (
    <tr>
      <td colSpan={columns}>
        <LoadingState label={label} />
      </td>
    </tr>
  );
}
