<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { apiMigrateApply, apiMigratePreview, type MigratePreview } from "../api";
import { app, notify } from "../state";
import { timeAgo } from "../lib/format";

const preview = ref<MigratePreview | null>(null);
const selected = ref<Set<string>>(new Set());
const busy = ref(false);
const path = ref("");
const clearMood = ref(true);

async function load() {
  busy.value = true;
  try {
    preview.value = await apiMigratePreview(path.value.trim());
    selected.value = new Set(
      (preview.value.items || [])
        .filter((item) => !item.imported && item.target_score > 0)
        .map((item) => item.user_id),
    );
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

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
  if (!app.role) {
    notify("先在上方选一个角色", true);
    return;
  }
  if (!selected.value.size) {
    notify("没有选中任何用户", true);
    return;
  }
  busy.value = true;
  try {
    const res = await apiMigrateApply({
      role: app.role,
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

const pendingCount = computed(
  () => (preview.value?.items || []).filter((item) => !item.imported).length,
);
const allSelected = computed(() => {
  const pending = (preview.value?.items || []).filter((item) => !item.imported && item.target_score > 0);
  return pending.length > 0 && selected.value.size === pending.length;
});
const importTarget = computed(() => app.role || "未选择角色");

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

    <n-alert style="margin-top: 12px" :bordered="false" type="default">
      {{ preview?.message || "同一用户多个群各有一条记录，导入时取最高那条；分数按比例换算到本插件上限。" }}
      <span v-if="preview?.db_path" class="faint"> · 来源 {{ preview.db_path }}</span>
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
          <td class="faint">{{ timeAgo(item.last_at) }}</td>
          <td>
            <n-tag size="small" round :bordered="false" :type="item.imported ? 'success' : 'warning'">
              {{ item.imported ? "已导入" : "待导入" }}
            </n-tag>
          </td>
        </tr>
        <tr v-if="!(preview?.items || []).length">
          <td colspan="7"><div class="empty">{{ busy ? "读取中…" : "没有找到可迁移的数据" }}</div></td>
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
