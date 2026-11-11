<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { apiUsers, type UserRow } from "../api";
import { app, notify } from "../state";
import { initials, timeAgo } from "../lib/format";
import ScoreDialog from "../components/ScoreDialog.vue";

const route = useRoute();
const router = useRouter();

const rows = ref<UserRow[]>([]);
const total = ref(0);
const busy = ref(false);
const keyword = ref(String(route.query.q || ""));
const adjustTarget = ref<UserRow | null>(null);

const page = computed(() => Number(route.query.page || 1));
const size = computed(() => Number(route.query.size || 50));
const stage = computed(() => String(route.query.stage || ""));
const sort = computed(() => String(route.query.sort || "score"));


const STAGES = [
  { key: "", label: "全部" },
  { key: "bonded", label: "羁绊" },
  { key: "intimate", label: "亲密" },
  { key: "close", label: "亲近" },
  { key: "familiar", label: "熟悉" },
  { key: "known", label: "眼熟" },
  { key: "stranger", label: "陌生" },
];

const SORTS = [
  { label: "按分数", value: "score" },
  { label: "按最近互动", value: "recent" },
  { label: "按昵称", value: "name" },
];

async function load() {
  if (!app.role) return;
  busy.value = true;
  try {
    const res = await apiUsers({
      role: app.role,
      q: String(route.query.q || ""),
      stage: stage.value,
      sort: sort.value,
      page: page.value,
      size: size.value,
    });
    rows.value = res.rows;
    total.value = res.total;
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

function patch(query: Record<string, any>) {
  router.replace({ query: { ...route.query, ...query } });
}

function submitSearch() {
  patch({ q: keyword.value, page: 1 });
}

let timer: ReturnType<typeof setTimeout> | null = null;
watch(keyword, () => {
  if (timer) clearTimeout(timer);
  timer = setTimeout(submitSearch, 320);
});

onMounted(load);
watch(() => [app.role, route.query.q, route.query.stage, route.query.sort, route.query.page], load);

function openUser(row: UserRow) {
  router.push({ path: `/users/${row.user_id}`, query: { ...route.query } });
}

function onSaved() {
  adjustTarget.value = null;
  load();
}

const pages = computed(() => Math.max(1, Math.ceil(total.value / size.value)));
</script>

<template>
  <div class="card">
    <div class="row" style="margin-bottom: 12px">
      <n-input
        v-model:value="keyword"
        style="width: 260px"
        clearable
        placeholder="搜索昵称或 QQ 号"
      />
      <n-button secondary @click="submitSearch">搜索</n-button>
      <div class="spacer"></div>
      <n-select
        style="width: 150px"
        :value="sort"
        :options="SORTS"
        @update:value="(value: string) => patch({ sort: value, page: 1 })"
      />
    </div>

    <div class="row" style="margin-bottom: 12px">
      <n-tag
        v-for="item in STAGES"
        :key="item.key"
        :checked="stage === item.key"
        checkable
        round
        :bordered="false"
        size="small"
        @update:checked="patch({ stage: item.key, page: 1 })"
      >
        {{ item.label }}
      </n-tag>
      <div class="spacer"></div>
      <span class="faint">共 {{ total }} 人</span>
    </div>

    <table>
      <thead>
        <tr>
          <th style="width: 52px">#</th>
          <th>用户</th>
          <th style="width: 200px">分数</th>
          <th style="width: 90px">档位</th>
          <th style="width: 110px">最近互动</th>
          <th style="width: 96px"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.user_id" class="clickable" @click="openUser(row)">
          <td class="faint mono">{{ row.rank }}</td>
          <td>
            <div class="who">
              <span class="avatar">{{ initials(row.display_name, row.user_id) }}</span>
              <div style="min-width: 0">
                <div class="n">{{ row.display_name || row.user_id }}</div>
                <div class="i">{{ row.user_id }}</div>
              </div>
            </div>
          </td>
          <td>
            <div class="row">
              <div class="bar" style="width: 110px">
                <i :style="{ width: Math.min(100, row.progress * 100) + '%' }"></i>
              </div>
              <span class="mono">{{ row.score }}</span>
            </div>
          </td>
          <td><n-tag size="small" round :bordered="false">{{ row.stage_label }}</n-tag></td>
          <td class="faint">{{ timeAgo(row.last_interaction_at) }}</td>
          <td>
            <n-button size="small" quaternary @click.stop="adjustTarget = row">调整</n-button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="6">
            <div class="empty">{{ busy ? "加载中…" : "没有匹配的用户" }}</div>
          </td>
        </tr>
      </tbody>
    </table>

    <div class="pager" v-if="pages > 1">
      <n-button size="small" quaternary :disabled="page <= 1" @click="patch({ page: page - 1 })">上一页</n-button>
      <span class="faint">{{ page }} / {{ pages }}</span>
      <n-button size="small" quaternary :disabled="page >= pages" @click="patch({ page: page + 1 })">下一页</n-button>
    </div>
  </div>

  <ScoreDialog
    v-if="adjustTarget"
    :user="adjustTarget"
    :role="app.role"
    @close="adjustTarget = null"
    @saved="onSaved"
  />
</template>
