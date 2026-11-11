<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { apiMood, apiOverview, type MoodData, type OverviewData } from "../api";
import { app, notify } from "../state";
import EChart from "../components/EChart.vue";
import { CHART_COLORS, codeLabel, signed, timeAgo } from "../lib/format";

const route = useRoute();
const router = useRouter();
const data = ref<OverviewData | null>(null);
const moodData = ref<MoodData | null>(null);
const busy = ref(false);

const days = computed(() => Number(route.query.days || 30));

async function load() {
  if (!app.role) return;
  busy.value = true;
  try {
    const [overview, mood] = await Promise.all([
      apiOverview(app.role, days.value),
      apiMood(app.role).catch(() => null),
    ]);
    data.value = overview;
    moodData.value = mood;
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

function setDays(value: number) {
  router.replace({ query: { ...route.query, days: value } });
}

onMounted(load);
watch(() => [app.role, days.value], load);

const axisColor = { axisLine: { lineStyle: { color: "#5f6678" } }, axisLabel: { color: "#8b93a7" } };

const donut = computed(() => ({
  color: CHART_COLORS,
  tooltip: { trigger: "item" },
  legend: { bottom: 0, textStyle: { color: "#8b93a7" }, itemWidth: 8, itemHeight: 8 },
  series: [
    {
      type: "pie",
      radius: ["52%", "74%"],
      center: ["50%", "44%"],
      avoidLabelOverlap: true,
      itemStyle: { borderColor: "transparent", borderWidth: 2 },
      label: { show: false },
      data: (data.value?.stages || [])
        .filter((s) => s.count > 0)
        .map((s) => ({ name: s.label, value: s.count })),
    },
  ],
}));

const trend = computed(() => {
  const points = data.value?.trend || [];
  return {
    color: CHART_COLORS,
    tooltip: { trigger: "axis" },
    grid: { left: 8, right: 12, top: 24, bottom: 4, containLabel: true },
    legend: { top: 0, textStyle: { color: "#8b93a7" }, itemWidth: 8, itemHeight: 8 },
    xAxis: { type: "category", data: points.map((p) => p.day.slice(5)), ...axisColor },
    yAxis: { type: "value", splitLine: { lineStyle: { color: "rgba(139,147,167,0.15)" } }, ...axisColor },
    series: [
      {
        name: "互动次数",
        type: "bar",
        barMaxWidth: 14,
        itemStyle: { borderRadius: [4, 4, 0, 0], opacity: 0.55 },
        data: points.map((p) => p.events),
      },
      {
        name: "净增分",
        type: "line",
        smooth: true,
        symbol: "none",
        lineStyle: { width: 2 },
        areaStyle: { opacity: 0.12 },
        data: points.map((p) => Math.round((p.gain || 0) + (p.loss || 0))),
      },
    ],
  };
});

const MOOD_LABELS: Record<string, string> = {
  joy: "喜悦",
  anger: "烦躁",
  sorrow: "低落",
  fear: "不安",
};

const moodRadar = computed(() => {
  const axes = moodData.value?.axes || { joy: 5, anger: 1, sorrow: 1, fear: 1 };
  return {
    color: [CHART_COLORS[0]],
    tooltip: {},
    radar: {
      indicator: ["joy", "anger", "sorrow", "fear"].map((key) => ({ name: MOOD_LABELS[key], max: 10 })),
      radius: "64%",
      axisName: { color: "#8b93a7" },
      axisLine: { lineStyle: { color: "rgba(139,147,167,0.18)" } },
      splitLine: { lineStyle: { color: "rgba(139,147,167,0.18)" } },
      splitArea: { show: false },
    },
    series: [
      {
        type: "radar",
        symbolSize: 4,
        lineStyle: { width: 2 },
        areaStyle: { opacity: 0.22 },
        data: [
          {
            name: "心情",
            value: ["joy", "anger", "sorrow", "fear"].map((key) => axes[key] ?? 1),
          },
        ],
      },
    ],
  };
});

const maxScore = computed(() => data.value?.max_score || 1000);
</script>

<template>
  <div class="row">
    <n-tag
      v-for="value in [7, 30, 90]"
      :key="value"
      :checked="days === value"
      checkable
      round
      :bordered="false"
      size="small"
      @update:checked="setDays(value)"
    >
      近 {{ value }} 天
    </n-tag>
    <div class="spacer"></div>
    <span v-if="busy" class="faint">加载中…</span>
  </div>

  <div class="grid cols-4">
    <div class="stat">
      <span class="k">有记录的人</span>
      <span class="v mono">{{ data?.totals.users ?? 0 }}</span>
      <span class="s">7 天活跃 {{ data?.totals.active_7d ?? 0 }} · 今日 {{ data?.totals.active_today ?? 0 }}</span>
    </div>
    <div class="stat">
      <span class="k">平均分</span>
      <span class="v mono">{{ data?.totals.avg_score ?? 0 }}</span>
      <span class="s">上限 {{ maxScore }}</span>
    </div>
    <div class="stat">
      <span class="k">今日事件</span>
      <span class="v mono">{{ data?.totals.events_today ?? 0 }}</span>
      <span class="s">7 天 {{ data?.totals.events_7d ?? 0 }} 条</span>
    </div>
    <div class="stat">
      <span class="k">角色</span>
      <span class="v" style="font-size: 18px">{{ data?.role_name || app.role || "—" }}</span>
      <span class="s">按人格映射，可在配置页归并</span>
    </div>
  </div>

  <div class="grid cols-2">
    <div class="card">
      <div class="card-h">
        <div class="card-t">关系分布</div>
        <div class="spacer"></div>
        <div class="card-s">按档位</div>
      </div>
      <EChart v-if="data?.totals.users" :option="donut" height="260px" />
      <div v-else class="empty">还没有任何好感度记录</div>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="card-t">互动趋势</div>
        <div class="spacer"></div>
        <div class="card-s">柱=次数，线=净增分</div>
      </div>
      <EChart v-if="data?.trend?.length" :option="trend" height="260px" />
      <div v-else class="empty">这个区间没有互动</div>
    </div>
  </div>

  <div class="grid cols-2">
    <div class="card">
      <div class="card-h">
        <div class="card-t">当前心情</div>
        <div class="spacer"></div>
        <div class="card-s">角色自己的四轴，半衰期 {{ moodData?.half_life_minutes ?? 90 }} 分钟</div>
      </div>
      <div v-if="moodData?.enabled">
        <div class="row" style="margin-bottom: 6px">
          <span class="pill">{{ moodData.neutral ? "平静" : "有情绪" }}</span>
          <span class="muted">{{ moodData.description }}</span>
        </div>
        <EChart :option="moodRadar" height="220px" />
      </div>
      <div v-else class="empty">心情未启用（配置 → 心情）</div>
    </div>

    <div class="card">
      <div class="card-h"><div class="card-t">分数前五</div></div>
      <div v-if="data?.top?.length" style="display: flex; flex-direction: column; gap: 10px">
        <div v-for="(item, index) in data.top" :key="item.user_id" class="row">
          <span class="faint mono" style="width: 18px">{{ index + 1 }}</span>
          <div class="who" style="flex: 1">
            <span class="n">{{ item.display_name || item.user_id }}</span>
          </div>
          <div class="bar" style="width: 120px">
            <i :style="{ width: Math.min(100, (item.score / maxScore) * 100) + '%' }"></i>
          </div>
          <span class="mono" style="width: 48px; text-align: right">{{ item.score }}</span>
        </div>
      </div>
      <div v-else class="empty">暂无数据</div>
    </div>
  </div>

  <div class="card">
    <div class="card-h">
      <div class="card-t">最近变动</div>
      <div class="spacer"></div>
      <div class="card-s">最近 12 条</div>
    </div>
    <div v-if="data?.recent?.length" style="display: flex; flex-direction: column; gap: 8px">
      <div v-for="(item, index) in data.recent" :key="index" class="row">
        <span class="pill">{{ codeLabel(item.code) }}</span>
        <span class="muted" style="flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
          {{ item.name || item.user_id }}
          <span v-if="item.group_name" class="faint">· {{ item.group_name }}</span>
        </span>
        <span class="mono" :class="item.delta >= 0 ? 'pos' : 'neg'">{{ signed(item.delta) }}</span>
        <span class="faint" style="width: 64px; text-align: right">{{ timeAgo(item.created_at) }}</span>
      </div>
    </div>
    <div v-else class="empty">暂无变动</div>
  </div>
</template>
