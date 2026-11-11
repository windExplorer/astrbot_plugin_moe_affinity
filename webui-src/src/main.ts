import { createApp } from "vue";
import { createRouter, createWebHashHistory } from "vue-router";
import App from "./App.vue";
import "./styles.css";
import { bootstrap } from "./state";

import OverviewView from "./views/OverviewView.vue";
import UsersView from "./views/UsersView.vue";
import UserView from "./views/UserView.vue";
import GroupsView from "./views/GroupsView.vue";
import GroupView from "./views/GroupView.vue";
import RankView from "./views/RankView.vue";
import RolesView from "./views/RolesView.vue";
import DataView from "./views/DataView.vue";
import ConfigView from "./views/ConfigView.vue";
import AuditView from "./views/AuditView.vue";

// hash 路由：AstrBot 静态资源按真实文件路径解析，history 模式刷新会 404。
// 同步导入 —— vite 配置里 inlineDynamicImports 是有意为之，懒加载在这里没有收益。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: "/", redirect: "/overview" },
    { path: "/overview", name: "overview", component: OverviewView, meta: { title: "总览" } },
    { path: "/users", name: "users", component: UsersView, meta: { title: "用户" } },
    { path: "/users/:id", name: "user", component: UserView, meta: { title: "用户详情" } },
    { path: "/groups", name: "groups", component: GroupsView, meta: { title: "群聊" } },
    { path: "/groups/:id", name: "group", component: GroupView, meta: { title: "群详情" } },
    { path: "/rank", name: "rank", component: RankView, meta: { title: "排行" } },
    { path: "/roles", name: "roles", component: RolesView, meta: { title: "角色" } },
    { path: "/data", name: "data", component: DataView, meta: { title: "数据迁移" } },
    { path: "/config", name: "config", component: ConfigView, meta: { title: "配置" } },
    { path: "/audit", name: "audit", component: AuditView, meta: { title: "审计" } },
    { path: "/:pathMatch(.*)*", redirect: "/overview" },
  ],
});

router.beforeEach(async () => {
  await bootstrap();
});

createApp(App).use(router).mount("#app");
