# 更新日志

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
