<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { apiConfig, apiConflicts, apiSetConfig, type ConfigPayload, type ConflictRow } from "../api";
import { notify } from "../state";

const payload = ref<ConfigPayload | null>(null);
const draft = ref<Record<string, any>>({});
const busy = ref(false);
const conflicts = ref<ConflictRow[]>([]);

const GROUP_META: Record<string, { title: string; desc: string }> = {
  enabled: { title: "总开关", desc: "关闭后不计分、不注入，已有数据保留" },
  affinity: { title: "数值规则", desc: "上限、每日上限、闲置衰减" },
  scoring: { title: "计分规则", desc: "哪些行为加分、哪些扣分" },
  mood: { title: "心情", desc: "角色自己的四轴与半衰期" },
  inject: { title: "对话注入", desc: "注入多少、是否带分数" },
  advanced: { title: "高级", desc: "去重窗口与排障日志" },
};

const groups = computed(() => {
  const flat = payload.value?.flat || [];
  const buckets: Record<string, { key: string; spec: any }[]> = {};
  for (const entry of flat) {
    const top = entry.key.split(".")[0];
    (buckets[top] = buckets[top] || []).push(entry);
  }
  return Object.entries(buckets).map(([key, items]) => ({
    key,
    title: GROUP_META[key]?.title || key,
    desc: GROUP_META[key]?.desc || "",
    items,
  }));
});

function specOf(key: string): any {
  return payload.value?.flat.find((item) => item.key === key)?.spec || {};
}

function labelOf(key: string): string {
  return specOf(key).description || key.split(".").pop();
}

function hintOf(key: string): string {
  return specOf(key).hint || "";
}

function typeOf(key: string): string {
  return specOf(key).type || "string";
}

const selectOptions = (key: string) =>
  (specOf(key).options || []).map((option: string) => ({ label: option, value: option }));

async function load() {
  busy.value = true;
  try {
    const [cfg, conflict] = await Promise.all([apiConfig(), apiConflicts()]);
    payload.value = cfg;
    draft.value = { ...cfg.items };
    conflicts.value = conflict.items || [];
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

async function save() {
  if (!payload.value) return;
  const changed: Record<string, any> = {};
  for (const entry of payload.value.flat) {
    const key = entry.key;
    if (JSON.stringify(draft.value[key]) !== JSON.stringify(payload.value.items[key])) {
      changed[key] = draft.value[key];
    }
  }
  if (!Object.keys(changed).length) {
    notify("没有改动");
    return;
  }
  busy.value = true;
  try {
    const res = await apiSetConfig(changed);
    notify(`已保存 ${res.changed.length} 项`);
    await load();
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="row">
    <div class="card-t">插件配置</div>
    <div class="spacer"></div>
    <n-button size="small" quaternary :loading="busy" @click="load">重新读取</n-button>
    <n-button size="small" type="primary" :loading="busy" @click="save">保存</n-button>
  </div>

  <div v-if="conflicts.length" class="card">
    <div class="card-h">
      <div class="card-t">冲突检测</div>
      <div class="spacer"></div>
      <div class="card-s">其它插件若仍在管好感度，会与这里形成两套数字</div>
    </div>
    <div style="display: flex; flex-direction: column; gap: 10px">
      <div v-for="item in conflicts" :key="item.plugin + item.key" class="row">
        <n-tag size="small" round :bordered="false" :type="item.level === 'warn' ? 'warning' : 'success'">
          {{ item.level === "warn" ? "建议关闭" : "已关闭" }}
        </n-tag>
        <span class="muted" style="flex: 1">{{ item.note }}</span>
        <code class="faint">{{ item.plugin }} · {{ item.key }} = {{ item.value }}</code>
      </div>
    </div>
  </div>

  <div v-for="group in groups" :key="group.key" class="card">
    <div class="card-h">
      <div class="card-t">{{ group.title }}</div>
      <div class="spacer"></div>
      <div class="card-s">{{ group.desc }}</div>
    </div>

    <div class="grid cols-2">
      <div v-for="entry in group.items" :key="entry.key" class="field">
        <label>{{ labelOf(entry.key) }}</label>

        <n-switch v-if="typeOf(entry.key) === 'bool'" v-model:value="draft[entry.key]" />

        <n-select
          v-else-if="typeOf(entry.key) === 'string' && selectOptions(entry.key).length"
          v-model:value="draft[entry.key]"
          :options="selectOptions(entry.key)"
        />

        <n-input-number
          v-else-if="typeOf(entry.key) === 'int' || typeOf(entry.key) === 'float'"
          v-model:value="draft[entry.key]"
          style="width: 100%"
        />

        <n-dynamic-tags
          v-else-if="typeOf(entry.key) === 'list'"
          v-model:value="draft[entry.key]"
          :max="50"
        />

        <n-input v-else v-model:value="draft[entry.key]" />

        <span v-if="hintOf(entry.key)" class="faint" style="font-size: 12px">{{ hintOf(entry.key) }}</span>
      </div>
    </div>
  </div>
</template>
