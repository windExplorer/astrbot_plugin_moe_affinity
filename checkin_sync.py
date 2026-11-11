"""从签到插件（astrbot_plugin_get_px，画境拾珍）单向同步签到好感。

只读它的 ``checkin.sqlite3``，不改它的任何数据。口径见 docs/设计说明.md（v0.12.1 起）：

- 签到分**并入主分数**：每条签到记录写到该用户**有记录的每个角色**各一份
  （用户明确选择：私聊角色 A、群聊角色 B 都能感受到签到情分）；
- ``delta = 对方当日 affection_reward × 系数``（连签/双倍卡语义原样保留，evidence 留档）；
- ``dedupe_key = checkin:{role}:{user}:{date}``：签到一天最多一次，天然幂等，
  重扫/重导入绝不重复加分；走 ``store.apply_event`` 落库（主分数/档位/榜单直接可见，
  事件时间用签到记录的真实时间，不刷新成同步时刻）；
- 没有任何角色记录的用户先不入账（等聊过天之后的下一次「导入全部历史」会补齐）；
- 顺带把 ``checkin_users.created_at``（首次签到建档时间）回填为认识时间（只提前、不推后）。

同步时机有三条，共用同一套幂等键：

1. 后台循环每小时扫「昨天 + 今天」（``sync_recent``）；
2. 控制台手动「立即同步 / 导入全部历史」（``sync_recent`` / ``sync_full``）；
3. 用户用 /好感度 查询时按需补一次（``ensure_user_fresh``，带防重读：
   该用户今天的事件已在库里就完全不碰对方库；60 秒内读过也跳过）。

本模块只依赖标准库，调用方传入 store 与 rules（鸭子类型），便于纯函数自检。
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SOURCE = "checkin"  # 与 store.CODE_LABELS 的「签到」条目保持一致
CODE = "checkin.checkin"
PLUGIN_DIR_NAME = "astrbot_plugin_get_px"
DB_FILENAME = "checkin.sqlite3"
DEDUPE_PREFIX = "checkin:"

# 指令触发按需读取的节流：签到一天只有一次，读不到新东西就别反复开对方的库。
THROTTLE_SECONDS = 60
_USER_CHECK_AT: dict[str, float] = {}


def dedupe_key(role_id: str, user_id: str, date_key: str) -> str:
    """幂等键带角色：同一份记录按角色各落一份、各自只落一次。"""
    return f"{DEDUPE_PREFIX}{role_id}:{user_id}:{date_key}"


# ------------------------------------------------------------------ 时间
def _now_shanghai() -> datetime:
    """对方插件的 date_key 以 Asia/Shanghai 为基准，边界判断保持同一时区。"""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:  # Windows 缺 tzdata 等场景：退回本机时区
        return datetime.now()


def today_key() -> str:
    return _now_shanghai().date().isoformat()


def today_start_epoch() -> int:
    """今天（上海时区）零点的秒级时间戳，用于「今天是否已同步过」的存在性检查。"""
    return int(_now_shanghai().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def since_key(days: int = 2) -> str:
    """最近 N 天的起始 date_key（含今天）。"""
    d = date.fromisoformat(today_key()) - timedelta(days=max(1, int(days)) - 1)
    return d.isoformat()


def parse_ts(text: str) -> int:
    """对方库的 ISO 文本时间（含 +08:00 时区）→ 秒级时间戳；解析失败返回 0。"""
    raw = str(text or "").strip()
    if not raw:
        return 0
    try:
        return int(datetime.fromisoformat(raw).timestamp())
    except Exception:
        return 0


# ------------------------------------------------------------------ 只读对方库
def resolve_db_path(explicit: str | None, astrbot_data_dir: Path | None = None) -> Path | None:
    """定位对方库：显式路径优先；否则 data/plugin_data/astrbot_plugin_get_px/checkin.sqlite3。"""
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    if astrbot_data_dir is None:
        return None
    candidate = Path(astrbot_data_dir) / "plugin_data" / PLUGIN_DIR_NAME / DB_FILENAME
    return candidate if candidate.is_file() else None


def _connect(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1", (name,)
    ).fetchone()
    return row is not None


def read_records(db_path: Path, *, user_id: str = "", since_date: str = "") -> list[dict[str, Any]]:
    """读签到记录（可按用户、按 date_key 下限过滤）。库/表不存在返回空列表。"""
    conn = _connect(db_path)
    try:
        if not _has_table(conn, "checkin_records"):
            return []
        sql = (
            "SELECT date_key, user_id, username, base_affection, bonus_affection,"
            " affection_reward, boost_active, boost_multiplier, streak_days_after, created_at"
            " FROM checkin_records"
        )
        where: list[str] = []
        args: list[Any] = []
        if user_id:
            where.append("user_id = ?")
            args.append(str(user_id))
        if since_date:
            where.append("date_key >= ?")
            args.append(str(since_date))
        if where:
            sql += " WHERE " + " AND ".join(where)
        rows = conn.execute(sql + " ORDER BY date_key ASC, user_id ASC", args).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def read_profile_created(db_path: Path, user_ids: list[str]) -> dict[str, int]:
    """读首次签到建档时间（认识时间证据）。表不存在返回空。"""
    uids = sorted({str(u) for u in user_ids if str(u)})
    if not uids:
        return {}
    conn = _connect(db_path)
    try:
        if not _has_table(conn, "checkin_users"):
            return {}
        placeholders = ",".join("?" for _ in uids)
        rows = conn.execute(
            f"SELECT user_id, created_at FROM checkin_users WHERE user_id IN ({placeholders})",
            uids,
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, int] = {}
    for row in rows:
        stamp = parse_ts(row["created_at"])
        if stamp > 0:
            out[str(row["user_id"])] = stamp
    return out


def describe(db_path: Path | None) -> dict[str, Any]:
    """对方库概况（控制台状态卡用）。"""
    if db_path is None:
        return {"exists": False, "path": "", "users": 0, "records": 0, "first_date": "", "last_date": ""}
    conn = _connect(db_path)
    try:
        if not _has_table(conn, "checkin_records"):
            return {"exists": True, "path": str(db_path), "users": 0, "records": 0,
                    "first_date": "", "last_date": ""}
        users = int(conn.execute("SELECT COUNT(*) FROM checkin_users").fetchone()[0])
        agg = conn.execute(
            "SELECT COUNT(*) AS c, MIN(date_key) AS f, MAX(date_key) AS m FROM checkin_records"
        ).fetchone()
        return {
            "exists": True,
            "path": str(db_path),
            "users": users,
            "records": int(agg["c"] or 0),
            "first_date": str(agg["f"] or ""),
            "last_date": str(agg["m"] or ""),
        }
    finally:
        conn.close()


# ------------------------------------------------------------------ 事件构建与写入
def build_event(row: dict[str, Any], scale: float) -> dict[str, Any] | None:
    """一条签到记录 → 一条待落库事件（写入时会按角色展开）。delta 为 0 的记录跳过。"""
    uid = str(row.get("user_id") or "").strip()
    date_key = str(row.get("date_key") or "").strip()
    if not uid or not date_key:
        return None
    delta = round(float(row.get("affection_reward") or 0.0) * float(scale), 2)
    if delta == 0.0:
        return None
    return {
        "user_id": uid,
        "date_key": date_key,
        "delta": delta,
        "created_at": parse_ts(str(row.get("created_at") or "")),
        "evidence": json.dumps(
            {
                "from": "get_px",
                "date": date_key,
                "base": row.get("base_affection"),
                "bonus": row.get("bonus_affection"),
                "streak": row.get("streak_days_after"),
                "boost": bool(row.get("boost_active")),
                "username": str(row.get("username") or ""),
            },
            ensure_ascii=False,
        ),
    }


def streak_from_evidence(text: str) -> int:
    """从最近一条事件 evidence 里取连签天数（解析失败返回 0）。"""
    try:
        data = json.loads(str(text or ""))
        return max(0, int(data.get("streak") or 0))
    except Exception:
        return 0


def _sync_rows(store, rows: list[dict[str, Any]], *, scale: float, rules, profiles: dict[str, int]) -> dict[str, int]:
    """把签到记录写进「该用户有记录的每个角色」，返回落库统计。"""
    events = [e for e in (build_event(row, scale) for row in rows) if e is not None]
    written = 0
    if events:
        roles = store.roles_of_users([e["user_id"] for e in events])
        for event in events:
            for role_id in roles.get(event["user_id"], []):
                snapshot = store.apply_event(
                    role_id=role_id,
                    user_id=event["user_id"],
                    code=CODE,
                    delta=event["delta"],
                    source=SOURCE,
                    rules=rules,
                    evidence=event["evidence"],
                    dedupe_key=dedupe_key(role_id, event["user_id"], event["date_key"]),
                    now=event["created_at"] or None,  # 用签到真实时间，衰减结算链保持正确
                )
                if snapshot.get("status") == "ok" and float(snapshot.get("applied") or 0) != 0:
                    written += 1
    # 首次签到建档时间也是「第一次见面」的证据：回填认识时间（只提前、不推后）
    mapping = {uid: ts for uid, ts in profiles.items() if ts > 0}
    repaired = store.repair_first_seen_all(mapping) if mapping else 0
    return {"records": len(rows), "written": written, "first_seen_repaired": repaired}


def sync_recent(store, db_path: Path, *, scale: float = 1.0, rules=None, days: int = 2) -> dict[str, int]:
    """增量同步最近 N 天（后台循环用，幂等）。"""
    rows = read_records(db_path, since_date=since_key(days))
    profiles = read_profile_created(db_path, [str(r["user_id"]) for r in rows])
    return _sync_rows(store, rows, scale=scale, rules=rules, profiles=profiles)


def sync_full(store, db_path: Path, *, scale: float = 1.0, rules=None) -> dict[str, int]:
    """全量导入历史（控制台按钮用，幂等：已导入过的被唯一索引吞掉）。"""
    rows = read_records(db_path)
    profiles = read_profile_created(db_path, [str(r["user_id"]) for r in rows])
    return _sync_rows(store, rows, scale=scale, rules=rules, profiles=profiles)


def ensure_user_fresh(store, db_path: Path | None, user_id: str, *, scale: float = 1.0, rules=None) -> dict[str, Any]:
    """指令触发的按需同步：只看这一个用户，带两层防重读。

    1) 该用户今天的事件已经在库里 → 直接返回 ``fresh``，完全不碰对方库；
    2) 60 秒内刚读过 → 返回 ``throttled``（连点 /好感度 不会反复开对方的数据库）。
    """
    uid = str(user_id or "").strip()
    if not uid or db_path is None:
        return {"status": "unavailable"}
    if store.has_user_source_since(source=SOURCE, user_id=uid, since=today_start_epoch()):
        return {"status": "fresh"}
    now = time.time()
    if now - _USER_CHECK_AT.get(uid, 0.0) < THROTTLE_SECONDS:
        return {"status": "throttled"}
    _USER_CHECK_AT[uid] = now
    rows = read_records(db_path, user_id=uid, since_date=since_key(2))
    profiles = read_profile_created(db_path, [uid])
    stats = _sync_rows(store, rows, scale=scale, rules=rules, profiles=profiles)
    stats["status"] = "ok"
    return stats
