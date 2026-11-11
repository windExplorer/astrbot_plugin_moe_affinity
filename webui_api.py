"""控制台后端路由（AstrBot 插件 Page 的桥接 API）。

路由前缀 ``/astrbot_plugin_moe_affinity``，前端通过 ``window.AstrBotPluginPage`` 的
``apiGet/apiPost`` 调用，由 Dashboard 转发（不要用裸 fetch，sandbox iframe 里会 CORS 失败）。

返回信封必须遵循 AstrBot 的桥接约定：

- 成功 → ``{"status": "ok", "data": <payload>}``（Dashboard 只把 ``data`` 转发给前端）
- 失败 → ``{"status": "error", "message": "..."}``（Dashboard 直接抛错）

路由一律用**查询参数**传 id（``/user?id=123``），不用路径参数：桥接层对路径参数的处理
在各版本里不完全一致，查询参数没有这个问题。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from astrbot.api import logger

try:  # quart 是 AstrBot 的运行期依赖
    from quart import request
except Exception:  # pragma: no cover - 仅本地静态检查时缺失
    request = None  # type: ignore

from . import migrate_self_learning, rules_info, store

ROUTE_PREFIX = "/astrbot_plugin_moe_affinity"
PLUGIN_ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------- 工具
def ok(data: Any = None) -> dict:
    return {"status": "ok", "data": data}


def err(message: str) -> dict:
    return {"status": "error", "message": message}


def _q(name: str, default: str = "") -> str:
    try:
        if request is None:
            return default
        return (request.args.get(name) or default).strip()
    except Exception:
        return default


def _qi(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(_q(name) or default)))
    except Exception:
        return default


def _qb(name: str, default: bool = False) -> bool:
    raw = _q(name, "1" if default else "0").lower()
    return raw in ("1", "true", "yes", "on")


async def _payload() -> dict:
    try:
        if request is None:
            return {}
        return await request.get_json(silent=True) or {}
    except Exception:
        return {}


def _load_schema() -> dict:
    try:
        return json.loads((PLUGIN_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[moe_affinity] 读取配置 schema 失败: {exc}")
        return {}


def _flatten(schema: dict, prefix: str = "") -> list[dict]:
    """把嵌套 schema 摊平成点号键，供前端按分组渲染。"""
    out: list[dict] = []
    for key, node in schema.items():
        path = f"{prefix}{key}"
        if node.get("type") == "object":
            out.extend(_flatten(node.get("items") or {}, path + "."))
        else:
            out.append({"key": path, "spec": node})
    return out


def _get_nested(cfg: Any, path: str, default: Any = None) -> Any:
    node = cfg
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
    return default if node is None else node


def _set_nested(cfg: Any, path: str, value: Any) -> None:
    parts = path.split(".")
    node = cfg
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _coerce(spec: dict, value: Any) -> Any:
    kind = spec.get("type", "string")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")
    if kind == "int":
        return int(float(value))
    if kind == "float":
        return float(value)
    if kind == "list":
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        text = str(value or "").replace("，", ",")
        return [item.strip() for item in text.split(",") if item.strip()]
    text = str(value if value is not None else "")
    options = spec.get("options")
    if isinstance(options, list) and options and text not in options:
        raise ValueError(f"取值必须是 {options} 之一")
    return text


def _resolve_role(plugin, role: str | None = None) -> str:
    """没传 role 时取第一个角色（多半是默认人格），没角色就建 default。"""
    role_id = (role or "").strip()
    if role_id:
        return role_id
    roles = plugin.store.list_roles()
    if roles:
        return roles[0]["role_id"]
    return plugin.store.ensure_role("default", "default")


# ---------------------------------------------------------------------- 只读
async def h_ping(plugin) -> dict:
    roles = plugin.store.list_roles()
    return ok(
        {
            "plugin": plugin.__class__.__module__,
            "version": plugin.plugin_version,
            "api_version": plugin.api.API_VERSION,
            "db_ready": plugin.store.ping(),
            "data_dir": str(plugin.data_dir),
            "roles": len(roles),
            "server_time": int(time.time()),
        }
    )


async def _bound_personas(plugin) -> dict[str, int]:
    """统计「有人格绑定的会话数」：人格 → 会话数。

    AstrBot 的会话（conversation）各自绑定 persona_id，这是「这个角色正在被用」的
    最直接证据——比「有没有分数记录」准，因为刚导入数据时还没人聊过。
    """
    counts: dict[str, int] = {}
    try:
        manager = plugin.context.conversation_manager
        conversations = await manager.get_conversations()
    except Exception as exc:
        logger.debug(f"[moe_affinity] 读取会话绑定失败（按无绑定处理）: {exc}")
        conversations = []
    for conversation in conversations or []:
        persona_id = str(getattr(conversation, "persona_id", "") or "").strip()
        if persona_id:
            counts[persona_id] = counts.get(persona_id, 0) + 1

    # 还有一类没写进 conversation：umo 级的强制人格（session_service_config.persona_id）。
    # 这种会话的 conversation.persona_id 可能是空的，只看会话会漏掉它。
    try:
        from astrbot.api import sp

        prefs = await sp.session_get(None, "session_service_config")
        for pref in prefs or []:
            value = getattr(pref, "value", None)
            config = value.get("val") if isinstance(value, dict) else None
            if not isinstance(config, dict):
                continue
            persona_id = str(config.get("persona_id") or "").strip()
            if persona_id:
                counts[persona_id] = counts.get(persona_id, 0) + 1
    except Exception as exc:
        logger.debug(f"[moe_affinity] 读取会话级人格覆盖失败（忽略）: {exc}")
    return counts


async def h_roles(plugin) -> dict:
    roles = plugin.store.list_roles()
    if not roles:
        # 兜底：万一 initialize 没跑到（或人格列表为空），至少给一个可选的默认角色，
        # 否则控制台顶部「暂无角色」，迁移页也选不到导入目标。
        plugin.store.ensure_role("default", "默认人格")
        roles = plugin.store.list_roles()
    bound = await _bound_personas(plugin)
    mapping = plugin.store.role_personas()
    role_sessions: dict[str, int] = {}
    for item in mapping:
        hits = bound.get(item["persona_id"], 0)
        if hits:
            role_sessions[item["role_id"]] = role_sessions.get(item["role_id"], 0) + hits
    for role in roles:
        sessions = role_sessions.get(role["role_id"], 0)
        users = int(role.get("users") or 0)
        role["bound_sessions"] = sessions
        # 使用中 = 有会话绑定，或者已经有分数记录（人格被删/会话清空但数据还在）
        role["in_use"] = sessions > 0 or users > 0
    return ok({"items": roles, "total": len(roles)})


async def h_overview(plugin) -> dict:
    role = _resolve_role(plugin, _q("role"))
    days = _qi("days", 30, 1, 180)
    data = plugin.store.overview(role, plugin._rules(), days=days)
    data["role_name"] = next(
        (item["display_name"] for item in plugin.store.list_roles() if item["role_id"] == role),
        role,
    )
    return ok(data)


async def h_users(plugin) -> dict:
    role = _resolve_role(plugin, _q("role"))
    data = plugin.store.list_users(
        role,
        plugin._rules(),
        q=_q("q"),
        stage=_q("stage"),
        sort=_q("sort", "score"),
        page=_qi("page", 1, 1, 100000),
        size=_qi("size", 50, 1, 200),
    )
    data["role_id"] = role
    return ok(data)


async def h_user(plugin) -> dict:
    role = _resolve_role(plugin, _q("role"))
    user_id = _q("id")
    if not user_id:
        return err("缺少 id")
    data = plugin.store.user_detail(
        role, user_id, plugin._rules(), days=_qi("days", 30, 1, 180)
    )
    data["role_id"] = role
    return ok(data)


async def h_groups(plugin) -> dict:
    role = _resolve_role(plugin, _q("role"))
    rows = plugin.store.group_rows(role, plugin._rules())
    return ok({"items": rows, "total": len(rows), "role_id": role})


async def h_group(plugin) -> dict:
    role = _resolve_role(plugin, _q("role"))
    group_id = _q("id")
    if not group_id:
        return err("缺少 id")
    data = plugin.store.group_detail(role, group_id, plugin._rules())
    data["role_id"] = role
    return ok(data)


async def h_rank(plugin) -> dict:
    role = _resolve_role(plugin, _q("role"))
    limit = _qi("limit", 20, 1, 200)
    rows = plugin.store.ranking(role, plugin._rules(), limit=limit)
    return ok({"items": rows, "role_id": role, "limit": limit})


async def h_audit(plugin) -> dict:
    return ok(plugin.store.audit_rows(page=_qi("page", 1, 1, 100000), size=_qi("size", 50, 1, 200)))


async def h_config(plugin) -> dict:
    schema = _load_schema()
    cfg = getattr(plugin, "config", None) or {}
    items: dict[str, Any] = {}
    for entry in _flatten(schema):
        key = entry["key"]
        spec = entry["spec"]
        default = spec.get("default")
        items[key] = _get_nested(cfg, key, default)
    return ok({"items": items, "schema": schema, "flat": _flatten(schema)})


async def h_conflicts(plugin) -> dict:
    """检查其它插件是否仍在管好感度/情绪（只提示，不干预）。"""
    items: list[dict] = []
    checks = {
        "astrbot_plugin_self_learning": [
            ("enable_affection_system", "仍在计分好感度"),
            ("include_affection_info", "仍在把好感度注入 prompt"),
            ("enable_emotion_learning_gate", "情绪学习闸门仍在读旧数据"),
            ("enable_startup_random_mood", "仍会随机写 bot_mood"),
            ("enable_daily_mood", "每日心情任务仍开启"),
        ],
    }
    for plugin_name, keys in checks.items():
        try:
            star = plugin.context.get_registered_star(plugin_name)
        except Exception:
            star = None
        if not star or not getattr(star, "activated", False):
            continue
        conf = getattr(star, "config", None) or {}
        for key, note in keys:
            if not isinstance(conf, dict) or key not in conf:
                continue
            value = conf.get(key)
            items.append(
                {
                    "plugin": plugin_name,
                    "key": key,
                    "value": value,
                    "note": note,
                    "level": "warn" if value else "ok",
                }
            )
    return ok({"items": items, "checked_at": int(time.time())})


# ---------------------------------------------------------------------- 写入
async def h_score(plugin) -> dict:
    body = await _payload()
    role = _resolve_role(plugin, str(body.get("role") or ""))
    user_id = str(body.get("id") or "").strip()
    if not user_id:
        return err("缺少 id")
    try:
        value = float(body.get("value"))
    except (TypeError, ValueError):
        return err("value 必须是数字")
    mode = str(body.get("mode") or "delta").lower()
    rules = plugin._rules()
    snapshot = plugin.store.snapshot(role, user_id, rules)
    before = float(snapshot.get("score") or 0)
    delta = value if mode == "delta" else value - before
    if not delta:
        return ok({"changed": 0, "snapshot": snapshot})
    result = plugin.store.apply_event(
        role_id=role,
        user_id=user_id,
        code="manual.adjust",
        delta=delta,
        source="manual",
        rules=rules,
        operator=str(body.get("operator") or "dashboard"),
        evidence=str(body.get("reason") or ""),
        display_name=str(body.get("name") or ""),
    )
    return ok({"changed": round(delta, 1), "snapshot": result})


async def h_reset(plugin) -> dict:
    body = await _payload()
    role = _resolve_role(plugin, str(body.get("role") or ""))
    user_id = str(body.get("id") or "").strip()
    if not user_id:
        return err("缺少 id")
    result = plugin.store.reset_user(
        role,
        user_id,
        plugin._rules(),
        operator=str(body.get("operator") or "dashboard"),
        reason=str(body.get("reason") or ""),
    )
    return ok(result)


async def h_set_config(plugin) -> dict:
    body = await _payload()
    items = body.get("items")
    if not isinstance(items, dict) or not items:
        return err("没有要写入的配置项")
    schema = _load_schema()
    spec_map = {entry["key"]: entry["spec"] for entry in _flatten(schema)}
    cfg = getattr(plugin, "config", None)
    if cfg is None or not hasattr(cfg, "__setitem__"):
        return err("插件配置不可写")
    changed: list[str] = []
    for key, raw in items.items():
        spec = spec_map.get(key)
        if spec is None:
            continue
        try:
            value = _coerce(spec, raw)
        except Exception as exc:
            return err(f"{key} 取值非法: {exc}")
        _set_nested(cfg, key, value)
        changed.append(key)
    if not changed:
        return err("没有可写入的配置项（键名不在 _conf_schema.json 中）")
    try:
        if hasattr(cfg, "save_config"):
            cfg.save_config()
    except Exception as exc:
        logger.warning(f"[moe_affinity] save_config 失败: {exc}")
        return err(f"配置已改内存但落盘失败: {exc}")
    return ok({"changed": changed})


async def h_mood(plugin) -> dict:
    role = _resolve_role(plugin, _q("role"))
    return ok(plugin._mood_view(role))


async def h_rules(plugin) -> dict:
    """当前生效的规则说明（数值全部取自实时配置，改配置后这页会同步）。"""
    return ok(
        rules_info.build(
            rules=plugin._rules(),
            scoring_cfg=plugin._scoring(),
            mood_cfg=plugin._mood_cfg(),
            stages=list(store.STAGE_TABLE),
            version=plugin.plugin_version,
        )
    )


async def h_roles_personas(plugin) -> dict:
    """角色 ↔ 人格映射，并标出哪个人格已经从 AstrBot 里删掉。"""
    mapping = {item["persona_id"]: item["role_id"] for item in plugin.store.role_personas()}
    alive: set[str] = set()
    try:
        manager = getattr(plugin.context, "persona_manager", None)
        for persona in getattr(manager, "personas_v3", []) or []:
            name = str(persona.get("name") or "")
            if name:
                alive.add(name)
    except Exception:
        pass
    roles = {item["role_id"]: item for item in plugin.store.list_roles()}
    bound = await _bound_personas(plugin)
    items = []
    for persona_id, role_id in mapping.items():
        role = roles.get(role_id) or {}
        users = int(role.get("users") or 0)
        sessions = int(bound.get(persona_id, 0))
        items.append(
            {
                "persona_id": persona_id,
                "role_id": role_id,
                "role_name": role.get("display_name") or role_id,
                "persona_exists": persona_id in alive,
                "in_use": sessions > 0 or users > 0,
                "users": users,
                "bound_sessions": sessions,
                "last_active_at": int(role.get("last_active_at") or 0),
            }
        )
    return ok({"items": items, "alive": sorted(alive)})


async def h_role_rename(plugin) -> dict:
    body = await _payload()
    role_id = str(body.get("role_id") or "").strip()
    name = str(body.get("name") or "").strip()
    if not role_id or not name:
        return err("缺少 role_id 或 name")
    if not plugin.store.rename_role(role_id, name):
        return err("角色不存在")
    return ok({"role_id": role_id, "name": name})


async def h_role_merge(plugin) -> dict:
    body = await _payload()
    target = str(body.get("target") or "").strip()
    source = str(body.get("source") or "").strip()
    if not target or not source:
        return err("缺少 target 或 source")
    if target == source:
        return err("不能把角色并到自己身上")
    result = plugin.store.merge_roles(target, source, strategy="max")
    return ok(result)


def _legacy_db_path(plugin, explicit: str | None = None) -> Path | None:
    """定位自我学习插件的老库；优先用它的配置 messages_db_path。"""
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    try:
        star = plugin.context.get_registered_star("astrbot_plugin_self_learning")
        conf = getattr(star, "config", None) if star else None
        if isinstance(conf, dict):
            configured = str(conf.get("messages_db_path") or "").strip()
            if configured:
                path = Path(configured).expanduser()
                if path.is_file():
                    return path
    except Exception:
        pass
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path
    except Exception:
        return None
    base = Path(get_astrbot_data_path()) / "plugin_data"
    return migrate_self_learning.resolve_db_path(None, base / "astrbot_plugin_self_learning")


async def h_migrate_preview(plugin) -> dict:
    path = _legacy_db_path(plugin, _q("path"))
    role = _resolve_role(plugin, _q("role"))
    if path is None:
        return ok(
            {
                "exists": False,
                "db_path": "",
                "items": [],
                "totals": {"users": 0, "pending": 0, "imported": 0},
                "message": "没找到自我学习插件的 messages.db，可在下方手动填写路径。",
            }
        )
    try:
        legacy = migrate_self_learning.read_legacy(path)
    except Exception as exc:
        return ok(
            {
                "exists": True,
                "db_path": str(path),
                "items": [],
                "totals": {"users": 0, "pending": 0, "imported": 0},
                "message": f"读取失败：{exc}（若它用的是 MySQL 而不是 SQLite，则不支持自动迁移）",
            }
        )
    plan = migrate_self_learning.build_plan(legacy)
    # 幂等键按角色隔离：同一个人可以分别导入到每个角色
    imported = plugin.store.imported_user_ids(migrate_self_learning.role_prefix(role))
    # 兼容旧版全局键，避免升级后把已导入的角色重新导一遍导致分数翻倍
    imported |= plugin.store.legacy_imported_user_ids(role)
    for item in plan:
        item["imported"] = item["user_id"] in imported
    pending = sum(1 for item in plan if not item["imported"])
    return ok(
        {
            "exists": True,
            "db_path": str(path),
            "role_id": role,
            "items": plan,
            "totals": {"users": len(plan), "pending": pending, "imported": len(plan) - pending},
            "message": "",
        }
    )


async def h_migrate_apply(plugin) -> dict:
    body = await _payload()
    path = _legacy_db_path(plugin, str(body.get("path") or "") or None)
    if path is None:
        return err("没找到老库文件")
    role = _resolve_role(plugin, str(body.get("role") or ""))
    wanted = body.get("user_ids")
    wanted_set = {str(item) for item in wanted} if isinstance(wanted, list) else None
    try:
        legacy = migrate_self_learning.read_legacy(path)
    except Exception as exc:
        return err(f"读取老库失败: {exc}")
    plan = migrate_self_learning.build_plan(legacy)
    imported_ids = plugin.store.imported_user_ids(migrate_self_learning.role_prefix(role))
    imported_ids |= plugin.store.legacy_imported_user_ids(role)
    imported = skipped = 0
    errors: list[str] = []
    for item in plan:
        user_id = item["user_id"]
        if wanted_set is not None and user_id not in wanted_set:
            continue
        if user_id in imported_ids:
            skipped += 1
            continue
        if item["target_score"] <= 0:
            skipped += 1
            continue
        try:
            plugin.store.apply_event(
                role_id=role,
                user_id=user_id,
                code=migrate_self_learning.CODE,
                delta=float(item["target_score"]),
                source=migrate_self_learning.SOURCE,
                rules=plugin._rules(),
                evidence=migrate_self_learning.evidence_json(item),
                dedupe_key=migrate_self_learning.dedupe_key(role, user_id),
                operator="migrate",
            )
            imported += 1
        except Exception as exc:
            errors.append(f"{user_id}: {exc}")
    mood_cleared = 0
    if bool(body.get("clear_mood")):
        try:
            mood_cleared = migrate_self_learning.clear_active_mood(path)
        except Exception as exc:
            errors.append(f"清理心情失败: {exc}")
    return ok(
        {
            "imported": imported,
            "skipped": skipped,
            "errors": errors,
            "mood_cleared": mood_cleared,
            "role_id": role,
        }
    )


def _aiocqhttp_platforms(plugin) -> list:
    try:
        insts = plugin.context.platform_manager.platform_insts
    except Exception:
        return []
    out = []
    for inst in insts or []:
        try:
            if getattr(inst.meta(), "name", "") == "aiocqhttp":
                out.append(inst)
        except Exception:
            continue
    return out


async def h_sync(plugin) -> dict:
    """从 OneBot 拉好友/群列表（可选群成员）补昵称与群名。"""
    body = await _payload()
    with_members = bool(body.get("members"))
    platforms = _aiocqhttp_platforms(plugin)
    if not platforms:
        return err("未找到 aiocqhttp 平台实例（请先启用一个 OneBot 适配器）")

    users: dict[str, str] = {}
    groups: dict[str, str] = {}
    errors: list[str] = []
    for inst in platforms:
        try:
            bot = inst.get_client()
        except Exception as exc:
            errors.append(f"获取协议端客户端失败: {exc}")
            continue
        try:
            friends = await bot.call_action("get_friend_list")
            for friend in friends or []:
                uid = str(friend.get("user_id") or "")
                name = str(friend.get("remark") or friend.get("nickname") or "").strip()
                if uid and name:
                    users[uid] = name
        except Exception as exc:
            errors.append(f"好友列表拉取失败: {exc}")
        try:
            group_list = await bot.call_action("get_group_list")
            for group in group_list or []:
                gid = str(group.get("group_id") or "")
                if gid:
                    groups[gid] = str(group.get("group_name") or "")
        except Exception as exc:
            errors.append(f"群列表拉取失败: {exc}")

        if with_members:
            for gid in list(groups):
                try:
                    members = await bot.call_action("get_group_member_list", group_id=int(gid))
                except Exception:
                    continue
                for member in members or []:
                    uid = str(member.get("user_id") or "")
                    name = str(member.get("card") or member.get("nickname") or "").strip()
                    # 群名片优先，但好友备注已经有名字时不覆盖
                    if uid and name and uid not in users:
                        users[uid] = name

    renamed = plugin.store.upsert_user_names(users)
    group_count = plugin.store.upsert_groups(groups)
    return ok(
        {
            "ok": bool(users or groups),
            "users": len(users),
            "groups": group_count,
            "renamed": renamed,
            "members": with_members,
            "errors": errors,
        }
    )


AVATAR_URLS = {
    "user": "https://q1.qlogo.cn/g?b=qq&nk={id}&s=100",
    "group": "https://p.qlogo.cn/gh/{id}/{id}/100",
}


async def _fetch_avatars(keys: list[str]) -> dict[str, str]:
    """按 key 抓头像并转成 data URI（前端 <img> 直接用，不依赖外部域名可达）。"""
    try:
        import aiohttp
    except Exception as exc:  # pragma: no cover - 运行期依赖
        logger.warning(f"[moe_affinity] aiohttp 不可用，头像功能关闭: {exc}")
        return {}
    import base64

    out: dict[str, str] = {}
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for key in keys:
                kind, _, ident = key.partition(":")
                template = AVATAR_URLS.get(kind)
                if not template or not ident:
                    continue
                try:
                    async with session.get(template.format(id=ident)) as resp:
                        if resp.status != 200:
                            continue
                        raw = await resp.read()
                        if not raw:
                            continue
                        ctype = resp.headers.get("Content-Type") or "image/jpeg"
                        out[key] = f"data:{ctype};base64,{base64.b64encode(raw).decode('ascii')}"
                except Exception:
                    continue
    except Exception as exc:
        logger.debug(f"[moe_affinity] 头像抓取失败: {exc}")
    return out


async def h_avatars(plugin) -> dict:
    kind = _q("type", "user")
    if kind not in AVATAR_URLS:
        kind = "user"
    ids = [item.strip() for item in _q("ids").split(",") if item.strip()][:100]
    if not ids:
        return ok({"items": {}, "got": 0, "total": 0})
    keys = [f"{kind}:{ident}" for ident in ids]
    cached = {} if _qb("force") else plugin.store.get_avatars(keys)
    missing = [key for key in keys if key not in cached]
    if missing:
        fetched = await _fetch_avatars(missing)
        if fetched:
            plugin.store.put_avatars(fetched)
            cached.update(fetched)
    return ok(
        {
            "items": {key.split(":", 1)[1]: value for key, value in cached.items()},
            "got": len(cached),
            "total": len(keys),
        }
    )


# ---------------------------------------------------------------------- 注册
def _bind(plugin, fn: Callable[[Any], Awaitable[dict]]) -> Callable[[], Awaitable[dict]]:
    async def handler() -> dict:
        try:
            return await fn(plugin)
        except Exception as exc:  # 任何异常都转成信封，避免前端只看到 500
            logger.exception(f"[moe_affinity] {fn.__name__} 处理异常")
            return err(f"{fn.__name__} 失败: {exc}")

    handler.__name__ = f"moe_affinity_{fn.__name__}"
    return handler


def register_apis(plugin) -> None:
    routes: list[tuple[str, Callable[[Any], Awaitable[dict]], list[str]]] = [
        ("/ping", h_ping, ["GET"]),
        ("/roles", h_roles, ["GET"]),
        ("/mood", h_mood, ["GET"]),
        ("/rules", h_rules, ["GET"]),
        ("/roles/personas", h_roles_personas, ["GET"]),
        ("/roles/rename", h_role_rename, ["POST"]),
        ("/roles/merge", h_role_merge, ["POST"]),
        ("/migrate/preview", h_migrate_preview, ["GET"]),
        ("/migrate/apply", h_migrate_apply, ["POST"]),
        ("/overview", h_overview, ["GET"]),
        ("/users", h_users, ["GET"]),
        ("/user", h_user, ["GET"]),
        ("/groups", h_groups, ["GET"]),
        ("/group", h_group, ["GET"]),
        ("/sync", h_sync, ["POST"]),
        ("/avatars", h_avatars, ["GET"]),
        ("/rank", h_rank, ["GET"]),
        ("/audit", h_audit, ["GET"]),
        ("/config", h_config, ["GET"]),
        ("/config", h_set_config, ["POST"]),
        ("/score", h_score, ["POST"]),
        ("/reset", h_reset, ["POST"]),
        ("/conflicts", h_conflicts, ["GET"]),
    ]
    for path, fn, methods in routes:
        plugin.context.register_web_api(
            f"{ROUTE_PREFIX}{path}",
            _bind(plugin, fn),
            methods,
            f"MoeAffinity {path}",
        )
    logger.info(f"[moe_affinity] 已注册 {len(routes)} 条控制台路由（前缀 {ROUTE_PREFIX}）")
