"""图卡自检（脚本式，直接 python tests/test_card.py，退出码即结论）。

覆盖：昵称清洗、档位配色、真实渲染（需本机有中文字体，如 Windows 微软雅黑）、
打包清单与热重载列表守卫、配置 schema 的 card 分组。
不 import main.py —— 本机没有 astrbot 运行时，main 的守卫用文本断言。
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import card  # noqa: E402

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name} {detail}")
        FAILED.append(name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="affinity_card_"))
    try:
        # ---- 纯函数 ----
        check("sanitize 零宽与换行", card.sanitize_name("\u200b小\n 明\t") == "小 明")
        check("sanitize 限长省略号", card.sanitize_name("一" * 30, max_len=10).endswith("…"))
        check("档位配色映射", card.stage_color("bonded") == "#E05A8A")
        check("档位配色兜底", card.stage_color("nope") != "")
        check("hex 解析容错", card._hex_rgb("#FFF") == (246, 198, 211))

        # ---- 真实渲染（无字体则跳过，不算失败）----
        font = card.find_font()
        if not font:
            print("  skip 本机没有可用中文字体，渲染用例跳过")
            return
        print(f"  info 字体：{font}")
        png = tmp / "avatar.png"
        Image = card.Image
        Image.new("RGB", (64, 64), (200, 100, 120)).save(png, "PNG")
        avatar = png.read_bytes()

        path = card.render_detail_card(
            title="你与「小叽」的关系",
            nickname="测\u200b试\n用户", uid="123456", avatar_data=avatar,
            score=345.6, stage_key="familiar", stage_label="熟悉",
            next_stage_label="亲近", next_stage_at=600, progress=0.15,
            rank=2, known_days=30, last_gap="3 小时前",
            mood_line="心情：平静（喜悦 5.0 · 烦躁 1.0 · 低落 1.0 · 不安 1.0）",
            checkin_line="签到 45 分 · 累计 12 天 · 连签 3 天",
            stardust_line="星尘 8.5 分 · 累计 9 天（抽签同步 + 送礼）",
            recent_line="最近互动：聊天 +1 · 被叫 +2",
            cards_root=tmp / "cards", file_key="test",
        )
        check("档案卡落盘", Path(path).exists() and Path(path).stat().st_size > 10000)

        path2 = card.render_rank_card(
            title="好感榜", role_label="「小叽」",
            rows=[
                (1, "第一名用户", "1024", "羁绊", "bonded"),
                (2, "第二名", "512", "亲密", "intimate"),
                (3, "第三名", "256", "亲近", "close"),
                (4, "第四名", "64", "眼熟", "known"),
            ],
            avatars=[avatar, None, avatar, None],
            cards_root=tmp / "cards", file_key="rank_test",
            footer="参与 4 人 · 取前 10 名",
        )
        check("榜单卡落盘", Path(path2).exists() and Path(path2).stat().st_size > 10000)

        path3 = card.render_rank_card(
            rows=[], cards_root=tmp / "cards", file_key="rank_empty",
        )
        check("榜单空态卡", Path(path3).exists())

        # ---- 守卫：打包清单 / 热重载列表 ----
        zps = (ROOT / "build_zip.ps1").read_text(encoding="utf-8")
        check("build_zip includeList 含 card.py", '"card.py"' in zps)
        main_py = (ROOT / "main.py").read_text(encoding="utf-8")
        check("main 热重载列表含 card", "webui_api, card," in main_py)
        check("main 卡模块导入兜底", "from . import card" in main_py and "card = None" in main_py)

        # ---- schema：card 分组每节点都有 type（AstrBot 装载期硬约束）----
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        card_cfg = schema.get("card") or {}
        check("schema card 是 object", card_cfg.get("type") == "object")
        bad = [
            k for k, v in (card_cfg.get("items") or {}).items() if not v.get("type")
        ]
        check("schema card 子节点 type 齐全", not bad, str(bad))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print(f"RESULT: FAIL ({len(FAILED)}): {', '.join(FAILED)}")
        sys.exit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
