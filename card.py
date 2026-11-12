# -*- coding: utf-8 -*-
"""萌萌好感度 · Pillow 图卡渲染（纯 Pillow，无 astrbot 依赖，可独立测试）。

两种版式，共用「渐变背景 + 圆角面板 + 标题区」底座（风格与萌萌星语提示卡同源）：
  关系档案卡（/好感度）：身份行 + 总分大字 + 档位徽章 + 进度条 + 排名/心情/签到/星尘行；
  好感榜卡（/好感榜）：名次 + 头像 + 昵称 + 档位徽章 + 分数，前三名奖牌色。

字体不打包、多级查找（插件数据目录 fonts/ → AstrBot data/fonts → 系统候选）；
PIL 缺失 / 找不到字体时由调用方（main.py）回落纯文本，绝不影响指令本身。
"""
from __future__ import annotations

import io
import re
import threading
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 常见 CJK 字体候选（按优先级；Windows 在前，Linux Noto/文泉驿在后）
_SYSTEM_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

# 不能当正文用的字体名特征（emoji/symbol/icon 字体没有中文字形）
_SKIP_FONT_NAME_HINTS = ("emoji", "symbol", "icon")
_FONT_PATTERNS = ("*.ttf", "*.otf", "*.ttc")

_font_cache: dict = {}
_font_lock = threading.RLock()

# 档位配色（键与 store.STAGE_TABLE 对齐）；展示层专用，数值口径永远在 store
STAGE_COLORS: dict[str, str] = {
    "bonded": "#E05A8A",
    "intimate": "#E86A8A",
    "close": "#F0A0B8",
    "familiar": "#E8C46A",
    "known": "#A8C8E8",
    "stranger": "#9BA8B8",
}


def stage_color(stage_key: str, fallback: str = "#E86A8A") -> str:
    return STAGE_COLORS.get(str(stage_key or ""), fallback)


def font_dirs(data_dir) -> list:
    """卡片字体候选目录：插件数据目录 fonts/ → AstrBot 公共 data/fonts/（两级反推）。

    不依赖任何其它插件（星语的字体机制不借用）；都没有时由 find_font 走系统候选。
    """
    d = Path(data_dir)
    out = [d / "fonts", d.parent.parent / "fonts"]
    legacy = Path("data/fonts")
    if legacy not in out:
        out.append(legacy)
    return out


def _iter_font_files(dirs):
    for d in dirs or []:
        p = Path(d)
        if not p.is_dir():
            continue
        for pat in _FONT_PATTERNS:
            for x in sorted(p.glob(pat)):
                if any(skip in x.name.lower() for skip in _SKIP_FONT_NAME_HINTS):
                    continue
                yield x


def _font_loadable(path: str) -> bool:
    try:
        _font(path, 20)
        return True
    except Exception:
        return False


def find_font(explicit: str | None = None, extra_dirs=None) -> str | None:
    """多级查找：显式路径 → 候选目录 → 系统字体；找不到返回 None（调用方回落纯文本）。"""
    candidates: list = []
    if explicit:
        candidates.append(explicit)
    candidates.extend(str(x) for x in _iter_font_files(extra_dirs))
    candidates += _SYSTEM_FONT_CANDIDATES
    for c in candidates:
        if c and Path(c).is_file() and _font_loadable(c):
            return c
    return None


def _font(font_file: str, size: float) -> ImageFont.FreeTypeFont:
    key = (font_file, int(size))
    with _font_lock:
        if key not in _font_cache:
            _font_cache[key] = ImageFont.truetype(font_file, int(size))
        return _font_cache[key]


def _bold_variant(font_file: str) -> str:
    """同目录粗体变体（msyh.ttc→msyhbd.ttc、*Regular*→*Bold*）；没有就退回原字体。"""
    p = Path(font_file)
    for a, b in (("msyh.ttc", "msyhbd.ttc"), ("msyh.ttf", "msyhbd.ttc"), ("Regular", "Bold")):
        if a in p.name:
            cand = p.with_name(p.name.replace(a, b))
            if cand.is_file():
                return str(cand)
    return font_file


def _hex_rgb(hex_color: str) -> tuple:
    h = str(hex_color or "").lstrip("#")
    if len(h) != 6:
        return (246, 198, 211)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (246, 198, 211)


def _mix(c1: tuple, c2: tuple, t: float) -> tuple:
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def draw_len(text: str, font: ImageFont.FreeTypeFont) -> float:
    try:
        return font.getlength(text)
    except AttributeError:  # 老版 Pillow
        return font.getsize(text)[0]


def _fit_text(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    """按像素宽度截断并补省略号；放不下一个字符时返回空串。"""
    text = str(text or "")
    if max_width <= 0:
        return ""
    if draw_len(text, font) <= max_width:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw_len(text[:mid] + ell, font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo] + ell) if lo > 0 else ""


# 昵称里的危险字符：零宽类直接删、换行/制表等空白压成单个空格（平台昵称什么都能塞）
_ZW_RE = re.compile(r"[\u200b-\u200f\u2028\u2029\u2060\ufeff]")
_BAD_WS_RE = re.compile(r"\s+")


def sanitize_name(text, max_len: int = 16) -> str:
    """清洗昵称类用户输入：删零宽字符、空白压平并限长（超长加省略号）。"""
    cleaned = _BAD_WS_RE.sub(" ", _ZW_RE.sub("", str(text or ""))).strip()
    if len(cleaned) > max_len:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return cleaned


def _circle_img(img: Image.Image, diameter: int) -> Image.Image:
    img = img.resize((diameter, diameter))
    mask = Image.new("L", (diameter * 4, diameter * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, diameter * 4 - 1, diameter * 4 - 1), fill=255)
    mask = mask.resize((diameter, diameter))
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _gradient(w: int, h: int, base_rgb: tuple) -> Image.Image:
    """纵向渐变：白底混主题色（顶浓底淡）。"""
    top = _mix(base_rgb, (255, 255, 255), 0.52)
    bottom = _mix(base_rgb, (255, 255, 255), 0.90)
    img = Image.new("RGB", (1, h))
    px = img.load()
    for y in range(h):
        px[0, y] = _mix(top, bottom, y / max(1, h - 1))
    return img.resize((w, h))


def _panel_base(w: int, h: int, base_rgb: tuple, radius: int) -> Image.Image:
    grad = _gradient(w, h, base_rgb).convert("RGBA")
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=255)
    grad.putalpha(mask)
    return grad


def _sparkle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, rgb: tuple, alpha: int = 120):
    """四角星装饰。"""
    color = rgb + (alpha,)
    draw.polygon(
        [(cx, cy - r), (cx + r * 0.28, cy - r * 0.28), (cx + r, cy), (cx + r * 0.28, cy + r * 0.28),
         (cx, cy + r), (cx - r * 0.28, cy + r * 0.28), (cx - r, cy), (cx - r * 0.28, cy - r * 0.28)],
        fill=color,
    )


def _card_base(width: int, height: int, accent_hex: str, font_file: str,
               subtitle: str, title: str, title_right: str = "") -> dict:
    """通用底座：渐变背景 + 圆角面板 + 星饰 + 标题区。返回 ctx，内容从 ctx["y"] 起画。"""
    W, H = int(width), int(height)
    u = W / 900.0
    accent = _hex_rgb(accent_hex)
    ink, sub = (74, 74, 96), (150, 150, 168)
    line = (238, 236, 242, 255)
    img = _gradient(W, H, accent).convert("RGBA")
    deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    dd = ImageDraw.Draw(deco)
    _sparkle(dd, W * 0.80, int(20 * u), 11 * u, accent, 110)
    _sparkle(dd, W * 0.16, H - int(20 * u), 8 * u, accent, 90)
    img = Image.alpha_composite(img, deco)
    margin = int(44 * u)
    panel = _panel_base(W - 2 * margin, H - 2 * margin, accent, int(36 * u))
    img.paste(panel, (margin, margin), panel)
    draw = ImageDraw.Draw(img)
    left, right = margin + int(42 * u), W - margin - int(42 * u)
    y = margin + int(32 * u)
    disp = _bold_variant(font_file)
    meta_font = _font(font_file, int(25 * u))
    draw.text((left, y), subtitle, font=meta_font, fill=sub)
    if title_right:
        draw.text((right, y), title_right, font=meta_font, fill=sub, anchor="ra")
    # 大标题自适应：先缩字号再截断
    avail = right - left - (draw_len(title_right, meta_font) + int(28 * u) if title_right else 0)
    avail = max(int(150 * u), int(avail))
    title_size = int(56 * u)
    title_font = _font(disp, title_size)
    while title_size > int(34 * u) and draw_len(str(title), title_font) > avail:
        title_size -= int(4 * u)
        title_font = _font(disp, title_size)
    draw.text((left, y + int(34 * u)), _fit_text(str(title), title_font, avail),
              font=title_font, fill=ink)
    y += int(116 * u)
    draw.line([(left, y), (right, y)], fill=line, width=max(1, int(2 * u)))
    y += int(24 * u)
    return {
        "img": img, "draw": draw, "left": left, "right": right, "y": y, "u": u,
        "ink": ink, "sub": sub, "disp": disp, "accent": accent, "line": line,
        "W": W, "H": H, "margin": margin, "font_file": font_file,
    }


def _card_save(ctx: dict, cards_root, filename: str, signer: str, footer: str = "") -> str:
    draw, u, ff = ctx["draw"], ctx["u"], ctx["font_file"]
    fy = ctx["H"] - ctx["margin"] - int(24 * u)
    if footer:
        draw.text((ctx["left"], fy), footer,
                  font=_font(ff, int(22 * u)), fill=ctx["sub"], anchor="ls")
    draw.text((ctx["right"], fy), f"—— {signer}",
              font=_font(ff, int(26 * u)), fill=ctx["sub"], anchor="rs")
    cards_root = Path(cards_root)
    cards_root.mkdir(parents=True, exist_ok=True)
    out = cards_root / filename
    ctx["img"].convert("RGB").save(out, "PNG")
    return str(out)


def _identity_row(ctx: dict, nickname: str, uid: str, avatar_data, right_text: str = "") -> None:
    """身份行：圆头像（无图用首字占位圆）+ 昵称 + QQ 号；右侧可选小字（认识 N 天）。"""
    nickname = sanitize_name(nickname)
    img, draw, u, ff = ctx["img"], ctx["draw"], ctx["u"], ctx["font_file"]
    left, y = ctx["left"], ctx["y"]
    av_d = int(88 * u)
    av = None
    if avatar_data:
        try:
            av = Image.open(io.BytesIO(avatar_data)).convert("RGBA")
        except Exception:
            av = None
    if av is not None:
        avatar_img = _circle_img(av, av_d)
    else:
        placeholder = Image.new("RGBA", (av_d * 2, av_d * 2),
                                _mix(ctx["accent"], (255, 255, 255), 0.35) + (255,))
        ImageDraw.Draw(placeholder).text(
            (av_d, av_d), (nickname or "友")[0],
            font=_font(ff, int(60 * u)), fill=(255, 255, 255, 255), anchor="mm")
        avatar_img = _circle_img(placeholder, av_d)
    img.paste(avatar_img, (left, y), avatar_img)
    tx = left + av_d + int(26 * u)
    name_font = _font(ff, int(38 * u))
    name_avail = (ctx["right"] - (draw_len(right_text, _font(ff, int(24 * u))) + int(20 * u))
                  if right_text else ctx["right"]) - tx
    draw.text((tx, y + int(4 * u)), _fit_text(nickname or "旅行者", name_font, max(1, name_avail)),
              font=name_font, fill=ctx["ink"])
    if uid:
        draw.text((tx, y + int(54 * u)), f"QQ {uid}",
                  font=_font(ff, int(22 * u)), fill=ctx["sub"])
    if right_text:
        draw.text((ctx["right"], y + int(30 * u)), right_text,
                  font=_font(ff, int(24 * u)), fill=ctx["sub"], anchor="ra")
    ctx["y"] = y + av_d + int(20 * u)


def _info_row(ctx: dict, text: str, *, badge: tuple | None = None) -> None:
    """一条淡底信息行（心情 / 签到 / 星尘 / 最近互动共用）。badge=(文字, rgb) 时画徽章。"""
    draw, u, ff = ctx["draw"], ctx["u"], ctx["font_file"]
    left, right, y = ctx["left"], ctx["right"], ctx["y"]
    row_h = int(56 * u)
    tint = _mix(ctx["accent"], (255, 255, 255), 0.6) + (185,)
    draw.rounded_rectangle([left, y, right, y + row_h], radius=int(14 * u), fill=tint)
    cy = y + row_h / 2
    tx = left + int(20 * u)
    if badge:
        label, rgb = badge
        pill_h = int(38 * u)
        pad = int(18 * u)
        pill_font = _font(ff, int(22 * u))
        tw = draw_len(str(label), pill_font) + pad * 2
        lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        draw.rounded_rectangle([tx, cy - pill_h / 2, tx + tw, cy + pill_h / 2],
                               radius=pill_h // 2, fill=rgb + (255,))
        draw.text((tx + tw / 2, cy), str(label), font=pill_font,
                  fill=(58, 58, 74) if lum > 160 else (255, 255, 255), anchor="mm")
        tx += tw + int(16 * u)
    draw.text((tx, cy), _fit_text(str(text), _font(ff, int(25 * u)), right - tx - int(20 * u)),
              font=_font(ff, int(25 * u)), fill=ctx["ink"], anchor="lm")
    ctx["y"] = y + row_h + int(12 * u)


def render_detail_card(
    *,
    title: str,
    nickname: str,
    uid: str = "",
    avatar_data: bytes | None = None,
    score: float,
    stage_key: str = "stranger",
    stage_label: str = "陌生",
    next_stage_label: str = "",
    next_stage_at=None,
    progress: float = 0.0,
    rank: int = 1,
    known_days: int = 0,
    last_gap: str = "刚刚",
    mood_line: str = "",
    checkin_line: str = "",
    stardust_line: str = "",
    recent_line: str = "",
    accent_hex: str = "#F6C6D3",
    cards_root,
    file_key: str = "detail",
    signer: str = "萌萌好感度",
    footer: str = "发送 /好感度 查看详情 · 管理员可用 /好感榜 看排行",
    font_path: str | None = None,
    extra_font_dirs=None,
    width: int = 900,
) -> str:
    """关系档案卡（/好感度）。渲染失败抛异常由调用方回落纯文本。"""
    font_file = find_font(font_path, extra_font_dirs)
    if not font_file:
        raise RuntimeError("未找到可用中文字体")
    u0 = width / 900.0
    rows = [r for r in (mood_line, checkin_line, stardust_line, recent_line) if r]
    # 高度按实际块算（height 只是下限）：标题区 216 + 身份行 108
    # + 分数块 202（分隔 18 + 大字区 140 + 进度条 20 + 间隙 24）
    # + 信息行 68/行（「排名」行固定画，再 +1）+ 底部 110
    H = int((216 + 108 + 202 + (len(rows) + 1) * 68 + 110) * u0)
    ctx = _card_base(width, H, accent_hex, font_file,
                     subtitle="萌萌好感度 · 关系档案", title=title)
    _identity_row(ctx, nickname, uid, avatar_data, right_text=f"认识 {max(0, int(known_days))} 天")
    draw, u, ff = ctx["draw"], ctx["u"], ctx["font_file"]
    left, right = ctx["left"], ctx["right"]

    # ---- 分数块：标签 + 大字 + 档位徽章 + 进度条 ----
    draw.line([(left, ctx["y"]), (right, ctx["y"])], fill=ctx["line"], width=1)
    ctx["y"] += int(18 * u)
    score_font = _font(ctx["disp"], int(84 * u))
    score_text = f"{float(score):.2f}"
    stage_rgb = _hex_rgb(stage_color(stage_key))
    lum = 0.299 * stage_rgb[0] + 0.587 * stage_rgb[1] + 0.114 * stage_rgb[2]
    pill_font = _font(ctx["disp"], int(28 * u))
    pill_h = int(52 * u)
    pill_w = draw_len(stage_label, pill_font) + int(36 * u)
    pill_x = right - pill_w
    draw.text((left, ctx["y"]), "好感度", font=_font(ff, int(26 * u)), fill=ctx["sub"])
    draw.text((left, ctx["y"] + int(34 * u)), score_text, font=score_font, fill=ctx["ink"])
    draw.rounded_rectangle([pill_x, ctx["y"] + int(6 * u), right, ctx["y"] + int(6 * u) + pill_h],
                           radius=pill_h // 2, fill=stage_rgb + (255,))
    draw.text((pill_x + pill_w / 2, ctx["y"] + int(6 * u) + pill_h / 2), stage_label,
              font=pill_font, fill=(58, 58, 74) if lum > 160 else (255, 255, 255), anchor="mm")
    if next_stage_label and next_stage_at:
        need = max(0, int(round(float(next_stage_at) - float(score))))
        note = f"距「{next_stage_label}」还差 {need}"
    else:
        note = "已是最高档位"
    draw.text((left + draw_len(score_text, score_font) + int(20 * u), ctx["y"] + int(102 * u)),
              note, font=_font(ff, int(24 * u)), fill=ctx["sub"])
    ctx["y"] += int(140 * u)
    # 进度条：档位内进度；到顶档画满
    bar_h = int(20 * u)
    prog = max(0.0, min(1.0, float(progress or 0.0)))
    draw.rounded_rectangle([left, ctx["y"], right, ctx["y"] + bar_h],
                           radius=bar_h // 2, fill=_mix(ctx["accent"], (255, 255, 255), 0.6) + (255,))
    fill_w = max(bar_h, int((right - left) * prog)) if prog > 0 else bar_h
    draw.rounded_rectangle([left, ctx["y"], left + fill_w, ctx["y"] + bar_h],
                           radius=bar_h // 2, fill=stage_rgb + (255,))
    ctx["y"] += bar_h + int(24 * u)

    # ---- 信息行：排名 / 心情 / 签到 / 星尘 / 最近 ----
    _info_row(ctx, f"当前角色内排名 第 {max(1, int(rank))} 名 · 最近互动 {last_gap}")
    for line in rows:
        _info_row(ctx, line)
    return _card_save(ctx, cards_root, f"affinity_{file_key}.png", signer, footer=footer)


def render_rank_card(
    *,
    title: str = "好感榜",
    subtitle: str = "萌萌好感度 · 角色内排行",
    role_label: str = "",
    rows: list,
    avatars=None,
    accent_hex: str = "#F6C6D3",
    cards_root,
    file_key: str = "rank",
    signer: str = "萌萌好感度",
    footer: str = "",
    empty_text: str = "还没有任何好感度记录。",
    font_path: str | None = None,
    extra_font_dirs=None,
    width: int = 900,
) -> str:
    """好感榜卡。rows=[(名次, 昵称, 分数, 档位名, 档位键)]；avatars 与 rows 等长（bytes|None）。"""
    font_file = find_font(font_path, extra_font_dirs)
    if not font_file:
        raise RuntimeError("未找到可用中文字体")
    u0 = width / 900.0
    row_h = int(78 * u0)
    H = int((218 + 30 + max(1, len(rows)) * row_h + 96 + 16) * u0)
    ctx = _card_base(width, H, accent_hex, font_file,
                     subtitle=subtitle, title=title, title_right=str(role_label or ""))
    draw, u, ff = ctx["draw"], ctx["u"], ctx["font_file"]
    left, right = ctx["left"], ctx["right"]
    y = ctx["y"]
    medal = [(212, 175, 55), (176, 180, 196), (205, 140, 100)]

    if not rows:
        box_h = int(120 * u)
        draw.rounded_rectangle([left, y, right, y + box_h], radius=int(14 * u),
                               fill=_mix(ctx["accent"], (255, 255, 255), 0.6) + (185,))
        draw.text(((left + right) // 2, y + box_h / 2), empty_text,
                  font=_font(ff, int(30 * u)), fill=ctx["sub"], anchor="mm")
        return _card_save(ctx, cards_root, f"rank_{file_key}.png", signer, footer=footer)

    # 名次列自适应（两位数/三位数都不挤昵称）；头像列 46u；档位徽章列全表同宽
    name_font = _font(ff, int(32 * u))
    rank_size = int(44 * u)
    rank_font = _font(ctx["disp"], rank_size)
    rank_cap = int(72 * u)
    while rank_size > int(26 * u) and max(draw_len(str(r[0]), rank_font) for r in rows) > rank_cap:
        rank_size -= int(4 * u)
        rank_font = _font(ctx["disp"], rank_size)
    rank_col = max(draw_len(str(r[0]), rank_font) for r in rows)
    avatars = list(avatars or [])
    has_av = bool(avatars)
    av_d, av_gap = int(46 * u), int(14 * u)
    name_x = left + int(rank_col) + int(22 * u) + ((av_d + av_gap) if has_av else 0)
    score_font = _font(ctx["disp"], int(42 * u))
    right_w = max(draw_len(str(r[2]), score_font) for r in rows)
    pill_font = _font(ctx["disp"], int(26 * u))
    pill_h = int(46 * u)
    pill_w = int(max(draw_len(str(r[3]), pill_font) for r in rows) + int(30 * u))
    pill_right = right - int(right_w) - int(18 * u)
    pill_left = pill_right - pill_w
    name_avail = int(pill_left - name_x - int(26 * u))

    for i, (rank, name, score_text, stage_label, stage_key) in enumerate(rows):
        name = sanitize_name(name)
        cy = y + row_h * i
        rank_rgb = medal[rank - 1] if isinstance(rank, int) and 1 <= rank <= 3 else ctx["sub"]
        draw.text((left, cy + row_h / 2), str(rank), font=rank_font, fill=rank_rgb, anchor="lm")
        if has_av:
            av_x = left + int(rank_col) + int(22 * u)
            av_y = cy + row_h // 2 - av_d // 2
            data = avatars[i] if i < len(avatars) else None
            av_img = None
            if data:
                try:
                    av_img = _circle_img(Image.open(io.BytesIO(data)).convert("RGBA"), av_d)
                except Exception:
                    av_img = None
            if av_img is None:
                ph = Image.new("RGBA", (av_d, av_d), (0, 0, 0, 0))
                ImageDraw.Draw(ph).ellipse(
                    [0, 0, av_d - 1, av_d - 1],
                    fill=_mix(ctx["accent"], (255, 255, 255), 0.5) + (255,))
                ImageDraw.Draw(ph).text((av_d / 2, av_d / 2), (name or "友")[0],
                                        font=_font(ff, int(22 * u)), fill=ctx["ink"], anchor="mm")
                ctx["img"].paste(ph, (av_x, av_y), ph)
            else:
                ctx["img"].paste(av_img, (av_x, av_y), av_img)
        draw.text((name_x, cy + row_h / 2), _fit_text(name, name_font, name_avail),
                  font=name_font, fill=ctx["ink"], anchor="lm")
        grgb = _hex_rgb(stage_color(stage_key))
        lum = 0.299 * grgb[0] + 0.587 * grgb[1] + 0.114 * grgb[2]
        draw.rounded_rectangle([pill_left, cy + (row_h - pill_h) // 2,
                                pill_right, cy + (row_h + pill_h) // 2],
                               radius=pill_h // 2, fill=grgb + (255,))
        draw.text(((pill_left + pill_right) / 2, cy + row_h / 2), stage_label,
                  font=pill_font, fill=(58, 58, 74) if lum > 160 else (255, 255, 255), anchor="mm")
        draw.text((right, cy + row_h / 2), str(score_text),
                  font=score_font, fill=ctx["ink"], anchor="rm")
        if i < len(rows) - 1:
            draw.line([(left, cy + row_h), (right, cy + row_h)], fill=ctx["line"], width=1)
    return _card_save(ctx, cards_root, f"rank_{file_key}.png", signer, footer=footer)
