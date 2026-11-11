<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { apiGroup, type GroupDetail } from "../api";
import { app, notify } from "../state";
import EChart from "../components/EChart.vue";
import { CHART_COLORS, initials, signed, timeAgo } from "../lib/format";

const route = useRoute();
const router = useRouter();
const data = ref<GroupDetail | null>(null);
const busy = ref(false);

const groupId = computed(() => String(route.params.id || ""));

async function load() {
  if (!app.role || !groupId.value) return;
  busy.value = true;
  try {
    data.value = await apiGroup(app.role, groupId.value);
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
watch(() => [app.role, groupId.value], load);

const trend = computed(() => {
  const points = data.value?.trend || [];
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
        name: "本群净增",
        type: "line",
        smooth: true,
        symbol: "none",
        areaStyle: { opacity: 0.15 },
        data: points.map((p) => Math.round(p.delta)),
      },
    ],
  };
});
</script>

<template>
  <div class="row">
    <n-button size="small" quaternary @click="router.push('/groups')">← 返回群列表</n-button>
    <div class="spacer"></div>
    <span v-if="busy" class="faint">加载中…</span>
  </div>

  <div class="card">
    <div class="card-h">
      <div class="card-t">{{ data?.group_name || "未命名群" }}</div>
      <span class="pill">{{ groupId }}</span>
      <div class="spacer"></div>
      <span class="faint">有记录成员 {{ data?.rows?.length || 0 }}</span>
    </div>
    <EChart v-if="data?.trend?.length" :option="trend" height="220px" />
    <div v-else class="empty">本群暂无趋势数据</div>
  </div>

  <div class="card">
    <div class="card-h">
      <div class="card-t">本群成员</div>
      <div class="spacer"></div>
      <div class="card-s">分数是全局的，贡献是本群挣的</div>
    </div>
    <table>
      <thead>
        <tr>
          <th>成员</th>
          <th style="width: 100px">全局分数</th>
          <th style="width: 80px">档位</th>
          <th style="width: 90px">本群贡献</th>
          <th style="width: 80px">事件</th>
          <th style="width: 110px">最近互动</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in data?.rows || []"
          :key="row.user_id"
          class="clickable"
          @click="router.push({ path: `/users/${row.user_id}`, query: { role: app.role } })"
        >
          <td>
            <div class="who">
              <span class="avatar">{{ initials(row.display_name, row.user_id) }}</span>
              <div>
                <div class="n">{{ row.display_name || row.user_id }}</div>
                <div class="i">{{ row.user_id }}</div>
              </div>
            </div>
          </td>
          <td class="mono">{{ row.score }}</td>
          <td><span class="pill">{{ row.stage_label }}</span></td>
          <td class="mono" :class="row.group_delta >= 0 ? 'pos' : 'neg'">{{ signed(row.group_delta) }}</td>
          <td class="mono">{{ row.events }}</td>
          <td class="faint">{{ timeAgo(row.last_interaction_at) }}</td>
        </tr>
        <tr v-if="!(data?.rows || []).length">
          <td colspan="6"><div class="empty">本群还没有成员记录</div></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
