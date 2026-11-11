"""从自我学习插件（astrbot_plugin_self_learning）迁移旧好感度。

只读它的 ``messages.db``（SQLite），不改它的任何数据；``clear_active_mood`` 是唯一的写操作，
需要调用方显式开启，且只把 ``bot_mood.is_active`` 置 0（保留历史行）。

迁移口径（与 docs/设计说明.md 第 11 节一致）：

- 老库里是 ``per_group``：同一个人在多个群各有一行；
- 导入到我们这边是「角色 × 用户」一份，所以**按用户取最高**（最高分那条代表这段关系的最好证据），
  其余群的记录整条塞进事件 ``evidence`` 留档；
- 分数按比例换算：``老分 / 老上限 * 我们的上限``，不直接搬数字（老库上限是 100，我们是 1000）。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

SOURCE = "import:self_learning"
CODE = "import.seed"
DEDUPE_PREFIX = f"{SOURCE}:"


def role_prefix(role_id: str) -> str:
    """某个角色的导入幂等键前缀。

    **必须带角色**：老数据是「一个用户一份」，但我们这边是「角色 × 用户」，
    同一个人要分别导入到每个角色里。如果全局去重，导入第二个人格时会被判成「已导入」。
    """
    return f"{DEDUPE_PREFIX}{role_id}:"


def dedupe_key(role_id: str, user_id: str) -> str:
    return f"{role_prefix(role_id)}{user_id}"


def resolve_db_path(explicit: str | None, default_dir: Path | None) -> Path | None:
    """定位老库文件：显式路径优先，其次插件数据目录下的 messages.db。"""
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    if default_dir is None:
        return None
    candidate = Path(default_dir) / "messages.db"
    return candidate if candidate.is_file() else None


def read_legacy(db_path: Path) -> list[dict[str, Any]]:
    """读老库并按用户聚合。读不到表 / 文件损坏时抛异常，由调用方转成提示。"""
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT group_id, user_id, affection_level, max_affection, updated_at"
            " FROM user_affections"
        ).fetchall()
    finally:
        conn.close()

    by_user: dict[str, dict[str, Any]] = {}
    for row in rows:
        user_id = str(row["user_id"] or "").strip()
        if not user_id:
            continue
        limit = float(row["max_affection"] or 100) or 100.0
        score = float(row["affection_level"] or 0)
        ratio = max(0.0, min(1.0, score / limit))
        entry = by_user.setdefault(
            user_id,
            {"user_id": user_id, "ratio": 0.0, "best_score": 0.0, "best_limit": limit,
             "last_at": 0, "groups": []},
        )
        entry["groups"].append(
            {"group_id": str(row["group_id"] or ""), "score": round(score, 1), "limit": round(limit, 1)}
        )
        entry["last_at"] = max(int(entry["last_at"]), int(row["updated_at"] or 0))
        if ratio > entry["ratio"] or (ratio == entry["ratio"] and score > entry["best_score"]):
            entry["ratio"] = ratio
            entry["best_score"] = score
            entry["best_limit"] = limit
    return sorted(by_user.values(), key=lambda item: item["ratio"], reverse=True)


# 老库是 100 分制，我们无上限；按「老库满分对应 1000 分（亲密档）」换算，
# 这样迁移过来的熟人不会一上来就顶到羁绊。
MIGRATION_SCALE = 1000


def build_plan(legacy_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把老库记录换算成目标分数（比例 × MIGRATION_SCALE，不设上限）。"""
    plan = []
    for row in legacy_rows:
        target = int(round(row["ratio"] * MIGRATION_SCALE))
        plan.append(
            {
                "user_id": row["user_id"],
                "legacy_score": round(row["best_score"], 1),
                "legacy_max": round(row["best_limit"], 1),
                "target_score": max(0, target),
                "groups": row["groups"],
                "group_count": len(row["groups"]),
                "last_at": int(row["last_at"]),
            }
        )
    return plan


def evidence_json(item: dict[str, Any]) -> str:
    return json.dumps(
        {"from": "self_learning", "legacy_score": item["legacy_score"],
         "legacy_max": item["legacy_max"], "groups": item["groups"]},
        ensure_ascii=False,
    )


def clear_active_mood(db_path: Path) -> int:
    """把老库残留的「当前心情」置为非活跃，避免闸门/面板继续读到过期心情。"""
    conn = sqlite3.connect(db_path.as_posix(), timeout=5)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "bot_mood" not in tables:
            return 0
        cursor = conn.execute("UPDATE bot_mood SET is_active = 0 WHERE is_active = 1")
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()
