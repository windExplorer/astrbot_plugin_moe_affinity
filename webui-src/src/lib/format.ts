export function timeAgo(ts?: number): string {
  if (!ts) return "—";
  const diff = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
  return new Date(ts * 1000).toLocaleDateString();
}

/** 认识多少天（按首次建档时间算，满 24 小时才 +1）。 */
export function daysSince(ts?: number): number {
  if (!ts) return 0;
  return Math.max(0, Math.floor((Date.now() / 1000 - ts) / 86400));
}

export function fmtDate(ts?: number): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function fmtTime(ts?: number): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function initials(name?: string, fallback = "?"): string {
  const text = (name || fallback).trim();
  if (!text) return "?";
  const first = Array.from(text)[0] || "?";
  return first.toUpperCase();
}

export function signed(value: number): string {
  const fixed = Math.round(value * 10) / 10;
  return `${fixed > 0 ? "+" : ""}${fixed}`;
}

export const CODE_LABELS: Record<string, string> = {
  "chat.daily_first": "每日首次",
  "chat.message": "聊天",
  "chat.attention": "被叫",
  "chat.praise": "夸奖",
  "chat.negative": "不愉快",
  "manual.adjust": "管理员调整",
  "manual.reset": "重置",
  "manual.merge": "角色归并",
  "import.seed": "迁移导入",
  "checkin.checkin": "签到",
};

export function codeLabel(code: string): string {
  return CODE_LABELS[code] || code;
}

/** 深色/浅色通用的 ECharts 基础配色 */
export const CHART_COLORS = ["#f2789f", "#8b7cf6", "#4cc9f0", "#39c98d", "#f0b429", "#f05d5e"];
