"""从萌萌星语（astrbot_plugin_moe_star_whisper）单向同步星尘好感。

只读它的 ``star_whisper.db``，不改它的任何数据。口径见 docs/设计说明.md（v0.13.0 起）：

- 每日抽签是「持续来见面」的互动行为，按签到同款模式**并入主分数**：
  写到该用户**有记录的每个角色**各一份（用户在签到同步时已明确选择此展开方式）；
- ``delta = 对方当日抽签星尘实得 × 系数``（默认 0.1，即每天抽签约 +1~2 分、
  周日双倍最多 +5；吉凶/连签/双倍日加成都在同一笔 ``reason='draw'`` 流水里，
  整笔按实得折算，evidence 留档）；
- 只同步正项 ``reason='draw'``：买道具 / 玩法消费不回扣好感度（花钱 ≠ 变冷淡），
  管理员手动发放（无 ref_date）也不转（那是运营操作，不是互动情分）；
- ``dedupe_key = stardust:{role}:{user}:{date}:draw``：抽签一天最多一笔，天然幂等；
- 没有任何角色记录的用户先不入账（等聊过天之后的下一次同步会补齐）。

**送礼不走这里**：送礼是星语侧花星尘买好感度的即时服务，用户当场要看到结果，
由星语直接调跨插件契约 ``AffinityApi.gift_to_user``（main.py），不入库同步。

同步时机（与签到共用同一后台循环，幂等键保证重扫不重复加分）：
1. 后台循环每小时扫「昨天 + 今天」（``sync_recent``）；
2. 用户 /好感度 查询时按需补一次（``ensure_user_fresh``，带防重读）。

本模块只依赖标准库，调用方传入 store 与 rules（鸭子类型），便于纯函数自检。
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SOURCE = "stardust"  # 与 store.CODE_LABELS 的「星尘」条目保持一致
CODE_DRAW = "stardust.draw"
CODE_GIFT = "stardust.gift"  # 送礼事件由 main.py 的契约直接写入，这里只定义口径
PLUGIN_DIR_NAME = "astrbot_plugin_moe_star_whisper"
DB_FILENAME = "star_whisper.db"
DEDUPE_PREFIX = "stardust:"

# 指令触发按需读取的节流：抽签一天只有一笔，读不到新东西就别反复开对方的库。
THROTTLE_SECONDS = 60
_USER_CHECK_AT: dict[str, float] = {}


def dedupe_key(role_id: str, user_id: str, date_key: str) -> str:
    """幂等键带角色：同一份抽签流水按角色各落一份、各自只落一次。"""
    return f"{DEDUPE_PREFIX}{role_id}:{user_id}:{date_key}:draw"


def gift_dedupe_key(role_id: str, user_id: str, client_ref: str) -> str:
    """送礼幂等键（星语侧传 client_ref，如 ``gift:2026-11-12:2``）。"""
    return f"{DEDUPE_PREFIX}{role_id}:{user_id}:{client_ref}"


# ------------------------------------------------------------------ 时间
def _now_shanghai() -> datetime:
    """对方插件的 ref_date 以插件时区（默认 Asia/Shanghai）为基准，边界判断保持同一时区。"""
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
    """最近 N 天的起始 ref_date（含今天）。"""
    d = date.fromisoformat(today_key()) - timedelta(days=max(1, int(days)) - 1)
    return d.isoformat()


def parse_ts(text: str) -> int:
    """对方库的本地时间文本（%Y-%m-%d %H:%M:%S）→ 秒级时间戳；解析失败返回 0。"""
    raw = str(text or "").strip()
    if not raw:
        return 0
    try:
        return int(datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").timestamp())
    except Exception:
        try:
            return int(datetime.fromisoformat(raw).timestamp())
        except Exception:
            return 0


# ------------------------------------------------------------------ 只读对方库
def resolve_db_path(explicit: str | None, astrbot_data_dir: Path | None = None) -> Path | None:
    """定位对方库：显式路径优先；否则 data/plugin_data/astrbot_plugin_moe_star_whisper/star_whisper.db。"""
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


def read_rewards(db_path: Path, *, user_id: str = "", since_date: str = "") -> list[dict[str, Any]]:
    """读对方星尘流水里的抽签正项（reason='draw' AND delta>0）。

    只取正项：消费（buy/fun/gift 负项）不回扣好感度；管理员手动发放不带
    ref_date，用 ref_date != '' 过滤掉。库/表不存在或损坏返回空列表。
    """
    try:
        conn = _connect(db_path)
    except sqlite3.Error:
        return []
    try:
        if not _has_table(conn, "ledger"):
            return []
        sql = (
            "SELECT user_id, delta, reason, ref_date, created_at"
            " FROM ledger WHERE reason = 'draw' AND delta > 0 AND ref_date != ''"
        )
        where: list[str] = []
        args: list[Any] = []
        if user_id:
            where.append("user_id = ?")
            args.append(str(user_id))
        if since_date:
            where.append("ref_date >= ?")
            args.append(str(since_date))
        if where:
            sql += " AND " + " AND ".join(where)
        rows = conn.execute(sql + " ORDER BY ref_date ASC, user_id ASC", args).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def describe(db_path: Path | None) -> dict[str, Any]:
    """对方库概况（排障/控制台用）。"""
    if db_path is None:
        return {"exists": False, "path": "", "records": 0, "first_date": "", "last_date": ""}
    try:
        conn = _connect(db_path)
    except sqlite3.Error:
        return {"exists": False, "path": str(db_path), "records": 0,
                "first_date": "", "last_date": ""}
    try:
        if not _has_table(conn, "ledger"):
            return {"exists": True, "path": str(db_path), "records": 0,
                    "first_date": "", "last_date": ""}
        agg = conn.execute(
            "SELECT COUNT(*) AS c, MIN(ref_date) AS f, MAX(ref_date) AS m FROM ledger"
            " WHERE reason = 'draw' AND delta > 0 AND ref_date != ''"
        ).fetchone()
        return {
            "exists": True,
            "path": str(db_path),
            "records": int(agg["c"] or 0),
            "first_date": str(agg["f"] or ""),
            "last_date": str(agg["m"] or ""),
        }
    finally:
        conn.close()


# ------------------------------------------------------------------ 事件构建与写入
def build_event(row: dict[str, Any], scale: float) -> dict[str, Any] | None:
    """一条星尘抽签流水 → 一条待落库事件（写入时会按角色展开）。delta 为 0 的记录跳过。"""
    uid = str(row.get("user_id") or "").strip()
    date_key = str(row.get("ref_date") or "").strip()
    if not uid or not date_key:
        return None
    delta = round(float(row.get("delta") or 0.0) * float(scale), 2)
    if delta == 0.0:
        return None
    return {
        "user_id": uid,
        "date_key": date_key,
        "delta": delta,
        "created_at": parse_ts(str(row.get("created_at") or "")),
        "evidence": json.dumps(
            {
                "from": PLUGIN_DIR_NAME,
                "date": date_key,
                "stardust": row.get("delta"),
                "reason": str(row.get("reason") or ""),
                "scale": float(scale),
            },
            ensure_ascii=False,
        ),
    }


def _sync_rows(store, rows: list[dict[str, Any]], *, scale: float, rules) -> dict[str, int]:
    """把抽签星尘写进「该用户有记录的每个角色」，返回落库统计。"""
    events = [e for e in (build_event(row, scale) for row in rows) if e is not None]
    written = 0
    if events:
        roles = store.roles_of_users([e["user_id"] for e in events])
        for event in events:
            for role_id in roles.get(event["user_id"], []):
                snapshot = store.apply_event(
                    role_id=role_id,
                    user_id=event["user_id"],
                    code=CODE_DRAW,
                    delta=event["delta"],
                    source=SOURCE,
                    rules=rules,
                    evidence=event["evidence"],
                    dedupe_key=dedupe_key(role_id, event["user_id"], event["date_key"]),
                    now=event["created_at"] or None,  # 用抽签真实时间，衰减结算链保持正确
                )
                if snapshot.get("status") == "ok" and float(snapshot.get("applied") or 0) != 0:
                    written += 1
    return {"records": len(rows), "written": written}


def sync_recent(store, db_path: Path, *, scale: float = 0.1, rules=None, days: int = 2) -> dict[str, int]:
    """增量同步最近 N 天（后台循环用，幂等）。"""
    rows = read_rewards(db_path, since_date=since_key(days))
    return _sync_rows(store, rows, scale=scale, rules=rules)


def sync_full(store, db_path: Path, *, scale: float = 0.1, rules=None) -> dict[str, int]:
    """全量导入历史（幂等：已导入过的被唯一索引吞掉）。"""
    rows = read_rewards(db_path)
    return _sync_rows(store, rows, scale=scale, rules=rules)


def ensure_user_fresh(store, db_path: Path | None, user_id: str, *, scale: float = 0.1, rules=None) -> dict[str, Any]:
    """指令触发的按需同步：只看这一个用户，带两层防重读。

    1) 该用户今天的星尘事件已经在库里 → 直接返回 ``fresh``，完全不碰对方库；
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
    rows = read_rewards(db_path, user_id=uid, since_date=since_key(2))
    stats = _sync_rows(store, rows, scale=scale, rules=rules)
    stats["status"] = "ok"
    return stats
