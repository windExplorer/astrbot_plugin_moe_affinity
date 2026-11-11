<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts";

const props = withDefaults(defineProps<{ option: any; height?: string }>(), { height: "240px" });

const host = ref<HTMLDivElement | null>(null);
let chart: echarts.ECharts | null = null;
let observer: ResizeObserver | null = null;

function render() {
  if (!host.value) return;
  if (!chart) chart = echarts.init(host.value, undefined, { renderer: "canvas" });
  chart.setOption(props.option || {}, true);
}

onMounted(() => {
  render();
  if (host.value && typeof ResizeObserver !== "undefined") {
    observer = new ResizeObserver(() => chart?.resize());
    observer.observe(host.value);
  }
});

watch(() => props.option, render, { deep: true });

onBeforeUnmount(() => {
  observer?.disconnect();
  chart?.dispose();
  chart = null;
});
</script>

<template>
  <div ref="host" :style="{ height: props.height, width: '100%' }"></div>
</template>
