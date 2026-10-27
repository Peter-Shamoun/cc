"""Build a .drawio file from extract.json (the browser dump of every board's SVG)."""
import base64, json, re, sys, urllib.parse, zlib
from xml.sax.saxutils import escape, quoteattr

extract_path, output_path = sys.argv[1], sys.argv[2]
data = json.load(open(extract_path))

GOOGLE_FONTS = {
    "Barlow": "https://fonts.googleapis.com/css2?family=Barlow:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&display=swap",
    "Barlow Condensed": "https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@500;600;700&display=swap",
    "IBM Plex Mono": "https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&display=swap",
}

cells = []
next_id = [2]


def new_id(prefix):
    next_id[0] += 1
    return f"{prefix}-{next_id[0]}"


def num(v, default=0.0):
    if v in (None, "", "none"):
        return default
    return float(re.sub(r"[a-z%]+$", "", str(v)))


def fmt(v):
    v = round(float(v), 2)
    return str(int(v)) if v == int(v) else str(v)


def color(v):
    if v in (None, "", "none", "transparent") or v.startswith("rgba(0, 0, 0, 0)"):
        return "none"
    m = re.match(r"rgba?\(([\d.]+), ([\d.]+), ([\d.]+)", v)
    if m:
        return "#%02X%02X%02X" % tuple(round(float(x)) for x in m.groups())
    return v.upper()


def first_font(stack):
    return stack.split(",")[0].strip().strip("\"'")


def dash_pattern(dasharray, stroke_width):
    # draw.io scales dashPattern by strokeWidth, SVG does not
    parts = [float(x) for x in re.split(r"[ ,]+", dasharray.replace("px", "").strip()) if x]
    return " ".join(fmt(p / stroke_width) for p in parts)


def stroke_style(s):
    out = {}
    stroke = color(s["stroke"])
    out["strokeColor"] = stroke
    if stroke != "none":
        sw = num(s["stroke-width"], 1)
        out["strokeWidth"] = fmt(sw)
        if s["stroke-dasharray"] not in ("none", ""):
            out["dashed"] = "1"
            out["dashPattern"] = dash_pattern(s["stroke-dasharray"], sw)
    return out


def opacity_style(s):
    out = {}
    if num(s["opacity"], 1) < 1:
        out["opacity"] = fmt(num(s["opacity"]) * 100)
    if num(s["fill-opacity"], 1) < 1:
        out["fillOpacity"] = fmt(num(s["fill-opacity"]) * 100)
    if num(s["stroke-opacity"], 1) < 1:
        out["strokeOpacity"] = fmt(num(s["stroke-opacity"]) * 100)
    return out


def style_string(d):
    return "".join(f"{k}={v};" if v is not None else f"{k};" for k, v in d.items())


def add_vertex(parent, x, y, w, h, style, value=""):
    cid = new_id("v")
    cells.append(
        f'<mxCell id="{cid}" value={quoteattr(value)} style={quoteattr(style_string(style))} vertex="1" parent="{parent}">'
        f'<mxGeometry x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" as="geometry"/></mxCell>'
    )
    return cid


def add_edge(parent, points, style):
    cid = new_id("e")
    (sx, sy), (tx, ty), mids = points[0], points[-1], points[1:-1]
    mid_xml = ""
    if mids:
        mid_xml = '<Array as="points">' + "".join(f'<mxPoint x="{fmt(x)}" y="{fmt(y)}"/>' for x, y in mids) + "</Array>"
    cells.append(
        f'<mxCell id="{cid}" value="" style={quoteattr(style_string(style))} edge="1" parent="{parent}">'
        f'<mxGeometry relative="1" as="geometry"><mxPoint x="{fmt(sx)}" y="{fmt(sy)}" as="sourcePoint"/>'
        f'<mxPoint x="{fmt(tx)}" y="{fmt(ty)}" as="targetPoint"/>{mid_xml}</mxGeometry></mxCell>'
    )
    return cid


# ---------- SVG path parsing (absolute segments) ----------
def parse_path(d):
    tokens = re.findall(r"[MmLlHhVvQqCcAaZz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    segs, i, cmd = [], 0, None
    cx = cy = sx = sy = 0.0

    def take(n):
        nonlocal i
        vals = [float(t) for t in tokens[i:i + n]]
        i += n
        return vals

    while i < len(tokens):
        if re.match(r"[A-Za-z]", tokens[i]):
            cmd = tokens[i]
            i += 1
        rel = cmd.islower()
        c = cmd.upper()
        ox, oy = (cx, cy) if rel else (0, 0)
        if c == "Z":
            segs.append(("Z",))
            cx, cy = sx, sy
            continue
        if c == "M":
            x, y = take(2); cx, cy = x + ox, y + oy; sx, sy = cx, cy
            segs.append(("M", cx, cy)); cmd = "l" if rel else "L"
        elif c == "L":
            x, y = take(2); cx, cy = x + ox, y + oy; segs.append(("L", cx, cy))
        elif c == "H":
            (x,) = take(1); cx = x + (cx if rel else 0); segs.append(("L", cx, cy))
        elif c == "V":
            (y,) = take(1); cy = y + (cy if rel else 0); segs.append(("L", cx, cy))
        elif c == "Q":
            x1, y1, x, y = take(4)
            segs.append(("Q", x1 + ox, y1 + oy, x + ox, y + oy)); cx, cy = x + ox, y + oy
        elif c == "C":
            x1, y1, x2, y2, x, y = take(6)
            segs.append(("C", x1 + ox, y1 + oy, x2 + ox, y2 + oy, x + ox, y + oy)); cx, cy = x + ox, y + oy
        elif c == "A":
            rx, ry, rot, large, sweep, x, y = take(7)
            segs.append(("A", rx, ry, rot, int(large), int(sweep), x + ox, y + oy)); cx, cy = x + ox, y + oy
    return segs


def seg_points(segs):
    pts = []
    for s in segs:
        if s[0] in ("M", "L"):
            pts.append((s[1], s[2]))
        elif s[0] == "Q":
            pts += [(s[1], s[2]), (s[3], s[4])]
        elif s[0] == "C":
            pts += [(s[1], s[2]), (s[3], s[4]), (s[5], s[6])]
        elif s[0] == "A":
            pts.append((s[6], s[7]))
    return pts


def polyline_with_corners(segs):
    """Straight connector with Q-rounded corners -> vertex list (Q control point = corner)."""
    pts = []
    for s in segs:
        if s[0] in ("M", "L"):
            pts.append((s[1], s[2]))
        elif s[0] == "Q":
            pts.pop()
            pts.append((s[1], s[2]))
    cleaned = []
    for p in pts:
        if cleaned and abs(cleaned[-1][0] - p[0]) < 0.01 and abs(cleaned[-1][1] - p[1]) < 0.01:
            continue
        cleaned.append(p)
    # drop collinear middle points
    out = [cleaned[0]]
    for k in range(1, len(cleaned) - 1):
        a, b, c = out[-1], cleaned[k], cleaned[k + 1]
        if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) > 0.01:
            out.append(b)
    out.append(cleaned[-1])
    return out


def stencil(segs, bx, by, w, h):
    def p(x, y):
        return f'x="{fmt(x - bx)}" y="{fmt(y - by)}"'
    body = []
    for s in segs:
        if s[0] == "M":
            body.append(f"<move {p(s[1], s[2])}/>")
        elif s[0] == "L":
            body.append(f"<line {p(s[1], s[2])}/>")
        elif s[0] == "Q":
            body.append(f'<quad x1="{fmt(s[1] - bx)}" y1="{fmt(s[2] - by)}" x2="{fmt(s[3] - bx)}" y2="{fmt(s[4] - by)}"/>')
        elif s[0] == "C":
            body.append(f'<curve x1="{fmt(s[1] - bx)}" y1="{fmt(s[2] - by)}" x2="{fmt(s[3] - bx)}" y2="{fmt(s[4] - by)}" x3="{fmt(s[5] - bx)}" y3="{fmt(s[6] - by)}"/>')
        elif s[0] == "A":
            body.append(f'<arc rx="{fmt(s[1])}" ry="{fmt(s[2])}" x-axis-rotation="{fmt(s[3])}" large-arc-flag="{s[4]}" sweep-flag="{s[5]}" {p(s[6], s[7])}/>')
        elif s[0] == "Z":
            body.append("<close/>")
    xml = (f'<shape w="{fmt(w)}" h="{fmt(h)}" aspect="variable" strokewidth="inherit"><connections/><background>'
           f'<path>{"".join(body)}</path></background><foreground><fillstroke/></foreground></shape>')
    raw = zlib.compressobj(9, zlib.DEFLATED, -15)
    packed = raw.compress(urllib.parse.quote(xml, safe="~()*!.'").encode()) + raw.flush()
    return base64.b64encode(packed).decode()


MARKERS = {  # marker id suffix -> (arrow, size)
    "arr": ("block", 10), "arrb": ("block", 10), "arrn": ("block", 12), "arrs": ("block", 9),
}


def marker_style(url, end):
    key = "endArrow" if end else "startArrow"
    if not url or url == "none":
        return {key: "none"}
    mid = re.search(r"#([^\"')]+)", url).group(1)
    arrow, size = MARKERS[mid.rsplit("-", 1)[-1]]
    pre = "end" if end else "start"
    return {key: arrow, f"{pre}Fill": "1", f"{pre}Size": str(size)}


# ---------- converters ----------
def convert_rect(parent, it):
    a, s = it["a"], it["s"]
    if a.get("width", "").endswith("%"):
        return  # board background: the board container already paints it
    x, y = num(a.get("x")) + it["m"][4], num(a.get("y")) + it["m"][5]
    w, h = num(a["width"]), num(a["height"])
    st = {"rounded": "0", "whiteSpace": "wrap", "html": "1", "fillColor": color(s["fill"])}
    rx = num(a.get("rx"), 0)
    if rx:
        st.update(rounded="1", absoluteArcSize="1", arcSize=fmt(rx * 2))
    st.update(stroke_style(s))
    st.update(opacity_style(s))
    add_vertex(parent, x, y, w, h, st)


def convert_circle(parent, it):
    a, s = it["a"], it["s"]
    cx, cy, r = num(a["cx"]) + it["m"][4], num(a["cy"]) + it["m"][5], num(a["r"])
    st = {"ellipse": None, "html": "1", "fillColor": color(s["fill"])}
    st.update(stroke_style(s)); st.update(opacity_style(s))
    add_vertex(parent, cx - r, cy - r, 2 * r, 2 * r, st)


def convert_line(parent, it):
    a, s = it["a"], it["s"]
    dx, dy = it["m"][4], it["m"][5]
    st = {"html": "1", "rounded": "0", "endArrow": "none", "startArrow": "none"}
    st.update(stroke_style(s)); st.update(opacity_style(s))
    if s["stroke-linecap"] == "round":
        st["lineCap"] = "round"
    st.update(marker_style(s["marker-end"], True)); st.update(marker_style(s["marker-start"], False))
    add_edge(parent, [(num(a["x1"]) + dx, num(a["y1"]) + dy), (num(a["x2"]) + dx, num(a["y2"]) + dy)], st)


CYLINDER = re.compile(r"^M([\d.]+),([\d.]+) A([\d.]+),([\d.]+) 0 0 1 ([\d.]+),\2 V([\d.]+) A\3,\4 0 0 1 \1,\6 Z$")
CYLINDER_LID = re.compile(r"^M([\d.]+),([\d.]+) A([\d.]+),([\d.]+) 0 0 0 ([\d.]+),\2$")


def convert_path(parent, it, skip_next_lid):
    a, s = it["a"], it["s"]
    d = a["d"].strip()
    dx, dy = it["m"][4], it["m"][5]
    fill = color(s["fill"])
    if skip_next_lid and CYLINDER_LID.match(d):
        return False
    m = CYLINDER.match(d)
    if m:
        x1, y1, rx, ry, x2, y2 = map(float, m.groups())
        st = {"shape": "cylinder3", "boundedLbl": "1", "backgroundOutline": "1", "size": fmt(ry), "html": "1",
              "whiteSpace": "wrap", "fillColor": fill}
        st.update(stroke_style(s)); st.update(opacity_style(s))
        add_vertex(parent, x1 + dx, y1 - ry + dy, x2 - x1, y2 - y1 + 2 * ry, st)
        return True
    segs = parse_path(d)
    segs = [(t[0],) + tuple(v + (dx if k % 2 == 0 else dy) if t[0] != "A" else v for k, v in enumerate(t[1:])) for t in segs]
    kinds = {t[0] for t in segs}
    closed = "Z" in kinds
    # connectors: open, unfilled, straight segments with optional rounded corners
    if not closed and kinds <= {"M", "L", "Q"} and sum(t[0] == "M" for t in segs) == 1 and (fill == "none" or len(segs) == 2):
        pts = polyline_with_corners(segs)
        st = {"html": "1", "rounded": "1" if "Q" in kinds else "0", "endArrow": "none", "startArrow": "none",
              "edgeStyle": "none", "jumpStyle": "none"}
        st.update(stroke_style(s)); st.update(opacity_style(s))
        st.update(marker_style(s["marker-end"], True)); st.update(marker_style(s["marker-start"], False))
        add_edge(parent, pts, st)
        return False
    pts = seg_points(segs)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    bx, by = min(xs), min(ys)
    w, h = max(xs) - bx, max(ys) - by
    st = {"html": "1", "fillColor": fill}
    st.update(stroke_style(s)); st.update(opacity_style(s))
    corners = [(t[1], t[2]) for t in segs if t[0] in ("M", "L")]
    if kinds == {"M", "L", "Z"} and len(corners) == 4 and \
            {(round(p[0] - bx, 1), round(p[1] - by, 1)) for p in corners} == {(w / 2, 0), (w, h / 2), (w / 2, h), (0, h / 2)}:
        st = {"rhombus": None, **st}
    elif kinds == {"M", "L", "Z"} and len(corners) == 6 and \
            sorted({round(p[1] - by, 1) for p in corners}) == [0, round(h / 2, 1), round(h, 1)]:
        inset = min(p[0] for p in corners if abs(p[1] - by) < 0.1) - bx
        st = {"shape": "hexagon", "perimeter": "hexagonPerimeter2", "fixedSize": "1", "size": fmt(inset), **st}
    else:
        if w == 0 or h == 0:  # degenerate: keep a tiny box so the stencil scales correctly
            w, h = max(w, 0.01), max(h, 0.01)
        st = {"shape": "stencil(" + stencil(segs, bx, by, w, h) + ")", **st}
    add_vertex(parent, bx, by, w, h, st)
    return False


def convert_text(parent, it):
    s = it["s"]
    bx, by, bw, bh = it["bb"]
    anchor = s["text-anchor"]
    pad = 6
    if anchor == "middle":
        x, w, align = bx - pad / 2, bw + pad, "center"
    elif anchor == "end":
        x, w, align = bx - pad, bw + pad, "right"
    else:
        x, w, align = bx, bw + pad, "left"
    html = ""
    for span in it["spans"]:
        ss = span["s"]
        css = (f"font-family:{ss['font-family'].replace(chr(34), chr(39))};font-size:{ss['font-size']};"
               f"font-weight:{ss['font-weight']};font-style:{ss['font-style']};color:{color(ss['fill'])};"
               f"letter-spacing:{ss['letter-spacing'] if ss['letter-spacing'] != 'normal' else '0px'};white-space:pre;")
        html += f'<span style="{css}">{escape(span["text"])}</span>'
    family = first_font(s["font-family"])
    st = {"text": None, "html": "1", "whiteSpace": "nowrap", "overflow": "visible", "align": align,
          "verticalAlign": "middle", "spacing": "0", "spacingTop": "0", "spacingBottom": "0", "spacingLeft": "0",
          "spacingRight": "0", "fontFamily": family, "fontSize": fmt(num(s["font-size"])),
          "fontColor": color(s["fill"]), "strokeColor": "none", "fillColor": "none"}
    if family in GOOGLE_FONTS:
        st["fontSource"] = urllib.parse.quote(GOOGLE_FONTS[family], safe="")
    weight = num(s["font-weight"], 400)
    st["fontStyle"] = str((1 if weight >= 600 else 0) + (2 if s["font-style"] == "italic" else 0))
    st.update(opacity_style(s))
    add_vertex(parent, x, by, w, bh, st, html)


def convert_icon(parent, it):
    x, y, w, h = it["x"], it["y"], it["w"], it["h"]
    inherited = it["style"]
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(w)}" height="{fmt(h)}" viewBox="{fmt(x)} {fmt(y)} {fmt(w)} {fmt(h)}">'
           f'<g fill="{color(inherited["fill"])}" stroke="{color(inherited["stroke"])}">{it["svg"]}</g></svg>')
    encoded = base64.b64encode(svg.encode()).decode()
    st = {"shape": "image", "verticalLabelPosition": "bottom", "verticalAlign": "top", "aspect": "fixed",
          "imageAspect": "0", "image": "data:image/svg+xml," + encoded}
    add_vertex(parent, x, y, w, h, st)


canvas = data["canvas"]
BX, BY = canvas["bounds"][0], canvas["bounds"][1]

# dashed connectors from the main board's zoom badges to each drill-down (drawn under the boards)
ONLY_BOARD = sys.argv[3] if len(sys.argv) > 3 else None
for link in ([] if ONLY_BOARD else canvas["links"]):
    (x1, y1), (x2, y2) = link["start"], link["end"]
    p1, p2 = (x1 - BX, y1 - BY), (x2 - BX, y2 - BY)
    add_edge("1", [p1, p2], {"html": "1", "endArrow": "none", "startArrow": "none", "strokeColor": "#4A6BB0",
                             "strokeWidth": "7", "dashed": "1", "dashPattern": f"{fmt(26 / 7)} {fmt(16 / 7)}", "opacity": "75"})
    add_vertex("1", p2[0] - 34, p2[1] - 34, 68, 68, {"ellipse": None, "html": "1", "fillColor": "#4A6BB0",
                                                     "strokeColor": "#FFFFFF", "strokeWidth": "5"})
    add_vertex("1", p2[0] - 34, p2[1] - 34, 68, 68,
               {"text": None, "html": "1", "align": "center", "verticalAlign": "middle", "spacing": "0",
                "fontFamily": "Barlow Condensed", "fontSource": urllib.parse.quote(GOOGLE_FONTS["Barlow Condensed"], safe=""),
                "fontSize": "34", "fontStyle": "1", "fontColor": "#FFFFFF", "strokeColor": "none", "fillColor": "none"},
               link["number"])

for board in data["boards"]:
    if ONLY_BOARD:
        if board["slug"] != ONLY_BOARD:
            continue
        board = dict(board, left=0, top=0)
    board_id = add_vertex("1", board["left"], board["top"], board["w"], board["h"], {
        "rounded": "1", "absoluteArcSize": "1", "arcSize": "12", "html": "1", "fillColor": "#FFFFFF",
        "strokeColor": "none", "container": "1", "collapsible": "0", "pointerEvents": "0", "recursiveResize": "0",
        "shadow": "1", "shadowColor": "#1C2B57", "shadowOpacity": "16", "shadowBlur": "24", "shadowOffsetX": "0",
        "shadowOffsetY": "18", **({"shadow": "0"} if ONLY_BOARD else {})})
    skip_lid = False
    for it in board["items"]:
        t = it["t"]
        if t == "rect":
            convert_rect(board_id, it)
        elif t == "circle":
            convert_circle(board_id, it)
        elif t == "line":
            convert_line(board_id, it)
        elif t == "text":
            convert_text(board_id, it)
        elif t == "icon":
            convert_icon(board_id, it)
        elif t == "path":
            skip_lid = convert_path(board_id, it, skip_lid)
            continue
        skip_lid = False

W, H = canvas["bounds"][2], canvas["bounds"][3]
xml = (f'<mxfile host="Electron" type="device"><diagram id="claimclarity-canvas" name="ClaimClarity architecture">'
       f'<mxGraphModel dx="0" dy="0" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" '
       f'page="0" pageScale="1" pageWidth="{W}" pageHeight="{H}" background="#DCE2EC" math="0" shadow="0">'
       f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{"".join(cells)}</root></mxGraphModel></diagram></mxfile>')
open(output_path, "w").write(xml)
print(f"{len(cells)} cells -> {output_path}")
