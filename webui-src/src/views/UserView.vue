<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { apiMood, apiSetFirstSeen, apiUser, type MoodData, type UserDetail, type UserRow } from "../api";
import { app, notify } from "../state";
import EChart from "../components/EChart.vue";
import ScoreDialog from "../components/ScoreDialog.vue";
import {
  CHART_COLORS,
  codeLabel,
  daysSince,
  fmtDate,
  fmtTime,
  signed,
  timeAgo,
} from "../lib/format";

const route = useRoute();
const router = useRouter();
const detail = ref<UserDetail | null>(null);
const mood = ref<MoodData | null>(null);
const busy = ref(false);
const editing = ref(false);

const userId = computed(() => String(route.params.id || ""));

async function load() {
  if (!app.role || !userId.value) return;
  busy.value = true;
  try {
    const [userRes, moodRes] = await Promise.all([
      apiUser(app.role, userId.value, 30),
      apiMood(app.role).catch(() => null),
    ]);
    detail.value = userRes;
    mood.value = moodRes;
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
watch(() => [app.role, userId.value], load);

const snap = computed(() => detail.value?.snapshot as any);
const ratio = computed(() => Math.min(100, (snap.value?.overall_progress || 0) * 100));
const knownDays = computed(() => daysSince(snap.value?.first_seen_at));
const gapToNext = computed(() => {
  if (!snap.value?.next_stage_label || !snap.value?.next_stage_at) return "";
  const need = Math.max(0, Math.ceil(snap.value.next_stage_at - snap.value.score));
  return `距${snap.value.next_stage_label}还差 ${need}`;
});
const roleLabel = computed(() => {
  const role = detail.value?.role;
  if (!role) return app.role;
  return role.alias || role.display_name || role.role_id;
});
const today = computed(() => detail.value?.today);
const todayGainRatio = computed(() => {
  const value = today.value;
  if (!value || !value.gain_cap) return 0;
  return Math.min(100, (value.gain / value.gain_cap) * 100);
});
const todayLossRatio = computed(() => {
  const value = today.value;
  if (!value || !value.loss_cap) return 0;
  return Math.min(100, (value.loss / value.loss_cap) * 100);
});
// 上限只约束聊天计分；导入/手动这些「不受限」的变动单独提示，避免「涨了 1000 但只显示 30」的疑惑
const extraGain = computed(() => {
  const value = today.value;
  if (value?.gain_all == null) return 0;
  return Math.max(0, value.gain_all - value.gain);
});
const extraLoss = computed(() => {
  const value = today.value;
  if (value?.loss_all == null) return 0;
  return Math.max(0, value.loss_all - value.loss);
});

// 认识时间人工调整：好友时长/私聊首聊这类协议拿不到的渠道，人工兜底
const firstSeenEditing = ref(false);
const firstSeenValue = ref<number | null>(null);
const firstSeenBusy = ref(false);

function startFirstSeenEdit() {
  firstSeenValue.value = (snap.value?.first_seen_at || 0) * 1000;
  firstSeenEditing.value = true;
}

async function saveFirstSeen() {
  if (!firstSeenValue.value) {
    notify("先选一个日期", true);
    return;
  }
  firstSeenBusy.value = true;
  try {
    await apiSetFirstSeen(app.role, userId.value, Math.floor(firstSeenValue.value / 1000));
    notify("认识时间已更新");
    firstSeenEditing.value = false;
    await load();
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    firstSeenBusy.value = false;
  }
}

const MOOD_LABELS: Record<string, string> = { joy: "喜悦", anger: "烦躁", sorrow: "低落", fear: "不安" };
const moodAxes = computed(() =>
  Object.entries(mood.value?.axes || {}).map(([key, value]) => ({
    key,
    label: MOOD_LABELS[key] || key,
    value,
  })),
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
    progress: snap.value.progress,
    overall_progress: snap.value.overall_progress,
    next_stage_at: snap.value.next_stage_at,
    next_stage_label: snap.value.next_stage_label,
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
    <n-button size="small" quaternary @click="back">← 返回用户列表</n-button>
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
        <Avatar :id="snap.user_id" :name="snap.display_name || snap.user_id" :size="56" />
        <div style="flex: 1; min-width: 0">
          <div style="font-size: 18px; font-weight: 650">{{ snap.display_name || snap.user_id }}</div>
          <div class="faint">
            {{ snap.user_id
            }}<span v-if="snap.platform"> · {{ snap.platform }}</span>
            <span v-if="roleLabel"> · 角色：{{ roleLabel }}</span>
          </div>
        </div>
        <div class="row">
          <n-button size="small" type="primary" secondary @click="editing = true">调整分数</n-button>
        </div>
      </div>

      <div class="row" style="margin-top: 16px">
        <div style="font-size: 30px; font-weight: 700" class="mono">{{ snap.score }}</div>
        <div class="muted">分</div>
        <span class="pill">{{ snap.stage_label }}</span>
        <span v-if="gapToNext" class="faint">{{ gapToNext }}</span>
        <div class="spacer"></div>
        <span class="faint">历史最高 {{ snap.peak }} · 本角色第 {{ snap.rank }} 名</span>
      </div>
      <div class="bar" style="margin-top: 8px; height: 8px">
        <i :style="{ width: ratio + '%' }"></i>
      </div>

      <div class="row" style="margin-top: 12px">
        <span class="faint">认识 {{ knownDays }} 天（首次 {{ fmtDate(snap.first_seen_at) }}）</span>
        <n-button size="tiny" quaternary @click="startFirstSeenEdit">调整</n-button>
        <span class="faint">·</span>
        <span class="faint">最近互动 {{ timeAgo(snap.last_interaction_at) }}（{{ fmtTime(snap.last_interaction_at) }}）</span>
        <span class="faint">·</span>
        <span class="faint">群贡献合计 {{ signed(detail?.group_delta_total || 0) }}</span>
      </div>
      <div v-if="firstSeenEditing" class="row" style="margin-top: 8px">
        <n-date-picker
          v-model:value="firstSeenValue"
          type="date"
          size="small"
          style="width: 180px"
          :clearable="false"
        />
        <n-button size="small" type="primary" :loading="firstSeenBusy" @click="saveFirstSeen">保存</n-button>
        <n-button size="small" quaternary @click="firstSeenEditing = false">取消</n-button>
        <span class="faint">成为好友的时间、私聊首聊协议拿不到，只能在这里人工指定</span>
      </div>
    </div>

    <div class="grid cols-3">
      <div class="stat">
        <span class="k">今日涨分</span>
        <span class="v mono">{{ today?.gain ?? 0 }} / {{ today?.gain_cap ?? "-" }}</span>
        <div class="bar" style="margin-top: 6px">
          <i :style="{ width: todayGainRatio + '%' }"></i>
        </div>
        <div v-if="extraGain > 0" class="s">另含导入/手动 +{{ Math.round(extraGain) }}（不计入上限）</div>
      </div>
      <div class="stat">
        <span class="k">今日扣分</span>
        <span class="v mono">{{ today?.loss ?? 0 }} / {{ today?.loss_cap ?? "-" }}</span>
        <div class="bar" style="margin-top: 6px">
          <i :style="{ width: todayLossRatio + '%' }"></i>
        </div>
        <div v-if="extraLoss > 0" class="s">另含导入/手动 -{{ Math.round(extraLoss) }}（不计入上限）</div>
      </div>
      <div class="stat">
        <span class="k">今日事件</span>
        <span class="v mono">{{ today?.events ?? 0 }}</span>
        <span class="s">次计分（纯指令不计）</span>
      </div>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="card-t">当前心情</div>
        <div class="spacer"></div>
        <div class="card-s">属于角色自己，不是对某人的态度 · {{ mood?.half_life_minutes ?? 90 }} 分钟半衰期</div>
      </div>
      <div v-if="mood?.enabled" class="row">
        <span class="pill">{{ mood.description }}</span>
        <n-tag v-for="axis in moodAxes" :key="axis.key" size="small" round :bordered="false">
          {{ axis.label }} {{ axis.value }} / 10
        </n-tag>
        <div class="spacer"></div>
        <span class="faint">更新于 {{ timeAgo(mood.updated_at) }}</span>
      </div>
      <div v-else class="empty">心情未启用（配置 → 心情）</div>
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
          <div class="card-s">分数全局一份，这里看每个群挣了多少</div>
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
            <tr
              v-for="group in detail.groups"
              :key="group.group_id"
              class="clickable"
              @click="router.push(`/groups/${group.group_id}`)"
            >
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

    <div v-if="detail?.personas?.length" class="card">
      <div class="card-h">
        <div class="card-t">人格贡献</div>
        <div class="spacer"></div>
        <div class="card-s">这份分数由哪些人格挣来（角色合并后会不止一个）</div>
      </div>
      <table>
        <thead>
          <tr>
            <th>人格</th>
            <th style="width: 90px">贡献</th>
            <th style="width: 80px">事件</th>
            <th style="width: 110px">最近</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in detail?.personas || []" :key="item.persona_id">
            <td class="mono">{{ item.persona_id }}</td>
            <td class="mono" :class="item.delta >= 0 ? 'pos' : 'neg'">{{ signed(item.delta) }}</td>
            <td class="mono">{{ item.events }}</td>
            <td class="faint">{{ timeAgo(item.last_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="card-t">事件流水</div>
        <div class="spacer"></div>
        <div class="card-s">最近 50 条 · 数据版本 {{ snap.revision }}</div>
      </div>
      <table>
        <thead>
          <tr>
            <th style="width: 110px">时间</th>
            <th style="width: 110px">事件</th>
            <th style="width: 80px">变化</th>
            <th style="width: 120px">分数</th>
            <th>来源 / 说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="event in detail?.events || []" :key="event.id">
            <td class="faint">{{ fmtTime(event.created_at) }}</td>
            <td><span class="pill">{{ codeLabel(event.code) }}</span></td>
            <td class="mono" :class="event.delta >= 0 ? 'pos' : 'neg'">{{ signed(event.delta) }}</td>
            <td class="faint mono">{{ Math.round(event.before) }} → {{ Math.round(event.after) }}</td>
            <td class="muted">
              {{ event.source }}
              <span v-if="event.persona_id" class="faint">· {{ event.persona_id }}</span>
              <span v-if="event.operator" class="faint">· {{ event.operator }}</span>
              <span v-if="event.group_name" class="faint">· {{ event.group_name }}</span>
              <span v-if="event.evidence" class="faint">· {{ event.evidence }}</span>
            </td>
          </tr>
          <tr v-if="!(detail?.events || []).length">
            <td colspan="5"><div class="empty">暂无流水</div></td>
          </tr>
        </tbody>
      </table>
    </div>
  </template>

  <ScoreDialog
    v-if="editing && asRow"
    :user="asRow"
    :role="app.role"
    @close="editing = false"
    @saved="saved"
  />
</template>
