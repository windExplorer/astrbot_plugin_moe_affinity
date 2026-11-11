"""萌萌好感度 — 全插件共用的好感度中枢。

口径见 docs/设计说明.md，几条硬规则：

- 好感度按「角色 × 用户」唯一；角色是人格的可归并映射（personas.py）；
- 分数只通过事件写入（store.apply_event），不直接改分数；
- 闲置衰减惰性结算，不跑定时任务；
- 注入只有一段短文本，带 marker 去重，且明确「不是权限依据」。
"""

import asyncio
import importlib
import json
import re
import time
from datetime import datetime
from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from . import checkin_sync, migrate_self_learning, mood, personas, rules_info, scoring, store, webui_api

PLUGIN_NAME = "astrbot_plugin_moe_affinity"
API_VERSION = "affinity.v1"


def _read_plugin_version() -> str:
    """版本号唯一来源是 metadata.yaml；不要在代码里手写第二份（v0.3.0 曾因此显示成 v0.2.0）。"""
    try:
        text = (Path(__file__).resolve().parent / "metadata.yaml").read_text(encoding="utf-8")
        matched = re.search(r"(?m)^\s*version:\s*\"?([^\"#\r\n]+?)\"?\s*(?:#.*)?$", text)
        if matched:
            return matched.group(1).strip()
    except Exception:
        pass
    return "v0.0.0"


PLUGIN_VERSION = _read_plugin_version()
MARKER = "<affinity_context>"
SKIP_SCORING = "moe_affinity_skip_scoring"
PERSONA_CACHE = "moe_affinity_persona_id"


def _reload_sibling_modules() -> None:
    """AstrBot 热更新只重载 main.py，依赖模块会残留在 sys.modules 里跑旧代码。"""
    for module in (
        store, scoring, mood, personas, rules_info, migrate_self_learning,
        checkin_sync, webui_api,
    ):
        try:
            importlib.reload(module)
        except Exception as exc:  # 重载失败不应让插件装不上
            logger.warning(f"[moe_affinity] 重载模块 {module.__name__} 失败: {exc}")


def _human_gap(seconds: int) -> str:
    if seconds < 3600:
        return "刚刚"
    if seconds < 86400:
        return f"{seconds // 3600} 小时前"
    return f"{seconds // 86400} 天前"


class AffinityApi:
    """跨插件契约（affinity.v1）。

    调用方：``context.get_registered_star("astrbot_plugin_moe_affinity").star_cls.api``。
    取不到、版本不匹配、或返回 ``status != "ok"`` 时必须安全降级，不能把缺失当 0 分。
    """

    API_VERSION = API_VERSION

    def __init__(self, plugin: "MoeAffinityPlugin"):
        self._plugin = plugin

    def fingerprint(self) -> str:
        return f"{API_VERSION}:1"

    def role_for(self, persona_id: str) -> str:
        """人格 → 角色 id（没有就按 1:1 建立）。"""
        return self._plugin.store.ensure_role(persona_id, personas.role_label(persona_id))

    async def get(self, role_id: str, user_id: str) -> dict:
        plugin = self._plugin
        return await asyncio.to_thread(
            plugin.store.snapshot, role_id, str(user_id), plugin._rules()
        )

    async def get_mood(self, role_id: str) -> dict:
        """角色当前心情（已按半衰期投影）。"""
        return await asyncio.to_thread(self._plugin._mood_view, role_id)

    async def add_event(
        self,
        role_id: str,
        user_id: str,
        *,
        source: str,
        code: str,
        delta: float = 0.0,
        dedupe_key: str | None = None,
        evidence: str = "",
        persona_id: str = "",
        session_id: str = "",
        group_id: str = "",
        display_name: str = "",
    ) -> dict:
        plugin = self._plugin
        return await asyncio.to_thread(
            plugin.store.apply_event,
            role_id=role_id,
            user_id=str(user_id),
            code=code,
            delta=float(delta),
            source=source,
            rules=plugin._rules(),
            persona_id=persona_id,
            session_id=session_id,
            group_id=group_id,
            is_group=bool(group_id),
            evidence=evidence,
            dedupe_key=dedupe_key,
            display_name=display_name,
        )


@register(
    PLUGIN_NAME,
    "windExplorer",
    "萌萌好感度：全插件共用的好感度中枢（关系分数 / 事件流水 / 跨插件契约）",
    PLUGIN_VERSION,
)
class MoeAffinityPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        _reload_sibling_modules()
        self.config = config or {}
        self.data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.store = store.AffinityStore(self.data_dir / "affinity.db")
        self.api = AffinityApi(self)
        self.plugin_version = PLUGIN_VERSION
        logger.info(
            f"[moe_affinity] 已装载 {PLUGIN_VERSION}，数据目录 {self.data_dir}"
        )

    async def initialize(self) -> None:
        """预建角色、注册控制台路由；失败只记日志，不影响插件本体。"""
        self._checkin_task = None
        try:
            created = self._seed_roles()
            if created:
                logger.info(f"[moe_affinity] 启动时预建 {created} 个角色")
        except Exception as exc:
            logger.warning(f"[moe_affinity] 预建角色失败: {exc}")
        try:
            webui_api.register_apis(self)
        except Exception as exc:
            logger.error(f"[moe_affinity] 控制台路由注册失败: {exc}")
        try:
            self._checkin_task = asyncio.create_task(self._checkin_loop())
        except Exception as exc:
            logger.warning(f"[moe_affinity] 签到同步任务启动失败: {exc}")

    def _seed_roles(self) -> int:
        """按当前人格预建角色。

        不预建的话，装机后还没聊过天时角色列表是空的：控制台下拉框「暂无角色」，
        迁移页也选不到角色（迁移本身要先决定导到哪个角色里）。
        """
        created = 0
        try:
            manager = getattr(self.context, "persona_manager", None)
            for persona in getattr(manager, "personas_v3", []) or []:
                name = str(persona.get("name") or "").strip()
                if not name:
                    continue
                if self.store.role_of_persona(name) is None:
                    self.store.ensure_role(name, name)
                    created += 1
        except Exception as exc:
            logger.warning(f"[moe_affinity] 读取人格列表失败: {exc}")
        if not self.store.list_roles():
            self.store.ensure_role("default", "默认人格")
            created += 1
        return created

    # ------------------------------------------------------------------ 配置
    def _section(self, name: str) -> dict:
        value = self.config.get(name)
        return value if isinstance(value, dict) else {}

    def _enabled(self) -> bool:
        return bool(self.config.get("enabled", True))

    def _rules(self) -> store.Rules:
        cfg = self._section("affinity")
        return store.Rules(
            daily_gain_cap=int(cfg.get("daily_gain_cap", 30) or 30),
            daily_loss_cap=int(cfg.get("daily_loss_cap", 20) or 20),
            decay_enabled=bool(cfg.get("decay_enabled", True)),
            decay_grace_days=int(cfg.get("decay_grace_days", 3) or 3),
            decay_early=int(cfg.get("decay_early_per_day", 2) or 2),
            decay_middle=int(cfg.get("decay_middle_per_day", 5) or 5),
            decay_late=int(cfg.get("decay_late_per_day", 8) or 8),
            dedupe_window_seconds=int(self._section("advanced").get("dedupe_window_seconds", 60) or 60),
        )

    def _mood_cfg(self) -> dict:
        cfg = self._section("mood")
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "half_life_minutes": int(cfg.get("half_life_minutes", 90) or 90),
            "neutral_band": float(cfg.get("neutral_band", 2) or 2),
            "max_delta": float(cfg.get("max_delta", 2) or 2),
            "inject": bool(cfg.get("inject", True)),
            "praise_delta": float(cfg.get("praise_delta", 2) or 0),
            "negative_delta": float(cfg.get("negative_delta", 2) or 0),
        }

    def _mood_view(self, role_id: str) -> dict:
        """读时投影：只存基准值 + updated_at，展示/注入时按半衰期折算。"""
        cfg = self._mood_cfg()
        now = int(time.time())
        row = self.store.get_mood(role_id)
        baseline = float(row["baseline"]) if row else mood.DEFAULT_BASELINE
        if row:
            base_axes = {axis: float(row[axis]) for axis in mood.AXES}
            updated_at = int(row["updated_at"])
        else:
            base_axes = mood.default_axes(baseline)
            updated_at = now
        axes = mood.decayed(
            base_axes, baseline, updated_at, now, int(cfg["half_life_minutes"])
        )
        return {
            "role_id": role_id,
            "enabled": cfg["enabled"],
            "axes": mood.rounded(axes),
            "raw": {axis: round(value, 2) for axis, value in axes.items()},
            "baseline": baseline,
            "description": mood.describe(axes, baseline, cfg["neutral_band"]),
            "neutral": mood.is_neutral(axes, baseline, cfg["neutral_band"]),
            "updated_at": updated_at,
            "half_life_minutes": cfg["half_life_minutes"],
        }

    def _nudge_mood(self, role_id: str, codes: list[str]) -> None:
        cfg = self._mood_cfg()
        if not cfg["enabled"] or not codes:
            return
        deltas = mood.deltas_from_events(
            codes,
            praise=cfg["praise_delta"],
            negative=cfg["negative_delta"],
        )
        if not any(abs(value) > 0 for value in deltas.values()):
            return
        now = int(time.time())
        row = self.store.get_mood(role_id)
        baseline = float(row["baseline"]) if row else mood.DEFAULT_BASELINE
        if row:
            base_axes = {axis: float(row[axis]) for axis in mood.AXES}
            projected = mood.decayed(
                base_axes, baseline, int(row["updated_at"]), now, int(cfg["half_life_minutes"])
            )
        else:
            projected = mood.default_axes(baseline)
        updated = mood.apply_deltas(projected, deltas, max_delta=cfg["max_delta"])
        self.store.save_mood(role_id, updated, baseline=baseline, now=now)

    def _scoring(self) -> scoring.ScoringConfig:
        cfg = self._section("scoring")
        return scoring.ScoringConfig(
            daily_first=int(cfg.get("daily_first", 2) or 0),
            per_message=int(cfg.get("per_message", 1) or 0),
            attention=int(cfg.get("attention", 2) or 0),
            praise=int(cfg.get("praise_delta", 3) or 0),
            negative=int(cfg.get("negative_delta", -6) or 0),
            praise_keywords=tuple(str(x) for x in (cfg.get("praise_keywords") or []) if str(x).strip()),
            negative_keywords=tuple(
                str(x) for x in (cfg.get("negative_keywords") or ["滚", "傻逼", "去死", "讨厌你", "闭嘴"])
            ),
        )

    # ------------------------------------------------------------------ 签到同步
    def _checkin_cfg(self) -> dict:
        cfg = self._section("checkin_sync")
        return {
            "enabled": bool(cfg.get("enabled", True)),
            "interval_minutes": max(15, int(cfg.get("interval_minutes", 60) or 60)),
            "scale": float(cfg.get("scale", 1.0) or 1.0),
            "inject": bool(cfg.get("inject", True)),
            "db_path": str(cfg.get("db_path") or "").strip(),
        }

    def _checkin_db_path(self):
        """定位签到插件的库（对方没装返回 None，同步静默待机）。"""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            data_dir = Path(get_astrbot_data_path())
        except Exception:
            data_dir = None
        return checkin_sync.resolve_db_path(self._checkin_cfg()["db_path"], data_dir)

    async def _checkin_loop(self) -> None:
        """每小时扫一次「昨天 + 今天」的签到记录；幂等键保证重扫不重复加分。"""
        await asyncio.sleep(180)  # 错开启动高峰，等其它插件装载完
        while True:
            try:
                cfg = self._checkin_cfg()
                if cfg["enabled"]:
                    path = self._checkin_db_path()
                    if path is not None:
                        stats = await asyncio.to_thread(
                            checkin_sync.sync_recent,
                            self.store,
                            path,
                            scale=cfg["scale"],
                            rules=self._rules(),
                        )
                        if stats.get("written"):
                            logger.info(f"[moe_affinity] 签到同步 +{stats['written']} 条")
            except Exception as exc:
                logger.warning(f"[moe_affinity] 签到同步失败: {exc}")
            await asyncio.sleep(self._checkin_cfg()["interval_minutes"] * 60)

    async def _ensure_checkin_fresh(self, user_id: str) -> None:
        """查询指令触发的按需同步；带防重读（今天已有事件 / 60 秒节流），失败静默。"""
        cfg = self._checkin_cfg()
        if not cfg["enabled"] or not user_id:
            return
        try:
            path = self._checkin_db_path()
            if path is None:
                return
            await asyncio.wait_for(
                asyncio.to_thread(
                    checkin_sync.ensure_user_fresh,
                    self.store,
                    path,
                    str(user_id),
                    scale=cfg["scale"],
                    rules=self._rules(),
                ),
                timeout=6,
            )
        except Exception as exc:
            logger.debug(f"[moe_affinity] 签到按需同步跳过: {exc}")

    async def _checkin_view(self, role_id: str, user_id: str) -> dict | None:
        """本角色内的签到概况（指令明细行 / 注入 / 控制台共用）。

        口径与主分数一致：只统计该角色内的签到事件，score 就是这段关系总分里
        签到占的那部分；days/streak 也按本角色算。
        """
        summaries = await asyncio.to_thread(
            self.store.user_source_summaries,
            source=checkin_sync.SOURCE,
            user_ids=[str(user_id)],
            role_id=role_id,
        )
        summary = summaries.get(str(user_id))
        if not summary:
            return None
        last = await asyncio.to_thread(
            self.store.user_source_last,
            source=checkin_sync.SOURCE,
            user_id=str(user_id),
            role_id=role_id,
        )
        view = dict(summary)
        view["streak"] = checkin_sync.streak_from_evidence((last or {}).get("evidence") or "")
        return view

    # ------------------------------------------------------------------ 通用
    async def _role_for(self, event: AstrMessageEvent) -> tuple[str, str]:
        persona_id = await personas.resolve_persona_id(self.context, event)
        role_id = self.store.ensure_role(persona_id, personas.role_label(persona_id))
        return role_id, persona_id

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            if event.is_admin():
                return True
        except Exception:
            pass
        try:
            admins = self.context.get_config().get("admins_id", []) or []
            return str(event.get_sender_id()) in {str(item) for item in admins}
        except Exception:
            return False

    @staticmethod
    def _at_target(event: AstrMessageEvent) -> str:
        for component in getattr(event.message_obj, "message", None) or []:
            qq = getattr(component, "qq", None)
            if qq and str(qq).isdigit():
                return str(qq)
        return ""

    @staticmethod
    def _args(event: AstrMessageEvent) -> str:
        text = (event.message_str or "").strip()
        parts = text.split(None, 1)
        return parts[1].strip() if len(parts) > 1 else ""

    @staticmethod
    def _mood_line(mood_view: dict | None) -> str:
        if not mood_view or not mood_view.get("enabled", True):
            return ""
        axes = mood_view.get("axes") or {}
        desc = str(mood_view.get("description") or "平静")
        detail = (
            f"喜悦 {axes.get('joy', '—')} · 烦躁 {axes.get('anger', '—')} · "
            f"低落 {axes.get('sorrow', '—')} · 不安 {axes.get('fear', '—')}"
        )
        return f"心情：{desc}（{detail}）"

    def _render_detail(
        self, snapshot: dict, *, title: str, mood_view: dict | None = None,
        checkin_view: dict | None = None,
    ) -> str:
        if snapshot.get("status") != "ok":
            return f"{title}\n暂无好感度记录（还没聊过，或数据未就绪）。"
        now = int(time.time())
        days = max(0, (now - int(snapshot["first_seen_at"])) // 86400)
        gap = _human_gap(max(0, now - int(snapshot["last_interaction_at"])))
        gap_to_next = ""
        next_at = snapshot.get("next_stage_at")
        next_label = snapshot.get("next_stage_label") or ""
        if next_at and next_label:
            need = max(0, int(round(float(next_at) - float(snapshot["score"]))))
            if need > 0:
                gap_to_next = f"（距{next_label}还差 {need}）"
        lines = [
            title,
            f"总分：{int(snapshot['score'])} · {snapshot['stage_label']}{gap_to_next}",
            f"排名：{snapshot['rank']}",
            f"认识 {days} 天 · 最近互动 {gap}",
        ]
        mood_line = self._mood_line(mood_view)
        if mood_line:
            lines.append(mood_line)
        # 明细行：按来源逐行展示（签到是第一个来源，未来第三方插件继续往下换行）
        if checkin_view:
            streak = int(checkin_view.get("streak") or 0)
            streak_text = f" · 连签 {streak} 天" if streak > 0 else ""
            lines.append(
                f"签到：{checkin_view.get('score', 0)} 分"
                f" · 累计 {checkin_view.get('days', 0)} 天{streak_text}"
            )
        return "\n".join(lines)

    # ------------------------------------------------------------------ 计分
    @filter.after_message_sent()
    async def on_after_message_sent(self, event: AstrMessageEvent):
        if not self._enabled():
            return
        if event.get_extra(SKIP_SCORING):
            return
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            return
        try:
            await self._score(event, user_id)
        except Exception as exc:
            logger.warning(f"[moe_affinity] 计分失败: {exc}")

    async def _score(self, event: AstrMessageEvent, user_id: str) -> None:
        # 优先用「这轮 LLM 请求时」解析到的人格：那是真正生成回复的人格，
        # 比在 after_message_sent 重新解析更准（避免会话状态在这里已变化）。
        cached = str(event.get_extra(PERSONA_CACHE) or "").strip()
        if cached:
            persona_id = cached
            role_id = self.store.ensure_role(cached, personas.role_label(cached))
        else:
            role_id, persona_id = await self._role_for(event)
        rules = self._rules()
        conf = self._scoring()
        platform = ""
        try:
            platform = event.get_platform_name() or ""
        except Exception:
            pass
        session_id = str(getattr(event, "unified_msg_origin", "") or "")
        group_id = str(event.get_group_id() or "")
        payload = {
            "role_id": role_id,
            "user_id": user_id,
            "rules": rules,
            "persona_id": persona_id,
            "platform": platform,
            "session_id": session_id,
            "group_id": group_id,
            "is_group": bool(group_id),
            "display_name": str(event.get_sender_name() or ""),
        }
        today = datetime.now().strftime("%Y-%m-%d")
        if conf.daily_first:
            await asyncio.to_thread(
                self.store.apply_event,
                code="chat.daily_first",
                delta=float(conf.daily_first),
                dedupe_key=scoring.daily_key(role_id, user_id, today),
                **payload,
            )
        codes: list[str] = []
        for code, delta in scoring.evaluate(
            event.message_str, is_attention=bool(event.is_at_or_wake_command), cfg=conf
        ):
            codes.append(code)
            await asyncio.to_thread(
                self.store.apply_event, code=code, delta=float(delta), **payload
            )
        if codes:
            await asyncio.to_thread(self._nudge_mood, role_id, codes)

    # ------------------------------------------------------------------ 注入
    @filter.on_llm_request()
    async def inject_affinity(self, event: AstrMessageEvent, req):
        if not self._enabled() or not self._section("inject").get("enable", True):
            return
        if req is None:
            return
        if MARKER in (getattr(req, "system_prompt", "") or ""):
            return
        user_id = str(event.get_sender_id() or "")
        if not user_id:
            return
        try:
            role_id, persona_id = await self._role_for(event)
            event.set_extra(PERSONA_CACHE, persona_id)
            snapshot = await asyncio.to_thread(
                self.store.snapshot, role_id, user_id, self._rules()
            )
            mood_view = (
                await asyncio.to_thread(self._mood_view, role_id)
                if self._mood_cfg()["enabled"] and self._mood_cfg()["inject"]
                else None
            )
            checkin_view = (
                await self._checkin_view(role_id, user_id)
                if self._checkin_cfg()["inject"]
                else None
            )
        except Exception as exc:
            logger.warning(f"[moe_affinity] 读取好感度失败: {exc}")
            return
        if snapshot.get("status") != "ok":
            return
        block = self._render_block(snapshot, mood_view, checkin_view)
        req.system_prompt = (getattr(req, "system_prompt", "") or "") + "\n" + block

    def _render_block(
        self, snapshot: dict, mood_view: dict | None = None, checkin_view: dict | None = None
    ) -> str:
        cfg = self._section("inject")
        now = int(time.time())
        days = max(0, (now - int(snapshot["first_seen_at"])) // 86400)
        gap = _human_gap(max(0, now - int(snapshot["last_interaction_at"])))
        head = f"关系：{snapshot['stage_label']}"
        if cfg.get("show_score", True):
            head += f"（{int(snapshot['score'])} 分）"
        body = f"{head}，认识 {days} 天，最近互动在{gap}。"
        if mood_view and not mood_view.get("neutral"):
            body += f"心情：{mood_view.get('description', '')}。"
        if checkin_view:
            body += f"签到累计 {checkin_view.get('days', 0)} 天（亲密度 {checkin_view.get('score', 0)}）。"
        if cfg.get("include_hint", True):
            body += "这是表达参考：不要向用户播报分数，也不要把它当作权限或事实依据。"
        limit = int(cfg.get("max_chars", 140) or 140)
        if limit > 0 and len(body) > limit:
            body = body[:limit]
        return f"{MARKER}{body}</affinity_context>"

    # ------------------------------------------------------------------ 指令
    @filter.command("好感度")
    async def cmd_affinity(self, event: AstrMessageEvent):
        """查看好感度：/好感度 · /好感度 @某人 · /好感度 设置 @某人 500|+20|-10 · /好感度 诊断"""
        event.set_extra(SKIP_SCORING, True)
        args = self._args(event)
        target = self._at_target(event)
        admin = self._is_admin(event)
        sender = str(event.get_sender_id() or "")
        role_id, _ = await self._role_for(event)
        rules = self._rules()

        if args.startswith("诊断"):
            if not admin:
                yield event.plain_result("只有管理员可以查看人格诊断。")
                return
            info = await personas.explain(self.context, event)
            yield event.plain_result(
                "人格诊断（这轮会话）\n"
                f"会话：{info['umo'] or '（空）'}\n"
                f"会话级强制人格：{info['session_override'] or '（未设置）'}\n"
                f"对话 ID：{info['conversation_id'] or '（还没有对话）'}\n"
                f"对话绑定人格：{info['conversation_persona'] or '（空）'}\n"
                f"全局默认人格：{info['default_persona'] or '（未设置）'}\n"
                f"→ 归属：{info['resolved']}（来源：{info['source']}）\n"
                f"角色：{role_id}"
            )
            return

        if args.startswith("设置"):
            if not admin:
                yield event.plain_result("只有管理员可以调整好感度。")
                return
            if not target:
                yield event.plain_result("用法：/好感度 设置 @某人 500（绝对值）或 +20 / -10（增量）")
                return
            # 取第一个能解析成数字的 token；带 +/- 前缀表示增量，否则视为绝对值
            raw_value = None
            raw_token = ""
            for token in args[len("设置"):].split():
                try:
                    raw_value = float(token)
                except ValueError:
                    continue
                raw_token = token
                break
            if raw_value is None:
                yield event.plain_result("没有找到要设置的分数。用法：/好感度 设置 @某人 500")
                return
            raw = raw_value
            mode = "delta" if raw_token.startswith(("+", "-")) else "set"
            snapshot = await asyncio.to_thread(
                self.store.snapshot, role_id, target, rules
            )
            before = float(snapshot.get("score") or 0)
            delta = raw if mode == "delta" else raw - before
            result = await asyncio.to_thread(
                self.store.apply_event,
                role_id=role_id,
                user_id=target,
                code="manual.adjust",
                delta=delta,
                source="manual",
                rules=rules,
                operator=sender,
                evidence=args,
                display_name="",
            )
            yield event.plain_result(
                f"已调整 {target}：{int(before)} → {int(result.get('score') or 0)}"
            )
            return

        # 查询前顺手补一次签到同步（带防重读：今天已有事件 / 60 秒节流）
        await self._ensure_checkin_fresh(target if (target and target != sender) else sender)

        if target and target != sender:
            if not admin:
                yield event.plain_result("普通成员只能查看自己的好感度。")
                return
            snapshot = await asyncio.to_thread(self.store.snapshot, role_id, target, rules)
            target_name = str(snapshot.get("display_name") or "").strip() or target
            alias = await asyncio.to_thread(self.store.role_alias, role_id)
            title = (
                f"{target_name} 与「{alias}」的关系" if alias else f"「{target_name}」的好感度"
            )
            mood_view = await asyncio.to_thread(self._mood_view, role_id)
            checkin_view = await self._checkin_view(role_id, target)
            yield event.plain_result(
                self._render_detail(
                    snapshot, title=title, mood_view=mood_view, checkin_view=checkin_view
                )
            )
            return

        if not sender:
            yield event.plain_result("拿不到你的用户 ID，无法查询。")
            return
        snapshot = await asyncio.to_thread(self.store.snapshot, role_id, sender, rules)
        recent = await asyncio.to_thread(self.store.recent_events, role_id, sender, 3)
        # 标题：人格/角色设了别名就用别名（「你与『小叽』的关系」），
        # 没设别名就退回被查询者的 QQ 昵称，不要暴露 persona id。
        name = (
            str(event.get_sender_name() or "").strip()
            or str(snapshot.get("display_name") or "").strip()
            or sender
        )
        alias = await asyncio.to_thread(self.store.role_alias, role_id)
        title = f"你与「{alias}」的关系" if alias else f"「{name}」的好感度"
        mood_view = await asyncio.to_thread(self._mood_view, role_id)
        checkin_view = await self._checkin_view(role_id, sender)
        text = self._render_detail(
            snapshot, title=title, mood_view=mood_view, checkin_view=checkin_view
        )
        if recent:
            parts = [
                f"{store.CODE_LABELS.get(item['code'], item['code'])} {int(item['delta']):+d}"
                for item in recent
            ]
            text += "\n最近：" + " · ".join(parts)
        yield event.plain_result(text)

    @filter.command("好感榜")
    async def cmd_ranking(self, event: AstrMessageEvent):
        """好感度排行（管理员）：/好感榜 [人数]"""
        event.set_extra(SKIP_SCORING, True)
        if not self._is_admin(event):
            yield event.plain_result("只有管理员可以查看排行榜。")
            return
        args = self._args(event)
        limit = 10
        for token in args.split():
            if token.isdigit():
                limit = max(1, min(50, int(token)))
                break
        role_id, _ = await self._role_for(event)
        rows = await asyncio.to_thread(self.store.ranking, role_id, self._rules(), limit=limit)
        if not rows:
            yield event.plain_result(f"「{role_id}」还没有任何好感度记录。")
            return
        lines = [f"「{role_id}」好感榜"]
        for row in rows:
            name = row["display_name"] or row["user_id"]
            lines.append(f"{row['rank']}. {name} — {int(row['score'])} · {row['stage_label']}")
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ LLM 工具
    @filter.llm_tool(name="query_affinity")
    async def query_affinity(self, event: AstrMessageEvent, target: str = "me") -> str:
        """查询当前角色与某人的好感度（只读）。

        只应在用户明确询问好感度、关系如何、排名多少时调用。
        不要凭记忆或猜测编造分数；返回 not_recorded 表示还没有记录，不是 0 分。

        Args:
            target(string): 查询对象。填 "me" 表示当前用户；管理员可填对方 QQ 号。
        """
        sender = str(event.get_sender_id() or "")
        who = (target or "me").strip()
        if who in ("me", "", "我", "自己"):
            who = sender
        elif who != sender and not self._is_admin(event):
            return '{"status":"denied","message":"普通成员只能查询自己的好感度。"}'
        await self._ensure_checkin_fresh(who)
        role_id, persona_id = await self._role_for(event)
        snapshot = await asyncio.to_thread(self.store.snapshot, role_id, who, self._rules())
        snapshot.update(role_id=role_id, persona_id=persona_id, read_only=True)
        snapshot["checkin"] = await self._checkin_view(role_id, who)
        return json.dumps(snapshot, ensure_ascii=False)

    async def terminate(self):
        task = getattr(self, "_checkin_task", None)
        if task is not None:
            task.cancel()
        return None
