"""核心自检（脚本式，直接 python tests/test_core.py，退出码即结论）。

只覆盖纯逻辑：配置 schema、档位、衰减、计分规则、存储层。
不 import main.py —— 本机没有 astrbot 运行时。
"""

import json
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import scoring  # noqa: E402
import store  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILED.append(name)


# ---------------------------------------------------------------- schema
def test_schema():
    print("[schema]")
    schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    allowed = {"int", "float", "bool", "string", "text", "list", "file", "object", "template_list", "dict"}
    bad = []

    def walk(node, path):
        if "type" not in node:
            bad.append(f"{path}: 缺少 type")
            return
        if node["type"] not in allowed:
            bad.append(f"{path}: 非法 type {node['type']}")
        if node["type"] == "object":
            items = node.get("items")
            if not isinstance(items, dict) or not items:
                bad.append(f"{path}: object 缺少 items")
                return
            for key, child in items.items():
                walk(child, f"{path}.{key}")

    for key, node in schema.items():
        walk(node, key)
    check("每个节点都有合法 type", not bad, str(bad))
    check("top-level 键齐全", {"enabled", "affinity", "scoring", "inject", "advanced"} <= set(schema))


# ---------------------------------------------------------------- 档位
def test_stage():
    print("[stage]")
    cases = [(0, "stranger"), (99, "stranger"), (100, "known"), (249, "known"),
             (250, "familiar"), (449, "familiar"), (450, "close"), (649, "close"),
             (650, "intimate"), (849, "intimate"), (850, "bonded"), (1000, "bonded")]
    for score, expect in cases:
        got, _label = store.stage_of(score, 1000)
        check(f"{score} -> {expect}", got == expect, f"got {got}")
    check("上限为 100 时按比例", store.stage_of(85, 100)[0] == "bonded")


# ---------------------------------------------------------------- 衰减
def test_decay():
    print("[decay]")
    check("宽限内不变", store.decay_for_days(3) == 0)
    check("第 4 天 -2", store.decay_for_days(4) == 2)
    check("第 7 天 -8", store.decay_for_days(7) == 8)
    check("第 8 天 -13", store.decay_for_days(8) == 13)
    check("第 14 天 -43", store.decay_for_days(14) == 43)
    check("第 15 天 -51", store.decay_for_days(15) == 51)
    check("第 20 天 -91", store.decay_for_days(20) == 91)
    check("关闭衰减时 store 不扣", store.AffinityStore._settle(
        100.0, 0, 20 * 86400, store.Rules(decay_enabled=False))[0] == 100.0)


# ---------------------------------------------------------------- 计分
def test_scoring():
    print("[scoring]")
    conf = scoring.ScoringConfig()
    codes = [c for c, _ in scoring.evaluate("今天好累啊", cfg=conf)]
    check("普通消息给 chat.message", codes == ["chat.message"], str(codes))

    codes = [c for c, _ in scoring.evaluate("在吗", is_attention=True, cfg=conf)]
    check("被唤醒额外给 attention", codes == ["chat.attention", "chat.message"], str(codes))

    codes = [c for c, _ in scoring.evaluate("滚开", is_attention=True, cfg=conf)]
    check("负面优先且不再正向", codes == ["chat.negative"], str(codes))

    conf2 = scoring.ScoringConfig(praise_keywords=("好可爱",))
    codes = [c for c, _ in scoring.evaluate("你好可爱", cfg=conf2)]
    check("夸奖词命中", codes == ["chat.praise", "chat.message"], str(codes))

    check("空消息不计分", scoring.evaluate("   ", cfg=conf) == [])
    key = scoring.daily_key("role", "user", "2026-11-11")
    check("每日键稳定", key == "daily_first:role:user:2026-11-11")


# ---------------------------------------------------------------- 存储
def test_store():
    print("[store]")
    tmp = Path(tempfile.mkdtemp(prefix="moe_affinity_test_"))
    try:
        db = store.AffinityStore(tmp / "t.db")
        rules = store.Rules()
        noon = int(datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp())

        role = db.ensure_role("persona_a", "人格A")
        check("role 自动建立", role == "persona_a")
        check("同一人格复用 role", db.ensure_role("persona_a") == role)

        snap = db.apply_event(role_id=role, user_id="u1", code="chat.message",
                              delta=5, rules=rules, now=noon)
        check("首次写入 score=5", snap["score"] == 5, str(snap))

        dup = db.apply_event(role_id=role, user_id="u1", code="chat.message",
                             delta=5, rules=rules, now=noon + 10)
        check("同码 60s 内去重", dup["status"] == "duplicate" and dup["score"] == 5, str(dup))

        again = db.apply_event(role_id=role, user_id="u1", code="chat.message",
                               delta=5, rules=rules, now=noon + 120)
        check("超出窗口后正常加分", again["score"] == 10, str(again))

        # 每日上限：继续加，最多到 30
        t = noon + 240
        for _ in range(10):
            db.apply_event(role_id=role, user_id="u1", code="chat.message",
                           delta=5, rules=rules, now=t)
            t += 120
        snap = db.snapshot(role, "u1", rules, now=t)
        check("每日涨分上限 30", snap["score"] == 30, str(snap))

        # 管理员调整不受每日上限约束
        admin = db.apply_event(role_id=role, user_id="u1", code="manual.adjust",
                               delta=-20, source="manual", rules=rules, now=t)
        check("手动扣分不受上限", admin["score"] == 10, str(admin))

        # 闲置衰减
        later = t + 20 * 86400
        snap = db.snapshot(role, "u1", rules, now=later)
        check("20 天后衰减到 0", snap["score"] == 0, str(snap))

        # 排行
        db.apply_event(role_id=role, user_id="u2", code="chat.message",
                       delta=50, rules=rules, now=noon)
        db.apply_event(role_id=role, user_id="u3", code="chat.message",
                       delta=20, rules=rules, now=noon)
        rows = db.ranking(role, rules, now=noon + 60)
        check("排行按分数降序", [r["user_id"] for r in rows] == ["u2", "u3", "u1"], str(rows))

        # 角色归并取最高
        role_b = db.ensure_role("persona_b")
        db.apply_event(role_id=role_b, user_id="u1", code="import.seed",
                       delta=250, source="import", rules=rules, now=noon)
        result = db.merge_roles(role, role_b, strategy="max")
        check("归并执行", result["status"] == "ok", str(result))
        merged = db.snapshot(role, "u1", rules, now=noon + 60)
        check("归并取最高 (250)", merged["score"] == 250, str(merged))
        check("被归并角色已消失", db.role_of_persona("persona_b") == role)

        # 重置
        reset = db.reset_user(role, "u1", rules, operator="admin", reason="test")
        check("重置归零", reset["score"] == 0, str(reset))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_console_queries():
    print("[console]")
    tmp = Path(tempfile.mkdtemp(prefix="moe_affinity_console_"))
    try:
        db = store.AffinityStore(tmp / "c.db")
        rules = store.Rules()
        noon = int(datetime.now().replace(hour=12, minute=0, second=0, microsecond=0).timestamp())
        role = db.ensure_role("p1", "人格1")

        db.apply_event(role_id=role, user_id="u1", code="chat.message", delta=10, rules=rules,
                       group_id="100", is_group=True, display_name="小明", now=noon)
        db.apply_event(role_id=role, user_id="u2", code="chat.message", delta=40, rules=rules,
                       group_id="100", is_group=True, display_name="小红", now=noon)
        db.apply_event(role_id=role, user_id="u1", code="chat.message", delta=20, rules=rules,
                       group_id="200", is_group=True, now=noon + 120)
        db.apply_event(role_id=role, user_id="u1", code="manual.adjust", delta=-5,
                       source="manual", rules=rules, operator="admin",
                       evidence="测试", now=noon + 240)

        db.upsert_groups({"100": "测试群", "200": "二群"})
        groups = db.group_rows(role, rules)
        names = {g["group_id"]: g["group_name"] for g in groups}
        check("群列表两条", len(groups) == 2, str(groups))
        check("群名已补", names.get("100") == "测试群", str(names))
        # u2 要求的 +40 被每日上限夹到 +30，所以群 100 合计 10 + 30 = 40
        check("群贡献合计 40", {g["group_id"]: g["delta"] for g in groups}["100"] == 40, str(groups))

        found = db.list_users(role, rules, q="小红")
        check("按昵称搜索", found["total"] == 1 and found["rows"][0]["user_id"] == "u2", str(found))
        ordered = db.list_users(role, rules, sort="score")
        check("列表按分数降序", ordered["rows"][0]["user_id"] == "u2", str(ordered["rows"]))
        paged = db.list_users(role, rules, page=2, size=1)
        check("分页 size=1 第二页 1 条", len(paged["rows"]) == 1 and paged["total"] == 2, str(paged))
        filtered = db.list_users(role, rules, stage="stranger")
        check("按档位过滤", filtered["total"] == 2, str(filtered))

        view = db.overview(role, rules, days=7, now=noon + 300)
        check("总览人数", view["totals"]["users"] == 2, str(view["totals"]))
        # u1 = 10 + 20 - 5 = 25，u2 被上限夹到 30 → 平均 27.5
        check("总览平均分 27.5", view["totals"]["avg_score"] == 27.5, str(view["totals"]))
        check("总览事件数 4", view["totals"]["events_7d"] == 4, str(view["totals"]))
        check("趋势有数据", len(view["trend"]) >= 1, str(view["trend"]))
        check("Top 有值", view["top"][0]["user_id"] == "u2", str(view["top"]))

        detail = db.user_detail(role, "u1", rules, days=7, now=noon + 300)
        check("详情可读", detail["status"] == "ok", str(detail.get("status")))
        check("详情两个群", len(detail["groups"]) == 2, str(detail["groups"]))
        check("详情群贡献合计 30", detail["group_delta_total"] == 30, str(detail["group_delta_total"]))
        check("详情流水 3 条", len(detail["events"]) == 3, str(len(detail["events"])))

        audit = db.audit_rows()
        check("审计记录手动事件", audit["total"] == 1 and audit["rows"][0]["operator"] == "admin", str(audit))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_migration():
    print("[migration]")
    import sqlite3

    import migrate_self_learning as mig

    tmp = Path(tempfile.mkdtemp(prefix="moe_affinity_migrate_"))
    try:
        db_path = tmp / "messages.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                "CREATE TABLE user_affections(id INTEGER PRIMARY KEY, group_id TEXT, user_id TEXT,"
                " affection_level INTEGER, max_affection INTEGER, created_at INTEGER, updated_at INTEGER);"
                "CREATE TABLE bot_mood(id INTEGER PRIMARY KEY, group_id TEXT, mood_type TEXT, is_active INTEGER);"
            )
            conn.executemany(
                "INSERT INTO user_affections(group_id, user_id, affection_level, max_affection,"
                " created_at, updated_at) VALUES(?,?,?,?,?,?)",
                [
                    ("g1", "u1", 20, 100, 1, 100),
                    ("g2", "u1", 80, 100, 1, 200),  # 同一用户取最高
                    ("g1", "u2", 50, 100, 1, 150),
                ],
            )
            conn.executemany(
                "INSERT INTO bot_mood(group_id, mood_type, is_active) VALUES(?,?,?)",
                [("g1", "angry", 1), ("g2", "happy", 0)],
            )
            conn.commit()
        finally:
            conn.close()

        legacy = mig.read_legacy(db_path)
        check("按用户聚合为 2 条", len(legacy) == 2, str(legacy))
        best = next(item for item in legacy if item["user_id"] == "u1")
        check("同一用户取最高分", best["best_score"] == 80, str(best))
        check("保留全部群记录", len(best["groups"]) == 2, str(best["groups"]))

        plan = mig.build_plan(legacy, 1000)
        mapped = {item["user_id"]: item["target_score"] for item in plan}
        check("比例换算 80/100 -> 800", mapped["u1"] == 800, str(mapped))
        check("比例换算 50/100 -> 500", mapped["u2"] == 500, str(mapped))
        check("换算后不超上限", all(item["target_score"] <= 1000 for item in plan))

        evidence = json.loads(mig.evidence_json(plan[0]))
        check("evidence 记录来源与群明细", evidence["from"] == "self_learning" and evidence["groups"], str(evidence))

        cleared = mig.clear_active_mood(db_path)
        check("只清理活跃心情 1 条", cleared == 1, str(cleared))
        conn = sqlite3.connect(db_path)
        try:
            left = conn.execute("SELECT COUNT(*) FROM bot_mood WHERE is_active = 1").fetchone()[0]
        finally:
            conn.close()
        check("清理后无活跃心情", left == 0, str(left))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_schema()
    test_stage()
    test_decay()
    test_scoring()
    test_store()
    test_console_queries()
    test_migration()
    print()
    if FAILED:
        print(f"RESULT: FAIL ({len(FAILED)}): " + ", ".join(FAILED))
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
