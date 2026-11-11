<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { apiMergeRoles, apiRenameRole, apiRolePersonas, apiRoles, type RolePersonaRow, type RoleRow } from "../api";
import { app, bootstrap, notify } from "../state";

const roles = ref<RoleRow[]>([]);
const personas = ref<RolePersonaRow[]>([]);
const busy = ref(false);
const editing = ref<string>("");
const draftName = ref("");
const mergeSource = ref<RoleRow | null>(null);
const mergeTarget = ref("");

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
}

async function commitMerge() {
  if (!mergeSource.value || !mergeTarget.value) return;
  try {
    const res = await apiMergeRoles(mergeTarget.value, mergeSource.value.role_id);
    notify(`已合并：移动 ${res.moved} 人，合并同人 ${res.merged} 份（取最高）`);
    mergeSource.value = null;
    await bootstrap(true);
    await load();
  } catch (e: any) {
    notify(e?.message || String(e), true);
  }
}

const personaCount = computed(() => (roleId: string) => personas.value.filter((p) => p.role_id === roleId).length);

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
          <th style="width: 190px"></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="role in roles" :key="role.role_id">
          <td>
            <div v-if="editing === role.role_id" class="row">
              <input v-model="draftName" class="input" style="width: 180px" />
              <button class="btn primary" @click="commitRename(role)">保存</button>
              <button class="btn ghost" @click="editing = ''">取消</button>
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
              <button class="btn ghost" @click="startRename(role)">改名</button>
              <button class="btn ghost" @click="startMerge(role)" :disabled="roles.length < 2">并入其它角色</button>
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
          <th>状态</th>
          <th>归属角色</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="item in personas" :key="item.persona_id">
          <td class="mono">{{ item.persona_id }}</td>
          <td>
            <span class="pill" :class="item.persona_exists ? 'ok' : 'warn'">
              {{ item.persona_exists ? "存在" : "已移除" }}
            </span>
          </td>
          <td>{{ item.role_name }}</td>
        </tr>
        <tr v-if="!personas.length">
          <td colspan="3"><div class="empty">还没有人格映射</div></td>
        </tr>
      </tbody>
    </table>
  </div>

  <div v-if="mergeSource" class="mask" @click.self="mergeSource = null">
    <div class="dialog">
      <div class="card-h" style="margin: 0">
        <div class="card-t">合并角色</div>
      </div>
      <p class="muted" style="margin: 0">
        把「{{ mergeSource.display_name || mergeSource.role_id }}」并入下面选中的角色。
        同一用户两份分数**取最高**，被并入角色的数据会整体移走，操作不可撤销。
      </p>
      <div class="field">
        <label>并入到</label>
        <select v-model="mergeTarget" class="select">
          <option
            v-for="role in roles.filter((r) => r.role_id !== mergeSource?.role_id)"
            :key="role.role_id"
            :value="role.role_id"
          >
            {{ role.display_name || role.role_id }}
          </option>
        </select>
      </div>
      <div class="row" style="justify-content: flex-end">
        <button class="btn ghost" @click="mergeSource = null">取消</button>
        <button class="btn primary" @click="commitMerge">确认合并</button>
      </div>
    </div>
  </div>
</template>
