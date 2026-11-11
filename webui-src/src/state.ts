// 全局状态：角色选择、版本、健康状态、轻量 toast。
// 不引入 pinia —— 页面只有这一份跨视图状态，reactive 足够。

import { reactive } from "vue";
import { apiPing, apiRoles, type RoleRow } from "./api";
import { storageGet, storageSet } from "./bridge";

const ROLE_KEY = "moe.affinity.role";

export const app = reactive({
  ready: false,
  loading: false,
  error: "",
  version: "",
  apiVersion: "",
  dbReady: false,
  roles: [] as RoleRow[],
  role: "",
  toast: "",
  toastError: false,
});

let toastTimer: ReturnType<typeof setTimeout> | null = null;

export function notify(message: string, isError = false): void {
  app.toast = message;
  app.toastError = isError;
  if (toastTimer) clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    app.toast = "";
  }, isError ? 5000 : 2600);
}

export async function bootstrap(force = false): Promise<void> {
  if (app.ready && !force) return;
  app.loading = true;
  try {
    const [ping, roles] = await Promise.all([apiPing(), apiRoles()]);
    app.version = ping.version;
    app.apiVersion = ping.api_version;
    app.dbReady = ping.db_ready;
    app.roles = roles.items || [];
    const saved = storageGet(ROLE_KEY) || "";
    app.role = app.roles.some((r) => r.role_id === saved)
      ? saved
      : app.roles[0]?.role_id || "";
    app.ready = true;
    app.error = "";
  } catch (e: any) {
    app.error = e?.message || String(e);
    app.ready = false;
  } finally {
    app.loading = false;
  }
}

export function setRole(roleId: string): void {
  app.role = roleId;
  storageSet(ROLE_KEY, roleId);
}

export function roleName(roleId: string): string {
  return app.roles.find((r) => r.role_id === roleId)?.display_name || roleId;
}
