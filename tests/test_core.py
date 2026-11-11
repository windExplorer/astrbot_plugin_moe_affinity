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
    cases = [(0, "stranger"), (99, "stranger"), (100, "known"), (299, "known"),
             (300, "familiar"), (599, "familiar"), (600, "close"), (999, "close"),
             (1000, "intimate"), (1999, "intimate"), (2000, "bonded"), (99999, "bonded")]
    for score, expect in cases:
        got, _label = store.stage_of(score)
        check(f"{score} -> {expect}", got == expect, f"got {got}")
    info = store.stage_info(650)
    check("段内进度 650/600-1000", info["stage"] == "close" and 0 < info["progress"] < 1, str(info))
    check("顶档进度恒为 1", store.stage_info(5000)["progress"] == 1.0)
    check("下一档阈值", store.stage_info(650)["next_stage_at"] == 1000, str(store.stage_info(650)))
    # 分数条用 overall_progress：跨档必须单调，否则「刚升档进度条回退」
    check("整体进度单调递增",
          store.stage_info(600)["overall_progress"] > store.stage_info(599)["overall_progress"])
    check("整体进度封顶 1", store.stage_info(99999)["overall_progress"] == 1.0)
    check("整体进度按顶档 2000 折算", store.stage_info(1000)["overall_progress"] == 0.5)


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


def test_mood():
    print("[mood]")
    import mood

    base = mood.default_axes(5)
    check("默认值 joy=baseline，其余=1", base["joy"] == 5 and base["anger"] == 1, str(base))

    half = mood.decayed({"joy": 9, "anger": 9, "sorrow": 1, "fear": 1}, 5, 0, 90 * 60, 90)
    check("半衰期 90 分钟折半", abs(half["joy"] - 7.0) < 0.01 and abs(half["anger"] - 5.0) < 0.01, str(half))

    far = mood.decayed({"joy": 9, "anger": 9, "sorrow": 1, "fear": 1}, 5, 0, 900 * 60, 90)
    check("长时间后回到静息", abs(far["joy"] - 5) < 0.01 and abs(far["anger"] - 1) < 0.01, str(far))

    boosted = mood.apply_deltas({"joy": 5, "anger": 1, "sorrow": 1, "fear": 1},
                                {"joy": 9, "anger": -9}, max_delta=2)
    check("单轮变动被夹在 ±max_delta", boosted["joy"] == 7 and boosted["anger"] == 1, str(boosted))

    capped = mood.apply_deltas({"joy": 10, "anger": 1, "sorrow": 1, "fear": 1},
                               {"joy": 2}, max_delta=2)
    check("轴上限 10", capped["joy"] == 10, str(capped))

    check("平静判定", mood.is_neutral({"joy": 6, "anger": 1, "sorrow": 2, "fear": 1}, 5, 2))
    check("非平静判定", not mood.is_neutral({"joy": 9, "anger": 1, "sorrow": 1, "fear": 1}, 5, 2))
    check("平静文案", mood.describe({"joy": 5, "anger": 1, "sorrow": 1, "fear": 1}, 5, 2) == "心情平静")
    check("高兴文案", "心情" in mood.describe({"joy": 9, "anger": 1, "sorrow": 1, "fear": 1}, 5, 2))

    deltas = mood.deltas_from_events(["chat.praise", "chat.negative", "chat.attention"])
    check("事件映射到心情", deltas["joy"] > 0 and deltas["anger"] > 0 and deltas["sorrow"] > 0, str(deltas))

    tmp = Path(tempfile.mkdtemp(prefix="moe_affinity_mood_"))
    try:
        db = store.AffinityStore(tmp / "m.db")
        check("未设置时返回 None", db.get_mood("r1") is None)
        db.save_mood("r1", {"joy": 8, "anger": 3, "sorrow": 2, "fear": 1}, baseline=5, now=1000)
        row = db.get_mood("r1")
        check("心情落库", row and row["joy"] == 8 and row["baseline"] == 5, str(row))
        db.save_mood("r1", {"joy": 7, "anger": 2, "sorrow": 2, "fear": 1}, baseline=5, now=2000)
        row = db.get_mood("r1")
        check("再次保存覆盖并累加 revision", row["joy"] == 7 and row["revision"] == 2, str(row))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rules_info():
    print("[rules]")
    import rules_info

    data = rules_info.build(
        rules=store.Rules(),
        scoring_cfg=scoring.ScoringConfig(),
        mood_cfg={
            "enabled": True,
            "inject": True,
            "half_life_minutes": 90,
            "max_delta": 2,
            "neutral_band": 2,
            "praise_delta": 2,
            "negative_delta": 2,
        },
        stages=list(store.STAGE_TABLE),
        version="v-test",
    )
    stages = data["score"]["stages"]
    check("档位 6 行且从低到高", len(stages) == 6 and stages[0]["floor"] == 0, str(stages[:2]))
    check("顶档没有上界", stages[-1]["ceiling"] is None, str(stages[-1]))
    check("区间首尾相接", stages[0]["ceiling"] == 99 and stages[1]["floor"] == 100, str(stages[:2]))

    deltas = {row["code"]: row["delta"] for row in data["events"]}
    check("事件表覆盖五个内置事件", len(data["events"]) == 5, str(deltas))
    check("每日首次 +2", deltas["chat.daily_first"] == 2, str(deltas))
    check("负向事件为负", deltas["chat.negative"] < 0, str(deltas))

    decay_rows = data["decay"]["rows"]
    check("衰减 4 段", len(decay_rows) == 4, str(decay_rows))
    check("宽限期不扣", decay_rows[0]["per_day"] == 0, str(decay_rows[0]))
    check("第 15 天起每天 8", decay_rows[-1]["per_day"] == 8, str(decay_rows[-1]))

    check("心情 3 条触发规则", len(data["mood"]["rows"]) == 3, str(data["mood"]["rows"]))
    check("来源只含内置", len(data["sources"]) == 1 and data["sources"][0]["id"] == "core")
    check("第三方占位存在", data["third_party"]["available"] is False)

    wide = rules_info.build(
        rules=store.Rules(decay_grace_days=10),
        scoring_cfg=scoring.ScoringConfig(),
        mood_cfg={"enabled": False},
        stages=list(store.STAGE_TABLE),
    )
    ranges = [row["range"] for row in wide["decay"]["rows"]]
    check("宽限期超 7 天不出空区间", all("11–7" not in text for text in ranges), str(ranges))


def test_role_alias():
    print("[alias]")
    import sqlite3

    tmp = Path(tempfile.mkdtemp(prefix="moe_affinity_alias_"))
    try:
        db_path = tmp / "old.db"
        # 模拟老库：roles 表还没有 alias 列
        conn = sqlite3.connect(db_path)
        try:
            conn.executescript(
                "CREATE TABLE roles(role_id TEXT PRIMARY KEY,"
                " display_name TEXT NOT NULL DEFAULT '',"
                " created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);"
            )
            conn.commit()
        finally:
            conn.close()

        db = store.AffinityStore(db_path)  # 打开时应自动补列
        role = db.ensure_role("persona_a")
        check("老库自动补 alias 列", db.role_alias(role) == "")
        check("设置别名", db.set_role_alias(role, "小叽") and db.role_alias(role) == "小叽")
        check("有别名时优先用别名", db.role_label(role) == "小叽")
        check("清除别名回落到 display_name", db.set_role_alias(role, "") and db.role_label(role) == "persona_a")
        check("list_roles 带 alias 字段", "alias" in db.list_roles()[0], str(db.list_roles()[0]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_identity_cache():
    print("[identity]")
    tmp = Path(tempfile.mkdtemp(prefix="moe_affinity_identity_"))
    try:
        db = store.AffinityStore(tmp / "i.db")
        rules = store.Rules()
        role = db.ensure_role("p1")
        db.apply_event(role_id=role, user_id="10086", code="chat.message", delta=5, rules=rules)

        before = db.snapshot(role, "10086", rules)
        check("初始无昵称", before["display_name"] == "", str(before))

        updated = db.upsert_user_names({"10086": "小明", "10010": "不存在的人"})
        check("只更新已有记录", updated == 1, str(updated))
        after = db.snapshot(role, "10086", rules)
        check("昵称写入", after["display_name"] == "小明", str(after))

        db.apply_event(role_id=role, user_id="20000", code="chat.message", delta=1, rules=rules)
        check("缺名用户可列出", "20000" in db.users_without_name(), str(db.users_without_name()))
        db.upsert_user_names({"20000": "小李"})
        check("补名后不再列出", "20000" not in db.users_without_name())

        db.upsert_groups({"123": "有名字的群", "456": ""})
        unnamed = db.groups_without_name()
        check("缺名群可列出", unnamed == ["456"], str(unnamed))

        db.put_avatars({"user:10086": "data:image/jpeg;base64,AAA"})
        got = db.get_avatars(["user:10086", "user:999"])
        check("头像缓存命中", got.get("user:10086", "").startswith("data:image"), str(got))
        check("未缓存的不返回", "user:999" not in got)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_migration_role_scope():
    print("[migration/role]")
    import migrate_self_learning as mig

    check("幂等键带角色", mig.dedupe_key("roleA", "u1") != mig.dedupe_key("roleB", "u1"))
    check("同一角色键稳定", mig.dedupe_key("roleA", "u1") == mig.dedupe_key("roleA", "u1"))

    tmp = Path(tempfile.mkdtemp(prefix="moe_affinity_migrole_"))
    try:
        db = store.AffinityStore(tmp / "r.db")
        rules = store.Rules()
        role_a = db.ensure_role("persona_a")
        role_b = db.ensure_role("persona_b")

        first = db.apply_event(
            role_id=role_a, user_id="u1", code=mig.CODE, delta=100,
            source=mig.SOURCE, rules=rules, dedupe_key=mig.dedupe_key(role_a, "u1"),
        )
        second = db.apply_event(
            role_id=role_b, user_id="u1", code=mig.CODE, delta=100,
            source=mig.SOURCE, rules=rules, dedupe_key=mig.dedupe_key(role_b, "u1"),
        )
        check("第一个人格导入成功", first["status"] == "ok" and first["score"] == 100, str(first))
        check("第二个人格也能导入同一人", second["status"] == "ok" and second["score"] == 100, str(second))

        repeat = db.apply_event(
            role_id=role_a, user_id="u1", code=mig.CODE, delta=100,
            source=mig.SOURCE, rules=rules, dedupe_key=mig.dedupe_key(role_a, "u1"),
        )
        check("同一角色重复导入被去重", repeat["status"] == "duplicate", str(repeat))

        imported_a = db.imported_user_ids(mig.role_prefix(role_a))
        imported_b = db.imported_user_ids(mig.role_prefix(role_b))
        check("角色 A 已导入名单", imported_a == {"u1"}, str(imported_a))
        check("角色 B 已导入名单独立", imported_b == {"u1"}, str(imported_b))

        # 旧版全局键（不带角色）只能靠事件行的 role_id 归属
        db.apply_event(
            role_id=role_a, user_id="u9", code=mig.CODE, delta=50,
            source=mig.SOURCE, rules=rules, dedupe_key="import:self_learning:u9",
        )
        legacy_a = db.legacy_imported_user_ids(role_a)
        legacy_b = db.legacy_imported_user_ids(role_b)
        check("旧键归到当初导入的角色", legacy_a == {"u9"}, str(legacy_a))
        check("旧键不会串到另一个角色", legacy_b == set(), str(legacy_b))
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

        plan = mig.build_plan(legacy)
        mapped = {item["user_id"]: item["target_score"] for item in plan}
        check("比例换算 80/100 -> 800", mapped["u1"] == 800, str(mapped))
        check("比例换算 50/100 -> 500", mapped["u2"] == 500, str(mapped))
        check("换算不做上限裁剪", all(item["target_score"] >= 0 for item in plan))

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
    test_mood()
    test_rules_info()
    test_role_alias()
    test_identity_cache()
    test_migration_role_scope()
    test_migration()
    print()
    if FAILED:
        print(f"RESULT: FAIL ({len(FAILED)}): " + ", ".join(FAILED))
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
