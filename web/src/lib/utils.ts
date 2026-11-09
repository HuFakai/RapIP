import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
export function date(value?: number | null) {
  return value
    ? new Date(value * 1000).toLocaleString("zh-CN", { hour12: false })
    : "尚未验证";
}
export function bytes(value: number) {
  if (!Number.isFinite(value)) return "—";
  const size = Math.abs(value);
  const index =
    size < 1
      ? 0
      : Math.min(5, Math.max(0, Math.floor(Math.log(size) / Math.log(1024))));
  const amount = value / 1024 ** index;
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: size === 0 ? 0 : index === 0 ? 1 : 2 }).format(amount)} ${["B", "KB", "MB", "GB", "TB", "PB"][index]}`;
}
export function trafficScale(values: (number | null)[], rate = false) {
  const maximum = values.reduce<number>(
    (max, value) =>
      Number.isFinite(value) ? Math.max(max, Math.abs(value ?? 0)) : max,
    0,
  );
  const index =
    maximum < 1
      ? 0
      : Math.min(5, Math.floor(Math.log(maximum) / Math.log(1024)));
  return {
    divisor: 1024 ** index,
    unit: ["B", "KB", "MB", "GB", "TB", "PB"][index] + (rate ? "/s" : ""),
  };
}
export const labels: Record<string, string> = {
  RETAINED: "空闲保留",
  UNAVAILABLE: "原出口不可用",
  CANCELLED: "已取消",
  rotation_policy: "出口轮换策略",
  replacement_preview: "供应商替换预检",
  manual_replacement: "手动更换 IP",
  replacement_cancel: "取消等待替换",
  account_usage_sync: "同步套餐用量",
  account_delete: "移除上游账号",
  group_create: "创建资源组",
  group_update: "更新组策略",
  group_members: "调整组成员",
  group_delete: "删除资源组",
  quality_preview: "只读质量预览",
  bound_quality_refresh: "绑定 IP 质量复检",
  quality_check: "质量筛查准入",
  quality_round: "重新授权总额度",
  ipquality_report: "IPQuality 质量报告",
  read_only_probe: "单节点只读探测",

  RESERVED: "已预留·待连接",
  FRESH: "可分配",
  IN_USE: "使用中",
  DRAINING: "排空中",
  QUARANTINED: "隔离中",
  REPLACING: "补货中",
  ACTIVE: "活跃",
  WAITING: "等待出口",
  RELEASED: "已释放",
  ACCEPTED: "已受理",
  RUNNING: "执行中",
  COMPLETED: "已完成",
  FAILED: "失败",
  QUEUED: "排队中",
  PROCESSING: "上游处理中",
  RECONCILING: "核对中",
  UNCERTAIN: "待人工核对",
  BUDGET_BLOCKED: "预算暂停",
  SUBMITTING: "提交中",
  healthy: "正常",
  pending: "待同步",
  error: "异常",
  authentication_error: "认证异常",
};
