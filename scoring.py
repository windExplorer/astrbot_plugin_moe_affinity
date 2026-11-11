"""计分规则（纯函数，不依赖 astrbot，可直接单测）。

原则：

- 负面优先：命中负向词时这一条不再产生任何正向分，避免「一边骂一边涨」；
- 正面关键词默认关闭（空列表），因为中文短词误判率极高（自我学习插件踩过 `好`/`6`/`hi` 的坑）；
- 分数只描述「这一轮发生了什么」，每日上限由 store 层统一夹紧。
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ScoringConfig:
    daily_first: int = 2
    per_message: int = 1
    attention: int = 2
    praise: int = 3
    negative: int = -6
    praise_keywords: tuple[str, ...] = field(default_factory=tuple)
    negative_keywords: tuple[str, ...] = field(
        default_factory=lambda: ("滚", "傻逼", "去死", "讨厌你", "闭嘴")
    )


def _hits(text: str, keywords: tuple[str, ...]) -> list[str]:
    return [word for word in keywords if word and word in text]


def evaluate(text: str, *, is_attention: bool = False, cfg: ScoringConfig | None = None) -> list[tuple[str, float]]:
    """一轮用户消息 → 若干 (事件码, 增量)。

    ``chat.daily_first`` 不在这里产生：它需要「每人每天一次」的幂等键，由调用方负责。
    """
    conf = cfg or ScoringConfig()
    body = (text or "").strip()
    if not body:
        return []

    bad = _hits(body, conf.negative_keywords)
    if bad:
        return [("chat.negative", float(conf.negative))]

    events: list[tuple[str, float]] = []
    good = _hits(body, conf.praise_keywords)
    if good:
        events.append(("chat.praise", float(conf.praise)))
    if is_attention:
        events.append(("chat.attention", float(conf.attention)))
    events.append(("chat.message", float(conf.per_message)))
    return events


def daily_key(role_id: str, user_id: str, day: str) -> str:
    """每日首次对话的幂等键。"""
    return f"daily_first:{role_id}:{user_id}:{day}"
