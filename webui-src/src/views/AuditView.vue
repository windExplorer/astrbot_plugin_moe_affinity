<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { apiAudit, type AuditRow } from "../api";
import { notify } from "../state";
import { codeLabel, fmtTime, signed } from "../lib/format";

const route = useRoute();
const router = useRouter();
const rows = ref<AuditRow[]>([]);
const total = ref(0);
const busy = ref(false);

const page = computed(() => Number(route.query.page || 1));
const size = 50;
const pages = computed(() => Math.max(1, Math.ceil(total.value / size)));

async function load() {
  busy.value = true;
  try {
    const res = await apiAudit(page.value, size);
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

onMounted(load);
watch(() => route.query.page, load);
</script>

<template>
  <div class="card">
    <div class="card-h">
      <div class="card-t">管理员操作审计</div>
      <div class="spacer"></div>
      <div class="card-s">手动调整 / 重置 / 归并 / 导入，共 {{ total }} 条</div>
    </div>

    <table>
      <thead>
        <tr>
          <th style="width: 110px">时间</th>
          <th style="width: 100px">动作</th>
          <th>用户</th>
          <th style="width: 70px">变化</th>
          <th style="width: 120px">前后</th>
          <th style="width: 110px">操作者</th>
          <th>说明</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id">
          <td class="faint">{{ fmtTime(row.created_at) }}</td>
          <td><span class="pill">{{ codeLabel(row.code) }}</span></td>
          <td class="mono">{{ row.user_id }}</td>
          <td class="mono" :class="row.delta >= 0 ? 'pos' : 'neg'">{{ signed(row.delta) }}</td>
          <td class="faint mono">{{ Math.round(row.before) }} → {{ Math.round(row.after) }}</td>
          <td class="muted">{{ row.operator || row.source }}</td>
          <td class="muted">{{ row.evidence || "—" }}</td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="7"><div class="empty">{{ busy ? "加载中…" : "还没有管理员操作记录" }}</div></td>
        </tr>
      </tbody>
    </table>

    <div class="pager" v-if="pages > 1">
      <n-button size="small" quaternary :disabled="page <= 1" @click="patch({ page: page - 1 })">上一页</n-button>
      <span class="faint">{{ page }} / {{ pages }}</span>
      <n-button size="small" quaternary :disabled="page >= pages" @click="patch({ page: page + 1 })">下一页</n-button>
    </div>
  </div>
</template>
