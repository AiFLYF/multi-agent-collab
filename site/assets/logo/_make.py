# -*- coding: utf-8 -*-
"""生成 multi-agent-collab 的 logo / 图标资产。

设计概念（与站点第 05 章「协作拓扑」同源）：
  中央六边形 = .agents/ 共享黑板；六个平台色节点 = 六个 AI 会话；
  六条连线 = 每个会话读规则 / 写心跳 / 读指令。六重对称，几何全部算出来。

用法（在仓库根目录）：
  python site/assets/logo/_make.py

产出全部落在本脚本所在目录。wordmark 用站点自托管的 Sora / PlexMono
转成矢量路径，所以 SVG 不依赖查看端是否装了字体。
"""
import math
import pathlib

from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent            # 仓库根
FONTS = ROOT / "site" / "vendor" / "fonts"

# ── 站点设计令牌（site/assets/css/style.css） ──────────────────────
CY = "#46E0FF"
CY_HI = "#A8F2FF"
CY_LO = "#29A9D8"
INK = "#DFF1FA"
INK2 = "#8FA9BC"
BG_DEEP = "#05070C"
BG_TILE_HI = "#0E1A28"

# 六个平台色：暗底用原色，亮底用加深版（原色里的浅青/青柠在白底上对比不足）
PLAT_DARK = ["#FF8A50", "#37D67A", "#53C8FF", "#B79CFF", "#6E9BFF", "#D9F24A"]
PLAT_LIGHT = ["#C85A18", "#1B8F4D", "#1673A8", "#6B4FCF", "#2E5FD0", "#77890A"]

# 亮底版本用的墨色
CY_LIGHT = "#0E7FA8"
INK_LIGHT = "#0B2231"
INK2_LIGHT = "#4A6E80"

VIEW = 96.0
C = VIEW / 2.0
ANGLES = [-90, -30, 30, 90, 150, 210]       # 从正上方起，顺时针


def n(v):
    """紧凑数字格式：不留多余的 0 和小数点。"""
    s = "%.2f" % v
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def emit(name, text):
    """写文件。显式 newline="\\n"：Windows 上 Python 默认会把 \\n 转成 \\r\\n，
    而仓库 .gitattributes 规定 * text=auto eol=lf，不统一就会整文件 diff。"""
    (HERE / name).write_text(text, encoding="utf-8", newline="\n")


def pt(r, deg, cx=C, cy=C):
    a = math.radians(deg)
    return (cx + r * math.cos(a), cy + r * math.sin(a))


def hex_path(r, cx=C, cy=C, rot=-90):
    pts = [pt(r, rot + i * 60, cx, cy) for i in range(6)]
    return "M" + " L".join("%s %s" % (n(x), n(y)) for x, y in pts) + " Z"


# ── 标记本体 ──────────────────────────────────────────────────────
def mark_body(link_color, node_colors, core_fill, halo=True,
              core_r=14.0, link_r0=12.5, link_r1=32.5, link_w=3.2,
              node_r=5.0, node_at=37.0, link_op="0.55"):
    out = []
    if halo:
        out.append('<circle cx="%s" cy="%s" r="27" fill="url(#halo)"/>' % (n(C), n(C)))
    for a in ANGLES:
        x0, y0 = pt(link_r0, a)
        x1, y1 = pt(link_r1, a)
        out.append(
            '<line x1="%s" y1="%s" x2="%s" y2="%s" stroke="%s" stroke-opacity="%s"'
            ' stroke-width="%s" stroke-linecap="round"/>'
            % (n(x0), n(y0), n(x1), n(y1), link_color, link_op, n(link_w))
        )
    if node_colors:
        for i, a in enumerate(ANGLES):
            x, y = pt(node_at, a)
            out.append('<circle cx="%s" cy="%s" r="%s" fill="%s"/>'
                       % (n(x), n(y), n(node_r), node_colors[i]))
    out.append('<path d="%s" fill="%s"/>' % (hex_path(core_r), core_fill))
    return "\n  ".join(out)


DEFS_CYAN = """<defs>
    <linearGradient id="core" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="%s"/>
      <stop offset="1" stop-color="%s"/>
    </linearGradient>
    <radialGradient id="halo">
      <stop offset="0" stop-color="%s" stop-opacity=".20"/>
      <stop offset="1" stop-color="%s" stop-opacity="0"/>
    </radialGradient>
  </defs>""" % (CY_HI, CY_LO, CY, CY)


def wrap(view_w, view_h, defs, body, label="multi-agent-collab"):
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s"'
             ' width="%s" height="%s" role="img" aria-label="%s">'
             % (n(view_w), n(view_h), n(view_w), n(view_h), label)]
    if defs:
        parts.append("  " + defs)
    parts.append("  " + body)
    parts.append("</svg>\n")
    return "\n".join(parts)


# ── 文字转矢量路径 ────────────────────────────────────────────────
def text_to_group(font_file, text, size, x, y, tracking=0.0, color=None):
    """返回 (svg 片段, 文本宽度)。tracking 单位是像素。"""
    font = TTFont(str(FONTS / font_file))
    upem = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    glyphs = font.getGlyphSet()
    hmtx = font["hmtx"]
    scale = size / upem
    track_fu = (tracking / scale) if scale else 0.0
    paths, pen = [], 0.0
    for ch in text:
        gname = cmap.get(ord(ch))
        if gname is None:                      # 缺字：留出半个字宽，别静默吞掉
            pen += upem * 0.5 + track_fu
            continue
        spen = SVGPathPen(glyphs)
        glyphs[gname].draw(spen)
        d = spen.getCommands()
        if d:
            paths.append('<path transform="translate(%s 0)" d="%s"/>' % (n(pen), d))
        pen += hmtx[gname][0] + track_fu
    width = pen * scale - tracking
    fill = ' fill="%s"' % color if color else ""
    g = ('<g transform="translate(%s %s) scale(%.6f -%.6f)"%s>\n    %s\n  </g>'
         % (n(x), n(y), scale, scale, fill, "\n    ".join(paths)))
    return g, width


# ── 1) 标记（暗底全彩） ───────────────────────────────────────────
emit("mark.svg", wrap(VIEW, VIEW, DEFS_CYAN, mark_body(CY, PLAT_DARK, "url(#core)")))

# ── 2) 单色标记（currentColor，随主题变色） ───────────────────────
emit("mark-mono.svg", wrap(VIEW, VIEW, "",
         mark_body("currentColor", ["currentColor"] * 6, "currentColor",
                   halo=False, link_op="0.5")))

# ── 3) favicon：16px 下彩色圆点会糊，改用单色粗线 + 放大核心；
#      并在 SVG 内按 prefers-color-scheme 换色，亮色标签栏也能看清 ──
favicon = wrap(VIEW, VIEW, "", mark_body(
    "var(--fav-ink)", [], "var(--fav-ink)", halo=False,
    core_r=15.5, link_r0=14.0, link_r1=39.0, link_w=7.6, link_op="0.92"))
favicon = favicon.replace(
    '<svg xmlns="http://www.w3.org/2000/svg"',
    '<svg xmlns="http://www.w3.org/2000/svg" style="--fav-ink:%s"' % CY)
favicon = favicon.replace(
    "</svg>\n",
    '<style>:root{--fav-ink:%s}@media (prefers-color-scheme:light){:root{--fav-ink:%s}}</style>\n</svg>\n'
    % (CY, CY_LIGHT))
emit("favicon.svg", favicon)

# ── 4) 磁贴：圆角版（头像 / 展示用）+ 直角满幅版（iOS 主屏 / PWA，
#      系统会自己切圆角，给圆角图会二次裁切） ───────────────────────
TILE = 512.0
MARK_IN_TILE = 344.0
tscale = MARK_IN_TILE / VIEW
toff = (TILE - MARK_IN_TILE) / 2.0


def tile_defs(radius):
    return """<defs>
    <linearGradient id="tilebg" x1="0" y1="0" x2="0.9" y2="1">
      <stop offset="0" stop-color="%s"/>
      <stop offset="1" stop-color="%s"/>
    </linearGradient>
    <linearGradient id="core" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="%s"/>
      <stop offset="1" stop-color="%s"/>
    </linearGradient>
    <radialGradient id="halo">
      <stop offset="0" stop-color="%s" stop-opacity=".20"/>
      <stop offset="1" stop-color="%s" stop-opacity="0"/>
    </radialGradient>
    <clipPath id="tileclip"><rect width="%s" height="%s" rx="%s"/></clipPath>
  </defs>""" % (BG_TILE_HI, BG_DEEP, CY_HI, CY_LO, CY, CY,
                n(TILE), n(TILE), n(radius))


def build_tile(radius):
    body = (
        '<g clip-path="url(#tileclip)">\n'
        '    <rect width="%s" height="%s" fill="url(#tilebg)"/>\n'
        '    <rect x="1" y="1" width="%s" height="%s" rx="%s" fill="none"'
        ' stroke="%s" stroke-opacity=".22" stroke-width="2"/>\n'
        '    <g transform="translate(%s %s) scale(%.6f)">\n      %s\n    </g>\n'
        '  </g>'
        % (n(TILE), n(TILE), n(TILE - 2), n(TILE - 2), n(max(0.0, radius - 1)), CY,
           n(toff), n(toff), tscale, mark_body(CY, PLAT_DARK, "url(#core)"))
    )
    return wrap(TILE, TILE, tile_defs(radius), body, "multi-agent-collab 图标")


emit("icon-tile.svg", build_tile(TILE * 0.22))
emit("icon-square.svg", build_tile(0.0))

# ── 5) 横向锁定版：暗底 / 亮底两套 ────────────────────────────────
WORD = "multi-agent-collab"
TAG = "SIX PLATFORMS · ONE BOARD"
WORD_SIZE = 41.0
TAG_SIZE = 10.5
TAG_TRACK = 3.6
GAP = 26.0
PAD = 6.0


def lockup_parts(variant):
    """返回 (defs, body, 总宽, 总高)。body 里的坐标已经是最终坐标。"""
    if variant == "dark":
        link, plats, core = CY, PLAT_DARK, "url(#core)"
        ink, ink2, halo = INK, INK2, True
        defs = DEFS_CYAN
    else:
        link, plats, core = CY_LIGHT, PLAT_LIGHT, CY_LIGHT
        ink, ink2, halo = INK_LIGHT, INK2_LIGHT, False
        defs = ""

    mark = mark_body(link, plats, core, halo=halo)
    mx, my = PAD, PAD
    tx = PAD + VIEW + GAP

    word_g, word_w = text_to_group("sora-800.woff2", WORD, WORD_SIZE, tx, 0, color=ink)
    tag_g, tag_w = text_to_group("plexmono-500.woff2", TAG, TAG_SIZE, tx, 0,
                                 tracking=TAG_TRACK, color=ink2)

    # 竖直排布：mark 占满，文字块与 mark 垂直居中
    cap = WORD_SIZE * 0.72                      # Sora 大写字高近似
    block_h = cap + 6 + TAG_SIZE * 1.4
    total_h = VIEW + PAD * 2
    word_base = my + (VIEW - block_h) / 2 + cap
    tag_base = word_base + 6 + TAG_SIZE * 1.15

    word_g = word_g.replace('translate(%s 0)' % n(tx),
                            'translate(%s %s)' % (n(tx), n(word_base)))
    tag_g = tag_g.replace('translate(%s 0)' % n(tx),
                          'translate(%s %s)' % (n(tx), n(tag_base)))

    total_w = tx + max(word_w, tag_w) + PAD
    body = ('<g transform="translate(%s %s)">\n    %s\n  </g>\n  %s\n  %s'
            % (n(mx), n(my), mark, word_g, tag_g))
    return defs, body, total_w, total_h


def build_lockup(variant):
    defs, body, w, h = lockup_parts(variant)
    return wrap(w, h, defs, body)


emit("logo.svg", build_lockup("dark"))
emit("logo-light.svg", build_lockup("light"))

# ── 6) 社交分享卡 1200×630 ────────────────────────────────────────
OG_W, OG_H = 1200.0, 630.0
_d, _b, lw, lh = lockup_parts("dark")
SCALE = 820.0 / lw
og_defs = DEFS_CYAN + """
    <radialGradient id="ogglow">
      <stop offset="0" stop-color="%s" stop-opacity=".10"/>
      <stop offset="1" stop-color="%s" stop-opacity="0"/>
    </radialGradient>
    <pattern id="oggrid" width="40" height="40" patternUnits="userSpaceOnUse">
      <path d="M40 0H0V40" fill="none" stroke="%s" stroke-opacity=".05"/>
    </pattern>""" % (CY, CY, CY)

og_body = (
    '<rect width="%s" height="%s" fill="%s"/>\n'
    '  <rect width="%s" height="%s" fill="url(#oggrid)"/>\n'
    '  <ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="url(#ogglow)"/>\n'
    '  <g transform="translate(%s %s) scale(%.5f)">\n    %s\n  </g>\n'
    '  <rect x="0" y="%s" width="%s" height="3" fill="%s" fill-opacity=".5"/>'
    % (n(OG_W), n(OG_H), BG_DEEP, n(OG_W), n(OG_H),
       n(OG_W / 2), n(OG_H / 2), n(OG_W * 0.62), n(OG_H * 0.52),
       n((OG_W - lw * SCALE) / 2), n((OG_H - lh * SCALE) / 2), SCALE, _b,
       n(OG_H - 3), n(OG_W), CY)
)
emit("og-image.svg", wrap(OG_W, OG_H, og_defs, og_body, "multi-agent-collab"))

print("已生成：")
for p in sorted(HERE.glob("*.svg")):
    print("   %-18s %6d bytes" % (p.name, p.stat().st_size))
