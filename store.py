"""萌萌好感度 — SQLite 存储层。

设计约束（改动前先读）：

1) 分数的唯一写入口是 ``apply_event``：先落 ``events`` 流水，再更新聚合行。
   不允许直接 UPDATE 分数，否则审计、曲线、撤销全部失效。
2) 分数按「角色 × 用户」唯一，角色是人格的可归并映射（见 personas.py）。
3) 闲置衰减是**惰性结算**：读写时按 ``last_settled_at`` 补齐并回写，不依赖定时任务。
4) 写操作走 ``BEGIN IMMEDIATE``，避免并发加分互相覆盖。
5) 本层是同步的：单条语句毫秒级，热点路径只有一个事务。
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

# 档位表：(分数下界, 键, 中文名)，从高到低匹配。
# 分数**无上限**，所以档位只能用绝对阈值，不能按比例划分。
STAGE_TABLE: tuple[tuple[int, str, str], ...] = (
    (2000, "bonded", "羁绊"),
    (1000, "intimate", "亲密"),
    (600, "close", "亲近"),
    (300, "familiar", "熟悉"),
    (100, "known", "眼熟"),
    (0, "stranger", "陌生"),
)

TOP_STAGE_AT = STAGE_TABLE[0][0]

# 事件码 → 中文名（指令输出与控制台共用一套说法）
CODE_LABELS: dict[str, str] = {
    "chat.daily_first": "每日首次",
    "chat.message": "聊天",
    "chat.attention": "被叫",
    "chat.praise": "夸奖",
    "chat.negative": "不愉快",
    "manual.adjust": "管理员调整",
    "manual.reset": "重置",
    "manual.merge": "角色归并",
    "import.seed": "迁移导入",
    "checkin.checkin": "签到",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    role_id     TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    alias       TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS role_personas (
    persona_id  TEXT PRIMARY KEY,
    role_id     TEXT NOT NULL,
    added_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_role_personas_role ON role_personas(role_id);

CREATE TABLE IF NOT EXISTS affinities (
    role_id             TEXT NOT NULL,
    user_id             TEXT NOT NULL,
    platform            TEXT NOT NULL DEFAULT '',
    display_name        TEXT NOT NULL DEFAULT '',
    score               REAL NOT NULL DEFAULT 0,
    peak                REAL NOT NULL DEFAULT 0,
    revision            INTEGER NOT NULL DEFAULT 0,
    first_seen_at       INTEGER NOT NULL,
    last_interaction_at INTEGER NOT NULL,
    last_settled_at     INTEGER NOT NULL,
    PRIMARY KEY (role_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_affinities_role_score ON affinities(role_id, score DESC);

CREATE TABLE IF NOT EXISTS groups (
    group_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS avatar_cache (
    cache_key   TEXT PRIMARY KEY,
    data_uri    TEXT NOT NULL,
    updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS moods (
    role_id    TEXT PRIMARY KEY,
    joy        REAL NOT NULL DEFAULT 5,
    anger      REAL NOT NULL DEFAULT 1,
    sorrow     REAL NOT NULL DEFAULT 1,
    fear       REAL NOT NULL DEFAULT 1,
    baseline   REAL NOT NULL DEFAULT 5,
    updated_at INTEGER NOT NULL,
    revision   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    role_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    persona_id  TEXT NOT NULL DEFAULT '',
    platform    TEXT NOT NULL DEFAULT '',
    session_id  TEXT NOT NULL DEFAULT '',
    group_id    TEXT NOT NULL DEFAULT '',
    is_group    INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'core',
    code        TEXT NOT NULL,
    delta       REAL NOT NULL,
    before      REAL NOT NULL,
    after       REAL NOT NULL,
    evidence    TEXT NOT NULL DEFAULT '',
    dedupe_key  TEXT,
    operator    TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(role_id, user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_group ON events(role_id, group_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_source_user ON events(source, user_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_events_dedupe
    ON events(source, dedupe_key) WHERE dedupe_key IS NOT NULL;
"""


@dataclass(frozen=True)
class Rules:
    """一次结算需要的全部数值规则（由插件配置构造，避免 store 依赖配置对象）。"""

    daily_gain_cap: int = 30
    daily_loss_cap: int = 20
    decay_enabled: bool = True
    decay_grace_days: int = 3
    decay_early: int = 2
    decay_middle: int = 5
    decay_late: int = 8
    dedupe_window_seconds: int = 60


def stage_of(score: float) -> tuple[str, str]:
    """分数 → (档位键, 中文挡位名)。分数无上限，档位按绝对阈值。"""
    value = float(score)
    for floor, key, label in STAGE_TABLE:
        if value >= floor:
            return key, label
    return "stranger", "陌生"


def stage_info(score: float) -> dict[str, Any]:
    """档位 + 两套进度。

    ``progress`` 是**当前档位内**的进度（到下一档的百分比）；
    ``overall_progress`` 是**到顶档（羁绊 2000）**的整体进度，跨档单调递增。

    控制台的分数条用后者：用前者会出现「刚升档，进度条突然回退到 0」的错觉。
    """
    value = max(0.0, float(score))
    key, label = stage_of(value)
    floors = [floor for floor, _k, _l in STAGE_TABLE]
    floor = next((f for f in floors if value >= f), 0)
    index = floors.index(floor)
    ceiling = floors[index - 1] if index > 0 else None
    if ceiling is None:
        progress = 1.0
        next_label = ""
    else:
        span = max(1, ceiling - floor)
        progress = max(0.0, min(1.0, (value - floor) / span))
        next_label = STAGE_TABLE[index - 1][2]
    return {
        "stage": key,
        "stage_label": label,
        "stage_floor": floor,
        "next_stage_at": ceiling,
        "next_stage_label": next_label,
        "progress": round(progress, 4),
        "overall_progress": round(min(1.0, value / max(1, TOP_STAGE_AT)), 4),
    }


def decay_for_days(
    days: int,
    *,
    grace_days: int = 3,
    early: int = 2,
    middle: int = 5,
    late: int = 8,
) -> float:
    """连续 ``days`` 天无有效互动应扣掉的总分（分段速率）。

    3 天宽限内为 0；第 4–7 天每天 ``early``；第 8–14 天每天 ``middle``；第 15 天起每天 ``late``。
    """
    if days <= grace_days:
        return 0.0
    total = 0.0
    day = grace_days + 1
    while day <= days:
        if day <= 7:
            total += early
        elif day <= 14:
            total += middle
        else:
            total += late
        day += 1
    return total


def day_start(now: int | None = None) -> int:
    """本地时区当天 00:00 的时间戳。"""
    moment = datetime.fromtimestamp(now if now is not None else time.time())
    start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


class AffinityStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.executescript(SCHEMA)
            self._ensure_columns(conn)

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        """给老库补列（CREATE TABLE IF NOT EXISTS 不会改已存在的表）。"""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(roles)")}
        if "alias" not in columns:
            conn.execute("ALTER TABLE roles ADD COLUMN alias TEXT NOT NULL DEFAULT ''")

    # ------------------------------------------------------------------ 基础
    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            try:
                conn.execute("COMMIT")
            except sqlite3.OperationalError:
                # executescript 会先隐式提交，此时已无活动事务
                pass
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ 角色
    def role_of_persona(self, persona_id: str) -> str | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT role_id FROM role_personas WHERE persona_id = ?",
                (persona_id,),
            ).fetchone()
        return row["role_id"] if row else None

    def ensure_role(self, persona_id: str, display_name: str = "") -> str:
        """取人格对应的角色；没有就按 1:1 自动建一个。"""
        persona_id = (persona_id or "default").strip() or "default"
        now = int(time.time())
        with self._db() as conn:
            row = conn.execute(
                "SELECT role_id FROM role_personas WHERE persona_id = ?",
                (persona_id,),
            ).fetchone()
            if row:
                return row["role_id"]
            role_id = persona_id
            conn.execute(
                "INSERT OR IGNORE INTO roles(role_id, display_name, created_at, updated_at)"
                " VALUES(?, ?, ?, ?)",
                (role_id, display_name or persona_id, now, now),
            )
            conn.execute(
                "INSERT OR REPLACE INTO role_personas(persona_id, role_id, added_at)"
                " VALUES(?, ?, ?)",
                (persona_id, role_id, now),
            )
            return role_id

    def list_roles(self) -> list[dict[str, Any]]:
        """角色列表。

        ``users`` 是「有分数记录的人数」，``last_active_at`` 是最近一次互动时间——
        控制台用它区分「真的在用的角色」和「启动时按人格预建、但还没聊过的空壳」。
        """
        with self._db() as conn:
            rows = conn.execute(
                "SELECT r.role_id, r.display_name, r.alias, r.created_at,"
                " (SELECT COUNT(*) FROM role_personas p WHERE p.role_id = r.role_id) AS personas,"
                " (SELECT COUNT(*) FROM affinities a WHERE a.role_id = r.role_id) AS users,"
                " (SELECT COALESCE(MAX(a.last_interaction_at), 0) FROM affinities a"
                "    WHERE a.role_id = r.role_id) AS last_active_at"
                " FROM roles r ORDER BY r.created_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def merge_roles(self, target_role: str, source_role: str, *, strategy: str = "max") -> dict:
        """把 source 角色并入 target。策略固定为 max（取最高），见设计说明第 2 节。"""
        if target_role == source_role:
            return {"status": "noop", "moved": 0, "merged": 0}
        now = int(time.time())
        moved = merged = 0
        with self._db() as conn:
            if not conn.execute("SELECT 1 FROM roles WHERE role_id = ?", (target_role,)).fetchone():
                return {"status": "missing_target"}
            conn.execute(
                "UPDATE role_personas SET role_id = ? WHERE role_id = ?",
                (target_role, source_role),
            )
            source_rows = conn.execute(
                "SELECT * FROM affinities WHERE role_id = ?", (source_role,)
            ).fetchall()
            for src in source_rows:
                dst = conn.execute(
                    "SELECT * FROM affinities WHERE role_id = ? AND user_id = ?",
                    (target_role, src["user_id"]),
                ).fetchone()
                if dst is None:
                    conn.execute(
                        "UPDATE affinities SET role_id = ? WHERE role_id = ? AND user_id = ?",
                        (target_role, source_role, src["user_id"]),
                    )
                    moved += 1
                    continue
                before = dst["score"]
                after = max(before, src["score"]) if strategy == "max" else before + src["score"]
                after = max(0.0, min(float(after), 1e12))
                conn.execute(
                    "UPDATE affinities SET score = ?, peak = MAX(peak, ?), revision = revision + 1,"
                    " last_interaction_at = MAX(last_interaction_at, ?),"
                    " last_settled_at = MIN(last_settled_at, ?)"
                    " WHERE role_id = ? AND user_id = ?",
                    (
                        after,
                        after,
                        src["last_interaction_at"],
                        src["last_settled_at"],
                        target_role,
                        src["user_id"],
                    ),
                )
                conn.execute(
                    "INSERT INTO events(role_id, user_id, persona_id, platform, session_id, group_id,"
                    " is_group, source, code, delta, before, after, evidence, dedupe_key, operator,"
                    " created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        target_role,
                        src["user_id"],
                        "",
                        src["platform"],
                        "",
                        "",
                        0,
                        "merge",
                        "manual.merge",
                        after - before,
                        before,
                        after,
                        f"from_role={source_role}",
                        None,
                        "",
                        now,
                    ),
                )
                conn.execute(
                    "DELETE FROM affinities WHERE role_id = ? AND user_id = ?",
                    (source_role, src["user_id"]),
                )
                merged += 1
            conn.execute("DELETE FROM roles WHERE role_id = ?", (source_role,))
            conn.execute("UPDATE roles SET updated_at = ? WHERE role_id = ?", (now, target_role))
        return {"status": "ok", "moved": moved, "merged": merged}

    # ------------------------------------------------------------------ 结算
    @staticmethod
    def _settle(score: float, last_settled_at: int, now: int, rules: Rules) -> tuple[float, float, int]:
        """返回 (结算后分数, 衰减掉的分数, 新的结算点)。"""
        if not rules.decay_enabled:
            return float(score), 0.0, now
        days = int((now - last_settled_at) // 86400)
        if days <= 0:
            return float(score), 0.0, now
        lost = decay_for_days(
            days,
            grace_days=rules.decay_grace_days,
            early=rules.decay_early,
            middle=rules.decay_middle,
            late=rules.decay_late,
        )
        if lost <= 0:
            return float(score), 0.0, now
        after = max(0.0, float(score) - lost)
        return after, float(score) - after, now

    # ------------------------------------------------------------------ 写入
    def apply_event(
        self,
        *,
        role_id: str,
        user_id: str,
        code: str,
        rules: Rules,
        delta: float = 0.0,
        source: str = "core",
        persona_id: str = "",
        platform: str = "",
        session_id: str = "",
        group_id: str = "",
        is_group: bool = False,
        evidence: str = "",
        dedupe_key: str | None = None,
        operator: str = "",
        display_name: str = "",
        first_seen_at: int | None = None,
        now: int | None = None,
    ) -> dict[str, Any]:
        """记一条事件并更新聚合分数。返回快照 + 本事件的实际生效增量。

        ``first_seen_at`` 只在**首次建档**时生效（迁移时可以传老库的首次互动时间，
        否则「认识 N 天」会从导入那一刻重新算）。
        """
        if not role_id or not user_id:
            return {"status": "invalid", "message": "role_id / user_id 不能为空"}
        moment = int(now if now is not None else time.time())
        state = "ok"
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM affinities WHERE role_id = ? AND user_id = ?",
                (role_id, user_id),
            ).fetchone()
            if row is None:
                seen = moment
                if first_seen_at is not None:
                    # 不能晚于现在，也不能是未来时间
                    seen = max(0, min(int(first_seen_at), moment)) or moment
                conn.execute(
                    "INSERT INTO affinities(role_id, user_id, platform, display_name, score, peak,"
                    " revision, first_seen_at, last_interaction_at, last_settled_at)"
                    " VALUES(?,?,?,?,0,0,0,?,?,?)",
                    (role_id, user_id, platform, display_name, seen, moment, moment),
                )
                row = conn.execute(
                    "SELECT * FROM affinities WHERE role_id = ? AND user_id = ?",
                    (role_id, user_id),
                ).fetchone()

            before = float(row["score"])
            settled, decayed, settled_at = self._settle(
                before, int(row["last_settled_at"]), moment, rules
            )

            # 幂等：显式 dedupe_key 全局唯一；无 key 的 core 事件按「同码同人 N 秒内」去重
            duplicate = False
            if dedupe_key:
                duplicate = (
                    conn.execute(
                        "SELECT 1 FROM events WHERE source = ? AND dedupe_key = ? LIMIT 1",
                        (source, dedupe_key),
                    ).fetchone()
                    is not None
                )
            elif source == "core":
                duplicate = (
                    conn.execute(
                        "SELECT 1 FROM events WHERE role_id = ? AND user_id = ? AND code = ?"
                        " AND created_at >= ? LIMIT 1",
                        (role_id, user_id, code, moment - max(0, rules.dedupe_window_seconds)),
                    ).fetchone()
                    is not None
                )

            applied = 0.0
            # 每日上限只约束、也只累计核心计分（source="core"）：管理员调整 / 迁移导入
            # 本身不受限（一次 -100 不会被夹成 -20），也不占用当天的额度——
            # 否则导入几百分会把当天聊天计分的上限一口吃光。
            if not duplicate and delta and source == "core":
                start = day_start(moment)
                gains = conn.execute(
                    "SELECT COALESCE(SUM(delta), 0) AS s FROM events"
                    " WHERE role_id = ? AND user_id = ? AND delta > 0 AND created_at >= ?"
                    " AND source = 'core'",
                    (role_id, user_id, start),
                ).fetchone()["s"]
                losses = conn.execute(
                    "SELECT COALESCE(SUM(delta), 0) AS s FROM events"
                    " WHERE role_id = ? AND user_id = ? AND delta < 0 AND created_at >= ?"
                    " AND source = 'core'",
                    (role_id, user_id, start),
                ).fetchone()["s"]
                if delta > 0:
                    room = max(0.0, float(rules.daily_gain_cap) - float(gains))
                    applied = min(float(delta), room)
                else:
                    room = max(0.0, float(rules.daily_loss_cap) - abs(float(losses)))
                    applied = -min(abs(float(delta)), room)
            elif not duplicate and delta:
                applied = float(delta)
            elif duplicate:
                state = "duplicate"

            # 分数无上限，只有下限 0
            after = max(0.0, settled + applied)
            applied = after - settled
            if duplicate or applied == 0:
                # 没有实际分差就不写流水，但衰减结算仍要落库
                if decayed:
                    conn.execute(
                        "UPDATE affinities SET score = ?, last_settled_at = ?"
                        " WHERE role_id = ? AND user_id = ?",
                        (settled, settled_at, role_id, user_id),
                    )
                    before = settled
                snapshot = self._snapshot_row(conn, role_id, user_id, rules, moment)
                snapshot.update(status=state, applied=0.0, decayed=decayed)
                return snapshot

            conn.execute(
                "INSERT INTO events(role_id, user_id, persona_id, platform, session_id, group_id,"
                " is_group, source, code, delta, before, after, evidence, dedupe_key, operator,"
                " created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    role_id,
                    user_id,
                    persona_id,
                    platform,
                    session_id,
                    group_id,
                    1 if is_group else 0,
                    source,
                    code,
                    applied,
                    settled,
                    after,
                    evidence,
                    dedupe_key,
                    operator,
                    moment,
                ),
            )
            conn.execute(
                "UPDATE affinities SET score = ?, peak = MAX(peak, ?), revision = revision + 1,"
                " last_interaction_at = ?, last_settled_at = ?,"
                " display_name = CASE WHEN ? <> '' THEN ? ELSE display_name END,"
                " platform = CASE WHEN ? <> '' THEN ? ELSE platform END"
                " WHERE role_id = ? AND user_id = ?",
                (
                    after,
                    after,
                    moment,
                    settled_at,
                    display_name,
                    display_name,
                    platform,
                    platform,
                    role_id,
                    user_id,
                ),
            )
            snapshot = self._snapshot_row(conn, role_id, user_id, rules, moment)
        snapshot.update(status=state, applied=applied, decayed=decayed)
        return snapshot

    # ------------------------------------------------------------------ 读取
    def _snapshot_row(
        self, conn: sqlite3.Connection, role_id: str, user_id: str, rules: Rules, now: int
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT * FROM affinities WHERE role_id = ? AND user_id = ?",
            (role_id, user_id),
        ).fetchone()
        if row is None:
            return {"status": "not_recorded", "role_id": role_id, "user_id": user_id}
        score = float(row["score"])
        info = stage_info(score)
        higher = conn.execute(
            "SELECT COUNT(*) AS c FROM affinities WHERE role_id = ? AND score > ?",
            (role_id, score),
        ).fetchone()["c"]
        return {
            "status": "ok",
            "role_id": role_id,
            "user_id": user_id,
            "display_name": row["display_name"],
            "platform": row["platform"],
            "score": round(score, 1),
            "peak": round(float(row["peak"]), 1),
            "stage": info["stage"],
            "stage_label": info["stage_label"],
            "stage_floor": info["stage_floor"],
            "next_stage_at": info["next_stage_at"],
            "next_stage_label": info["next_stage_label"],
            "progress": info["progress"],
            "overall_progress": info["overall_progress"],
            "rank": int(higher) + 1,
            "first_seen_at": int(row["first_seen_at"]),
            "last_interaction_at": int(row["last_interaction_at"]),
            "revision": int(row["revision"]),
            "observed_at": now,
        }

    def snapshot(self, role_id: str, user_id: str, rules: Rules, now: int | None = None) -> dict:
        """读快照；顺带结算闲置衰减（惰性）。"""
        moment = int(now if now is not None else time.time())
        with self._db() as conn:
            row = conn.execute(
                "SELECT score, last_settled_at FROM affinities WHERE role_id = ? AND user_id = ?",
                (role_id, user_id),
            ).fetchone()
            if row is None:
                return {"status": "not_recorded", "role_id": role_id, "user_id": user_id}
            settled, decayed, settled_at = self._settle(
                float(row["score"]), int(row["last_settled_at"]), moment, rules
            )
            if decayed:
                conn.execute(
                    "UPDATE affinities SET score = ?, last_settled_at = ?"
                    " WHERE role_id = ? AND user_id = ?",
                    (settled, settled_at, role_id, user_id),
                )
            return self._snapshot_row(conn, role_id, user_id, rules, moment)

    def ranking(self, role_id: str, rules: Rules, *, limit: int = 10, offset: int = 0,
                now: int | None = None) -> list[dict]:
        """角色内排行；先把该角色所有人结算一遍，避免长期不活跃者的旧分数霸榜。"""
        moment = int(now if now is not None else time.time())
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM affinities WHERE role_id = ?", (role_id,)
            ).fetchall()
            for row in rows:
                settled, decayed, settled_at = self._settle(
                    float(row["score"]), int(row["last_settled_at"]), moment, rules
                )
                if decayed:
                    conn.execute(
                        "UPDATE affinities SET score = ?, last_settled_at = ?"
                        " WHERE role_id = ? AND user_id = ?",
                        (settled, settled_at, role_id, row["user_id"]),
                    )
            rows = conn.execute(
                "SELECT user_id, display_name, score, last_interaction_at FROM affinities"
                " WHERE role_id = ? ORDER BY score DESC, last_interaction_at DESC LIMIT ? OFFSET ?",
                (role_id, max(1, limit), max(0, offset)),
            ).fetchall()
        result = []
        for index, row in enumerate(rows, start=1 + max(0, offset)):
            info = stage_info(float(row["score"]))
            result.append(
                {
                    "rank": index,
                    "user_id": row["user_id"],
                    "display_name": row["display_name"],
                    "score": round(float(row["score"]), 1),
                    "stage": info["stage"],
                    "stage_label": info["stage_label"],
                    "progress": info["progress"],
                    "overall_progress": info["overall_progress"],
                    "next_stage_at": info["next_stage_at"],
                    "next_stage_label": info["next_stage_label"],
                    "last_interaction_at": int(row["last_interaction_at"]),
                }
            )
        return result

    def stats(self, role_id: str) -> dict[str, Any]:
        with self._db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM affinities WHERE role_id = ?", (role_id,)
            ).fetchone()["c"]
            avg = conn.execute(
                "SELECT COALESCE(AVG(score), 0) AS a FROM affinities WHERE role_id = ?", (role_id,)
            ).fetchone()["a"]
            events = conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE role_id = ?", (role_id,)
            ).fetchone()["c"]
        return {"users": int(total), "avg_score": round(float(avg), 1), "events": int(events)}

    def recent_events(self, role_id: str, user_id: str, limit: int = 5) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT code, delta, before, after, source, created_at FROM events"
                " WHERE role_id = ? AND user_id = ? ORDER BY id DESC LIMIT ?",
                (role_id, user_id, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    def reset_user(
        self, role_id: str, user_id: str, rules: Rules, *, operator: str = "", reason: str = ""
    ) -> dict:
        """清空某人的分数（保留流水，写一条 manual.reset 事件）。"""
        current = self.snapshot(role_id, user_id, rules)
        score = float(current.get("score") or 0)
        if current.get("status") != "ok":
            return {"status": "not_recorded"}
        return self.apply_event(
            role_id=role_id,
            user_id=user_id,
            code="manual.reset",
            delta=-score,
            source="manual",
            rules=rules,
            operator=operator,
            evidence=reason,
            display_name=str(current.get("display_name") or ""),
        )

    # ------------------------------------------------------------------ 控制台
    def upsert_groups(self, mapping: dict[str, str]) -> int:
        """群号 → 群名（由协议端同步写入）。"""
        if not mapping:
            return 0
        now = int(time.time())
        with self._db() as conn:
            for gid, name in mapping.items():
                conn.execute(
                    "INSERT INTO groups(group_id, name, updated_at) VALUES(?,?,?)"
                    " ON CONFLICT(group_id) DO UPDATE SET name = excluded.name,"
                    " updated_at = excluded.updated_at",
                    (str(gid), str(name or ""), now),
                )
        return len(mapping)

    def role_alias(self, role_id: str) -> str:
        """只取用户设置的别名（没设置返回空串）。"""
        with self._db() as conn:
            row = conn.execute(
                "SELECT alias FROM roles WHERE role_id = ?", (role_id,)
            ).fetchone()
        return str(row["alias"] or "").strip() if row else ""

    def role_label(self, role_id: str) -> str:
        """展示用名字：有别名用别名，否则用 display_name，最后回落 role_id。"""
        with self._db() as conn:
            row = conn.execute(
                "SELECT role_id, display_name, alias FROM roles WHERE role_id = ?", (role_id,)
            ).fetchone()
        if not row:
            return role_id
        alias = str(row["alias"] or "").strip()
        return alias or str(row["display_name"] or "").strip() or role_id

    def set_role_alias(self, role_id: str, alias: str) -> bool:
        with self._db() as conn:
            row = conn.execute("SELECT 1 FROM roles WHERE role_id = ?", (role_id,)).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE roles SET alias = ?, updated_at = ? WHERE role_id = ?",
                (str(alias or "").strip(), int(time.time()), role_id),
            )
        return True

    def rename_role(self, role_id: str, name: str) -> bool:
        with self._db() as conn:
            row = conn.execute("SELECT 1 FROM roles WHERE role_id = ?", (role_id,)).fetchone()
            if not row:
                return False
            conn.execute(
                "UPDATE roles SET display_name = ?, updated_at = ? WHERE role_id = ?",
                (str(name or role_id), int(time.time()), role_id),
            )
        return True

    def role_personas(self) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT persona_id, role_id, added_at FROM role_personas ORDER BY added_at ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def imported_user_ids(self, prefix: str) -> set[str]:
        """已导入过的 user_id（dedupe_key 形如 ``<prefix><user_id>``）。

        ``source`` 取 key 的前两段（``import:self_learning:``），
        因为前缀里还夹着角色 id，不能直接拿整串当 source。
        """
        source = ":".join(prefix.split(":")[:2])
        with self._db() as conn:
            rows = conn.execute(
                "SELECT dedupe_key FROM events WHERE source = ? AND dedupe_key LIKE ?",
                (source, prefix + "%"),
            ).fetchall()
        return {
            str(row["dedupe_key"])[len(prefix):]
            for row in rows
            if row["dedupe_key"]
        }

    def legacy_imported_user_ids(self, role_id: str, source: str = "import:self_learning") -> set[str]:
        """兼容 v0.7.3 之前的全局幂等键 ``<source>:<user_id>``。

        旧版 key 里没有角色，所以只能靠事件行的 ``role_id`` 判断它当初导进了哪个角色；
        否则升级后会把已经导入过的角色重新判成「待导入」，再导一次就把分数翻倍了。
        """
        prefix = f"{source}:"
        with self._db() as conn:
            rows = conn.execute(
                "SELECT dedupe_key FROM events WHERE source = ? AND role_id = ?"
                " AND dedupe_key LIKE ?",
                (source, role_id, prefix + "%"),
            ).fetchall()
        out: set[str] = set()
        for row in rows:
            key = str(row["dedupe_key"] or "")
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix):]
            if ":" in rest:  # 新格式（带角色），交给 imported_user_ids 处理
                continue
            out.add(rest)
        return out

    def upsert_user_names(self, mapping: dict[str, str]) -> int:
        """用协议端同步来的昵称/群名片补齐历史用户的名字。

        只更新**已有分数记录**的用户（不凭空造人）；同一 user_id 在所有角色下一起更新。
        """
        if not mapping:
            return 0
        updated = 0
        with self._db() as conn:
            for user_id, name in mapping.items():
                uid = str(user_id or "").strip()
                text = str(name or "").strip()
                if not uid or not text:
                    continue
                cursor = conn.execute(
                    "UPDATE affinities SET display_name = ?"
                    " WHERE user_id = ? AND display_name <> ?",
                    (text, uid, text),
                )
                updated += int(cursor.rowcount or 0)
        return updated

    def repair_first_seen(self, role_id: str, mapping: dict[str, int]) -> int:
        """把「认识时间」往更早的方向修（只提前、不推后），用于补齐迁移数据。"""
        if not mapping:
            return 0
        updated = 0
        with self._db() as conn:
            for user_id, first_at in mapping.items():
                stamp = int(first_at or 0)
                if stamp <= 0:
                    continue
                cursor = conn.execute(
                    "UPDATE affinities SET first_seen_at = ?"
                    " WHERE role_id = ? AND user_id = ?"
                    " AND (first_seen_at = 0 OR first_seen_at > ?)",
                    (stamp, role_id, user_id, stamp),
                )
                updated += int(cursor.rowcount or 0)
        return updated

    def repair_first_seen_all(self, mapping: dict[str, int]) -> int:
        """按「人」回填认识时间：不分角色，所有角色里该用户的 first_seen_at 都往早修。

        群入群时间是关于「这个人」的事实（TA 什么时候出现在 bot 的世界里），
        与角色无关；用户还没建档的人自然匹配不到行，等于跳过。
        """
        if not mapping:
            return 0
        updated = 0
        with self._db() as conn:
            for user_id, first_at in mapping.items():
                stamp = int(first_at or 0)
                uid = str(user_id or "").strip()
                if stamp <= 0 or not uid:
                    continue
                cursor = conn.execute(
                    "UPDATE affinities SET first_seen_at = ?"
                    " WHERE user_id = ?"
                    " AND (first_seen_at = 0 OR first_seen_at > ?)",
                    (stamp, uid, stamp),
                )
                updated += int(cursor.rowcount or 0)
        return updated

    def set_first_seen(self, role_id: str, user_id: str, stamp: int, *, now: int | None = None) -> bool:
        """手动指定认识时间（控制台兜底：好友时长/私聊首聊这类协议拿不到的渠道）。

        与 repair_first_seen 不同，这里**允许往回推后**——是显式的人工意图；
        仍然不允许超过当前时间。
        """
        moment = int(now if now is not None else time.time())
        stamp = max(0, min(int(stamp or 0), moment))
        with self._db() as conn:
            cursor = conn.execute(
                "UPDATE affinities SET first_seen_at = ? WHERE role_id = ? AND user_id = ?",
                (stamp, role_id, str(user_id or "").strip()),
            )
            return int(cursor.rowcount or 0) > 0

    def users_without_name(self, limit: int = 200) -> list[str]:
        """还没有昵称的用户 id（用于按需调 get_stranger_info 补名）。"""
        with self._db() as conn:
            rows = conn.execute(
                "SELECT DISTINCT user_id FROM affinities"
                " WHERE (display_name IS NULL OR display_name = '')"
                " ORDER BY last_interaction_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [str(row["user_id"]) for row in rows if row["user_id"]]

    def groups_without_name(self, limit: int = 100) -> list[str]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT group_id FROM groups WHERE (name IS NULL OR name = '')"
                " ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [str(row["group_id"]) for row in rows if row["group_id"]]

    def get_avatars(self, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        placeholders = ",".join("?" for _ in keys)
        with self._db() as conn:
            rows = conn.execute(
                f"SELECT cache_key, data_uri FROM avatar_cache WHERE cache_key IN ({placeholders})",
                list(keys),
            ).fetchall()
        return {row["cache_key"]: row["data_uri"] for row in rows}

    def put_avatars(self, mapping: dict[str, str], now: int | None = None) -> int:
        if not mapping:
            return 0
        moment = int(now if now is not None else time.time())
        with self._db() as conn:
            for key, data_uri in mapping.items():
                if not key or not data_uri:
                    continue
                conn.execute(
                    "INSERT INTO avatar_cache(cache_key, data_uri, updated_at) VALUES(?,?,?)"
                    " ON CONFLICT(cache_key) DO UPDATE SET data_uri = excluded.data_uri,"
                    " updated_at = excluded.updated_at",
                    (key, data_uri, moment),
                )
        return len(mapping)

    # ------------------------------------------------------- 用户级外部流水
    # 「对 bot 整体」粒度的外部数据（签到插件好感等）：不挂角色、不建 affinities 行、
    # 不占每日上限，聚合分数读时计算。事件仍在唯一索引 (source, dedupe_key) 上幂等。

    def record_user_events(self, *, source: str, code: str, rows: list[dict[str, Any]]) -> int:
        """批量写入用户级事件（幂等），返回实际落库条数。

        ``rows`` 每项需要 ``user_id / dedupe_key / delta / evidence / created_at``；
        before/after 维护该用户此来源的累计快照（仅审计展示用），重跑时被唯一索引吞掉、
        快照保持不动。
        """
        if not rows:
            return 0
        moment = int(time.time())
        inserted = 0
        with self._db() as conn:
            running: dict[str, float] = {}
            uids = sorted({str(row.get("user_id") or "").strip() for row in rows if row.get("user_id")})
            if uids:
                placeholders = ",".join("?" for _ in uids)
                for row in conn.execute(
                    f"SELECT user_id, COALESCE(SUM(delta), 0) AS s FROM events"
                    f" WHERE source = ? AND user_id IN ({placeholders}) GROUP BY user_id",
                    [source, *uids],
                ):
                    running[str(row["user_id"])] = float(row["s"])
            for row in rows:
                uid = str(row.get("user_id") or "").strip()
                key = str(row.get("dedupe_key") or "").strip()
                delta = round(float(row.get("delta") or 0.0), 2)
                if not uid or not key or delta == 0.0:
                    continue
                before = running.get(uid, 0.0)
                after = round(before + delta, 2)
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO events(role_id, user_id, persona_id, platform,"
                    " session_id, group_id, is_group, source, code, delta, before, after,"
                    " evidence, dedupe_key, operator, created_at)"
                    " VALUES('',?,'','','','',0,?,?,?,?,?,?,?,'sync',?)",
                    (
                        uid,
                        source,
                        code,
                        delta,
                        before,
                        after,
                        str(row.get("evidence") or ""),
                        key,
                        int(row.get("created_at") or moment),
                    ),
                )
                if cursor.rowcount:
                    running[uid] = after
                    inserted += 1
        return inserted

    def has_user_source_event(self, *, source: str, dedupe_key: str) -> bool:
        """某来源 + 幂等键的事件是否已存在（按需同步防重读的第一道闸）。"""
        with self._db() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM events WHERE source = ? AND dedupe_key = ? LIMIT 1",
                    (source, dedupe_key),
                ).fetchone()
                is not None
            )

    def user_source_summaries(self, *, source: str, user_ids: list[str]) -> dict[str, dict[str, Any]]:
        """按用户聚合某来源流水：分数 / 天数 / 最近一次时间。"""
        uids = sorted({str(u).strip() for u in user_ids if str(u).strip()})
        if not uids:
            return {}
        placeholders = ",".join("?" for _ in uids)
        with self._db() as conn:
            rows = conn.execute(
                f"SELECT user_id, SUM(delta) AS score, COUNT(*) AS days,"
                f" MAX(created_at) AS last_at FROM events"
                f" WHERE source = ? AND user_id IN ({placeholders}) GROUP BY user_id",
                [source, *uids],
            ).fetchall()
        return {
            str(row["user_id"]): {
                "score": round(float(row["score"] or 0.0), 2),
                "days": int(row["days"]),
                "last_at": int(row["last_at"] or 0),
            }
            for row in rows
        }

    def user_source_last(self, *, source: str, user_id: str) -> dict[str, Any] | None:
        """某用户该来源最近一条事件（取 evidence 里的连签等信息）。"""
        with self._db() as conn:
            row = conn.execute(
                "SELECT delta, before, after, evidence, created_at FROM events"
                " WHERE source = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
                (source, str(user_id).strip()),
            ).fetchone()
        return dict(row) if row else None

    def user_source_total(self, *, source: str) -> dict[str, Any]:
        """某来源的全局概况（控制台状态卡用）。"""
        with self._db() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS events, COUNT(DISTINCT user_id) AS users,"
                " COALESCE(SUM(delta), 0) AS score, MAX(created_at) AS last_at"
                " FROM events WHERE source = ?",
                (source,),
            ).fetchone()
        return {
            "events": int(row["events"]),
            "users": int(row["users"]),
            "score": round(float(row["score"] or 0.0), 2),
            "last_at": int(row["last_at"] or 0),
        }

    def _settle_role(self, conn: sqlite3.Connection, role_id: str, rules: Rules, now: int) -> None:
        rows = conn.execute(
            "SELECT user_id, score, last_settled_at FROM affinities WHERE role_id = ?", (role_id,)
        ).fetchall()
        for row in rows:
            settled, decayed, settled_at = self._settle(
                float(row["score"]), int(row["last_settled_at"]), now, rules
            )
            if decayed:
                conn.execute(
                    "UPDATE affinities SET score = ?, last_settled_at = ?"
                    " WHERE role_id = ? AND user_id = ?",
                    (settled, settled_at, role_id, row["user_id"]),
                )

    def list_users(
        self,
        role_id: str,
        rules: Rules,
        *,
        q: str = "",
        stage: str = "",
        sort: str = "score",
        page: int = 1,
        size: int = 50,
        now: int | None = None,
    ) -> dict[str, Any]:
        moment = int(now if now is not None else time.time())
        size = max(1, min(200, int(size)))
        page = max(1, int(page))
        order = {
            "score": "score DESC, last_interaction_at DESC",
            "recent": "last_interaction_at DESC, score DESC",
            "name": "display_name ASC, user_id ASC",
            "since": "first_seen_at ASC, score DESC",
        }.get(sort, "score DESC, last_interaction_at DESC")
        where = "role_id = ?"
        args: list[Any] = [role_id]
        if q:
            where += " AND (user_id LIKE ? OR display_name LIKE ?)"
            args += [f"%{q}%", f"%{q}%"]
        with self._db() as conn:
            self._settle_role(conn, role_id, rules, moment)
            rows = conn.execute(
                f"SELECT user_id, display_name, platform, score, first_seen_at,"
                f" last_interaction_at FROM affinities WHERE {where} ORDER BY {order}",
                args,
            ).fetchall()
            if stage:
                rows = [
                    row
                    for row in rows
                    if stage_of(float(row["score"]))[0] == stage
                ]
            total = len(rows)
            picked = rows[(page - 1) * size: page * size]
        items = []
        for index, row in enumerate(picked, start=(page - 1) * size + 1):
            info = stage_info(float(row["score"]))
            items.append(
                {
                    "rank": index,
                    "user_id": row["user_id"],
                    "display_name": row["display_name"],
                    "platform": row["platform"],
                    "score": round(float(row["score"]), 1),
                    "stage": info["stage"],
                    "stage_label": info["stage_label"],
                    "progress": info["progress"],
                    "overall_progress": info["overall_progress"],
                    "next_stage_at": info["next_stage_at"],
                    "next_stage_label": info["next_stage_label"],
                    "first_seen_at": int(row["first_seen_at"]),
                    "last_interaction_at": int(row["last_interaction_at"]),
                }
            )
        return {"total": total, "rows": items, "page": page, "size": size}

    def overview(self, role_id: str, rules: Rules, *, days: int = 30, now: int | None = None) -> dict:
        moment = int(now if now is not None else time.time())
        days = max(1, min(180, int(days)))
        start = day_start(moment) - (days - 1) * 86400
        with self._db() as conn:
            self._settle_role(conn, role_id, rules, moment)
            rows = conn.execute(
                "SELECT score, last_interaction_at FROM affinities WHERE role_id = ?", (role_id,)
            ).fetchall()
            stages = []
            for edge, key, label in reversed(STAGE_TABLE):
                count = sum(
                    1
                    for row in rows
                    if stage_of(float(row["score"]))[0] == key
                )
                stages.append({"key": key, "label": label, "count": count, "edge": edge})
            total_users = len(rows)
            avg = round(sum(float(r["score"]) for r in rows) / total_users, 1) if total_users else 0.0
            active_today = sum(1 for r in rows if int(r["last_interaction_at"]) >= day_start(moment))
            active_7d = sum(1 for r in rows if int(r["last_interaction_at"]) >= moment - 7 * 86400)
            trend = conn.execute(
                "SELECT date(created_at, 'unixepoch', 'localtime') AS day,"
                " COUNT(*) AS events,"
                " SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END) AS gain,"
                " SUM(CASE WHEN delta < 0 THEN delta ELSE 0 END) AS loss,"
                " COUNT(DISTINCT user_id) AS users"
                " FROM events WHERE role_id = ? AND created_at >= ?"
                " GROUP BY day ORDER BY day ASC",
                (role_id, start),
            ).fetchall()
            top = conn.execute(
                "SELECT user_id, display_name, score FROM affinities WHERE role_id = ?"
                " ORDER BY score DESC, last_interaction_at DESC LIMIT 5",
                (role_id,),
            ).fetchall()
            recent = conn.execute(
                "SELECT e.code, e.delta, e.user_id, a.display_name AS name, e.created_at,"
                " g.name AS group_name"
                " FROM events e"
                " LEFT JOIN affinities a ON a.role_id = e.role_id AND a.user_id = e.user_id"
                " LEFT JOIN groups g ON g.group_id = e.group_id"
                " WHERE e.role_id = ? ORDER BY e.id DESC LIMIT 12",
                (role_id,),
            ).fetchall()
            today_events = conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE role_id = ? AND created_at >= ?",
                (role_id, day_start(moment)),
            ).fetchone()["c"]
            week_events = conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE role_id = ? AND created_at >= ?",
                (role_id, moment - 7 * 86400),
            ).fetchone()["c"]
        return {
            "role_id": role_id,
            "days": days,
            "top_stage_at": TOP_STAGE_AT,
            "totals": {
                "users": total_users,
                "avg_score": avg,
                "active_today": active_today,
                "active_7d": active_7d,
                "events_today": int(today_events),
                "events_7d": int(week_events),
            },
            "stages": stages,
            "trend": [dict(row) for row in trend],
            "top": [
                {
                    "user_id": row["user_id"],
                    "display_name": row["display_name"],
                    "score": round(float(row["score"]), 1),
                }
                for row in top
            ],
            "recent": [dict(row) for row in recent],
        }

    def user_detail(
        self, role_id: str, user_id: str, rules: Rules, *, days: int = 30, now: int | None = None
    ) -> dict:
        moment = int(now if now is not None else time.time())
        days = max(1, min(180, int(days)))
        start = day_start(moment) - (days - 1) * 86400
        with self._db() as conn:
            row = conn.execute(
                "SELECT score, last_settled_at FROM affinities WHERE role_id = ? AND user_id = ?",
                (role_id, user_id),
            ).fetchone()
            if row is not None:
                settled, decayed, settled_at = self._settle(
                    float(row["score"]), int(row["last_settled_at"]), moment, rules
                )
                if decayed:
                    conn.execute(
                        "UPDATE affinities SET score = ?, last_settled_at = ?"
                        " WHERE role_id = ? AND user_id = ?",
                        (settled, settled_at, role_id, user_id),
                    )
            snapshot = self._snapshot_row(conn, role_id, user_id, rules, moment)
            if snapshot.get("status") != "ok":
                return {"status": "not_recorded", "snapshot": snapshot}
            series = conn.execute(
                "SELECT date(created_at, 'unixepoch', 'localtime') AS day,"
                " SUM(delta) AS delta, COUNT(*) AS events"
                " FROM events WHERE role_id = ? AND user_id = ? AND created_at >= ?"
                " GROUP BY day ORDER BY day ASC",
                (role_id, user_id, start),
            ).fetchall()
            groups = conn.execute(
                "SELECT e.group_id, g.name AS group_name, SUM(e.delta) AS delta,"
                " COUNT(*) AS events, MAX(e.created_at) AS last_at"
                " FROM events e LEFT JOIN groups g ON g.group_id = e.group_id"
                " WHERE e.role_id = ? AND e.user_id = ? AND e.group_id <> ''"
                " GROUP BY e.group_id ORDER BY delta DESC",
                (role_id, user_id),
            ).fetchall()
            events = conn.execute(
                "SELECT e.id, e.code, e.delta, e.before, e.after, e.source, e.operator,"
                " e.evidence, e.group_id, e.persona_id, e.created_at, g.name AS group_name"
                " FROM events e LEFT JOIN groups g ON g.group_id = e.group_id"
                " WHERE e.role_id = ? AND e.user_id = ? ORDER BY e.id DESC LIMIT 50",
                (role_id, user_id),
            ).fetchall()
            personas = conn.execute(
                "SELECT e.persona_id, SUM(e.delta) AS delta, COUNT(*) AS events,"
                " MAX(e.created_at) AS last_at"
                " FROM events e WHERE e.role_id = ? AND e.user_id = ? AND e.persona_id <> ''"
                " GROUP BY e.persona_id ORDER BY delta DESC",
                (role_id, user_id),
            ).fetchall()
            total_delta = conn.execute(
                "SELECT COALESCE(SUM(delta), 0) AS s FROM events"
                " WHERE role_id = ? AND user_id = ? AND group_id <> ''",
                (role_id, user_id),
            ).fetchone()["s"]
            today = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END), 0) AS gain,"
                " COALESCE(SUM(CASE WHEN delta < 0 THEN delta ELSE 0 END), 0) AS loss,"
                " COUNT(*) AS events FROM events"
                " WHERE role_id = ? AND user_id = ? AND created_at >= ?",
                (role_id, user_id, day_start(moment)),
            ).fetchone()
            # 上限进度只看核心计分（与 store.apply_event 的口径一致）；
            # 导入/手动这些「不受限」的变动单独给一个总数，展示时兜底说明。
            today_core = conn.execute(
                "SELECT COALESCE(SUM(CASE WHEN delta > 0 THEN delta ELSE 0 END), 0) AS gain,"
                " COALESCE(SUM(CASE WHEN delta < 0 THEN delta ELSE 0 END), 0) AS loss FROM events"
                " WHERE role_id = ? AND user_id = ? AND created_at >= ? AND source = 'core'",
                (role_id, user_id, day_start(moment)),
            ).fetchone()
            role_row = conn.execute(
                "SELECT display_name, alias FROM roles WHERE role_id = ?", (role_id,)
            ).fetchone()
            persona_total = conn.execute(
                "SELECT COUNT(*) AS c FROM role_personas WHERE role_id = ?", (role_id,)
            ).fetchone()["c"]
            settled_row = conn.execute(
                "SELECT last_settled_at FROM affinities WHERE role_id = ? AND user_id = ?",
                (role_id, user_id),
            ).fetchone()
        return {
            "status": "ok",
            "snapshot": snapshot,
            "today": {
                "gain": round(float(today_core["gain"]), 1),
                "loss": round(abs(float(today_core["loss"])), 1),
                "gain_all": round(float(today["gain"]), 1),
                "loss_all": round(abs(float(today["loss"])), 1),
                "events": int(today["events"]),
                "gain_cap": float(rules.daily_gain_cap),
                "loss_cap": float(rules.daily_loss_cap),
            },
            "role": {
                "role_id": role_id,
                "alias": str(role_row["alias"] or "") if role_row else "",
                "display_name": str(role_row["display_name"] or "") if role_row else "",
                "personas": int(persona_total),
            },
            "last_settled_at": int(settled_row["last_settled_at"]) if settled_row else 0,
            "series": [dict(item) for item in series],
            "groups": [dict(item) for item in groups],
            "personas": [dict(item) for item in personas],
            "events": [dict(item) for item in events],
            "group_delta_total": round(float(total_delta), 1),
        }

    def group_rows(self, role_id: str, rules: Rules, *, now: int | None = None) -> list[dict]:
        moment = int(now if now is not None else time.time())
        with self._db() as conn:
            rows = conn.execute(
                "SELECT e.group_id, g.name AS group_name, SUM(e.delta) AS delta,"
                " COUNT(*) AS events, COUNT(DISTINCT e.user_id) AS users,"
                " MAX(e.created_at) AS last_at"
                " FROM events e LEFT JOIN groups g ON g.group_id = e.group_id"
                " WHERE e.role_id = ? AND e.group_id <> ''"
                " GROUP BY e.group_id ORDER BY last_at DESC",
                (role_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def group_detail(
        self, role_id: str, group_id: str, rules: Rules, *, now: int | None = None
    ) -> dict:
        moment = int(now if now is not None else time.time())
        with self._db() as conn:
            self._settle_role(conn, role_id, rules, moment)
            name_row = conn.execute(
                "SELECT name FROM groups WHERE group_id = ?", (group_id,)
            ).fetchone()
            rows = conn.execute(
                "SELECT e.user_id, a.display_name, a.score, a.last_interaction_at,"
                " SUM(e.delta) AS delta, COUNT(*) AS events"
                " FROM events e LEFT JOIN affinities a"
                "   ON a.role_id = e.role_id AND a.user_id = e.user_id"
                " WHERE e.role_id = ? AND e.group_id = ?"
                " GROUP BY e.user_id ORDER BY delta DESC",
                (role_id, group_id),
            ).fetchall()
            trend = conn.execute(
                "SELECT date(created_at, 'unixepoch', 'localtime') AS day,"
                " SUM(delta) AS delta, COUNT(*) AS events, COUNT(DISTINCT user_id) AS users"
                " FROM events WHERE role_id = ? AND group_id = ?"
                " GROUP BY day ORDER BY day DESC LIMIT 30",
                (role_id, group_id),
            ).fetchall()
        items = []
        for row in rows:
            info = stage_info(float(row["score"] or 0))
            items.append(
                {
                    "user_id": row["user_id"],
                    "display_name": row["display_name"] or "",
                    "score": round(float(row["score"] or 0), 1),
                    "stage": info["stage"],
                    "stage_label": info["stage_label"],
                    "progress": info["progress"],
                    "overall_progress": info["overall_progress"],
                    "next_stage_at": info["next_stage_at"],
                    "next_stage_label": info["next_stage_label"],
                    "last_interaction_at": int(row["last_interaction_at"] or 0),
                    "group_delta": round(float(row["delta"] or 0), 1),
                    "events": int(row["events"]),
                }
            )
        return {
            "group_id": group_id,
            "group_name": name_row["name"] if name_row else "",
            "rows": items,
            "trend": list(reversed([dict(item) for item in trend])),
        }

    def audit_rows(self, *, page: int = 1, size: int = 50) -> dict:
        size = max(1, min(200, int(size)))
        page = max(1, int(page))
        with self._db() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM events WHERE source IN ('manual','merge','import')"
            ).fetchone()["c"]
            rows = conn.execute(
                "SELECT id, role_id, user_id, code, delta, before, after, source, operator,"
                " evidence, created_at FROM events"
                " WHERE source IN ('manual','merge','import')"
                " ORDER BY id DESC LIMIT ? OFFSET ?",
                (size, (page - 1) * size),
            ).fetchall()
        return {"total": int(total), "rows": [dict(row) for row in rows], "page": page, "size": size}

    # ------------------------------------------------------------------ 心情
    def get_mood(self, role_id: str) -> dict[str, Any] | None:
        """原始心情行（未做半衰期投影）。"""
        with self._db() as conn:
            row = conn.execute("SELECT * FROM moods WHERE role_id = ?", (role_id,)).fetchone()
        return dict(row) if row else None

    def save_mood(
        self,
        role_id: str,
        axes: dict[str, float],
        *,
        baseline: float,
        now: int | None = None,
    ) -> dict[str, Any]:
        moment = int(now if now is not None else time.time())
        joy = float(axes.get("joy", baseline))
        anger = float(axes.get("anger", 1.0))
        sorrow = float(axes.get("sorrow", 1.0))
        fear = float(axes.get("fear", 1.0))
        with self._db() as conn:
            conn.execute(
                "INSERT INTO moods(role_id, joy, anger, sorrow, fear, baseline, updated_at, revision)"
                " VALUES(?,?,?,?,?,?,?,1)"
                " ON CONFLICT(role_id) DO UPDATE SET joy = excluded.joy, anger = excluded.anger,"
                " sorrow = excluded.sorrow, fear = excluded.fear, baseline = excluded.baseline,"
                " updated_at = excluded.updated_at, revision = moods.revision + 1",
                (role_id, joy, anger, sorrow, fear, float(baseline), moment),
            )
            row = conn.execute("SELECT * FROM moods WHERE role_id = ?", (role_id,)).fetchone()
        return dict(row) if row else {}

    def ping(self) -> bool:
        try:
            with self._db() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False
