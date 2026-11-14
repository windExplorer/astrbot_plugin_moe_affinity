// API 封装：一律通过 AstrBot 官方插件 Page 桥接调用后端，不能用裸 fetch（sandbox 里会 CORS 失败）。
// 后端返回 {status:"ok"|"error", data|message}；Dashboard 会吃掉一层信封，这里再防御式解包一次。

import { whenBridgeReady } from "./bridge";

const PAGE_PLUGIN_NAME = "astrbot_plugin_moe_affinity";

function endpointCandidates(routePath: string): string[] {
  const clean = routePath.replace(/^\/+/, "");
  const list = [clean, "/" + clean, `${PAGE_PLUGIN_NAME}/${clean}`, `/${PAGE_PLUGIN_NAME}/${clean}`];
  return [...new Set(list.map((s) => s.replace(/\/+/g, "/")).filter(Boolean))];
}

function isRouteMissing(payload: any): boolean {
  if (!payload || typeof payload !== "object") return false;
  const text = String(payload.error || "") + " " + String(payload.message || "") + " " + String(payload.detail || "");
  return /未找到.*路由|route.*not.*found|not.*found.*route|404/i.test(text);
}

function unwrap(payload: any): any {
  if (payload && typeof payload === "object") {
    if (payload.status === "error") throw new Error(payload.message || "请求失败");
    if (payload.status === "ok" && "data" in payload) return payload.data;
    if (Object.prototype.hasOwnProperty.call(payload, "code")) {
      if (payload.code !== 0) throw new Error(payload.message || "请求失败");
      return payload.data;
    }
  }
  return payload;
}

function isRouteMissingError(e: any): boolean {
  const text = String(e?.message || e || "");
  return /未找到.*路由|route.*not.*found|not.*found.*route|404|not found/i.test(text);
}

function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | null = null;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(label + " 超时")), ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

async function request(path: string, method: "GET" | "POST", body?: unknown, timeoutMs = 20000): Promise<any> {
  const br = await whenBridgeReady();
  const url = new URL(path, "https://astrbot-plugin-page.local/");
  const candidates = endpointCandidates(url.pathname);
  const errors: string[] = [];

  if (method === "GET") {
    const params = Object.fromEntries(url.searchParams.entries());
    for (const c of candidates) {
      try {
        const payload = await withTimeout(
          br.apiGet(c, Object.keys(params).length ? params : undefined),
          timeoutMs,
          "GET " + c,
        );
        if (isRouteMissing(payload)) {
          errors.push(payload.message || "未找到该路由");
          continue;
        }
        return unwrap(payload);
      } catch (e: any) {
        if (!isRouteMissingError(e)) throw e;
        errors.push(e?.message || String(e));
      }
    }
    throw new Error(errors[0] || "未找到可用的页面 API 路由");
  }

  const payload = JSON.parse(JSON.stringify(body || {}));
  for (const c of candidates) {
    try {
      const data = await withTimeout(br.apiPost(c, payload), timeoutMs, "POST " + c);
      if (isRouteMissing(data)) {
        errors.push(data.message || "未找到该路由");
        continue;
      }
      return unwrap(data);
    } catch (e: any) {
      if (!isRouteMissingError(e)) throw e;
      errors.push(e?.message || String(e));
    }
  }
  throw new Error(errors[0] || "未找到可用的页面 API 路由");
}

export function apiGet<T = any>(endpoint: string, timeoutMs?: number): Promise<T> {
  return request(endpoint, "GET", undefined, timeoutMs) as Promise<T>;
}

export function apiPost<T = any>(endpoint: string, body?: unknown, timeoutMs?: number): Promise<T> {
  return request(endpoint, "POST", body || {}, timeoutMs) as Promise<T>;
}

// ---------------------------------------------------------------- 类型
export interface PingInfo {
  plugin: string;
  version: string;
  api_version: string;
  db_ready: boolean;
  data_dir: string;
  roles: number;
  server_time: number;
}

export interface RoleRow {
  role_id: string;
  display_name: string;
  alias: string;
  created_at: number;
  personas: number;
  users: number;
  last_active_at: number;
  in_use: boolean;
  is_default: boolean;
  bound_sessions: number;
}

export interface StageBucket {
  key: string;
  label: string;
  count: number;
  edge: number;
}

export interface TrendPoint {
  day: string;
  events: number;
  gain: number;
  loss: number;
  users: number;
}

export interface OverviewData {
  role_id: string;
  role_name?: string;
  days: number;
  top_stage_at: number;
  totals: {
    users: number;
    avg_score: number;
    active_today: number;
    active_7d: number;
    events_today: number;
    events_7d: number;
  };
  stages: StageBucket[];
  trend: TrendPoint[];
  top: { user_id: string; display_name: string; score: number }[];
  recent: { code: string; delta: number; user_id: string; name: string; created_at: number; group_name: string }[];
}

export interface UserRow {
  rank: number;
  user_id: string;
  display_name: string;
  platform: string;
  score: number;
  stage: string;
  stage_label: string;
  progress: number;
  overall_progress: number;
  next_stage_at: number | null;
  next_stage_label: string;
  first_seen_at: number;
  last_interaction_at: number;
  checkin_score?: number;
  checkin_days?: number;
  stardust_score?: number;
  stardust_days?: number;
}

export interface UsersPage {
  total: number;
  rows: UserRow[];
  page: number;
  size: number;
  role_id: string;
  missing_names: number;
}

export interface UserDetail {
  status: string;
  role_id: string;
  today?: {
    gain: number;
    loss: number;
    gain_all?: number;
    loss_all?: number;
    events: number;
    gain_cap: number;
    loss_cap: number;
  };
  role?: { role_id: string; alias: string; display_name: string; personas: number };
  last_settled_at?: number;
  snapshot?: UserRow & { peak: number; revision: number; stage_floor: number };
  series?: { day: string; delta: number; events: number }[];
  groups?: { group_id: string; group_name: string; delta: number; events: number; last_at: number }[];
  personas?: { persona_id: string; delta: number; events: number; last_at: number }[];
  events?: {
    id: number;
    code: string;
    delta: number;
    before: number;
    after: number;
    source: string;
    operator: string;
    evidence: string;
    group_id: string;
    persona_id: string;
    group_name: string;
    created_at: number;
  }[];
  group_delta_total?: number;
  checkin?: { score: number; days: number; last_at: number; streak: number } | null;
  stardust?: { score: number; days: number; last_at: number } | null;
}

export interface GroupRow {
  group_id: string;
  group_name: string;
  delta: number;
  events: number;
  users: number;
  last_at: number;
}

export interface GroupDetail {
  role_id: string;
  group_id: string;
  group_name: string;
  rows: {
    user_id: string;
    display_name: string;
    score: number;
    stage: string;
    stage_label: string;
    last_interaction_at: number;
    group_delta: number;
    events: number;
  }[];
  trend: { day: string; delta: number; events: number; users: number }[];
}

export interface RankRow {
  rank: number;
  user_id: string;
  display_name: string;
  score: number;
  stage: string;
  stage_label: string;
  progress: number;
  overall_progress: number;
  next_stage_at: number | null;
  next_stage_label: string;
  last_interaction_at: number;
}

export interface AuditRow {
  id: number;
  role_id: string;
  user_id: string;
  code: string;
  delta: number;
  before: number;
  after: number;
  source: string;
  operator: string;
  evidence: string;
  created_at: number;
}

export interface ConfigPayload {
  items: Record<string, any>;
  schema: Record<string, any>;
  flat: { key: string; spec: Record<string, any> }[];
}

export interface ConflictRow {
  plugin: string;
  key: string;
  value: any;
  note: string;
  level: "ok" | "warn";
}

// ---------------------------------------------------------------- 接口
export interface RolePersonaRow {
  persona_id: string;
  role_id: string;
  role_name: string;
  persona_exists: boolean;
  persona_kind: "persona" | "none";
  in_use: boolean;
  is_default: boolean;
  users: number;
  bound_sessions: number;
  last_active_at: number;
}

export interface DefaultProbe {
  source: string;
  persona_id?: string;
  runtime?: string;
  error?: string;
}

export interface RoleListData {
  items: RoleRow[];
  total: number;
  default_persona: string;
  default_persona_source: string;
  default_persona_probes: DefaultProbe[];
}

export interface RolePersonaData {
  items: RolePersonaRow[];
  alive: string[];
  default_persona: string;
  default_persona_source: string;
  default_persona_probes: DefaultProbe[];
}

export interface MigrateItem {
  user_id: string;
  legacy_score: number;
  legacy_max: number;
  target_score: number;
  groups: { group_id: string; score: number; limit: number }[];
  group_count: number;
  first_at: number;
  last_at: number;
  imported: boolean;
}

export interface MigratePreview {
  exists: boolean;
  db_path: string;
  role_id: string;
  items: MigrateItem[];
  totals: { users: number; pending: number; imported: number };
  message: string;
}

export interface MoodData {
  role_id: string;
  enabled: boolean;
  axes: Record<string, number>;
  raw: Record<string, number>;
  baseline: number;
  description: string;
  neutral: boolean;
  updated_at: number;
  half_life_minutes: number;
}

export interface RuleStage {
  key: string;
  label: string;
  floor: number;
  ceiling: number | null;
}

export interface RuleEvent {
  code: string;
  label: string;
  delta: number;
  note: string;
}

export interface RulesData {
  version: string;
  score: { unbounded: boolean; floor: number; stages: RuleStage[]; note: string };
  daily: { gain_cap: number; loss_cap: number; dedupe_seconds: number; note: string };
  decay: { enabled: boolean; rows: { range: string; per_day: number }[]; note: string };
  events: RuleEvent[];
  keywords: { praise: string[]; negative: string[] };
  mood: {
    enabled: boolean;
    inject: boolean;
    half_life_minutes: number;
    max_delta: number;
    neutral_band: number;
    axes: { key: string; label: string; rest: string }[];
    rows: { trigger: string; changes: [string, number][]; note: string }[];
    note: string;
  };
  scope: { unit: string; persona: string; group: string };
  timing: string;
  sources: { id: string; name: string; type: string; events: string[]; note: string }[];
  third_party: { available: boolean; note: string };
}

export const apiPing = () => apiGet<PingInfo>("/ping");
export const apiRules = () => apiGet<RulesData>("/rules");
export const apiMood = (role: string) => apiGet<MoodData>(`/mood?role=${encodeURIComponent(role)}`);
export const apiRoles = () => apiGet<RoleListData>("/roles");
export const apiRolePersonas = () => apiGet<RolePersonaData>("/roles/personas");
export const apiSetRoleAlias = (role_id: string, alias: string) =>
  apiPost<{ role_id: string; alias: string }>("/roles/alias", { role_id, alias });
export const apiRenameRole = (role_id: string, name: string) =>
  apiPost<{ role_id: string; name: string }>("/roles/rename", { role_id, name });
export const apiMergeRoles = (target: string, source: string) =>
  apiPost<{ status: string; moved: number; merged: number }>("/roles/merge", { target, source });
export const apiMigratePreview = (path = "", role = "") => {
  const p = new URLSearchParams();
  if (path) p.set("path", path);
  if (role) p.set("role", role);
  const qs = p.toString();
  return apiGet<MigratePreview>(`/migrate/preview${qs ? `?${qs}` : ""}`, 60000);
};
export const apiMigrateRepair = (body: { role: string; path?: string }) =>
  apiPost<{ role_id: string; candidates: number; repaired: number; message: string }>(
    "/migrate/repair",
    body,
    120000,
  );

export const apiMigrateApply = (body: {
  role: string;
  user_ids?: string[];
  clear_mood?: boolean;
  path?: string;
}) => apiPost<{ imported: number; skipped: number; errors: string[]; mood_cleared: number }>("/migrate/apply", body, 120000);
export const apiOverview = (role: string, days = 30) =>
  apiGet<OverviewData>(`/overview?role=${encodeURIComponent(role)}&days=${days}`);
export const apiUsers = (params: {
  role: string;
  q?: string;
  stage?: string;
  sort?: string;
  page?: number;
  size?: number;
}) => {
  const p = new URLSearchParams({ role: params.role });
  if (params.q) p.set("q", params.q);
  if (params.stage) p.set("stage", params.stage);
  if (params.sort) p.set("sort", params.sort);
  p.set("page", String(params.page || 1));
  p.set("size", String(params.size || 50));
  return apiGet<UsersPage>(`/users?${p.toString()}`);
};
export const apiUser = (role: string, id: string, days = 30) =>
  apiGet<UserDetail>(`/user?role=${encodeURIComponent(role)}&id=${encodeURIComponent(id)}&days=${days}`);
export const apiGroups = (role: string) =>
  apiGet<{ items: GroupRow[]; total: number; missing_names: number }>(
    `/groups?role=${encodeURIComponent(role)}`,
  );
export const apiGroup = (role: string, id: string) =>
  apiGet<GroupDetail>(`/group?role=${encodeURIComponent(role)}&id=${encodeURIComponent(id)}`);
export const apiSync = (members = false) =>
  apiPost<{ ok: boolean; users: number; groups: number; renamed: number; members: boolean; calibrated: number; errors: string[] }>(
    "/sync",
    { members },
    180000,
  );

export interface CheckinStatus {
  exists: boolean;
  path: string;
  users: number;
  records: number;
  first_date: string;
  last_date: string;
  enabled: boolean;
  scale: number;
  imported: { events: number; users: number; score: number; last_at: number };
}

export interface CheckinSyncStats {
  status?: string;
  records: number;
  written: number;
  first_seen_repaired: number;
}

export const apiCheckinStatus = () => apiGet<CheckinStatus>("/checkin/status");
export const apiCheckinSync = (full = false) =>
  apiPost<CheckinSyncStats>("/checkin/sync", { full }, 120000);

// 星尘同步（v0.13.3，萌萌星语）：对方库没有「人」维度，records 是抽签正项流水数
export interface StardustStatus {
  exists: boolean;
  path: string;
  records: number;
  first_date: string;
  last_date: string;
  enabled: boolean;
  scale: number;
  imported?: { events: number; users: number; last_at?: number };
}
export interface StardustSyncStats {
  status?: string;
  records: number;
  written: number;
}
export const apiStardustStatus = () => apiGet<StardustStatus>("/stardust/status");
export const apiStardustSync = (full = false) =>
  apiPost<StardustSyncStats>("/stardust/sync", { full }, 120000);
export const apiAvatars = (kind: "user" | "group", ids: string[], force = false) => {
  const p = new URLSearchParams({ type: kind, ids: ids.join(",") });
  if (force) p.set("force", "1");
  return apiGet<{ items: Record<string, string>; got: number; total: number }>(
    `/avatars?${p.toString()}`,
    60000,
  );
};
export const apiRank = (role: string, limit = 20) =>
  apiGet<{ items: RankRow[] }>(`/rank?role=${encodeURIComponent(role)}&limit=${limit}`);
export const apiAudit = (page = 1, size = 50) => apiGet<{ total: number; rows: AuditRow[] }>(`/audit?page=${page}&size=${size}`);
export const apiConfig = () => apiGet<ConfigPayload>("/config");
export const apiSetConfig = (items: Record<string, any>) => apiPost<{ changed: string[] }>("/config", { items });
export const apiSetScore = (body: {
  role: string;
  id: string;
  mode: "set" | "delta";
  value: number;
  reason?: string;
  name?: string;
}) => apiPost<{ changed: number }>("/score", { ...body, operator: "dashboard" });
export const apiResetScore = (body: { role: string; id: string; reason?: string }) =>
  apiPost<{ changed: number }>("/reset", { ...body, operator: "dashboard" });
export const apiSetFirstSeen = (role: string, id: string, stamp: number) =>
  apiPost<{ role_id: string; user_id: string; first_seen_at: number }>("/user/first_seen", {
    role,
    id,
    stamp,
  });
export const apiConflicts = () => apiGet<{ items: ConflictRow[]; checked_at: number }>("/conflicts");
