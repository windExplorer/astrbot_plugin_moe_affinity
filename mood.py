"""四轴心情（纯函数，不依赖 astrbot，可直接单测）。

口径（与 docs/设计说明.md 第 4 节一致）：

- 四轴 ``joy / anger / sorrow / fear``，取值 1–10，整数展示、内部允许小数；
- 心情属于**角色自己**，与「对谁的好感度」无关；
- 衰减是**读时投影**：只存基准值与 ``updated_at``，展示/注入时按半衰期折算，不写回库；
- 静息目标：``joy`` 回落到角色 baseline，其余三轴回落到 1；
- 回落不封顶、加速封顶：单次更新的每个轴最多变动 ``max_delta``，
  所以一条消息不可能把心情从一端甩到另一端。
"""

from __future__ import annotations

AXES = ("joy", "anger", "sorrow", "fear")
MIN_AXIS = 1.0
MAX_AXIS = 10.0
DEFAULT_BASELINE = 5.0


def rest_target(axis: str, baseline: float) -> float:
    return float(baseline) if axis == "joy" else MIN_AXIS


def _clamp(value: float) -> float:
    return max(MIN_AXIS, min(MAX_AXIS, float(value)))


def default_axes(baseline: float = DEFAULT_BASELINE) -> dict[str, float]:
    return {"joy": _clamp(baseline), "anger": MIN_AXIS, "sorrow": MIN_AXIS, "fear": MIN_AXIS}


def decayed(
    axes: dict[str, float],
    baseline: float,
    updated_at: int,
    now: int,
    half_life_minutes: int,
) -> dict[str, float]:
    """按半衰期把各轴折算到 ``now``（不修改传入的 axes）。"""
    if half_life_minutes <= 0:
        return {axis: _clamp(axes.get(axis, rest_target(axis, baseline))) for axis in AXES}
    minutes = max(0.0, (float(now) - float(updated_at)) / 60.0)
    remaining = 0.5 ** (minutes / float(half_life_minutes))
    out: dict[str, float] = {}
    for axis in AXES:
        target = rest_target(axis, baseline)
        previous = float(axes.get(axis, target))
        out[axis] = _clamp(previous + (target - previous) * (1.0 - remaining))
    return out


def apply_deltas(
    axes: dict[str, float],
    deltas: dict[str, float],
    *,
    max_delta: float = 2.0,
) -> dict[str, float]:
    """单轮更新：每个轴的变动量被夹在 ±max_delta 内，结果夹在 1–10。"""
    limit = max(0.0, float(max_delta))
    out: dict[str, float] = {}
    for axis in AXES:
        change = max(-limit, min(limit, float(deltas.get(axis, 0.0))))
        out[axis] = _clamp(float(axes.get(axis, MIN_AXIS)) + change)
    return out


def rounded(axes: dict[str, float]) -> dict[str, int]:
    return {axis: int(round(_clamp(axes.get(axis, MIN_AXIS)))) for axis in AXES}


def is_neutral(axes: dict[str, float], baseline: float, band: float) -> bool:
    """所有轴都落在静息目标附近 `band` 以内即视为平静。"""
    for axis in AXES:
        if abs(float(axes.get(axis, MIN_AXIS)) - rest_target(axis, baseline)) > float(band):
            return False
    return True


def dominant(axes: dict[str, float], baseline: float) -> tuple[str, float]:
    """偏离静息最远的轴及其偏离量。"""
    best_axis, best_gap = "joy", 0.0
    for axis in AXES:
        gap = float(axes.get(axis, MIN_AXIS)) - rest_target(axis, baseline)
        if abs(gap) > abs(best_gap):
            best_axis, best_gap = axis, gap
    return best_axis, best_gap


def describe(axes: dict[str, float], baseline: float, band: float) -> str:
    """一句话心情描述，供注入与控制台使用（不播报数字）。"""
    if is_neutral(axes, baseline, band):
        return "心情平静"
    axis, gap = dominant(axes, baseline)
    strong = abs(gap) >= max(2.0, float(band))
    phrases = {
        "joy": ("心情不错", "心情很好"),
        "anger": ("有点不高兴", "在生气"),
        "sorrow": ("有点低落", "很低落"),
        "fear": ("有些不安", "有些紧张"),
    }
    light, heavy = phrases[axis]
    if axis == "joy" and gap < 0:
        # joy 低于 baseline 也是可能的（被冷落），但不要用「很好」的文案
        return "有点提不起劲"
    return heavy if strong else light


def deltas_from_events(codes: list[str], *, praise: float = 2.0, negative: float = 2.0,
                       attention: float = 0.5) -> dict[str, float]:
    """把一轮计分事件码翻译成心情变化（规则驱动，不调 LLM）。"""
    deltas = {"joy": 0.0, "anger": 0.0, "sorrow": 0.0, "fear": 0.0}
    for code in codes:
        if code == "chat.praise":
            deltas["joy"] += praise
        elif code == "chat.negative":
            deltas["anger"] += negative
            deltas["sorrow"] += 0.5
            deltas["joy"] -= 0.5
        elif code == "chat.attention":
            deltas["joy"] += attention
    return deltas
