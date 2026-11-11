<script setup lang="ts">
import { computed, ref } from "vue";
import { apiResetScore, apiSetScore, type UserRow } from "../api";
import { notify } from "../state";

const props = defineProps<{ user: UserRow; role: string; maxScore?: number }>();
const emit = defineEmits<{ (e: "close"): void; (e: "saved"): void }>();

const visible = ref(true);
const mode = ref<"delta" | "set">("delta");
const value = ref<number>(10);
const reason = ref("");
const busy = ref(false);

const target = computed(() => props.user.display_name || props.user.user_id);

function close() {
  visible.value = false;
  emit("close");
}

async function submit() {
  busy.value = true;
  try {
    await apiSetScore({
      role: props.role,
      id: props.user.user_id,
      mode: mode.value,
      value: Number(value.value) || 0,
      reason: reason.value,
      name: props.user.display_name,
    });
    notify("已保存");
    emit("saved");
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

async function reset() {
  busy.value = true;
  try {
    await apiResetScore({ role: props.role, id: props.user.user_id, reason: reason.value || "手动重置" });
    notify("已重置");
    emit("saved");
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <n-modal :show="visible" @update:show="close">
    <div class="dialog">
      <div class="card-h" style="margin: 0">
        <div class="card-t">调整好感度</div>
        <div class="spacer"></div>
        <n-tag size="small" round :bordered="false">{{ user.score }} 分</n-tag>
      </div>
      <div class="muted">{{ target }} · {{ user.user_id }}</div>

      <div class="field">
        <label>方式</label>
        <n-radio-group v-model:value="mode" size="small">
          <n-radio-button value="delta">增减</n-radio-button>
          <n-radio-button value="set">设为指定值</n-radio-button>
        </n-radio-group>
      </div>

      <div class="field">
        <label>{{ mode === "delta" ? "增减多少（可填负数）" : "目标分数" }}</label>
        <n-input-number v-model:value="value" style="width: 100%" />
      </div>

      <div class="field">
        <label>原因（会记入审计）</label>
        <n-input v-model:value="reason" placeholder="例如：活动奖励 / 手动修正" />
      </div>

      <div class="row" style="justify-content: flex-end">
        <n-popconfirm negative-text="取消" positive-text="确认清零" @positive-click="reset">
          <template #trigger>
            <n-button quaternary :disabled="busy">清零</n-button>
          </template>
          清零后分数从 0 重新积累，历史流水保留。
        </n-popconfirm>
        <n-button @click="close" :disabled="busy">取消</n-button>
        <n-button type="primary" @click="submit" :disabled="busy">保存</n-button>
      </div>
    </div>
  </n-modal>
</template>
