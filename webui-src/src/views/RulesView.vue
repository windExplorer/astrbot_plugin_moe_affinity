<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { apiRules, type RulesData } from "../api";
import { notify } from "../state";

const data = ref<RulesData | null>(null);
const busy = ref(false);

async function load() {
  busy.value = true;
  try {
    data.value = await apiRules();
  } catch (e: any) {
    notify(e?.message || String(e), true);
  } finally {
    busy.value = false;
  }
}

const signed = (value: number) => `${value > 0 ? "+" : ""}${value}`;

const stageText = (stage: { floor: number; ceiling: number | null }) =>
  stage.ceiling === null ? `${stage.floor} 分以上` : `${stage.floor} – ${stage.ceiling}`;

const moodChangeText = (changes: [string, number][]) =>
  changes.map(([label, value]) => `${label} ${value > 0 ? "+" : ""}${value}`).join("，");

const hasThirdParty = computed(() => Boolean(data.value?.third_party?.available));

onMounted(load);
</script>

<template>
  <div class="row">
    <div class="card-t">好感度规则</div>
    <n-tag v-if="data?.version" size="small" round :bordered="false">当前版本 {{ data.version }}</n-tag>
    <div class="spacer"></div>
    <span v-if="busy" class="faint">加载中…</span>
    <n-button size="small" quaternary @click="load">刷新</n-button>
  </div>

  <template v-if="data">
    <div class="card">
      <div class="card-h">
        <div class="card-t">先看这三条</div>
      </div>
      <ol class="muted" style="margin: 0; padding-left: 20px; line-height: 1.9">
        <li>好感度按「<b>{{ data.scope.unit }}</b>」保存，一个人只有一份分数，群不另存。</li>
        <li>{{ data.timing }}</li>
        <li>分数无上限，但每天能涨的分数有上限（{{ data.daily.gain_cap }} 分），长期不聊会慢慢回落。</li>
      </ol>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="card-t">分数与档位</div>
        <div class="spacer"></div>
        <div class="card-s">{{ data.score.note }}</div>
      </div>
      <table>
        <thead>
          <tr>
            <th style="width: 120px">档位</th>
            <th style="width: 180px">分数区间</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="stage in data.score.stages" :key="stage.key">
            <td><n-tag size="small" round :bordered="false">{{ stage.label }}</n-tag></td>
            <td class="mono">{{ stageText(stage) }}</td>
            <td class="faint">
              {{ stage.key === "bonded" ? "顶档，没有上限" : `从 ${stage.floor} 分开始进入` }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="grid cols-3">
      <div class="stat">
        <span class="k">每日最多涨</span>
        <span class="v mono">{{ data.daily.gain_cap }}</span>
        <span class="s">分（核心计分合计）</span>
      </div>
      <div class="stat">
        <span class="k">每日最多扣</span>
        <span class="v mono">{{ data.daily.loss_cap }}</span>
        <span class="s">分（负向互动合计）</span>
      </div>
      <div class="stat">
        <span class="k">同类事件去重</span>
        <span class="v mono">{{ data.daily.dedupe_seconds }}</span>
        <span class="s">秒内只记一次</span>
      </div>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="card-t">什么行为会加分 / 扣分</div>
        <div class="spacer"></div>
        <div class="card-s">{{ data.daily.note }}</div>
      </div>
      <table>
        <thead>
          <tr>
            <th style="width: 160px">行为</th>
            <th style="width: 90px">变化</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in data.events" :key="row.code">
            <td>{{ row.label }}</td>
            <td class="mono" :class="row.delta >= 0 ? 'pos' : 'neg'">{{ signed(row.delta) }}</td>
            <td class="faint">{{ row.note }}</td>
          </tr>
        </tbody>
      </table>

      <div class="row" style="margin-top: 14px">
        <span class="muted">夸奖词</span>
        <template v-if="data.keywords.praise.length">
          <n-tag v-for="word in data.keywords.praise" :key="word" size="small" round :bordered="false">
            {{ word }}
          </n-tag>
        </template>
        <span v-else class="faint">未配置（夸奖加分关闭）</span>
      </div>
      <div class="row" style="margin-top: 8px">
        <span class="muted">负向词</span>
        <n-tag
          v-for="word in data.keywords.negative"
          :key="word"
          size="small"
          round
          :bordered="false"
          type="error"
        >
          {{ word }}
        </n-tag>
        <span v-if="!data.keywords.negative.length" class="faint">未配置（负向扣分关闭）</span>
      </div>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="card-t">长期不聊会回落</div>
        <div class="spacer"></div>
        <n-tag size="small" round :bordered="false" :type="data.decay.enabled ? 'success' : 'warning'">
          {{ data.decay.enabled ? "已启用" : "已关闭" }}
        </n-tag>
      </div>
      <table v-if="data.decay.enabled">
        <thead>
          <tr>
            <th style="width: 200px">连续无互动</th>
            <th style="width: 140px">每天扣</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in data.decay.rows" :key="row.range">
            <td>{{ row.range }}</td>
            <td class="mono" :class="row.per_day > 0 ? 'neg' : 'faint'">
              {{ row.per_day > 0 ? "−" + row.per_day : "不扣" }}
            </td>
          </tr>
        </tbody>
      </table>
      <p class="faint" style="margin: 10px 0 0">{{ data.decay.note }}</p>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="card-t">心情（角色自己的状态）</div>
        <div class="spacer"></div>
        <n-tag size="small" round :bordered="false" :type="data.mood.enabled ? 'success' : 'warning'">
          {{ data.mood.enabled ? "已启用" : "已关闭" }}
        </n-tag>
      </div>

      <div class="grid cols-4" style="margin-bottom: 14px">
        <div class="stat">
          <span class="k">回落半衰期</span>
          <span class="v mono">{{ data.mood.half_life_minutes }}</span>
          <span class="s">分钟</span>
        </div>
        <div class="stat">
          <span class="k">单轮最大变动</span>
          <span class="v mono">±{{ data.mood.max_delta }}</span>
          <span class="s">每轴</span>
        </div>
        <div class="stat">
          <span class="k">平静带</span>
          <span class="v mono">{{ data.mood.neutral_band }}</span>
          <span class="s">偏离不超过即算平静</span>
        </div>
        <div class="stat">
          <span class="k">注入对话</span>
          <span class="v">{{ data.mood.inject ? "开" : "关" }}</span>
          <span class="s">平静时不写</span>
        </div>
      </div>

      <table>
        <thead>
          <tr>
            <th style="width: 140px">触发</th>
            <th style="width: 260px">四轴变化</th>
            <th>说明</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in data.mood.rows" :key="row.trigger">
            <td>{{ row.trigger }}</td>
            <td class="mono">{{ moodChangeText(row.changes) }}</td>
            <td class="faint">{{ row.note }}</td>
          </tr>
        </tbody>
      </table>

      <div class="row" style="margin-top: 12px">
        <n-tag v-for="axis in data.mood.axes" :key="axis.key" size="small" round :bordered="false">
          {{ axis.label }}：{{ axis.rest }}
        </n-tag>
      </div>
      <p class="faint" style="margin: 10px 0 0">{{ data.mood.note }}</p>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="card-t">统计口径</div>
      </div>
      <table>
        <tbody>
          <tr>
            <td style="width: 140px" class="muted">人格</td>
            <td>{{ data.scope.persona }}</td>
          </tr>
          <tr>
            <td class="muted">群聊</td>
            <td>{{ data.scope.group }}</td>
          </tr>
          <tr>
            <td class="muted">计分时机</td>
            <td>{{ data.timing }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="card">
      <div class="card-h">
        <div class="card-t">分数来源</div>
        <div class="spacer"></div>
        <div class="card-s">未来第三方插件接入后，会在这里按来源分别列出规则</div>
      </div>

      <table>
        <thead>
          <tr>
            <th style="width: 180px">来源</th>
            <th style="width: 100px">类型</th>
            <th>事件</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="source in data.sources" :key="source.id">
            <td>{{ source.name }}</td>
            <td>
              <n-tag size="small" round :bordered="false">{{ source.type === "builtin" ? "内置" : source.type }}</n-tag>
            </td>
            <td class="faint">{{ source.events.join(" · ") }}（{{ source.note }}）</td>
          </tr>
        </tbody>
      </table>

      <n-alert style="margin-top: 12px" :bordered="false" :type="hasThirdParty ? 'success' : 'default'">
        {{ data.third_party.note }}
      </n-alert>
    </div>

    <p class="faint" style="margin: 0">
      本页数值全部实时取自当前配置；在「配置」页改动后会立刻反映到这里。
    </p>
  </template>
</template>
