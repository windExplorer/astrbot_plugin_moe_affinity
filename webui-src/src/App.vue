<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { dateZhCN, darkTheme, themeOverrides, zhCN } from "./ui";
import { app, bootstrap, isDark, notify, roleName, setRole } from "./state";

const route = useRoute();
const router = useRouter();

const NAV = [
  { path: "/overview", label: "总览" },
  { path: "/users", label: "用户" },
  { path: "/groups", label: "群聊" },
  { path: "/rank", label: "排行" },
  { path: "/roles", label: "角色" },
  { path: "/data", label: "数据迁移" },
  { path: "/rules", label: "规则说明" },
  { path: "/config", label: "配置" },
  { path: "/audit", label: "审计" },
];

const active = computed(() => "/" + (route.path.split("/")[1] || "overview"));
const title = computed(() => (route.meta.title as string) || "总览");

const roleOptions = computed(() =>
  app.roles.map((role) => ({
    label: role.display_name || role.role_id,
    value: role.role_id,
  })),
);

function onRole(value: string) {
  setRole(value);
  router.replace({ query: { ...route.query, role: value } });
}

async function refresh() {
  await bootstrap(true);
  notify(app.ready ? "已刷新" : app.error, !app.ready);
}
</script>

<template>
  <n-config-provider
    :theme="isDark ? darkTheme : null"
    :theme-overrides="themeOverrides"
    :locale="zhCN"
    :date-locale="dateZhCN"
  >
    <div class="app">
      <aside class="side">
        <div class="brand">
          <div class="logo">萌</div>
          <div>
            <div class="brand-t">萌萌好感度</div>
            <div class="brand-s">{{ app.version || "…" }}</div>
          </div>
        </div>

        <nav class="nav">
          <router-link
            v-for="item in NAV"
            :key="item.path"
            :to="item.path"
            :class="{ on: active === item.path }"
          >
            <span class="dot"></span>{{ item.label }}
          </router-link>
        </nav>

        <div class="side-foot">
          <div class="field">
            <label>当前角色</label>
            <n-select
              :value="app.role || null"
              :options="roleOptions"
              size="small"
              placeholder="暂无角色"
              :consistent-menu-width="false"
              @update:value="onRole"
            />
          </div>
          <n-button size="small" secondary block @click="refresh">刷新数据</n-button>
          <div class="faint" style="font-size: 12px">
            数据库 {{ app.dbReady ? "正常" : "未就绪" }}
          </div>
        </div>
      </aside>

      <main class="main">
        <header class="topbar">
          <div class="page-title">{{ title }}</div>
          <n-tag size="small" round :bordered="false">{{ roleName(app.role) }}</n-tag>
          <div class="spacer"></div>
          <n-button size="small" quaternary @click="refresh">刷新</n-button>
        </header>

        <div v-if="app.loading" class="loading-line"></div>

        <section class="content">
          <div v-if="!app.ready" class="card">
            <div class="card-h"><div class="card-t">无法连接控制台</div></div>
            <p class="muted">{{ app.error || "正在等待 AstrBot 插件页桥接…" }}</p>
            <n-button type="primary" @click="refresh">重试</n-button>
          </div>
          <router-view v-else :key="app.role" />
        </section>
      </main>

      <n-alert v-if="app.toast" class="toast" :type="app.toastError ? 'error' : 'success'" :bordered="false">
        {{ app.toast }}
      </n-alert>
    </div>
  </n-config-provider>
</template>
