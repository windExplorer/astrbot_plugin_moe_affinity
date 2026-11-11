<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { apiUser, type UserDetail, type UserRow } from "../api";
import { app, notify } from "../state";
import EChart from "../components/EChart.vue";
import ScoreDialog from "../components/ScoreDialog.vue";
import { CHART_COLORS, codeLabel, fmtTime, initials, signed, timeAgo } from "../lib/format";

const route = useRoute();
const router = useRouter();
const detail = ref<UserDetail | null>(null);
const busy = ref(false);
const editing = ref(false);

const userId = computed(() => String(route.params.id || ""));

async function load() {
  if (!app.role || !userId.value) return;
  busy.value = true;
  try {
    detail.value = await apiUser(app.role, userId.value, 30);
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
watch(() => [app.role, userId.value], load);

const snap = computed(() => detail.value?.snapshot as any);
const maxScore = computed(() => snap.value?.max_score || 1000);
const ratio = computed(() => Math.min(100, ((snap.value?.score || 0) / maxScore.value) * 100));
const knownDays = computed(() =>
  snap.value ? Math.max(0, Math.floor((Date.now() / 1000 - snap.value.first_seen_at) / 86400)) : 0,
);

const chart = computed(() => {
  const points = detail.value?.series || [];
  return {
    color: CHART_COLORS,
    tooltip: { trigger: "axis" },
    grid: { left: 8, right: 12, top: 20, bottom: 4, containLabel: true },
    xAxis: { type: "category", data: points.map((p) => p.day.slice(5)), axisLabel: { color: "#8b93a7" } },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "rgba(139,147,167,0.15)" } },
      axisLabel: { color: "#8b93a7" },
    },
    series: [
      {
        name: "当日净增",
        type: "bar",
        barMaxWidth: 16,
        itemStyle: { borderRadius: [4, 4, 0, 0] },
        data: points.map((p) => Math.round(p.delta)),
      },
    ],
  };
});

const asRow = computed<UserRow | null>(() => {
  if (!snap.value) return null;
  return {
    rank: snap.value.rank,
    user_id: snap.value.user_id,
    display_name: snap.value.display_name,
    platform: snap.value.platform,
    score: snap.value.score,
    stage: snap.value.stage,
    stage_label: snap.value.stage_label,
    first_seen_at: snap.value.first_seen_at,
    last_interaction_at: snap.value.last_interaction_at,
  };
});

function back() {
  router.push({ path: "/users", query: { role: app.role } });
}

function saved() {
  editing.value = false;
  load();
}
</script>

<template>
  <div class="row">
    <button class="btn ghost" @click="back">← 返回用户列表</button>
    <div class="spacer"></div>
    <span v-if="busy" class="faint">加载中…</span>
  </div>

  <div v-if="detail && detail.status !== 'ok'" class="card">
    <div class="card-h"><div class="card-t">没有记录</div></div>
    <p class="muted">这个用户还没有任何好感度记录。缺失不等于 0 分。</p>
  </div>

  <template v-else-if="snap">
    <div class="card">
      <div class="row" style="align-items: flex-start">
        <span class="avatar" style="width: 44px; height: 44px; border-radius: 13px; font-size: 16px">
          {{ initials(snap.display_name, snap.user_id) }}
        </span>
        <div style="flex: 1; min-width: 0">
          <div style="font-size: 18px; font-weight: 650">{{ snap.display_name || snap.user_id }}</div>
          <div class="faint">{{ snap.user_id }}<span v-if="snap.platform"> · {{ snap.platform }}</span></div>
        </div>
        <div class="row">
          <button class="btn" @click="editing = true">调整分数</button>
        </div>
      </div>

      <div class="row" style="margin-top: 16px">
        <div style="font-size: 30px; font-weight: 700" class="mono">{{ snap.score }}</div>
        <div class="muted">/ {{ maxScore }}</div>
        <span class="pill">{{ snap.stage_label }}</span>
        <div class="spacer"></div>
        <span class="faint">历史最高 {{ snap.peak }} · 本角色第 {{ snap.rank }} 名</span>
      </div>
      <div class="bar" style="margin-top: 8px; height: 8px">
        <i :style="{ width: ratio + '%' }"></i>
      </div>

      <div class="row" style="margin-top: 12px">
        <span class="faint">认识 {{ knownDays }} 天</span>
        <span class="faint">·</span>
        <span class="faint">最近互动 {{ timeAgo(snap.last_interaction_at) }}</span>
        <span class="faint">·</span>
        <span class="faint">本群贡献合计 {{ signed(detail?.group_delta_total || 0) }}</span>
      </div>
    </div>

    <div class="grid cols-2">
      <div class="card">
        <div class="card-h"><div class="card-t">近 30 天变动</div></div>
        <EChart v-if="detail?.series?.length" :option="chart" height="220px" />
        <div v-else class="empty">这个区间没有变动</div>
      </div>

      <div class="card">
        <div class="card-h">
          <div class="card-t">群贡献</div>
          <div class="spacer"></div>
          <div class="card-s">分数是全局一份，这里看每个群挣了多少</div>
        </div>
        <table v-if="detail?.groups?.length">
          <thead>
            <tr>
              <th>群</th>
              <th style="width: 80px">贡献</th>
              <th style="width: 70px">事件</th>
              <th style="width: 90px">最近</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="group in detail.groups" :key="group.group_id" class="clickable" @click="router.push(`/groups/${group.group_id}`)">
              <td>{{ group.group_name || group.group_id }}</td>
              <td class="mono" :class="group.delta >= 0 ? 'pos' : 'neg'">{{ signed(group.delta) }}</td>
              <td class="mono">{{ group.events }}</td>
              <td class="faint">{{ timeAgo(group.last_at) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">只在私聊互动过</div>
      </div>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="card-t">事件流水</div>
        <div class="spacer"></div>
        <div class="card-s">最近 50 条</div>
      </div>
      <table>
        <thead>
          <tr>
            <th style="width: 110px">时间</th>
            <th style="width: 110px">事件</th>
            <th style="width: 80px">变化</th>
            <th>来源 / 说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="event in detail?.events || []" :key="event.id">
            <td class="faint">{{ fmtTime(event.created_at) }}</td>
            <td><span class="pill">{{ codeLabel(event.code) }}</span></td>
            <td class="mono" :class="event.delta >= 0 ? 'pos' : 'neg'">{{ signed(event.delta) }}</td>
            <td class="muted">
              {{ event.source }}
              <span v-if="event.operator" class="faint">· {{ event.operator }}</span>
              <span v-if="event.group_name" class="faint">· {{ event.group_name }}</span>
              <span v-if="event.evidence" class="faint">· {{ event.evidence }}</span>
            </td>
          </tr>
          <tr v-if="!(detail?.events || []).length">
            <td colspan="4"><div class="empty">暂无流水</div></td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>

  <ScoreDialog
    v-if="editing && asRow"
    :user="asRow"
    :role="app.role"
    :max-score="maxScore"
    @close="editing = false"
    @saved="saved"
  />
</template>
