<script setup lang="ts">
import { computed } from "vue";
import { avatarOf } from "../lib/avatars";

const props = withDefaults(
  defineProps<{ id: string; kind?: "user" | "group"; name?: string; size?: number }>(),
  { kind: "user", size: 36 },
);

const src = computed(() => avatarOf(props.kind, props.id));
const initial = computed(() => (props.name || props.id || "?").trim().slice(0, 1).toUpperCase());
const style = computed(() => ({
  width: `${props.size}px`,
  height: `${props.size}px`,
  flexBasis: `${props.size}px`,
  borderRadius: `${Math.round(props.size / 3)}px`,
  fontSize: `${Math.max(11, Math.round(props.size * 0.42))}px`,
}));
</script>

<template>
  <span class="avatar" :style="style">
    <img v-if="src" :src="src" alt="" />
    <template v-else>{{ initial }}</template>
  </span>
</template>
