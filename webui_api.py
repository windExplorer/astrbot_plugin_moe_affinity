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


async def h_roles(plugin) -> dict:
    roles = plugin.store.list_roles()
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


async def h_sync_groups(plugin) -> dict:
    """从 OneBot 拉群列表补群名（aiocqhttp 专属，失败不影响控制台）。"""
    names: dict[str, str] = {}
    errors: list[str] = []
    try:
        insts = plugin.context.platform_manager.platform_insts
    except Exception:
        insts = []
    for inst in insts or []:
        try:
            if getattr(inst.meta(), "name", "") != "aiocqhttp":
                continue
            bot = inst.get_client()
            groups = await bot.call_action("get_group_list")
        except Exception as exc:
            errors.append(str(exc))
            continue
        for group in groups or []:
            gid = str(group.get("group_id") or "")
            if gid:
                names[gid] = str(group.get("group_name") or "")
    count = plugin.store.upsert_groups(names)
    return ok({"ok": bool(names), "count": count, "errors": errors})


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
        ("/overview", h_overview, ["GET"]),
        ("/users", h_users, ["GET"]),
        ("/user", h_user, ["GET"]),
        ("/groups", h_groups, ["GET"]),
        ("/group", h_group, ["GET"]),
        ("/groups/sync", h_sync_groups, ["POST"]),
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
