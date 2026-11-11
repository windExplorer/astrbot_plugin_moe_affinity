<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { apiRank, type RankRow } from "../api";
import { app, notify } from "../state";
import { initials, timeAgo } from "../lib/format";

const router = useRouter();
const rows = ref<RankRow[]>([]);
const limit = ref(20);
const busy = ref(false);

async function load() {
  if (!app.role) return;
  busy.value = true;
  try {
    rows.value = (await apiRank(app.role, limit.value)).items;
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
watch(() => [app.role, limit.value], load);

const maxScore = 1000;
</script>

<template>
  <div class="row">
    <button
      v-for="value in [10, 20, 50, 100]"
      :key="value"
      class="chip"
      :class="{ on: limit === value }"
      @click="limit = value"
    >
      前 {{ value }}
    </button>
    <div class="spacer"></div>
    <span class="faint">{{ busy ? "加载中…" : `共 ${rows.length} 人` }}</span>
  </div>

  <div class="card">
    <div v-if="rows.length" style="display: flex; flex-direction: column; gap: 6px">
      <div
        v-for="row in rows"
        :key="row.user_id"
        class="row clickable"
        style="padding: 8px 6px; border-radius: 10px"
        @click="router.push({ path: `/users/${row.user_id}`, query: { role: app.role } })"
      >
        <span
          class="mono"
          :style="{
            width: '26px',
            fontWeight: 700,
            color: row.rank <= 3 ? 'var(--accent)' : 'var(--faint)',
          }"
        >
          {{ row.rank }}
        </span>
        <span class="avatar">{{ initials(row.display_name, row.user_id) }}</span>
        <div style="min-width: 120px">
          <div class="n">{{ row.display_name || row.user_id }}</div>
          <div class="i">{{ row.user_id }}</div>
        </div>
        <div class="bar" style="flex: 1; min-width: 60px">
          <i :style="{ width: Math.min(100, (row.score / maxScore) * 100) + '%' }"></i>
        </div>
        <span class="pill">{{ row.stage_label }}</span>
        <span class="mono" style="width: 56px; text-align: right">{{ row.score }}</span>
        <span class="faint" style="width: 76px; text-align: right">{{ timeAgo(row.last_interaction_at) }}</span>
      </div>
    </div>
    <div v-else class="empty">暂无排行数据</div>
  </div>
</template>
