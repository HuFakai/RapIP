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
  if (value < 1024) return `${value} B`;
  const index = Math.min(3, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** index).toFixed(1)} ${["B", "KB", "MB", "GB"][index]}`;
}
export const labels: Record<string, string> = {
  group_create: "创建资源组",
  group_update: "更新组策略",
  group_members: "调整组成员",
  group_delete: "删除资源组",
  quality_preview: "只读质量预览",
  quality_check: "质量筛查准入",
  quality_round: "新筛选轮次",
  read_only_probe: "单节点只读探测",

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
  BUDGET_BLOCKED: "预算不足",
  SUBMITTING: "提交中",
  healthy: "正常",
  pending: "待同步",
  error: "异常",
  authentication_error: "认证异常",
};
