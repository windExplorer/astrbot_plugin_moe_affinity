"""人格解析与角色映射。

AstrBot 的人格是**按会话**解析的（``PersonaManager.resolve_selected_persona``）：
``umo 级 session_service_config.persona_id`` → ``conversation.persona_id`` → 全局默认人格。
所以同一个人在私聊与不同群可能落在不同人格上。这里把它解析成一个稳定的 role_id：

- 人格 = 角色的一个外壳，默认 1:1；
- ``[%None]``（明确不要人格）单独成 ``none`` 角色，不与 ``default`` 混在一起；
- 解析失败一律回落 ``default``，绝不因为读不到人格就丢掉这轮互动。
"""

from astrbot.api import sp

NONE_PERSONA = "[%None]"
DEFAULT_PERSONA = "default"


def role_key(persona_id: str | None) -> str:
    """人格 id → 角色键（也是默认的角色 id）。"""
    pid = (persona_id or "").strip()
    if not pid or pid == NONE_PERSONA:
        return "none" if pid == NONE_PERSONA else DEFAULT_PERSONA
    return pid


def role_label(persona_id: str | None) -> str:
    pid = (persona_id or "").strip()
    if pid == NONE_PERSONA:
        return "无自定义人格"
    return pid or DEFAULT_PERSONA


def _as_mapping(value):
    """返回能 ``.get`` 的对象；不要求必须是 ``dict`` 子类。

    v0.15.1 用 ``isinstance(cfg, dict)`` 做守卫，实测用户环境里两条配置路径都
    「读到了对象但 persona_id 为空、runtime 也为空」—— 配置容器在部分 AstrBot
    版本里不是 dict 子类，守卫会直接把整份配置判成无效。这里改成 duck typing。
    """
    if value is None:
        return {}
    if callable(getattr(value, "get", None)):
        return value
    return {}


def _read_persona_id_from_cfg(cfg) -> str:
    """从一份 AstrBot 配置里找默认人格 id，不假设容器类型 / runtime / 结构版本。"""
    cfg = _as_mapping(cfg)
    agent = _as_mapping(cfg.get("agent"))
    runner = _as_mapping(agent.get("config"))
    persona = _as_mapping(runner.get("persona"))
    settings = _as_mapping(cfg.get("provider_settings"))
    candidates = [
        persona.get("persona_id"),
        runner.get("persona_id"),
        agent.get("persona_id"),
        settings.get("default_personality"),
        cfg.get("default_personality"),
        cfg.get("persona_id"),
    ]
    for value in candidates:
        pid = str(value or "").strip()
        if pid and pid != NONE_PERSONA:
            return pid
    return ""


def _cfg_probe(cfg) -> dict:
    """配置结构摘要：只列键名与候选值，不列配置内容，便于定位版本差异。"""
    cfg = _as_mapping(cfg)
    agent = _as_mapping(cfg.get("agent"))
    runner = _as_mapping(agent.get("config"))
    persona = _as_mapping(runner.get("persona"))

    def keys_of(obj) -> list[str]:
        try:
            return sorted(str(k) for k in obj.keys())[:60]
        except Exception:
            return []

    return {
        "type": type(cfg).__name__,
        "top_keys": keys_of(cfg),
        "agent_keys": keys_of(agent),
        "runtime": str(agent.get("runtime") or ""),
        "persona_keys": keys_of(persona),
        "persona_id": str(persona.get("persona_id") or ""),
        "config_persona_id": str(runner.get("persona_id") or ""),
    }


def default_persona_detail(context, umo: str = "") -> dict:
    """返回 ``{persona_id, source, probes}``，把「这个值从哪读来的」摊开。

    配置对象是原地更新的单例，正常情况第一条路径就能拿到当前值；万一读不到，
    宁可明确标出「用的是启动快照」，也不要悄悄拿旧值冒充当前默认人格。
    每条 probes 还带配置结构摘要，读不到时能一眼看出是容器类型还是键名不同。
    """
    probes: list[dict] = []
    getters = (
        ("context.get_config", lambda: context.get_config(umo or None)),
        ("astrbot_config_mgr.get_conf", lambda: context.astrbot_config_mgr.get_conf(umo or None)),
    )
    for label, getter in getters:
        try:
            cfg = getter()
            pid = _read_persona_id_from_cfg(cfg)
            shape = _cfg_probe(cfg)
            probes.append({
                "source": label,
                "persona_id": pid,
                "runtime": shape["runtime"],
                "shape": shape,
            })
            if pid:
                return {"persona_id": pid, "source": label, "probes": probes}
        except Exception as exc:
            probes.append({"source": label, "error": f"{type(exc).__name__}: {exc}"})
    try:
        manager = getattr(context, "persona_manager", None)
        pid = str(getattr(manager, "default_persona", "") or "").strip() if manager else ""
        if pid:
            return {
                "persona_id": pid,
                "source": "persona_manager.default_persona (启动快照)",
                "probes": probes,
            }
    except Exception as exc:
        probes.append({"source": "启动快照", "error": f"{type(exc).__name__}: {exc}"})
    return {"persona_id": DEFAULT_PERSONA, "source": "fallback", "probes": probes}


def default_persona_id(context, umo: str = "") -> str:
    """当前生效的全局默认人格 id（每次动态读，不缓存）。"""
    return str(default_persona_detail(context, umo).get("persona_id") or DEFAULT_PERSONA)


async def async_default_persona(context, umo: str = "") -> str:
    """直接问 AstrBot 核心要当前默认人格 id，绕开插件对配置结构的一切猜测。

    ``PersonaManager.get_default_persona_v3`` 用核心自己的 ``acm.get_conf(umo)``
    读配置，与 ``resolve_selected_persona`` 同源；返回 Personality 对象，id 在
    ``name``。找不到人格时核心会回落内置 ``DEFAULT_PERSONALITY``（name="default"），
    这种兜底值不能拿来覆盖配置，所以由调用方判断后再采用。
    """
    try:
        manager = getattr(context, "persona_manager", None)
        if manager is None:
            return ""
        obj = await manager.get_default_persona_v3(umo or None)
        if isinstance(obj, dict):
            return str(obj.get("name") or "").strip()
    except Exception:
        pass
    return ""


async def resolve_persona_id(context, event) -> str:
    """解析当前事件最终生效的人格 id（与 AstrBot 回复时用的那一套优先级一致）。"""
    umo = str(getattr(event, "unified_msg_origin", "") or "")

    # 1) 会话级强制人格
    if umo:
        try:
            cfg = (
                await sp.get_async(
                    scope="umo", scope_id=umo, key="session_service_config", default={}
                )
                or {}
            )
            pid = str(cfg.get("persona_id") or "").strip()
            if pid:
                return pid
        except Exception:
            pass

    # 2) 会话绑定的 conversation.persona_id
    if umo:
        try:
            manager = getattr(context, "conversation_manager", None)
            if manager is not None:
                cid = await manager.get_curr_conversation_id(umo)
                if cid:
                    conv = await manager.get_conversation(umo, cid)
                    pid = str(getattr(conv, "persona_id", "") or "").strip()
                    if pid:
                        return pid
        except Exception:
            pass

    # 3) 全局默认人格（动态读配置，不用启动时的快照）
    return default_persona_id(context, umo)


async def explain(context, event) -> dict:
    """把三级人格解析的每一步都摊开，供 `/好感度 诊断` 排查「为什么都归到同一个人格」。"""
    umo = str(getattr(event, "unified_msg_origin", "") or "")
    info: dict[str, str] = {
        "umo": umo,
        "session_override": "",
        "conversation_id": "",
        "conversation_persona": "",
        "default_persona": "",
        "resolved": "",
        "source": "",
    }

    if umo:
        try:
            cfg = (
                await sp.get_async(
                    scope="umo", scope_id=umo, key="session_service_config", default={}
                )
                or {}
            )
            info["session_override"] = str(cfg.get("persona_id") or "").strip()
        except Exception as exc:
            info["session_override"] = f"<读取失败: {exc}>"

    if umo:
        try:
            manager = getattr(context, "conversation_manager", None)
            if manager is not None:
                cid = await manager.get_curr_conversation_id(umo)
                info["conversation_id"] = str(cid or "")
                if cid:
                    conv = await manager.get_conversation(umo, cid)
                    info["conversation_persona"] = str(
                        getattr(conv, "persona_id", "") or ""
                    ).strip()
        except Exception as exc:
            info["conversation_persona"] = f"<读取失败: {exc}>"

    info["default_persona"] = default_persona_id(context, umo)

    if info["session_override"] and not info["session_override"].startswith("<"):
        info["resolved"] = info["session_override"]
        info["source"] = "会话级强制人格"
    elif info["conversation_persona"] and not info["conversation_persona"].startswith("<"):
        info["resolved"] = info["conversation_persona"]
        info["source"] = "会话绑定的对话"
    else:
        info["resolved"] = info["default_persona"] or DEFAULT_PERSONA
        info["source"] = "全局默认人格"
    return info
