"""星尘好感同步守卫（v0.13.0）：纯函数 + 真 SQLite 三层，脚本式自检。

只覆盖 stardust_sync 纯逻辑与真实 store 的写入行为，不 import main.py——
本机没有 astrbot 运行时。退出码即结论。
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import stardust_sync  # noqa: E402
import store  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILED.append(name)


# ---------------------------------------------------------------- 常量与 schema
def test_constants():
    print("[constants]")
    check(stardust_sync.SOURCE == "stardust", "来源名固定 stardust")
    check(stardust_sync.CODE_DRAW == "stardust.draw", "抽签事件码")
    check(stardust_sync.CODE_GIFT == "stardust.gift", "送礼事件码")
    check(store.CODE_LABELS.get("stardust.draw") == "星尘", "CODE_LABELS 有星尘条目")
    check(store.CODE_LABELS.get("stardust.gift") == "送礼", "CODE_LABELS 有送礼条目")

    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    check("stardust_sync" in schema, "schema 有 stardust_sync 分组")
    node = schema.get("stardust_sync") or {}
    check(node.get("type") == "object", "stardust_sync 是 object 节点")
    for key in ("enabled", "scale", "inject", "db_path"):
        child = (node.get("items") or {}).get(key)
        check(child is not None and "type" in child, f"stardust_sync.{key} 有 type")


def test_keys():
    print("[dedupe keys]")
    k = stardust_sync.dedupe_key("r1", "u1", "2026-11-12")
    check(k == "stardust:r1:u1:2026-11-12:draw", "抽签幂等键格式", k)
    g = stardust_sync.gift_dedupe_key("r1", "u1", "gift:2026-11-12:2")
    check(g == "stardust:r1:u1:gift:2026-11-12:2", "送礼幂等键格式", g)


# ---------------------------------------------------------------- build_event
def test_build_event():
    print("[build_event]")
    row = {"user_id": "123", "delta": 15, "reason": "draw",
           "ref_date": "2026-11-12", "created_at": "2026-11-12 09:30:00"}
    e = stardust_sync.build_event(row, 0.2)
    check(e is not None and e["delta"] == 3.0, "星尘 15 × 0.2 = 3 分", str(e))
    check(e["date_key"] == "2026-11-12", "ref_date 进 date_key")
    check(e["created_at"] > 0, "created_at 可解析")
    evidence = json.loads(e["evidence"])
    check(evidence.get("stardust") == 15 and evidence.get("scale") == 0.2, "evidence 留档")
    check(stardust_sync.build_event(row, 0.0) is None, "系数 0 → 不产事件")
    check(stardust_sync.build_event({"user_id": "", "delta": 5, "ref_date": "2026-11-12"}, 1) is None,
          "缺 user_id → 不产事件")
    check(stardust_sync.build_event({"user_id": "1", "delta": 5, "ref_date": ""}, 1) is None,
          "缺 ref_date → 不产事件")


# ---------------------------------------------------------------- 只读对方库
def test_read_rewards():
    print("[read_rewards]")
    tmp = Path(tempfile.mkdtemp(prefix="stardust_test_"))
    db_dir = tmp / "plugin_data" / stardust_sync.PLUGIN_DIR_NAME
    db_dir.mkdir(parents=True)
    db = db_dir / stardust_sync.DB_FILENAME
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT,"
        " delta INTEGER NOT NULL, reason TEXT NOT NULL, ref_date TEXT, created_at TEXT NOT NULL)"
    )
    rows = [
        ("1", 15, "draw", "2026-11-11", "2026-11-11 08:00:00"),
        ("1", -80, "buy:reroll", "", "2026-11-11 08:05:00"),
        ("1", 20, "draw", "2026-11-12", "2026-11-12 09:00:00"),
        ("2", 30, "admin", "", "2026-11-12 10:00:00"),
        ("2", 8, "task:calendar", "2026-11-12", "2026-11-12 11:00:00"),
        ("2", 0, "draw", "2026-11-12", "2026-11-12 12:00:00"),
    ]
    conn.executemany(
        "INSERT INTO ledger (user_id, delta, reason, ref_date, created_at) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()

    got = stardust_sync.read_rewards(db)
    check(len(got) == 2, "只取 reason='draw' 且 delta>0 的正项", str(len(got)))
    check({r["user_id"] for r in got} == {"1"}, "只来自用户 1（admin/task/0 值都被过滤）")
    got2 = stardust_sync.read_rewards(db, user_id="2")
    check(got2 == [], "按用户过滤")
    got3 = stardust_sync.read_rewards(db, since_date="2026-11-12")
    check(len(got3) == 1 and got3[0]["ref_date"] == "2026-11-12", "按日期下限过滤")
    check(stardust_sync.read_rewards(tmp / "missing.db") == [], "库不存在返回空")
    check(stardust_sync.resolve_db_path(str(db)) == db, "显式路径生效")
    check(stardust_sync.resolve_db_path(str(tmp / "nope.db")) is None, "显式路径不存在返回 None")
    check(stardust_sync.resolve_db_path(None, tmp) == db, "自动定位 plugin_data 布局")


# ---------------------------------------------------------------- FakeStore 写入
class FakeStore:
    def __init__(self, roles):
        self._roles = roles  # user_id -> [role_id, ...]
        self.events = []

    def roles_of_users(self, user_ids):
        return {u: list(self._roles.get(u, [])) for u in user_ids}

    def apply_event(self, **kwargs):
        self.events.append(kwargs)
        return {"status": "ok", "applied": kwargs.get("delta")}


def test_sync_rows():
    print("[sync_rows]")
    fake = FakeStore({"1": ["roleA", "roleB"], "2": []})
    rows = [
        {"user_id": "1", "delta": 15, "reason": "draw",
         "ref_date": "2026-11-12", "created_at": "2026-11-12 09:00:00"},
    ]
    stats = stardust_sync._sync_rows(fake, rows, scale=0.2, rules=None)
    check(stats["written"] == 2, "按角色展开落两条", str(stats))
    check(len(fake.events) == 2, "两条事件")
    by_role = {e["role_id"]: e for e in fake.events}
    check(set(by_role) == {"roleA", "roleB"}, "各角色各一份")
    ev = by_role["roleA"]
    check(ev["code"] == "stardust.draw", "事件码")
    check(ev["source"] == "stardust", "来源")
    check(ev["delta"] == 3.0, "折算分值")
    check(ev["dedupe_key"] == stardust_sync.dedupe_key("roleA", "1", "2026-11-12"), "幂等键")
    check(ev["now"] is not None, "用抽签真实时间")

    empty = stardust_sync._sync_rows(FakeStore({"1": []}), rows, scale=0.2, rules=None)
    check(empty["written"] == 0, "没有角色记录的用户不入账")
    check(stardust_sync._sync_rows(FakeStore({}), [], scale=0.2, rules=None)["records"] == 0,
          "空输入安全")


# ---------------------------------------------------------------- 真 store 集成
def test_real_store():
    print("[real store]")
    tmp = Path(tempfile.mkdtemp(prefix="stardust_real_"))
    st = store.AffinityStore(tmp / "affinity.db")
    st.ensure_role("roleA", "角色A")
    rules = store.Rules(daily_gain_cap=30, daily_loss_cap=20)
    # 星尘来源不受每日上限约束（外部来源同款语义）：一次 +50 不被夹成 +30
    snap = st.apply_event(
        role_id="roleA", user_id="u1", code="stardust.draw", delta=50.0,
        source="stardust", rules=rules,
        dedupe_key=stardust_sync.dedupe_key("roleA", "u1", "2026-11-12"),
    )
    check(snap["status"] == "ok" and float(snap["applied"]) == 50.0, "外部来源不占每日上限",
          str(snap.get("applied")))
    again = st.apply_event(
        role_id="roleA", user_id="u1", code="stardust.draw", delta=50.0,
        source="stardust", rules=rules,
        dedupe_key=stardust_sync.dedupe_key("roleA", "u1", "2026-11-12"),
    )
    check(again.get("status") == "duplicate", "同幂等键重复写入被吞")
    st.apply_event(
        role_id="roleA", user_id="u1", code="stardust.gift", delta=4.0,
        source="stardust", rules=rules,
        dedupe_key=stardust_sync.gift_dedupe_key("roleA", "u1", "gift:2026-11-12:1"),
    )
    check(st.has_user_source_since(source="stardust", user_id="u1", since=0), "来源存在性检查")
    summaries = st.user_source_summaries(source="stardust", user_ids=["u1"], role_id="roleA")
    view = summaries.get("u1") or {}
    check(float(view.get("score") or 0) == 54.0, "来源分数 = 同步 + 送礼", str(view))
    check(int(view.get("days") or 0) == 1, "来源天数")
    roles = st.roles_of_users(["u1"])
    check(roles.get("u1") == ["roleA"], "roles_of_users 供展开用")


def main() -> int:
    test_constants()
    test_keys()
    test_build_event()
    test_read_rewards()
    test_sync_rows()
    test_real_store()
    print()
    if FAILED:
        print(f"RESULT: FAIL ({len(FAILED)} 项): {FAILED}")
        return 1
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
