# 萌萌好感度（astrbot_plugin_moe_affinity）

全插件共用的好感度中枢。目标是把散落在各个插件里、口径互不相同的「好感度 / 情绪 / 状态」收敛成一份，
并给自家其它插件提供一套版本化的读写契约。

> 当前是**仓库骨架版本（v0.1.0）**：只有工程结构和一个返回 `unavailable` 的契约占位。
> 数据表、事件模型、迁移工具、对话注入都还没实现，`enabled` 默认关闭，装载后不会改变任何对话行为。

## 为什么另起一个插件

现有的好感度实现各写各的，且各有硬伤：

- 自我学习插件：好感度只有「被 @ / 被唤醒」才涨，按群各存一份，没有时间衰减，
  情绪虽然建了表但主链路注入被写死关闭。
- 陪伴插件 7.x：好感度数值体系被官方整体移除（改成纯文本关系档案 + 定性记忆账本），
  情绪换成四轴心情按半衰期读时投影。

所以这里做的是「生态里缺的那一层数值化」，而不是照抄任何一方。

## 规划中的结构（尚未实现）

| 层 | 针对对象 | 变化原因 | 存储 |
| --- | --- | --- | --- |
| 关系层 | 对某个人 | 互动事件 | 存分数 + 上次结算时间，读时结算闲置衰减并回写 |
| 情绪层 | 对角色自己 | 事件 + 半衰期 | 存基准四轴 + updated_at，读时投影、不写库 |
| 状态层 | 对角色自己 | 世界时钟 | 只存锚点（睡眠/工作量），精力等一律现算 |

## 跨插件契约（占位）

```python
star = context.get_registered_star("astrbot_plugin_moe_affinity")
api = getattr(star.star_cls, "api", None) if star and star.activated else None
if api and api.API_VERSION == "affinity.v1":
    snapshot = await api.get(persona_id, user_id)
```

取不到、版本不匹配或返回 `status="unavailable"` 时，调用方必须安全降级，不能把缺失当成 0 分。

## 打包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_zip.ps1
```

产物 `dist/astrbot_plugin_moe_affinity_v<版本>.zip`，zip 内套一层同名插件目录。
新增顶层 `.py` 模块时必须同步 `build_zip.ps1` 的 `$includeList`（脚本自带漏列检查）。

## 许可

AGPL-3.0，见 `LICENSE`。
