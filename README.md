# 萌萌好感度（astrbot_plugin_moe_affinity）

全插件共用的好感度中枢。目标是把散落在各个插件里、口径互不相同的「好感度 / 情绪 / 状态」收敛成一份，
并给自家其它插件提供一套版本化的读写契约。

> 当前 **v0.5.0**：关系层、情绪层、控制台、跨插件契约、迁移工具均已可用；
> 状态层（精力/困倦/忙碌）**明确不做**。口径以 `docs/设计说明.md` 为准。

## 为什么另起一个插件

现有的好感度实现各写各的，且各有硬伤：

- 自我学习插件：好感度只有「被 @ / 被唤醒」才涨，按群各存一份，没有时间衰减，
  情绪虽然建了表但主链路注入被写死关闭。
- 陪伴插件 7.x：好感度数值体系被官方整体移除（改成纯文本关系档案 + 定性记忆账本），
  情绪换成四轴心情按半衰期读时投影。

所以这里做的是「生态里缺的那一层数值化」，而不是照抄任何一方。

## 结构

| 层 | 针对对象 | 变化原因 | 存储 | 状态 |
| --- | --- | --- | --- | --- |
| 关系层 | 对某个人 | 互动事件 | 分数 + `last_settled_at`，读时结算闲置衰减并回写 | 已实现 |
| 情绪层 | 对角色自己 | 事件 + 半衰期 | 基准四轴 + `updated_at`，读时投影不写库 | 已实现 |
| 状态层 | 对角色自己 | 世界时钟 | 只存锚点（睡眠/工作量），精力等一律现算 | 明确不做 |

身份口径：**角色（role）× 用户**唯一。人格只是角色的外壳，默认 1:1，可在控制台归并（取最高）。

## 指令

```
/好感度                      查看自己（按当前会话的人格解析角色）
/好感度 @某人                管理员
/好感榜 [n]                  管理员
/好感度 设置 @某人 500|+20|-10   管理员
```

## WebUI 控制台

AstrBot 插件页里的「萌萌好感度」页面，hash 路由，刷新不会掉回首页：

```
#/overview            总览：KPI / 关系分布 / 互动趋势 / Top5 / 最近变动
#/users?q=&stage=     用户列表：搜索、档位筛选、排序、分页（状态都在 URL 里）
#/users/:id           用户详情：分数曲线、群贡献分解、事件流水、调整/清零
#/groups              群聊：每个群贡献了多少分
#/groups/:id          群详情：本群趋势 + 成员榜
#/rank                排行
#/roles               角色管理：改名、合并（多个人格共享一份分数，取最高）
#/data                从自我学习插件迁移旧好感度（可预览、逐人勾选）
#/config              配置（schema 驱动，含与其它插件的冲突检测）
#/audit               管理员操作审计
```

本地改了前端后重建：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_webui.ps1
```

## 跨插件契约

```python
star = context.get_registered_star("astrbot_plugin_moe_affinity")
api = getattr(star.star_cls, "api", None) if star and star.activated else None
if api and api.API_VERSION == "affinity.v1":
    role_id = api.role_for(persona_id)          # 人格 → 角色（没有就按 1:1 建立）
    snapshot = await api.get(role_id, user_id)  # 只读快照
    snapshot = await api.add_event(             # 写一条事件（不直接改分数）
        role_id, user_id, source="my_plugin", code="event.code", delta=3,
        dedupe_key="一次性的幂等键",
    )
```

取不到、版本不匹配或返回 `status != "ok"` 时，调用方必须安全降级，不能把缺失当成 0 分。

## 打包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_zip.ps1
```

产物 `dist/astrbot_plugin_moe_affinity_v<版本>.zip`，zip 内套一层同名插件目录。
新增顶层 `.py` 模块时必须同步 `build_zip.ps1` 的 `$includeList`（脚本自带漏列检查）。

## 许可

AGPL-3.0，见 `LICENSE`。
