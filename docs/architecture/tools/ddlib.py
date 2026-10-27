"""Drill-down layout primitives with two emitters.

One layout (nodes, zones, edges, gates) per drill-down drives both outputs, so they cannot drift:
- to_excalidraw(): the prototype, in the style of the user's analyzer excalidraw (sketch font, black strokes,
  pink gate diamonds, red deny paths);
- to_svg(): the production figure in the ClaimClarity skin (tokens in diagram-design's style guide), with official
  AWS icons (exported from draw.io's aws4 set into tools/icons/).

Coordinates are absolute canvas pixels. Edges are explicit orthogonal polylines; gates sit ON an edge point.
"""
import base64
import json
import math
import re
from html import escape
from pathlib import Path

ICONS = Path(__file__).parent / "icons"

# ClaimClarity tokens (diagram-design style guide, ClaimClarity profile)
NAVY, BLUE, TINT, INK, LINE = "#1C2B57", "#4A6BB0", "#EAF0F9", "#1A1D23", "#C9CBCE"
SOFT, LINK, DENY, PAPER = "#5B6472", "#3A5A9E", "#B42318", "#FFFFFF"
COND, SANS, MONO = "'Barlow Condensed', 'Arial Narrow', sans-serif", "'Barlow', Arial, sans-serif", "'IBM Plex Mono', ui-monospace, monospace"
FONTS = "https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600;700&family=Barlow+Condensed:wght@500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap"


class Diagram:
    def __init__(self, slug, title, desc, w, h):
        self.slug, self.title, self.desc, self.w, self.h = slug, title, desc, w, h
        self.zones, self.nodes, self.edges, self.gates, self.texts, self.legend = [], [], [], [], [], []

    # ---- authoring -------------------------------------------------------------------------------------------
    def zone(self, x, y, w, h, label, kind="plain", icon=None):
        """kind: bucket (solid blue), phi (navy dashed: PHI / customer boundary), aws, vpc, subnet, worker, plain."""
        self.zones.append(dict(x=x, y=y, w=w, h=h, label=label, kind=kind, icon=icon))

    def node(self, id, x, y, w, h, name, sub=None, icon=None, shape="box", style="service", inner=None):
        """shape: box, db, folder, file, agent, decision, ui, token, steps, people.
        style: service (tint fill), white, external (dashed grey), focal (navy fill), optional (dashed)."""
        n = dict(id=id, x=x, y=y, w=w, h=h, name=name, sub=sub, icon=icon, shape=shape, style=style, inner=inner)
        self.nodes.append(n)
        return n

    def edge(self, pts, label=None, at=None, style="solid", end=True, start=False, lpos="above"):
        """style: solid, dashed, deny (red dashed, stop bar at the end), link."""
        self.edges.append(dict(pts=pts, label=label, at=at, style=style, end=end, start=start, lpos=lpos))

    def gate(self, n, x, y, label, side="above", icon=None):
        self.gates.append(dict(n=n, x=x, y=y, label=label, side=side, icon=icon))

    def text(self, x, y, s, role="note", anchor="start", color=None):
        self.texts.append(dict(x=x, y=y, s=s, role=role, anchor=anchor, color=color))


# ---------------------------------------------------------------------------------------------------------------
# icons
_icon_cache = {}


def icon_svg(name):
    """Inner markup + viewBox of an exported draw.io icon."""
    if name not in _icon_cache:
        raw = (ICONS / f"{name}.svg").read_text()
        vb = re.search(r'viewBox="([^"]+)"', raw).group(1)
        body = re.search(r"<svg[^>]*>(.*)</svg>", raw, re.S).group(1)
        body = re.sub(r"<defs>.*?</defs>", "", body, flags=re.S)
        body = re.sub(r"<switch>.*?</switch>", "", body, flags=re.S)
        body = re.sub(r'\s(style|pointer-events|data-cell-id)="[^"]*"', "", body)
        _icon_cache[name] = (vb, body)
    return _icon_cache[name]


def icon_dataurl(name):
    return "data:image/svg+xml;base64," + base64.b64encode((ICONS / f"{name}.svg").read_bytes()).decode()


# Tabler glyphs (MIT) from diagram-design's icon set, for non-AWS things
GLYPHS = {
    "entra": '<path d="M20 6v12a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-12a2 2 0 0 1 2 -2h10a2 2 0 0 1 2 2"/><path d="M10 16h6"/><path d="M11 11a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M4 8h3"/><path d="M4 12h3"/><path d="M4 16h3"/>',
    "git": '<path d="M5 18a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M5 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M15 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M7 8l0 8"/><path d="M9 18h6a2 2 0 0 0 2 -2v-5"/><path d="M14 14l3 -3l3 3"/>',
    "pipeline": '<path d="M5 18a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M5 6a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M15 12a2 2 0 1 0 4 0a2 2 0 1 0 -4 0"/><path d="M7 8l0 8"/><path d="M7 8a4 4 0 0 0 4 4h4"/>',
    "laptop": '<path d="M3 19l18 0"/><path d="M5 7a1 1 0 0 1 1 -1h12a1 1 0 0 1 1 1v8a1 1 0 0 1 -1 1h-12a1 1 0 0 1 -1 -1l0 -8"/>',
    "terminal": '<path d="M5 7l5 5l-5 5"/><path d="M12 19l7 0"/>',
    "shieldcheck": '<path d="M12 3a12 12 0 0 0 8.5 3a12 12 0 0 1 -8.5 15a12 12 0 0 1 -8.5 -15a12 12 0 0 0 8.5 -3"/><path d="M9 12l2 2l4 -4"/>',
    "test": '<path d="M20 8.04l-12.122 12.124a2.857 2.857 0 1 1 -4.041 -4.04l12.122 -12.124"/><path d="M7 13h8"/><path d="M19 15l1.5 1.6a2 2 0 1 1 -3 0l1.5 -1.6"/><path d="M15 3l6 6"/>',
    "building": '<path d="M3 21l18 0"/><path d="M5 21v-14l8 -4v18"/><path d="M19 21v-10l-6 -4"/><path d="M9 9l0 .01"/><path d="M9 12l0 .01"/><path d="M9 15l0 .01"/><path d="M9 18l0 .01"/>',
    "users": '<path d="M9 7m-4 0a4 4 0 1 0 8 0a4 4 0 1 0 -8 0"/><path d="M3 21v-2a4 4 0 0 1 4 -4h4a4 4 0 0 1 4 4v2"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/><path d="M21 21v-2a4 4 0 0 0 -3 -3.85"/>',
    "globe": '<path d="M3 12a9 9 0 1 0 18 0a9 9 0 0 0 -18 0"/><path d="M3.6 9h16.8"/><path d="M3.6 15h16.8"/><path d="M11.5 3a17 17 0 0 0 0 18"/><path d="M12.5 3a17 17 0 0 1 0 18"/>',
    "thumb": '<path d="M7 13v-8a1 1 0 0 0 -1 -1h-2a1 1 0 0 0 -1 1v7a1 1 0 0 0 1 1h3a4 4 0 0 1 4 4v1a2 2 0 0 0 4 0v-5h3a2 2 0 0 0 2 -2l-1 -5a2 3 0 0 0 -2 -2h-7a3 3 0 0 0 -3 3"/>',
    "lock": '<path d="M5 13a2 2 0 0 1 2 -2h10a2 2 0 0 1 2 2v6a2 2 0 0 1 -2 2h-10a2 2 0 0 1 -2 -2v-6"/><path d="M11 16a1 1 0 1 0 2 0a1 1 0 0 0 -2 0"/><path d="M8 11v-4a4 4 0 1 1 8 0v4"/>',
    "duck": '<path d="M4 6a8 3 0 1 0 16 0a8 3 0 1 0 -16 0"/><path d="M4 6v6a8 3 0 0 0 16 0v-6"/><path d="M4 12v6a8 3 0 0 0 16 0v-6"/>',
    "brain": '<path d="M15.5 13a3.5 3.5 0 0 0 -3.5 3.5v1a3.5 3.5 0 0 0 7 0v-1.8"/><path d="M8.5 13a3.5 3.5 0 0 1 3.5 3.5v1a3.5 3.5 0 0 1 -7 0v-1.8"/><path d="M17.5 16a3.5 3.5 0 0 0 0 -7h-.5"/><path d="M19 9.3v-2.8a3.5 3.5 0 0 0 -7 0"/><path d="M6.5 16a3.5 3.5 0 0 1 0 -7h.5"/><path d="M5 9.3v-2.8a3.5 3.5 0 0 1 7 0v10"/>',
}


def place_icon(name, x, y, s):
    if name in GLYPHS:
        return (f'<g transform="translate({x},{y}) scale({s / 24:.4f})" fill="none" stroke="{NAVY}" stroke-width="1.6" '
                f'stroke-linecap="round" stroke-linejoin="round">{GLYPHS[name]}</g>')
    vb, body = icon_svg(name)
    return f'<svg x="{x}" y="{y}" width="{s}" height="{s}" viewBox="{vb}" overflow="visible">{body}</svg>'


# ---------------------------------------------------------------------------------------------------------------
# SVG emitter
def _lines(s):
    return s.split("\n") if s else []


def _t(x, y, s, size, fam, fill, weight=400, anchor="start", ls=None, italic=False):
    extra = f' letter-spacing="{ls}"' if ls else ""
    st = ' font-style="italic"' if italic else ""
    return (f'<text x="{x}" y="{y}" font-family="{fam}" font-size="{size}" font-weight="{weight}" fill="{fill}" '
            f'text-anchor="{anchor}"{extra}{st}>{escape(s)}</text>')


_FONTFILES = {"sans": "Barlow-SemiBold.ttf", "sans4": "Barlow-Regular.ttf", "cond": "BarlowCondensed-SemiBold.ttf",
              "caps": "BarlowCondensed-SemiBold.ttf", "mono": "IBMPlexMono-Medium.ttf"}
_fontcache = {}


def _tw(s, size, fam="sans"):
    """Text width: measured with the real font when it is installed, else estimated."""
    try:
        from PIL import ImageFont
        key = (fam, size)
        if key not in _fontcache:
            _fontcache[key] = ImageFont.truetype(str(Path.home() / ".fonts" / _FONTFILES[fam]), size)
        txt = s.upper() if fam == "caps" else s
        w = _fontcache[key].getlength(txt)
        return w * (1.04 if fam == "caps" else 1.0)
    except Exception:
        k = {"sans": 0.55, "sans4": 0.5, "cond": 0.44, "mono": 0.6, "caps": 0.5}[fam]
        return len(s) * size * k


def rounded_path(pts, r=10):
    d = f"M{pts[0][0]},{pts[0][1]}"
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        l1 = math.hypot(x1 - x0, y1 - y0); l2 = math.hypot(x2 - x1, y2 - y1)
        rr = min(r, l1 / 2, l2 / 2)
        ax, ay = x1 - (x1 - x0) / l1 * rr, y1 - (y1 - y0) / l1 * rr
        bx, by = x1 + (x2 - x1) / l2 * rr, y1 + (y2 - y1) / l2 * rr
        d += f" L{ax:.1f},{ay:.1f} Q{x1},{y1} {bx:.1f},{by:.1f}"
    d += f" L{pts[-1][0]},{pts[-1][1]}"
    return d


def svg_zone(z):
    x, y, w, h = z["x"], z["y"], z["w"], z["h"]
    k = z["kind"]
    sty = {"bucket": (f'fill="none" stroke="{BLUE}" stroke-width="1.6"', BLUE),
           "phi": (f'fill="none" stroke="{NAVY}" stroke-width="1.8" stroke-dasharray="9 5"', NAVY),
           "aws": (f'fill="none" stroke="{INK}" stroke-width="1.4"', INK),
           "vpc": (f'fill="none" stroke="#8C4FFF" stroke-width="1.6"', "#6B3BCC"),
           "subnet": (f'fill="{TINT}" fill-opacity="0.45" stroke="{BLUE}" stroke-width="1.2" stroke-dasharray="5 4"', LINK),
           "worker": (f'fill="{TINT}" fill-opacity="0.35" stroke="#ED7100" stroke-width="1.6"', "#B35400"),
           "plain": (f'fill="none" stroke="{LINE}" stroke-width="1.2"', SOFT),
           "lane": (f'fill="{TINT}" fill-opacity="0.30" stroke="none"', SOFT)}[k]
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{0 if k in ("aws","vpc","lane") else 6}" {sty[0]}/>']
    lx = x + 14
    if z["icon"]:
        out.append(place_icon(z["icon"], x + 12, y + 10, 26)); lx = x + 46
    lab = z["label"]
    out.append(f'<rect x="{lx - 4}" y="{y + 12}" width="{_tw(lab, 15, "caps") + 12:.0f}" height="22" fill="{PAPER}" opacity="{0 if k in ("subnet","worker","lane") else 1}"/>')
    out.append(_t(lx, y + 29, lab.upper(), 15, COND, sty[1], 700, ls="0.06em"))
    return "".join(out)


def _cyl(x, y, w, h, fill, stroke, sw=1.4):
    ry = min(12, h * 0.12)
    return (f'<path d="M{x},{y + ry} A{w / 2},{ry} 0 0 1 {x + w},{y + ry} V{y + h - ry} A{w / 2},{ry} 0 0 1 {x},{y + h - ry} Z" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
            f'<path d="M{x},{y + ry} A{w / 2},{ry} 0 0 0 {x + w},{y + ry}" fill="none" stroke="{stroke}" stroke-width="{sw}"/>'), ry


NAME, SUB, ALAB, GLAB, GR = 18, 14, 15, 16, 36   # node name, sublabel, arrow label, gate label, gate half-diagonal


def _is_mono(s):
    return s.rstrip().endswith("/") or s.startswith("_") or s.endswith(".py") or s.endswith(".*") or s.startswith("customer_")


def node_layout(n):
    """Where the icon and text lines go, and how wide the content is (for the overflow check)."""
    x, y, w, h, shape = n["x"], n["y"], n["w"], n["h"], n["shape"]
    names, subs = _lines(n["name"]), _lines(n["sub"])
    top_pad = {"db": 12, "folder": 8, "ui": 18}.get(shape, 0)
    ic = n["icon"] if shape not in ("decision", "steps", "grid") else None
    isz = 40 if h >= 76 else 32
    left = x + (30 if shape == "agent" else 16)
    widths = [_tw(s, NAME - 1, "mono") if _is_mono(s) else _tw(s, NAME, "sans") for s in names] + [_tw(s, SUB, "sans4") for s in subs]
    content = max(widths) if widths else 0
    if ic:
        tx, anchor, need = left + isz + 12, "start", (left - x) + isz + 12 + content + 14
    else:
        tx, anchor, need = x + w / 2, "middle", content + 28
    if shape == "decision":
        need = content * 1.9
    if shape == "agent":
        need += 30
    lh = len(names) * 22 + len(subs) * 18
    y0 = y + top_pad + (h - top_pad - lh) / 2 + 16
    return dict(names=names, subs=subs, ic=ic, isz=isz, left=left, tx=tx, anchor=anchor, need=need, y0=y0, top_pad=top_pad)


def svg_node(n):
    x, y, w, h, shape, style = n["x"], n["y"], n["w"], n["h"], n["shape"], n["style"]
    fill, stroke, txt, dash, sw = TINT, BLUE, INK, "", 1.4
    if style == "white":
        fill = PAPER
    elif style == "external":
        fill, stroke, dash = PAPER, "#7A7F88", ' stroke-dasharray="5 4"'
    elif style == "optional":
        fill, stroke, dash = PAPER, BLUE, ' stroke-dasharray="6 4"'
    elif style == "focal":
        fill, stroke, txt = NAVY, NAVY, PAPER
    elif style == "denied":
        fill, stroke, dash = "#FDF1F0", DENY, ' stroke-dasharray="6 4"'
    out = [f'<g data-node="{escape(n["id"])}">']
    if shape in ("box", "steps", "ui", "grid"):
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="4" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
    elif shape == "db":
        c, ry = _cyl(x, y, w, h, fill, stroke); out.append(c)
    elif shape == "folder":
        out.append(f'<path d="M{x},{y + 12} V{y + 4} Q{x},{y} {x + 4},{y} H{x + 46} L{x + 56},{y + 10} H{x + w - 4} Q{x + w},{y + 10} {x + w},{y + 14} '
                   f'V{y + h - 4} Q{x + w},{y + h} {x + w - 4},{y + h} H{x + 4} Q{x},{y + h} {x},{y + h - 4} Z" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>')
    elif shape in ("file", "token"):
        f = 18
        out.append(f'<path d="M{x},{y} H{x + w - f} L{x + w},{y + f} V{y + h} H{x} Z" fill="{fill if shape == "file" else PAPER}" stroke="{stroke}" stroke-width="{sw}"{dash}/>'
                   f'<path d="M{x + w - f},{y} V{y + f} H{x + w}" fill="none" stroke="{stroke}" stroke-width="{sw}"/>')
    elif shape == "agent":
        k = 24
        out.append(f'<path d="M{x + k},{y} H{x + w - k} L{x + w},{y + h / 2} L{x + w - k},{y + h} H{x + k} L{x},{y + h / 2} Z" fill="{NAVY}" stroke="{NAVY}"/>')
        txt = PAPER
    elif shape == "decision":
        cx, cy = x + w / 2, y + h / 2
        out.append(f'<path d="M{cx},{y} L{x + w},{cy} L{cx},{y + h} L{x},{cy} Z" fill="{PAPER}" stroke="{NAVY}" stroke-width="1.8"/>')
    if shape == "ui":
        out.append(f'<rect x="{x}" y="{y}" width="{w}" height="18" rx="4" fill="{NAVY}"/>'
                   f'<circle cx="{x + 12}" cy="{y + 9}" r="3" fill="{PAPER}"/><circle cx="{x + 22}" cy="{y + 9}" r="3" fill="{PAPER}"/>'
                   f'<rect x="{x + 16}" y="{y + h - 36}" width="{w - 32}" height="22" rx="11" fill="{PAPER}" stroke="{LINE}"/>')
    L = node_layout(n)
    if shape == "grid":
        out.append(_t(x + 18, y + 34, L["names"][0], NAME, SANS, txt, 600))
        items, cols = n["inner"], n.get("cols", 2)
        cw = (w - 36) / cols
        for i, (ic2, lab) in enumerate(items):
            cx0, cy0 = x + 18 + (i % cols) * cw, y + 58 + (i // cols) * 46
            out.append(place_icon(ic2, cx0, cy0, 32) + _t(cx0 + 42, cy0 + 22, lab, 15, SANS, INK, 500))
    elif shape == "steps":
        hx = x + 18
        if n["icon"]:
            out.append(place_icon(n["icon"], x + 16, y + 14, 34)); hx = x + 62
        out.append(_t(hx, y + 37, L["names"][0], NAME, SANS, txt, 600))
        if n.get("badge"):
            g, lab = n["badge"]
            bw = _tw(lab, SUB, "sans") + 46
            out.append(f'<rect x="{x + w - bw - 14}" y="{y + 14}" width="{bw:.0f}" height="32" rx="16" fill="{PAPER}" stroke="{NAVY}" stroke-width="1.2"/>'
                       + place_icon(g, x + w - bw - 2, y + 18, 24) + _t(x + w - bw + 28, y + 35, lab, SUB, SANS, NAVY, 600))
        steps = n["inner"]
        ly = y + h - 50
        x0, x1 = x + 50, x + w - 50
        out.append(f'<line x1="{x0}" y1="{ly}" x2="{x1}" y2="{ly}" stroke="{BLUE}" stroke-width="1.6"/>')
        for i, st in enumerate(steps):
            sx = x0 + (x1 - x0) * i / (len(steps) - 1)
            last = i == len(steps) - 1
            out.append(f'<circle cx="{sx}" cy="{ly}" r="8" fill="{NAVY if last else PAPER}" stroke="{NAVY}" stroke-width="1.8"/>')
            out.append(_t(sx, ly + 30, st, 15, SANS, INK, 600, "middle"))
    else:
        if L["ic"]:
            iy = y + L["top_pad"] + (h - L["top_pad"] - L["isz"]) / 2
            out.append(place_icon(L["ic"], L["left"], iy, L["isz"]))
        yy = L["y0"]
        ncol = NAVY if shape == "decision" else txt
        for s in L["names"]:
            m = _is_mono(s)
            out.append(_t(L["tx"], yy, s, NAME - 1 if m else NAME, MONO if m else SANS, ncol, 500 if m else 600, L["anchor"])); yy += 22
        for s in L["subs"]:
            out.append(_t(L["tx"], yy, s, SUB, SANS, PAPER if txt == PAPER else SOFT, 400, L["anchor"])); yy += 18
    out.append("</g>")
    return "".join(out)


def label_boxes(e):
    """Mask rects (x, y, w, h) of an edge label, one per line."""
    if not e["label"]:
        return []
    x, y = e["at"]
    out = []
    for i, s in enumerate(e["label"].split("\n")):
        w = _tw(s, ALAB, "caps") + 12
        rx = x - w / 2 if e["lpos"] in ("above", "below") else (x - 6 if e["lpos"] == "right" else x - w + 6)
        out.append((rx, y + i * 19 - ALAB - 1, w, ALAB + 7))
    return out


def svg_edge(e, uid):
    pts = e["pts"]
    color = {"solid": BLUE, "dashed": BLUE, "deny": DENY, "link": LINK, "navy": NAVY}[e["style"]]
    dash = ' stroke-dasharray="7 5"' if e["style"] in ("dashed", "deny") else ""
    me = f' marker-end="url(#{uid}-arr)"' if e["end"] and e["style"] != "deny" else ""
    ms = f' marker-start="url(#{uid}-arrs)"' if e["start"] else ""
    out = [f'<path d="{rounded_path(pts)}" fill="none" stroke="{color}" stroke-width="{2.2 if e["style"] == "deny" else 1.8}"{dash}{me}{ms}/>']
    if e["style"] == "deny":
        (xa, ya), (xb, yb) = pts[-2], pts[-1]
        if xa == xb:
            out.append(f'<line x1="{xb - 16}" y1="{yb}" x2="{xb + 16}" y2="{yb}" stroke="{DENY}" stroke-width="5"/>')
        else:
            out.append(f'<line x1="{xb}" y1="{yb - 16}" x2="{xb}" y2="{yb + 16}" stroke="{DENY}" stroke-width="5"/>')
        mx, my = e.get("xat") or ((xa + xb) / 2, (ya + yb) / 2)
        out.append(f'<circle cx="{mx}" cy="{my}" r="12" fill="{PAPER}" stroke="{DENY}" stroke-width="2.2"/>'
                   f'<path d="M{mx - 5.5},{my - 5.5} L{mx + 5.5},{my + 5.5} M{mx + 5.5},{my - 5.5} L{mx - 5.5},{my + 5.5}" stroke="{DENY}" stroke-width="2.6"/>')
    return "".join(out)


def svg_edge_label(e):
    color = {"solid": LINK, "dashed": LINK, "deny": DENY, "link": LINK, "navy": NAVY}[e["style"]]
    out = []
    for (bx, by, bw, bh), s in zip(label_boxes(e), (e["label"] or "").split("\n")):
        out.append(f'<rect x="{bx:.0f}" y="{by:.0f}" width="{bw:.0f}" height="{bh}" fill="{PAPER}"/>'
                   + _t(bx + 6, by + ALAB + 1, s.upper(), ALAB, COND, color, 600, "start", ls="0.04em"))
    return "".join(out)


def gate_box(g):
    lines = g["label"].split("\n")
    wmax = max(_tw(s, GLAB, "sans") for s in lines) + (32 if g["icon"] else 0)
    hgt = len(lines) * 20
    x, y, side = g["x"], g["y"], g["side"]
    if side == "above":
        lx, ly = x - wmax / 2, y - GR - 14 - hgt
    elif side == "below":
        lx, ly = x - wmax / 2, y + GR + 14
    elif side == "right":
        lx, ly = x + GR + 14, y - hgt / 2
    else:
        lx, ly = x - GR - 14 - wmax, y - hgt / 2
    return lx - 8, ly - 5, wmax + 16, hgt + 10


def svg_gate(g):
    x, y, n = g["x"], g["y"], g["n"]
    r = GR
    out = [f'<path d="M{x},{y - r} L{x + r},{y} L{x},{y + r} L{x - r},{y} Z" fill="{NAVY}" stroke="{PAPER}" stroke-width="3"/>',
           _t(x, y + 10, str(n), 30, COND, PAPER, 700, "middle")]
    bx, by, bw, bh = gate_box(g)
    out.append(f'<rect x="{bx:.0f}" y="{by:.0f}" width="{bw:.0f}" height="{bh}" rx="3" fill="{PAPER}" stroke="{NAVY}" stroke-width="1.2"/>')
    tx = bx + 8
    lines = g["label"].split("\n")
    if g["icon"]:
        out.append(place_icon(g["icon"], tx, by + (bh - 26) / 2, 26)); tx += 32
    for i, s in enumerate(lines):
        out.append(_t(tx, by + 5 + 16 + i * 20, s, GLAB, SANS, NAVY, 600))
    return "".join(out)


# ---- geometry check: run on every build, prints problems ---------------------------------------------------------
def _ov(a, b, pad=0):
    return a[0] < b[0] + b[2] + pad and b[0] < a[0] + a[2] + pad and a[1] < b[1] + b[3] + pad and b[1] < a[1] + a[3] + pad


def _seg_hits_rect(p, q, r, shrink=3):
    x0, y0, w, h = r[0] + shrink, r[1] + shrink, r[2] - 2 * shrink, r[3] - 2 * shrink
    (ax, ay), (bx, by) = p, q
    if ax == bx:
        return x0 < ax < x0 + w and max(min(ay, by), y0) < min(max(ay, by), y0 + h)
    if ay == by:
        return y0 < ay < y0 + h and max(min(ax, bx), x0) < min(max(ax, bx), x0 + w)
    return False


def _cross(s1, s2):
    (a, b), (c, d) = s1, s2
    if a[0] == b[0] and c[1] == d[1]:
        return min(c[0], d[0]) < a[0] < max(c[0], d[0]) and min(a[1], b[1]) < c[1] < max(a[1], b[1])
    if a[1] == b[1] and c[0] == d[0]:
        return _cross(s2, s1)
    return False


def check(d):
    probs = []
    boxes = {n["id"]: (n["x"], n["y"], n["w"], n["h"]) for n in d.nodes}
    for n in d.nodes:
        L = node_layout(n)
        if L["need"] > n["w"] + 1 and n["shape"] not in ("steps", "grid"):
            probs.append(f"text overflow in {n['id']}: needs {L['need']:.0f}, has {n['w']}")
    for g in d.gates:
        gb = gate_box(g); dia = (g["x"] - GR, g["y"] - GR, 2 * GR, 2 * GR)
        for k, b in boxes.items():
            if _ov(gb, b, 6):
                probs.append(f"gate {g['n']} label touches {k}")
            if _ov(dia, b, 4):
                probs.append(f"gate {g['n']} diamond touches {k}")
        for z in d.zones:
            for side in [(z["x"], z["y"], z["w"], 0), (z["x"], z["y"] + z["h"], z["w"], 0), (z["x"], z["y"], 0, z["h"]), (z["x"] + z["w"], z["y"], 0, z["h"])]:
                if _ov(gb, side, 2):
                    probs.append(f"gate {g['n']} label crosses zone '{z['label'][:20]}' border")
    for i, e in enumerate(d.edges):
        for lb in label_boxes(e):
            for k, b in boxes.items():
                if _ov(lb, b, 4):
                    probs.append(f"edge {i} label '{e['label'][:16]}' touches {k}")
            for g in d.gates:
                if _ov(lb, gate_box(g), 4) or _ov(lb, (g["x"] - GR, g["y"] - GR, 2 * GR, 2 * GR), 4):
                    probs.append(f"edge {i} label '{e['label'][:16]}' touches gate {g['n']}")
        segs = list(zip(e["pts"], e["pts"][1:]))
        for p, q in segs:
            for k, b in boxes.items():
                if _seg_hits_rect(p, q, b):
                    probs.append(f"edge {i} runs through {k}")
            for g in d.gates:
                if _seg_hits_rect(p, q, gate_box(g), 1):
                    probs.append(f"edge {i} runs through gate {g['n']} label")
        for j, f in enumerate(d.edges[i + 1:], i + 1):
            for s1 in segs:
                for s2 in zip(f["pts"], f["pts"][1:]):
                    if _cross(s1, s2):
                        probs.append(f"edges {i} and {j} cross")
            for lb in label_boxes(f):
                for p, q in segs:
                    if _seg_hits_rect(p, q, lb, 0):
                        probs.append(f"edge {i} runs through label of edge {j}")
            for lb in label_boxes(e):
                for p, q in zip(f["pts"], f["pts"][1:]):
                    if _seg_hits_rect(p, q, lb, 0):
                        probs.append(f"edge {j} runs through label of edge {i}")
    for g1 in d.gates:
        for g2 in d.gates:
            if g1 is not g2 and _ov(gate_box(g1), gate_box(g2), 4):
                probs.append(f"gate labels {g1['n']} and {g2['n']} overlap")
    return sorted(set(probs))


def svg_text(t):
    role = t["role"]
    if role == "title":
        return _t(t["x"], t["y"], t["s"].upper(), 28, COND, NAVY, 700, t["anchor"], ls="0.05em")
    if role == "subtitle":
        return _t(t["x"], t["y"], t["s"], 16, SANS, SOFT, 400, t["anchor"])
    if role == "eyebrow":
        return _t(t["x"], t["y"], t["s"].upper(), 14, COND, t["color"] or SOFT, 700, t["anchor"], ls="0.08em")
    if role == "mono":
        return _t(t["x"], t["y"], t["s"], 14, MONO, t["color"] or INK, 400, t["anchor"])
    return _t(t["x"], t["y"], t["s"], 14, SANS, t["color"] or SOFT, 500, t["anchor"])


LEGEND = [("gate", "AWS permission gate (1–6)"), ("deny", "Denied path"), ("flow", "Data / request"),
          ("dashed", "Conditional / return"), ("phi", "Customer B boundary")]


def svg_legend(d, items):
    y = d.h - 34
    out = [f'<line x1="40" y1="{y - 26}" x2="{d.w - 40}" y2="{y - 26}" stroke="{LINE}" stroke-width="1"/>',
           _t(40, y + 5, "LEGEND", 13, COND, SOFT, 700, ls="0.12em")]
    x = 120
    for k, lab in items:
        if k == "gate":
            out.append(f'<path d="M{x + 12},{y - 12} L{x + 24},{y} L{x + 12},{y + 12} L{x},{y} Z" fill="{NAVY}"/>'); off = 34
        elif k == "deny":
            out.append(f'<line x1="{x}" y1="{y}" x2="{x + 34}" y2="{y}" stroke="{DENY}" stroke-width="2" stroke-dasharray="7 5"/>'
                       f'<line x1="{x + 36}" y1="{y - 9}" x2="{x + 36}" y2="{y + 9}" stroke="{DENY}" stroke-width="4"/>'); off = 48
        elif k == "flow":
            out.append(f'<line x1="{x}" y1="{y}" x2="{x + 34}" y2="{y}" stroke="{BLUE}" stroke-width="1.6" marker-end="url(#{d.slug}-arr)"/>'); off = 46
        elif k == "dashed":
            out.append(f'<line x1="{x}" y1="{y}" x2="{x + 34}" y2="{y}" stroke="{BLUE}" stroke-width="1.6" stroke-dasharray="7 5" marker-end="url(#{d.slug}-arr)"/>'); off = 46
        elif k == "phi":
            out.append(f'<rect x="{x}" y="{y - 9}" width="34" height="18" fill="none" stroke="{NAVY}" stroke-width="1.6" stroke-dasharray="7 4"/>'); off = 46
        elif k == "agent":
            out.append(f'<path d="M{x + 7},{y - 10} H{x + 27} L{x + 34},{y} L{x + 27},{y + 10} H{x + 7} L{x},{y} Z" fill="{NAVY}"/>'); off = 46
        elif k == "decision":
            out.append(f'<path d="M{x + 12},{y - 12} L{x + 24},{y} L{x + 12},{y + 12} L{x},{y} Z" fill="{PAPER}" stroke="{NAVY}" stroke-width="1.6"/>'); off = 34
        elif k == "service":
            out.append(f'<rect x="{x}" y="{y - 9}" width="34" height="18" fill="{TINT}" stroke="{BLUE}" stroke-width="1.2"/>'); off = 46
        elif k == "store":
            c, _ = _cyl(x + 4, y - 12, 24, 24, TINT, BLUE, 1.2); out.append(c); off = 38
        elif k == "external":
            out.append(f'<rect x="{x}" y="{y - 9}" width="34" height="18" fill="{PAPER}" stroke="#7A7F88" stroke-width="1.2" stroke-dasharray="5 4"/>'); off = 46
        out.append(_t(x + off, y + 5, lab, 14, SANS, INK, 500))
        x += off + _tw(lab, 14, "sans") + 40
    return "".join(out)


def to_svg(d, legend=None):
    uid = d.slug
    defs = (f'<marker id="{uid}-arr" markerWidth="11" markerHeight="9" refX="10" refY="4.5" orient="auto" markerUnits="userSpaceOnUse">'
            f'<path d="M0,0 L11,4.5 L0,9 Z" fill="{BLUE}"/></marker>'
            f'<marker id="{uid}-arrs" markerWidth="10" markerHeight="8" refX="1" refY="4" orient="auto" markerUnits="userSpaceOnUse">'
            f'<path d="M10,0 L0,4 L10,8 Z" fill="{BLUE}"/></marker>')
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {d.w} {d.h}" width="{d.w}" height="{d.h}" role="img" '
             f'aria-labelledby="{uid}-title {uid}-desc">',
             f'<title id="{uid}-title">{escape(d.title)}</title><desc id="{uid}-desc">{escape(d.desc)}</desc>',
             f'<defs>{defs}</defs>', f'<rect width="100%" height="100%" fill="{PAPER}"/>']
    parts += [svg_zone(z) for z in d.zones]
    parts += [svg_edge(e, uid) for e in d.edges]
    parts += [svg_node(n) for n in d.nodes]
    parts += [svg_edge_label(e) for e in d.edges]
    parts += [svg_gate(g) for g in d.gates]
    parts += [svg_text(t) for t in d.texts]
    parts.append(svg_legend(d, legend or LEGEND))
    parts.append("</svg>")
    return "\n".join(parts)


def to_html(d, legend=None):
    """diagram-design page wrapper (source of truth for export_svg.py / export_png.py)."""
    svg = to_svg(d, legend)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{escape(d.title)}</title><link href="{FONTS}" rel="stylesheet">'
            f'<style>body{{margin:0;background:{PAPER};color:{INK};font-family:{SANS}}}'
            f'.diagram-container{{width:100%;overflow-x:auto}} svg{{width:100%;min-width:{d.w}px;display:block;height:auto}}'
            f'@media print{{.diagram-container{{overflow-x:visible}} svg{{min-width:0}}}}</style></head>'
            f'<body><main><div class="diagram-container">{svg}</div></main></body></html>')


# ---------------------------------------------------------------------------------------------------------------
# Excalidraw emitter (prototype in the user's analyzer style)
class _X:
    def __init__(self):
        self.els, self.files, self.seed = [], {}, 1000

    def _base(self, typ, x, y, w, h, **kw):
        self.seed += 7
        e = dict(id=f"{typ}_{self.seed}", type=typ, x=x, y=y, width=w, height=h, angle=0, strokeColor="#1e1e1e",
                 backgroundColor="transparent", fillStyle="solid", strokeWidth=2, strokeStyle="solid", roughness=1,
                 opacity=100, groupIds=[], frameId=None, roundness=None, seed=self.seed, version=1,
                 versionNonce=self.seed * 3, isDeleted=False, boundElements=None, updated=1, link=None, locked=False)
        e.update(kw)
        self.els.append(e)
        return e

    def rect(self, x, y, w, h, **kw):
        return self._base("rectangle", x, y, w, h, **kw)

    def ellipse(self, x, y, w, h, **kw):
        return self._base("ellipse", x, y, w, h, **kw)

    def diamond(self, x, y, w, h, **kw):
        return self._base("diamond", x, y, w, h, **kw)

    def line(self, pts, typ="line", **kw):
        x0, y0 = pts[0]
        rel = [[px - x0, py - y0] for px, py in pts]
        xs, ys = [p[0] for p in rel], [p[1] for p in rel]
        e = self._base(typ, x0, y0, max(xs) - min(xs), max(ys) - min(ys), points=rel, lastCommittedPoint=None,
                       startBinding=None, endBinding=None, **kw)
        return e

    def text(self, x, y, s, size=20, align="left", color="#1e1e1e", w=None):
        lines = s.split("\n")
        tw = w or max(len(l) for l in lines) * size * 0.56
        if align == "center":
            x = x - tw / 2
        elif align == "right":
            x = x - tw
        return self._base("text", x, y, tw, len(lines) * size * 1.25, text=s, originalText=s, fontSize=size, fontFamily=5,
                          textAlign=align, verticalAlign="top", containerId=None, lineHeight=1.25, strokeColor=color,
                          autoResize=True, baseline=size)

    def image(self, x, y, s, name):
        fid = f"icon-{name}"
        self.files[fid] = dict(mimeType="image/svg+xml", id=fid, dataURL=icon_dataurl(name), created=1)
        return self._base("image", x, y, s, s, fileId=fid, status="saved", scale=[1, 1], strokeColor="transparent")


def to_excalidraw(d):
    X = _X()
    for z in d.zones:
        dashed = z["kind"] in ("phi", "subnet", "worker", "plain", "lane")
        col = {"phi": "#69db7c", "worker": "#1e1e1e", "bucket": "#1e1e1e"}.get(z["kind"], "#1e1e1e")
        X.rect(z["x"], z["y"], z["w"], z["h"], strokeStyle="dashed" if dashed else "solid", strokeColor=col,
               strokeWidth=4 if z["kind"] in ("phi", "bucket", "aws") else 2, roughness=0 if z["kind"] == "lane" else 1,
               roundness={"type": 3})
        X.text(z["x"] + 14, z["y"] + 10, z["label"], 22)
    for e in d.edges:
        col = {"deny": "#e03131", "navy": "#1e1e1e"}.get(e["style"], "#1e1e1e")
        a = X.line(e["pts"], "arrow", strokeColor=col, strokeStyle="dashed" if e["style"] in ("dashed", "deny") else "solid",
                   startArrowhead="arrow" if e["start"] else None, endArrowhead=("bar" if e["style"] == "deny" else "arrow") if e["end"] else None,
                   elbowed=False, roundness=None, roughness=0)
        if e["style"] == "deny":
            (xa, ya), (xb, yb) = e["pts"][-2], e["pts"][-1]
            mx, my = e.get("xat") or ((xa + xb) / 2, (ya + yb) / 2)
            X.text(mx, my - 14, "X", 24, "center", "#e03131")
        if e["label"]:
            x, y = e["at"]
            al = {"above": "center", "left": "right", "right": "left", "below": "center"}[e["lpos"]]
            X.text(x, y - 18, e["label"], 18, al, "#e03131" if e["style"] == "deny" else "#1e1e1e")
    for n in d.nodes:
        x, y, w, h = n["x"], n["y"], n["w"], n["h"]
        sh = n["shape"]
        dash = "dashed" if n["style"] in ("external", "optional", "denied") else "solid"
        if sh == "decision":
            X.diamond(x, y, w, h, strokeColor="#1e1e1e")
        elif sh == "agent":
            X.rect(x, y, w, h, roundness={"type": 3}, strokeWidth=4)
        elif sh == "db":
            X.rect(x, y + 10, w, h - 10, roundness=None, strokeStyle=dash)
            X.ellipse(x, y, w, 20, backgroundColor="#ffffff")
        else:
            X.rect(x, y, w, h, roundness={"type": 3}, strokeStyle=dash,
                   strokeColor="#e03131" if n["style"] == "denied" else "#1e1e1e")
        tx, al = x + w / 2, "center"
        if n["icon"] and n["icon"] not in GLYPHS and sh not in ("decision", "steps"):
            X.image(x + 8, y + (14 if sh == "db" else 8), 26, n["icon"])
        label = n["name"] + (("\n" + n["sub"]) if n["sub"] else "")
        if sh == "grid":
            X.text(x + 16, y + 12, n["name"], 20)
            cols = n.get("cols", 2); cw = (w - 36) / cols
            for i, (ic2, lab) in enumerate(n["inner"]):
                cx0, cy0 = x + 18 + (i % cols) * cw, y + 58 + (i // cols) * 46
                if ic2 not in GLYPHS:
                    X.image(cx0, cy0, 28, ic2)
                X.text(cx0 + 38, cy0 + 4, lab, 16)
        elif sh == "steps":
            X.text(x + 16, y + 12, n["name"], 20)
            steps = n["inner"]; ly = y + h - 44; x0, x1 = x + 40, x + w - 40
            X.line([(x0, ly), (x1, ly)], roughness=0)
            for i, s in enumerate(steps):
                sx = x0 + (x1 - x0) * i / (len(steps) - 1)
                X.ellipse(sx - 7, ly - 7, 14, 14, backgroundColor="#1e1e1e" if i == len(steps) - 1 else "#ffffff")
                X.text(sx, ly + 12, s, 18, "center")
        else:
            nl = label.count("\n") + 1
            X.text(tx, y + h / 2 - nl * 10 + (6 if n["icon"] else 0), label, 16, al)
    for g in d.gates:
        X.diamond(g["x"] - 30, g["y"] - 30, 60, 60, backgroundColor="#ffc9c9", strokeColor="#1e1e1e")
        X.text(g["x"], g["y"] - 14, str(g["n"]), 24, "center")
        lines = g["label"]
        nl = lines.count("\n") + 1
        wmax = max(len(s) for s in lines.split("\n")) * 18 * 0.56
        side = g["side"]
        if side == "above":
            X.text(g["x"], g["y"] - 40 - nl * 22.5, lines, 18, "center")
        elif side == "below":
            X.text(g["x"], g["y"] + 40, lines, 18, "center")
        elif side == "right":
            X.text(g["x"] + 40, g["y"] - nl * 11, lines, 18, "left")
        else:
            X.text(g["x"] - 40, g["y"] - nl * 11, lines, 18, "right", w=wmax)
    for t in d.texts:
        size = {"title": 32, "subtitle": 20, "eyebrow": 20}.get(t["role"], 18)
        al = {"start": "left", "middle": "center", "end": "right"}[t["anchor"]]
        X.text(t["x"], t["y"] - size, t["s"], size, al, "#e03131" if t["color"] == DENY else "#1e1e1e")
    return json.dumps(dict(type="excalidraw", version=2, source="https://excalidraw.com", elements=X.els,
                           appState=dict(viewBackgroundColor="#ffffff", gridSize=20), files=X.files), indent=1)
