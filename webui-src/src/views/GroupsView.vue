<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { apiGroups, apiSyncGroups, type GroupRow } from "../api";
import { app, notify } from "../state";
import { signed, timeAgo } from "../lib/format";

const router = useRouter();
const rows = ref<GroupRow[]>([]);
const busy = ref(false);
const syncing = ref(false);

async function load() {
  if (!app.role) return;
  busy.value = true;
  try {
    rows.value = (await apiGroups(app.role)).items;
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

async function sync() {
  syncing.value = true;
  try {
    const res = await apiSyncGroups();
    notify(res.ok ? `已同步 ${res.count} 个群名` : res.errors?.[0] || "同步失败", !res.ok);
    await load();
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    syncing.value = false;
  }
}

onMounted(load);
watch(() => app.role, load);
</script>

<template>
  <div class="card">
    <div class="row" style="margin-bottom: 12px">
      <div class="card-t">群聊贡献</div>
      <div class="spacer"></div>
      <span class="faint">分数是全局一份，这里统计每个群贡献了多少</span>
      <button class="btn ghost" :disabled="syncing" @click="sync">
        {{ syncing ? "同步中…" : "同步群名" }}
      </button>
    </div>

    <table>
      <thead>
        <tr>
          <th>群</th>
          <th style="width: 90px">人数</th>
          <th style="width: 100px">贡献</th>
          <th style="width: 80px">事件</th>
          <th style="width: 110px">最近活跃</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.group_id"
          class="clickable"
          @click="router.push(`/groups/${row.group_id}`)"
        >
          <td>
            <div style="font-weight: 550">{{ row.group_name || "未命名群" }}</div>
            <div class="faint">{{ row.group_id }}</div>
          </td>
          <td class="mono">{{ row.users }}</td>
          <td class="mono" :class="row.delta >= 0 ? 'pos' : 'neg'">{{ signed(row.delta) }}</td>
          <td class="mono">{{ row.events }}</td>
          <td class="faint">{{ timeAgo(row.last_at) }}</td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="5"><div class="empty">{{ busy ? "加载中…" : "还没有群聊记录" }}</div></td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
