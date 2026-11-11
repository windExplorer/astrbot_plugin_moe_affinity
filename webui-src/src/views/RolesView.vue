<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import {
  apiMergeRoles,
  apiRenameRole,
  apiRolePersonas,
  apiRoles,
  type RolePersonaRow,
  type RoleRow,
} from "../api";
import { app, bootstrap, notify } from "../state";

const roles = ref<RoleRow[]>([]);
const personas = ref<RolePersonaRow[]>([]);
const busy = ref(false);
const editing = ref<string>("");
const draftName = ref("");
const mergeSource = ref<RoleRow | null>(null);
const mergeTarget = ref("");
const mergeVisible = ref(false);

async function load() {
  busy.value = true;
  try {
    const [roleRes, personaRes] = await Promise.all([apiRoles(), apiRolePersonas()]);
    roles.value = roleRes.items;
    personas.value = personaRes.items;
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

function startRename(role: RoleRow) {
  editing.value = role.role_id;
  draftName.value = role.display_name || role.role_id;
}

async function commitRename(role: RoleRow) {
  const name = draftName.value.trim();
  if (!name || name === role.display_name) {
    editing.value = "";
    return;
  }
  try {
    await apiRenameRole(role.role_id, name);
    notify("已改名");
    editing.value = "";
    await load();
    await bootstrap(true);
  } catch (e: any) {
    notify(e?.message || String(e), true);
  }
}

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
    .map((role) => ({ label: role.display_name || role.role_id, value: role.role_id })),
);

onMounted(load);
</script>

<template>
  <div class="card">
    <div class="card-h">
      <div class="card-t">角色</div>
      <div class="spacer"></div>
      <div class="card-s">角色是人格的可归并外壳；归并后共享一份分数（取最高）</div>
    </div>

    <table>
      <thead>
        <tr>
          <th>角色</th>
          <th style="width: 90px">人格数</th>
          <th style="width: 80px">用户数</th>
          <th style="width: 210px"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="role in roles" :key="role.role_id">
          <td>
            <div v-if="editing === role.role_id" class="row">
              <n-input v-model:value="draftName" size="small" style="width: 180px" />
              <n-button size="small" type="primary" @click="commitRename(role)">保存</n-button>
              <n-button size="small" quaternary @click="editing = ''">取消</n-button>
            </div>
            <div v-else>
              <div style="font-weight: 550">{{ role.display_name || role.role_id }}</div>
              <div class="faint">{{ role.role_id }}</div>
            </div>
          </td>
          <td class="mono">{{ personaCount(role.role_id) }}</td>
          <td class="mono">{{ role.users }}</td>
          <td>
            <div class="row" v-if="editing !== role.role_id">
              <n-button size="small" quaternary @click="startRename(role)">改名</n-button>
              <n-button size="small" quaternary :disabled="roles.length < 2" @click="startMerge(role)">
                并入其它角色
              </n-button>
            </div>
          </td>
        </tr>
        <tr v-if="!roles.length">
          <td colspan="4"><div class="empty">{{ busy ? "加载中…" : "还没有角色（先让机器人聊几句）" }}</div></td>
        </tr>
      </tbody>
    </table>
  </div>

  <div class="card">
    <div class="card-h">
      <div class="card-t">人格 → 角色</div>
      <div class="spacer"></div>
      <div class="card-s">人格删掉后映射仍保留，分数不会丢</div>
    </div>
    <table>
      <thead>
        <tr>
          <th>人格</th>
          <th style="width: 110px">状态</th>
          <th>归属角色</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in personas" :key="item.persona_id">
          <td class="mono">{{ item.persona_id }}</td>
          <td>
            <n-tag size="small" round :bordered="false" :type="item.persona_exists ? 'success' : 'warning'">
              {{ item.persona_exists ? "存在" : "已移除" }}
            </n-tag>
          </td>
          <td>{{ item.role_name }}</td>
        </tr>
        <tr v-if="!personas.length">
          <td colspan="3"><div class="empty">还没有人格映射</div></td>
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
