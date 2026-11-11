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

    # 3) 全局默认人格
    try:
        manager = getattr(context, "persona_manager", None)
        pid = str(getattr(manager, "default_persona", "") or "").strip() if manager else ""
        if pid:
            return pid
    except Exception:
        pass
    return DEFAULT_PERSONA


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

    try:
        manager = getattr(context, "persona_manager", None)
        info["default_persona"] = (
            str(getattr(manager, "default_persona", "") or "").strip() if manager else ""
        )
    except Exception:
        pass

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
