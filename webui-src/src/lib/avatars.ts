// 头像懒加载：把同一帧内出现的 id 合并成一次请求（服务端已缓存成 data URI）。
// 用响应式 Map 存结果，组件读取后会自动重渲染；抓不到就记空串，避免反复请求。

import { reactive } from "vue";
import { apiAvatars } from "../api";

type Kind = "user" | "group";

const cache = reactive<Record<string, string>>({});
const pending: Record<Kind, Set<string>> = { user: new Set(), group: new Set() };
let timer: ReturnType<typeof setTimeout> | null = null;

const keyOf = (kind: Kind, id: string) => `${kind}:${id}`;

export function avatarOf(kind: Kind, id: string): string | undefined {
  if (!id) return undefined;
  const key = keyOf(kind, id);
  if (key in cache) return cache[key] || undefined;
  pending[kind].add(id);
  if (!timer) timer = setTimeout(flush, 60);
  return undefined;
}

async function flush() {
  timer = null;
  const batches: [Kind, string[]][] = [
    ["user", Array.from(pending.user)],
    ["group", Array.from(pending.group)],
  ];
  pending.user.clear();
  pending.group.clear();

  for (const [kind, ids] of batches) {
    if (!ids.length) continue;
    try {
      const res = await apiAvatars(kind, ids.slice(0, 100));
      for (const [id, uri] of Object.entries(res.items || {})) {
        cache[keyOf(kind, id)] = String(uri || "");
      }
      // 没返回的标记成空，避免每次渲染都重新排队
      for (const id of ids) {
        const key = keyOf(kind, id);
        if (!(key in cache)) cache[key] = "";
      }
    } catch {
      for (const id of ids) cache[keyOf(kind, id)] = "";
    }
  }
}
