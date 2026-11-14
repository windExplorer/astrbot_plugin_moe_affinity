<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  apiMergeRoles,
  apiRolePersonas,
  apiRoles,
  apiSetRoleAlias,
  type RolePersonaRow,
  type RoleRow,
} from "../api";
import { app, bootstrap, notify } from "../state";
import { timeAgo } from "../lib/format";

const roles = ref<RoleRow[]>([]);
const personas = ref<RolePersonaRow[]>([]);
const busy = ref(false);
const editing = ref<string>("");
const draftName = ref("");
const mergeSource = ref<RoleRow | null>(null);
const mergeTarget = ref("");
const mergeVisible = ref(false);
const onlyInUse = ref(true);
const defaultPersona = ref("");

async function load() {
  busy.value = true;
  try {
    const [roleRes, personaRes] = await Promise.all([apiRoles(), apiRolePersonas()]);
    roles.value = roleRes.items;
    personas.value = personaRes.items;
    defaultPersona.value = roleRes.default_persona || "";
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

const rolesView = computed(() =>
  onlyInUse.value ? roles.value.filter((role) => role.in_use) : roles.value,
);
const personasView = computed(() =>
  onlyInUse.value ? personas.value.filter((item) => item.in_use) : personas.value,
);
const unusedRoles = computed(() => roles.value.filter((role) => !role.in_use).length);
const unusedPersonas = computed(() => personas.value.filter((item) => !item.in_use).length);

function startAlias(role: RoleRow) {
  editing.value = role.role_id;
  draftName.value = role.alias || "";
}

async function commitAlias(role: RoleRow) {
  const alias = draftName.value.trim();
  if (alias === (role.alias || "")) {
    editing.value = "";
    return;
  }
  try {
    await apiSetRoleAlias(role.role_id, alias);
    notify(alias ? `别名已设为「${alias}」` : "已清除别名");
    editing.value = "";
    await load();
    await bootstrap(true);
  } catch (e: any) {
    notify(e?.message || String(e), true);
  }
}

const roleTitle = (role: RoleRow) => role.alias || role.display_name || role.role_id;

function startMerge(role: RoleRow) {
  mergeSource.value = role;
  mergeTarget.value = roles.value.find((r) => r.role_id !== role.role_id)?.role_id || "";
  mergeVisible.value = true;
}

async function commitMerge() {
  if (!mergeSource.value || !mergeTarget.value) return;
  try {
    const res = await apiMergeRoles(mergeTarget.value, mergeSource.value.role_id);
    notify(`已合并：移动 ${res.moved} 人，合并同人 ${res.merged} 份（取最高）`);
    mergeVisible.value = false;
    mergeSource.value = null;
    await bootstrap(true);
    await load();
  } catch (e: any) {
    notify(e?.message || String(e), true);
  }
}

const personaCount = (roleId: string) => personas.value.filter((p) => p.role_id === roleId).length;

const targetOptions = computed(() =>
  roles.value
    .filter((role) => role.role_id !== mergeSource.value?.role_id)
    .map((role) => ({
      label: `${roleTitle(role)}${role.in_use ? "" : "（未使用）"}`,
      value: role.role_id,
    })),
);

onMounted(load);
</script>

<template>
  <div class="card">
    <div class="card-h">
      <div class="card-t">角色</div>
      <n-tag size="small" round :bordered="false">使用中 {{ roles.length - unusedRoles }}</n-tag>
      <n-tag v-if="unusedRoles" size="small" round :bordered="false" type="warning">
        未使用 {{ unusedRoles }}
      </n-tag>
      <div class="spacer"></div>
      <div class="row">
        <n-switch v-model:value="onlyInUse" size="small" />
        <span class="muted">只看使用中</span>
      </div>
    </div>

    <p class="faint" style="margin: 0 0 12px">
      当前 AstrBot 默认人格：<span class="mono">{{ defaultPersona || "—" }}</span>（带「默认」标签的角色就是它，
      改默认人格会实时跟着变，不用重启）。别名会用在指令输出和控制台里；没设别名时，指令退回显示 QQ 昵称。
      「使用中」= 当前默认人格、有会话绑定到该人格、或已有分数记录；三者都没有的空壳不算，
      关掉上面的开关可以看到全部。
    </p>

    <table>
      <thead>
        <tr>
          <th>角色</th>
          <th style="width: 90px">人格数</th>
          <th style="width: 80px">会话</th>
          <th style="width: 80px">用户数</th>
          <th style="width: 110px">最近活跃</th>
          <th style="width: 210px"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="role in rolesView" :key="role.role_id">
          <td>
            <div v-if="editing === role.role_id" class="row">
              <n-input v-model:value="draftName" size="small" style="width: 180px" placeholder="留空 = 使用 QQ 昵称" />
              <n-button size="small" type="primary" @click="commitAlias(role)">保存</n-button>
              <n-button size="small" quaternary @click="editing = ''">取消</n-button>
            </div>
            <div v-else>
              <div class="row" style="gap: 6px">
                <span style="font-weight: 550">{{ roleTitle(role) }}</span>
                <n-tag v-if="role.is_default" size="tiny" round :bordered="false" type="success">默认</n-tag>
                <n-tag v-if="role.alias" size="tiny" round :bordered="false" type="info">别名</n-tag>
                <n-tag v-if="!role.in_use" size="tiny" round :bordered="false" type="warning">未使用</n-tag>
              </div>
              <div class="faint">
                {{ role.role_id }}<template v-if="role.alias && role.display_name"> · {{ role.display_name }}</template>
              </div>
            </div>
          </td>
          <td class="mono">{{ personaCount(role.role_id) }}</td>
          <td class="mono">{{ role.bound_sessions }}</td>
          <td class="mono">{{ role.users }}</td>
          <td class="faint">{{ role.last_active_at ? timeAgo(role.last_active_at) : "—" }}</td>
          <td>
            <div class="row" v-if="editing !== role.role_id">
              <n-button size="small" quaternary @click="startAlias(role)">设置别名</n-button>
              <n-button size="small" quaternary :disabled="roles.length < 2" @click="startMerge(role)">
                并入其它角色
              </n-button>
            </div>
          </td>
        </tr>
        <tr v-if="!rolesView.length">
          <td colspan="5">
            <div class="empty">
              {{ busy ? "加载中…" : onlyInUse ? "还没有使用中的角色（先让机器人聊几句）" : "还没有角色" }}
            </div>
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <div class="card-h">
      <div class="card-t">人格 → 角色</div>
      <div class="spacer"></div>
      <div class="card-s">
        人格删掉后映射仍保留，分数不会丢
        <template v-if="unusedPersonas"> · 另有 {{ unusedPersonas }} 个人格未使用</template>
      </div>
    </div>
    <table>
      <thead>
        <tr>
          <th>人格</th>
          <th style="width: 100px">人格状态</th>
          <th style="width: 110px">使用情况</th>
          <th>归属角色</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in personasView" :key="item.persona_id">
          <td class="mono">
            {{ item.persona_id }}
            <n-tag v-if="item.is_default" size="tiny" round :bordered="false" type="success">默认</n-tag>
          </td>
          <td>
            <n-tag size="small" round :bordered="false" :type="item.persona_exists ? 'success' : 'warning'">
              {{ item.persona_exists ? "存在" : "已移除" }}
            </n-tag>
          </td>
          <td>
            <n-tag v-if="item.in_use" size="small" round :bordered="false">
              {{ item.bound_sessions }} 会话 · {{ item.users }} 人
            </n-tag>
            <n-tag v-else size="small" round :bordered="false" type="warning">未使用</n-tag>
          </td>
          <td>{{ item.role_name }}</td>
        </tr>
        <tr v-if="!personasView.length">
          <td colspan="4"><div class="empty">还没有人格映射</div></td>
        </tr>
      </tbody>
    </table>
  </div>

  <n-modal v-model:show="mergeVisible">
    <div class="dialog">
      <div class="card-h" style="margin: 0"><div class="card-t">合并角色</div></div>
      <p class="muted" style="margin: 0">
        把「{{ mergeSource?.display_name || mergeSource?.role_id }}」并入下面选中的角色。
        同一用户两份分数取最高，被并入角色的数据会整体移走，操作不可撤销。
      </p>
      <div class="field">
        <label>并入到</label>
        <n-select v-model:value="mergeTarget" :options="targetOptions" />
      </div>
      <div class="row" style="justify-content: flex-end">
        <n-button quaternary @click="mergeVisible = false">取消</n-button>
        <n-button type="primary" @click="commitMerge">确认合并</n-button>
      </div>
    </div>
  </n-modal>
</template>
