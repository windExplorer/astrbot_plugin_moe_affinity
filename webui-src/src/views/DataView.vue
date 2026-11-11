<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { apiMigrateApply, apiMigratePreview, type MigrateItem, type MigratePreview } from "../api";
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
      (preview.value.items || []).filter((item) => !item.imported && item.target_score > 0).map((item) => item.user_id),
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
  // reactive Set：重新赋值触发更新
  selected.value = new Set(selected.value);
}

function toggleAll() {
  const pending = (preview.value?.items || []).filter((item) => !item.imported && item.target_score > 0);
  selected.value =
    selected.value.size === pending.length ? new Set() : new Set(pending.map((item) => item.user_id));
}

async function apply() {
  if (!app.role) {
    notify("先选一个角色", true);
    return;
  }
  if (!selected.value.size) {
    notify("没有选中任何用户", true);
    return;
  }
  if (!window.confirm(`把 ${selected.value.size} 位用户的旧好感度导入到「${app.role}」？`)) return;
  busy.value = true;
  try {
    const res = await apiMigrateApply({
      role: app.role,
      user_ids: Array.from(selected.value),
      clear_mood: clearMood.value,
      path: path.value.trim() || undefined,
    });
    notify(`导入 ${res.imported} 人，跳过 ${res.skipped} 人${res.mood_cleared ? `，清理心情 ${res.mood_cleared} 条` : ""}`);
    if (res.errors?.length) notify(res.errors[0], true);
    await load();
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

const pendingCount = computed(() => (preview.value?.items || []).filter((item) => !item.imported).length);
const allSelected = computed(() => {
  const pending = (preview.value?.items || []).filter((item) => !item.imported && item.target_score > 0);
  return pending.length > 0 && selected.value.size === pending.length;
});

onMounted(load);
</script>

<template>
  <div class="card">
    <div class="card-h">
      <div class="card-t">从自我学习插件迁移</div>
      <div class="spacer"></div>
      <button class="btn ghost" :disabled="busy" @click="load">{{ busy ? "读取中…" : "重新读取" }}</button>
    </div>

    <div class="row">
      <input v-model="path" class="input" style="flex: 1; min-width: 260px" placeholder="老库路径（留空 = 自动查找 messages.db）" />
      <label class="row muted">
        <input v-model="clearMood" type="checkbox" />
        顺手把残留心情置为非活跃
      </label>
    </div>

    <p class="faint" style="margin: 10px 0 0">
      {{ preview?.message || "同一用户多个群各有一条记录，导入时取最高那条；分数按比例换算到本插件上限。" }}
      <span v-if="preview?.db_path"> · 来源 {{ preview.db_path }}</span>
    </p>
  </div>

  <div class="card">
    <div class="row" style="margin-bottom: 10px">
      <div class="card-t">待导入 {{ pendingCount }} 人</div>
      <div class="spacer"></div>
      <button class="btn ghost" @click="toggleAll">{{ allSelected ? "全不选" : "全选未导入" }}</button>
      <button class="btn primary" :disabled="busy || !selected.size" @click="apply">
        导入选中（{{ selected.size }}）
      </button>
    </div>

    <table>
      <thead>
        <tr>
          <th style="width: 36px"></th>
          <th>用户</th>
          <th style="width: 120px">旧分数</th>
          <th style="width: 110px">换算后</th>
          <th style="width: 80px">群数</th>
          <th style="width: 100px">最近更新</th>
          <th style="width: 90px">状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in preview?.items || []" :key="item.user_id" class="clickable" @click="!item.imported && toggle(item.user_id)">
          <td>
            <input
              type="checkbox"
              :checked="selected.has(item.user_id)"
              :disabled="item.imported"
              @click.stop="toggle(item.user_id)"
            />
          </td>
          <td class="mono">{{ item.user_id }}</td>
          <td class="mono">{{ item.legacy_score }} / {{ item.legacy_max }}</td>
          <td class="mono">{{ item.target_score }}</td>
          <td class="mono">{{ item.group_count }}</td>
          <td class="faint">{{ timeAgo(item.last_at) }}</td>
          <td>
            <span class="pill" :class="item.imported ? 'ok' : 'warn'">
              {{ item.imported ? "已导入" : "待导入" }}
            </span>
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
      <li>本页只读对方的数据库；唯一写操作是「把残留心情置为非活跃」，且需要勾选上面的选项。</li>
    </ol>
  </div>
</template>
