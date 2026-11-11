<script setup lang="ts">
import { ref } from "vue";
import { apiResetScore, apiSetScore, type UserRow } from "../api";
import { notify } from "../state";

const props = defineProps<{ user: UserRow; role: string; maxScore?: number }>();
const emit = defineEmits<{ (e: "close"): void; (e: "saved"): void }>();

const mode = ref<"delta" | "set">("delta");
const value = ref<number>(10);
const reason = ref("");
const busy = ref(false);

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
  if (!window.confirm(`确定把 ${props.user.display_name || props.user.user_id} 的分数清零？`)) return;
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
  <div class="mask" @click.self="emit('close')">
    <div class="dialog">
      <div class="card-h" style="margin: 0">
        <div class="card-t">调整好感度</div>
        <div class="spacer"></div>
        <span class="pill">{{ props.user.score }} 分</span>
      </div>
      <div class="muted">
        {{ props.user.display_name || props.user.user_id }} · {{ props.user.user_id }}
      </div>

      <div class="field">
        <label>方式</label>
        <div class="row">
          <button class="chip" :class="{ on: mode === 'delta' }" @click="mode = 'delta'">增减</button>
          <button class="chip" :class="{ on: mode === 'set' }" @click="mode = 'set'">设为指定值</button>
        </div>
      </div>

      <div class="field">
        <label>{{ mode === "delta" ? "增减多少（可填负数）" : "目标分数" }}</label>
        <input v-model.number="value" class="input" type="number" />
      </div>

      <div class="field">
        <label>原因（会记入审计）</label>
        <input v-model="reason" class="input" placeholder="例如：活动奖励 / 手动修正" />
      </div>

      <div class="row" style="justify-content: flex-end">
        <button class="btn ghost" @click="reset" :disabled="busy">清零</button>
        <button class="btn" @click="emit('close')" :disabled="busy">取消</button>
        <button class="btn primary" @click="submit" :disabled="busy">保存</button>
      </div>
    </div>
  </div>
</template>
