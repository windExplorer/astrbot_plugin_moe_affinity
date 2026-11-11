<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import {
  apiMigrateApply,
  apiMigratePreview,
  apiMigrateRepair,
  apiRoles,
  type MigratePreview,
  type RoleRow,
} from "../api";
import { app, notify } from "../state";
import { fmtDate, timeAgo } from "../lib/format";

const preview = ref<MigratePreview | null>(null);
const selected = ref<Set<string>>(new Set());
const busy = ref(false);
const path = ref("");
const clearMood = ref(true);
const roles = ref<RoleRow[]>([]);
const target = ref(app.role || "");

const roleOptions = computed(() =>
  roles.value.map((role) => ({
    label: `${role.display_name || role.role_id}${role.in_use ? "" : "（未使用）"}`,
    value: role.role_id,
  })),
);

async function load() {
  busy.value = true;
  try {
    const roleRes = await apiRoles().catch(() => ({ items: [] as RoleRow[] }));
    roles.value = roleRes.items || [];
    if (!target.value || !roles.value.some((role) => role.role_id === target.value)) {
      target.value = app.role || roles.value[0]?.role_id || "";
    }
    await reloadPreview();
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

/** 只重读预览：幂等状态是按「角色」算的，切换导入目标必须重新算一遍。 */
async function reloadPreview() {
  if (!target.value) return;
  const res = await apiMigratePreview(path.value.trim(), target.value);
  preview.value = res;
  selected.value = new Set(
    (res.items || [])
      .filter((item) => !item.imported && item.target_score > 0)
      .map((item) => item.user_id),
  );
}

watch(target, () => {
  busy.value = true;
  reloadPreview()
    .catch((e: any) => notify(e?.message || String(e), true))
    .finally(() => {
      busy.value = false;
    });
});

function toggle(userId: string) {
  if (selected.value.has(userId)) selected.value.delete(userId);
  else selected.value.add(userId);
  selected.value = new Set(selected.value);
}

function toggleAll() {
  const pending = (preview.value?.items || []).filter((item) => !item.imported && item.target_score > 0);
  selected.value =
    selected.value.size === pending.length ? new Set() : new Set(pending.map((item) => item.user_id));
}

async function apply() {
  if (!target.value) {
    notify("先在上方选一个导入目标角色", true);
    return;
  }
  if (!selected.value.size) {
    notify("没有选中任何用户", true);
    return;
  }
  busy.value = true;
  try {
    const res = await apiMigrateApply({
      role: target.value,
      user_ids: Array.from(selected.value),
      clear_mood: clearMood.value,
      path: path.value.trim() || undefined,
    });
    notify(
      `导入 ${res.imported} 人，跳过 ${res.skipped} 人` +
        (res.mood_cleared ? `，清理心情 ${res.mood_cleared} 条` : ""),
    );
    if (res.errors?.length) notify(res.errors[0], true);
    await load();
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

async function repairTime() {
  if (!target.value) {
    notify("先选一个角色", true);
    return;
  }
  busy.value = true;
  try {
    const res = await apiMigrateRepair({ role: target.value, path: path.value.trim() || undefined });
    notify(
      res.message || `已回填 ${res.repaired} 人的认识时间（候选 ${res.candidates} 人）`,
      !res.message && res.repaired === 0,
    );
    await load();
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

const pendingCount = computed(
  () => (preview.value?.items || []).filter((item) => !item.imported).length,
);
const allSelected = computed(() => {
  const pending = (preview.value?.items || []).filter((item) => !item.imported && item.target_score > 0);
  return pending.length > 0 && selected.value.size === pending.length;
});
const importTarget = computed(
  () => roles.value.find((role) => role.role_id === target.value)?.display_name || target.value || "未选择角色",
);

onMounted(load);
</script>

<template>
  <div class="card">
    <div class="card-h">
      <div class="card-t">从自我学习插件迁移</div>
      <div class="spacer"></div>
      <n-button size="small" quaternary :loading="busy" @click="load">重新读取</n-button>
    </div>

    <div class="row">
      <n-input
        v-model:value="path"
        style="flex: 1; min-width: 260px"
        clearable
        placeholder="老库路径（留空 = 自动查找 messages.db）"
      />
      <div class="row muted">
        <n-switch v-model:value="clearMood" size="small" />
        <span>顺手把残留心情置为非活跃</span>
      </div>
    </div>

    <div class="row" style="margin-top: 10px">
      <span class="muted">导入到</span>
      <n-select
        v-model:value="target"
        :options="roleOptions"
        style="width: 260px"
        placeholder="选择目标角色"
      />
      <span class="faint">侧栏当前角色：{{ app.role || "未选" }}（可在这里单独指定，含未使用的人格）</span>
    </div>

    <n-alert style="margin-top: 12px" :bordered="false" type="default">
      {{ preview?.message || "同一用户多个群各有一条记录，导入时取最高那条；分数按比例换算（老库满分对应 1000 分）。" }}
      <span v-if="preview?.db_path" class="faint"> · 来源 {{ preview.db_path }}</span>
      <br />
      <span class="faint">
        认识时间自动取老库里最早的一份证据（好感度建行时间 / 消息记录 / 对话史 / 交互史），
        导入前可在下方「最早记录」列核对。
      </span>
    </n-alert>

    <n-alert style="margin-top: 8px" :bordered="false" type="info">
      导入是<b>按角色记的</b>：同一个人可以分别导入到每个角色，每个角色只需导入一次。
      切换上面的「导入到」会重新计算该角色的待导入列表。
    </n-alert>
  </div>

  <div class="card">
    <div class="row" style="margin-bottom: 10px">
      <div class="card-t">待导入 {{ pendingCount }} 人</div>
      <n-tag size="small" round :bordered="false">导入到：{{ importTarget }}</n-tag>
      <div class="spacer"></div>
      <n-button size="small" quaternary @click="toggleAll">
        {{ allSelected ? "全不选" : "全选未导入" }}
      </n-button>
      <n-button size="small" quaternary :loading="busy" @click="repairTime">回填认识时间</n-button>
      <n-button size="small" type="primary" :loading="busy" :disabled="!selected.size" @click="apply">
        导入选中（{{ selected.size }}）
      </n-button>
    </div>

    <table>
      <thead>
        <tr>
          <th style="width: 44px"></th>
          <th>用户</th>
          <th style="width: 120px">旧分数</th>
          <th style="width: 110px">换算后</th>
          <th style="width: 80px">群数</th>
          <th style="width: 110px">最早记录</th>
          <th style="width: 100px">最近更新</th>
          <th style="width: 90px">状态</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in preview?.items || []"
          :key="item.user_id"
          :class="{ clickable: !item.imported }"
          @click="!item.imported && toggle(item.user_id)"
        >
          <td>
            <n-checkbox
              :checked="selected.has(item.user_id)"
              :disabled="item.imported"
              @update:checked="toggle(item.user_id)"
              @click.stop
            />
          </td>
          <td class="mono">{{ item.user_id }}</td>
          <td class="mono">{{ item.legacy_score }} / {{ item.legacy_max }}</td>
          <td class="mono">{{ item.target_score }}</td>
          <td class="mono">{{ item.group_count }}</td>
          <td class="faint">{{ item.first_at > 0 ? fmtDate(item.first_at) : "—" }}</td>
          <td class="faint">{{ timeAgo(item.last_at) }}</td>
          <td>
            <n-tag size="small" round :bordered="false" :type="item.imported ? 'success' : 'warning'">
              {{ item.imported ? "已导入" : "待导入" }}
            </n-tag>
          </td>
        </tr>
        <tr v-if="!(preview?.items || []).length">
          <td colspan="8"><div class="empty">{{ busy ? "读取中…" : "没有找到可迁移的数据" }}</div></td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <div class="card-h"><div class="card-t">迁移前需要确认</div></div>
    <ol class="muted" style="margin: 0; padding-left: 20px; line-height: 1.9">
      <li>自我学习插件里 <code>enable_affection_system</code> 与 <code>include_affection_info</code> 已关闭。</li>
      <li>它的情绪开关（<code>enable_emotion_learning_gate</code> / <code>enable_startup_random_mood</code> / <code>enable_daily_mood</code>）也已关闭。</li>
      <li>本页只读对方的数据库；唯一写操作是「把残留心情置为非活跃」，且需要打开上面的开关。</li>
    </ol>
  </div>
</template>
