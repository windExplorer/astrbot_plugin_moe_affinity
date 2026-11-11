<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { apiGroups, apiSync, type GroupRow } from "../api";
import { app, notify } from "../state";
import { signed, timeAgo } from "../lib/format";

const router = useRouter();
const rows = ref<GroupRow[]>([]);
const busy = ref(false);
const syncing = ref(false);
const missingGroups = ref(0);

async function load() {
  if (!app.role) return;
  busy.value = true;
  try {
    const res = await apiGroups(app.role);
    rows.value = res.items;
    missingGroups.value = res.missing_names || 0;
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

async function sync() {
  syncing.value = true;
  try {
    const res = await apiSync(true);
    const calibrated = res.calibrated > 0 ? `，认识时间校准 ${res.calibrated} 人` : "";
    notify(
      res.ok
        ? `已同步 ${res.groups} 个群、「${res.renamed}」条昵称（含群成员）${calibrated}`
        : res.errors?.[0] || "同步失败",
      !res.ok,
    );
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
      <n-button size="small" quaternary :loading="syncing" @click="sync">同步群名与成员</n-button>
    </div>

    <n-alert style="margin-bottom: 12px" :bordered="false" type="default">
      同步会顺带用**群成员的入群时间**校准「认识天数」（取各群最早，且只会把时间往早修）；
      bot 自己比某人晚入群时，按 bot 入群那一刻算起。
    </n-alert>

    <n-alert v-if="missingGroups" style="margin-bottom: 12px" :bordered="false" type="warning">
      还有 {{ missingGroups }} 个群没有群名。点「同步群名与成员」补齐；bot 已退出的群拿不到名字。
    </n-alert>

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
            <div class="who">
              <Avatar :id="row.group_id" kind="group" :name="row.group_name || row.group_id" />
              <div style="min-width: 0">
                <div class="n" style="font-weight: 550">{{ row.group_name || "未命名群" }}</div>
                <div class="i">{{ row.group_id }}</div>
              </div>
            </div>
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
