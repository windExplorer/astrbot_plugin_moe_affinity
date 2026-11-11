# 更新日志

## v0.2.0 (2026-11-11)

第一个可用版本：数据层、计分、注入、指令、跨插件契约全部落地（WebUI 控制台尚未实现）。

**核心口径**（详见 `docs/设计说明.md`）

- 好感度按「**角色 × 用户**」唯一。角色是人格的可归并映射：新人格首次出现自动建 1:1 角色，
  控制台可把多个人格归并到同一角色（归并策略固定取最高，不累加、不取平均）。
  这样「私聊人格1 / 群A 人格2 / 群B 人格3」不会把一个人拆成三份互不相干的分数，
  也不会把不同角色强行算成同一个人。
- 群维度不分裂分数：事件记录 `session_id / group_id`，群视图展示的是「本群成员的全局分数 + 本群贡献」。
- 分数 0–1000，6 档（陌生 / 眼熟 / 熟悉 / 亲近 / 亲密 / 羁绊），按上限比例划分。
- 闲置衰减：连续 3 天无互动不动，第 4–7 天每天 −2、第 8–14 天每天 −5、第 15 天起每天 −8；
  **惰性结算**（读写时按 `last_settled_at` 补算并回写），不依赖定时任务。

**新增**

- `store.py`：SQLite 存储层。`roles` / `role_personas` / `affinities` / `events` 四张表；
  分数唯一写入口是 `apply_event`（先落流水再更新聚合行），支持 `dedupe_key` 幂等、
  每日涨/扣分上限、角色归并、排行、重置。写操作统一 `BEGIN IMMEDIATE`。
- `scoring.py`：纯函数计分规则。负面命中时该条不再产生正向分；
  夸奖词默认关闭（中文短词误判率高）。
- `personas.py`：按 AstrBot 的真实优先级解析人格
  （`umo 级 session_service_config` → `conversation.persona_id` → 全局默认），
  `[%None]` 单独映射为 `none` 角色。
- `main.py`：
  - `after_message_sent` 计分（命令消息跳过），`on_llm_request` 注入一段 ≤140 字的
    `<affinity_context>` 区块（marker 去重，附「不要播报分数、不是权限依据」）；
  - 指令 `/好感度`（含「距下一档还差多少」「最近 3 条事件」）、`/好感榜`、
    `/好感度 设置 @某人 500|+20|-10`；
  - LLM 只读工具 `query_affinity`（普通成员只能查自己；`not_recorded` 不等于 0 分）；
  - 跨插件契约 `AffinityApi`（`affinity.v1`）：`role_for` / `get` / `add_event`；
  - 热重载：`__init__` 里用 `importlib.reload` 重载 `store/scoring/personas`，
    避免从旧版本热更上来时跑旧代码。
- `_conf_schema.json`：按「数值 / 计分 / 注入 / 高级」分组的完整配置。
- `tests/test_core.py`：脚本式自检（schema 递归取 `type`、档位边界、衰减分段、
  计分规则、存储层去重/上限/衰减/排行/归并/重置）。

**待实现**

- WebUI 控制台（hash 路由、用户/群视图、排行、图表、配置页、手动改分、审计）。
- 情绪层（四轴心情 + 半衰期投影）与状态层（精力/困倦/忙碌）。
- 自我学习插件旧数据迁移。

版本 v0.1.0 -> v0.2.0。

## v0.1.0 (2026-11-11)

仓库骨架版本，尚无功能实现。

**新建**

- 建立插件基本信息：`metadata.yaml` / `_conf_schema.json` / `main.py` / `README.md` / `LICENSE`。
- `main.py` 只有一个 `Star` 子类与跨插件契约占位 `AffinityApi`
  （`API_VERSION = "affinity.v1"`，所有读写入口返回 `status="unavailable"`），
  目的是先把「别的插件怎么发现并调用本插件」这条路径定下来，避免以后各家自己摸实例。
- `enabled` 默认 `false`：骨架阶段即使开启也不会注册任何钩子、不写库、不注入 prompt。
- `build_zip.ps1`：显式 `$includeList` + 顶层 `.py` 漏列自检 + 撞版检测，
  zip 内套一层 `astrbot_plugin_moe_affinity/`。
- 数据目录用 `StarTools.get_data_dir(PLUGIN_NAME)` 定位，不写插件安装目录
  （`data/plugins/<名>/` 每次更新会被整体删除）。

**待实现**

- 关系层：`(人格, 用户)` 唯一的一份分数 + 事件流水（幂等 `dedupe_key`）。
- 情绪层：人格四轴心情（joy/anger/sorrow/fear，1–10）+ 半衰期读时投影。
- 状态层：精力/困倦/忙碌等锚点投影。
- 对话注入：单段紧凑文本 + 字数预算 + 「不播报数值、不作为权限依据」约束。
- 迁移：自我学习插件 `user_affections` / `bot_mood` 一次性导入。
- 指令与只读 LLM 工具：`/好感度`、`/好感榜`、`query_affinity`。

版本 v0.0.0 -> v0.1.0。
