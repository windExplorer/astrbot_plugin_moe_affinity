"""把「当前生效的规则」整理成结构化数据，供控制台的规则说明页渲染。

纯函数，不依赖 astrbot，可直接单测。所有数值都来自调用方传入的配置对象，
所以改配置后这一页会跟着变，不会出现「页面写 1000、实际已经改了」的情况。

未来第三方插件接入时，只要往 ``sources`` 里追加一项（来源 id / 名称 / 事件表），
页面不需要改结构。
"""

from __future__ import annotations

from typing import Any


def _stage_rows(stages: list[tuple[int, str, str]]) -> list[dict[str, Any]]:
    """[(下界, 键, 名称)] → 带区间的行（从低到高）。"""
    ordered = sorted(stages, key=lambda item: item[0])
    rows = []
    for index, (floor, key, label) in enumerate(ordered):
        ceiling = ordered[index + 1][0] - 1 if index + 1 < len(ordered) else None
        rows.append({"key": key, "label": label, "floor": floor, "ceiling": ceiling})
    return rows


def _decay_rows(rules: Any) -> list[dict[str, Any]]:
    grace = max(0, int(getattr(rules, "decay_grace_days", 3)))
    early = int(getattr(rules, "decay_early", 2))
    middle = int(getattr(rules, "decay_middle", 5))
    late = int(getattr(rules, "decay_late", 8))
    candidates = [
        (1, grace, 0),
        (grace + 1, 7, early),
        (8, 14, middle),
        (15, None, late),
    ]
    rows = []
    for start, end, per_day in candidates:
        if start > 7 and end == 7:
            continue  # 宽限期过长时，早段区间已经空了
        text = f"第 {start} 天起" if end is None else f"第 {start}–{end} 天"
        if end is not None and start > end:
            continue
        rows.append({"range": text, "per_day": per_day})
    return rows


def _event_rows(scoring_cfg: Any) -> list[dict[str, Any]]:
    return [
        {
            "code": "chat.daily_first",
            "label": "每日首次对话",
            "delta": int(getattr(scoring_cfg, "daily_first", 2)),
            "note": "每个角色×用户每天只记一次",
        },
        {
            "code": "chat.message",
            "label": "普通对话",
            "delta": int(getattr(scoring_cfg, "per_message", 1)),
            "note": "受每日涨分上限约束",
        },
        {
            "code": "chat.attention",
            "label": "被 @ / 唤醒",
            "delta": int(getattr(scoring_cfg, "attention", 2)),
            "note": "同上，额外奖励",
        },
        {
            "code": "chat.praise",
            "label": "夸奖",
            "delta": int(getattr(scoring_cfg, "praise", 3)),
            "note": "需命中夸奖词；默认词表为空 = 关闭",
        },
        {
            "code": "chat.negative",
            "label": "负向互动",
            "delta": int(getattr(scoring_cfg, "negative", -6)),
            "note": "命中负向词；该条不再产生任何正向分",
        },
    ]


def _mood_rows(mood_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    praise = float(mood_cfg.get("praise_delta", 2) or 0)
    negative = float(mood_cfg.get("negative_delta", 2) or 0)
    return [
        {"trigger": "被夸奖", "changes": [["喜悦", praise]], "note": "受单轮上限约束"},
        {
            "trigger": "被冒犯",
            "changes": [["烦躁", negative], ["低落", 0.5], ["喜悦", -0.5]],
            "note": "负面优先，同时压低喜悦",
        },
        {"trigger": "被叫 / @", "changes": [["喜悦", 0.5]], "note": "轻微正反馈"},
    ]


def build(
    *,
    rules: Any,
    scoring_cfg: Any,
    mood_cfg: dict[str, Any],
    stages: list[tuple[int, str, str]],
    version: str = "",
) -> dict[str, Any]:
    """组装规则说明页需要的全部数据。"""
    gain_cap = int(getattr(rules, "daily_gain_cap", 30))
    loss_cap = int(getattr(rules, "daily_loss_cap", 20))
    dedupe = int(getattr(rules, "dedupe_window_seconds", 60))

    return {
        "version": version,
        "score": {
            "unbounded": True,
            "floor": 0,
            "stages": _stage_rows(stages),
            "note": "分数没有上限；档位按绝对阈值划分，越往后越难。",
        },
        "daily": {
            "gain_cap": gain_cap,
            "loss_cap": loss_cap,
            "dedupe_seconds": dedupe,
            "note": "每日上限只约束核心计分；管理员调整与迁移导入不受限。",
        },
        "decay": {
            "enabled": bool(getattr(rules, "decay_enabled", True)),
            "rows": _decay_rows(rules),
            "note": "只有「连续无有效互动」才衰减；读写时惰性结算，不跑定时任务。",
        },
        "events": _event_rows(scoring_cfg),
        "keywords": {
            "praise": list(getattr(scoring_cfg, "praise_keywords", ()) or ()),
            "negative": list(getattr(scoring_cfg, "negative_keywords", ()) or ()),
        },
        "mood": {
            "enabled": bool(mood_cfg.get("enabled", True)),
            "inject": bool(mood_cfg.get("inject", True)),
            "half_life_minutes": int(mood_cfg.get("half_life_minutes", 90)),
            "max_delta": float(mood_cfg.get("max_delta", 2)),
            "neutral_band": float(mood_cfg.get("neutral_band", 2)),
            "axes": [
                {"key": "joy", "label": "喜悦", "rest": "回落到角色基线"},
                {"key": "anger", "label": "烦躁", "rest": "回落到 1"},
                {"key": "sorrow", "label": "低落", "rest": "回落到 1"},
                {"key": "fear", "label": "不安", "rest": "回落到 1"},
            ],
            "rows": _mood_rows(mood_cfg),
            "note": "心情属于角色自己，与「对谁的好感度」无关；读时按半衰期投影，不写库。",
        },
        "scope": {
            "unit": "角色 × 用户",
            "persona": "人格是角色的外壳，默认 1:1，可在「角色」页合并；合并取最高分。",
            "group": "群不各存一份分数，群视图展示的是「本群贡献了多少分」。",
        },
        "timing": "只有机器人真的回复了这条消息才计分（after_message_sent）；纯指令、报错、未被唤醒都不计分。",
        "sources": [
            {
                "id": "core",
                "name": "本插件",
                "type": "builtin",
                "events": [row["code"] for row in _event_rows(scoring_cfg)],
                "note": "规则驱动，不额外调用 LLM。",
            }
        ],
        "third_party": {
            "available": False,
            "note": "第三方插件可通过 affinity.v1 契约写入事件；接入后会在这里按来源列出规则。",
        },
    }
