"""Build a Lucid-safe .drawio file from extract.json.

The same layout as build.py, restricted to what Lucidchart's draw.io importer handles
(see tools/lucid/check_drawio_for_lucid.py):
  * one flat layer: no containers, every cell is parented to "1" in absolute coordinates
  * plain-text labels (html=0), one cell per styled run, in Helvetica or Courier New,
    with the font size shrunk where needed to fit the original run width in Lucid's metrics
  * rect / ellipse / rhombus / hexagon / PNG image only; cylinders are built from ellipses and rects,
    icons and the remaining custom outlines are PNGs rendered by Chromium
  * integer stroke widths and no opacity (fill opacity is blended into the colour)
  * every edge bound to a source and target cell, with exact exit/entry points

Usage: python3 build_lucid.py extract.json out.drawio [--drawio /path/to/drawio]
With --drawio, the file is re-saved through the draw.io CLI (a genuine draw.io export, which Lucid's
importer expects). Numeric character references (draw.io writes &#39; for apostrophes) are then written as plain
UTF-8 characters, because Lucid shows them literally.
"""
import json, os, re, subprocess, sys, tempfile, urllib.parse
from xml.sax.saxutils import quoteattr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "lucid"))
import build as svg_shapes  # noqa: E402  path parsing and colour helpers
from validate_lucid import font  # noqa: E402  Liberation metrics, Lucid's default font

SANS, MONO = "Helvetica", "Courier New"
TABLE_COLOR = "#DCE2EC"
PNG_SCALE = 3
MIN_FONT_SCALE = 0.85  # below this, text keeps its size and runs wider than the original instead

cells, vertex_boxes, png_jobs = [], [], []
fmt, num, color = svg_shapes.fmt, svg_shapes.num, svg_shapes.color
next_id = [0]


def new_id(prefix):
    next_id[0] += 1
    return f"{prefix}{next_id[0]}"


def blend(hex_color, alpha, under="#FFFFFF"):
    if hex_color == "none" or alpha >= 1:
        return hex_color
    a = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(under[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02X%02X%02X" % tuple(round(x * alpha + y * (1 - alpha)) for x, y in zip(a, b))


def integer_stroke(s):
    stroke = color(s["stroke"])
    out = {"strokeColor": stroke}
    if stroke == "none":
        return out
    width = num(s["stroke-width"], 1)
    rounded_width = max(1, int(width + 0.5))
    out["strokeColor"] = blend(stroke, num(s["stroke-opacity"], 1) * num(s["opacity"], 1))
    out["strokeWidth"] = str(rounded_width)
    if s["stroke-dasharray"] not in ("none", ""):
        out["dashed"] = "1"
        out["dashPattern"] = svg_shapes.dash_pattern(s["stroke-dasharray"], rounded_width)
    return out


def solid_fill(s, under="#FFFFFF"):
    return blend(color(s["fill"]), num(s["fill-opacity"], 1) * num(s["opacity"], 1), under)


def style_string(d):
    return "".join(f"{k}={v};" if v is not None else f"{k};" for k, v in d.items())


def add_vertex(x, y, w, h, style, value="", bindable=True):
    cid = new_id("v")
    cells.append(f'<mxCell id="{cid}" value={quoteattr(value)} style={quoteattr(style_string(style))} vertex="1" parent="1">'
                 f'<mxGeometry x="{fmt(x)}" y="{fmt(y)}" width="{fmt(w)}" height="{fmt(h)}" as="geometry"/></mxCell>')
    if bindable:
        vertex_boxes.append((cid, x, y, w, h))
    return cid


def add_image(x, y, w, h, svg_markup):
    key = new_id("png")
    png_jobs.append({"key": key, "svg": svg_markup, "w": w, "h": h, "scale": PNG_SCALE})
    return add_vertex(x, y, w, h, {"shape": "image", "imageAspect": "0", "aspect": "fixed", "html": "0",
                                   "image": "@@" + key + "@@"})


pending_edges = []


def add_edge(points, style):
    cells.append(None)
    pending_edges.append((len(cells) - 1, points, style))


def endpoint_terminal(point):
    """Smallest bindable vertex containing the point (3px tolerance); otherwise a transparent 2x2 anchor."""
    px, py = point
    best = None
    for cid, x, y, w, h in vertex_boxes:
        if x - 3 <= px <= x + w + 3 and y - 3 <= py <= y + h + 3 and (best is None or w * h < best[3] * best[4]):
            best = (cid, x, y, w, h)
    if best is None:
        cid = add_vertex(px - 1, py - 1, 2, 2, {"rounded": "0", "html": "0", "fillColor": "none", "strokeColor": "none"}, bindable=False)
        best = (cid, px - 1, py - 1, 2, 2)
    cid, x, y, w, h = best
    clamp = lambda v: min(1.0, max(0.0, v))  # noqa: E731
    return cid, round(clamp((px - x) / w), 4), round(clamp((py - y) / h), 4)


def flush_edges():
    for slot, points, style in pending_edges:
        (sx, sy), (tx, ty), mids = points[0], points[-1], points[1:-1]
        source, exit_x, exit_y = endpoint_terminal(points[0])
        target, entry_x, entry_y = endpoint_terminal(points[-1])
        style = {**style, "exitX": exit_x, "exitY": exit_y, "exitDx": "0", "exitDy": "0", "exitPerimeter": "0",
                 "entryX": entry_x, "entryY": entry_y, "entryDx": "0", "entryDy": "0", "entryPerimeter": "0"}
        mid_xml = ""
        if mids:
            mid_xml = '<Array as="points">' + "".join(f'<mxPoint x="{fmt(x)}" y="{fmt(y)}"/>' for x, y in mids) + "</Array>"
        cells[slot] = (f'<mxCell id="{new_id("e")}" value="" style={quoteattr(style_string(style))} edge="1" parent="1" '
                     f'source="{source}" target="{target}"><mxGeometry relative="1" as="geometry">'
                     f'<mxPoint x="{fmt(sx)}" y="{fmt(sy)}" as="sourcePoint"/><mxPoint x="{fmt(tx)}" y="{fmt(ty)}" as="targetPoint"/>'
                     f'{mid_xml}</mxGeometry></mxCell>')


def edge_style(s, rounded):
    st = {"edgeStyle": "none", "html": "0", "rounded": "1" if rounded else "0", "endArrow": "none", "startArrow": "none"}
    st.update(integer_stroke(s))
    st.update(svg_shapes.marker_style(s["marker-end"], True))
    st.update(svg_shapes.marker_style(s["marker-start"], False))
    return st


def path_png_svg(it, bx, by, w, h, pad):
    attrs = " ".join(f'{k}={quoteattr(v)}' for k, v in it["a"].items())
    s = it["s"]
    inherited = f'fill="{color(s["fill"])}" stroke="{color(s["stroke"])}" stroke-width="{num(s["stroke-width"], 1)}"'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(w + 2 * pad)}" height="{fmt(h + 2 * pad)}" '
            f'viewBox="{fmt(bx - pad)} {fmt(by - pad)} {fmt(w + 2 * pad)} {fmt(h + 2 * pad)}"><g {inherited}><path {attrs}/></g></svg>')


def convert_board_items(board):
    ox, oy = board["left"], board["top"]
    add_vertex(ox, oy, board["w"], board["h"], {"rounded": "1", "absoluteArcSize": "1", "arcSize": "12", "html": "0",
                                                "fillColor": "#FFFFFF", "strokeColor": "none"})
    layout.update(board_layout(board))
    skip_lid = False
    for it in board["items"]:
        t, s = it["t"], it.get("s")
        dx, dy = ox + it.get("m", [0] * 6)[4], oy + it.get("m", [0] * 6)[5]
        if t == "path" and skip_lid and svg_shapes.CYLINDER_LID.match(it["a"]["d"].strip()):
            skip_lid = False
            continue
        skip_lid = False
        if t == "rect":
            a = it["a"]
            if a.get("width", "").endswith("%"):
                continue
            st = {"rounded": "0", "html": "0", "fillColor": solid_fill(s)}
            rx = num(a.get("rx"), 0)
            if rx:
                st.update(rounded="1", absoluteArcSize="1", arcSize=fmt(rx * 2))
            st.update(integer_stroke(s))
            x, y, w, h = num(a.get("x")) + dx, num(a.get("y")) + dy, num(a["width"]), num(a["height"])
            add_vertex(x, y, w, h, st)
            if is_label_chip(st, h):
                layout["chips"].append({"index": len(cells) - 1, "x": x, "y": y, "w": w, "h": h})
        elif t == "circle":
            a = it["a"]
            r = num(a["r"])
            st = {"ellipse": None, "html": "0", "fillColor": solid_fill(s)}
            st.update(integer_stroke(s))
            add_vertex(num(a["cx"]) + dx - r, num(a["cy"]) + dy - r, 2 * r, 2 * r, st)
        elif t == "line":
            a = it["a"]
            add_edge([(num(a["x1"]) + dx, num(a["y1"]) + dy), (num(a["x2"]) + dx, num(a["y2"]) + dy)], edge_style(s, False))
        elif t == "icon":
            svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{fmt(it["w"])}" height="{fmt(it["h"])}" '
                   f'viewBox="{fmt(it["x"])} {fmt(it["y"])} {fmt(it["w"])} {fmt(it["h"])}">'
                   f'<g fill="{color(it["style"]["fill"])}" stroke="{color(it["style"]["stroke"])}">{it["svg"]}</g></svg>')
            add_image(it["x"] + ox, it["y"] + oy, it["w"], it["h"], svg)
        elif t == "text":
            convert_text(it, ox, oy)
        elif t == "path":
            skip_lid = convert_path(it, dx, dy)


def convert_path(it, dx, dy):
    s, d = it["s"], it["a"]["d"].strip()
    fill = solid_fill(s)
    m = svg_shapes.CYLINDER.match(d)
    if m:
        x1, y1, rx, ry, x2, y2 = map(float, m.groups())
        x1, x2, y1, y2 = x1 + dx, x2 + dx, y1 + dy, y2 + dy
        stroke = integer_stroke(s)
        sw = int(stroke.get("strokeWidth", "1"))
        add_vertex(x1, y2 - ry, x2 - x1, 2 * ry, {"ellipse": None, "html": "0", "fillColor": fill, **stroke})
        add_vertex(x1, y1, x2 - x1, y2 - y1, {"rounded": "0", "html": "0", "fillColor": fill, "strokeColor": "none"})
        for side_x in (x1, x2):
            add_vertex(side_x - sw / 2, y1, sw, y2 - y1, {"rounded": "0", "html": "0", "fillColor": stroke["strokeColor"],
                                                          "strokeColor": "none"}, bindable=False)
        add_vertex(x1, y1 - ry, x2 - x1, 2 * ry, {"ellipse": None, "html": "0", "fillColor": fill, **stroke})
        return True
    segs = svg_shapes.parse_path(d)
    segs = [(t[0],) + tuple(v + (dx if k % 2 == 0 else dy) if t[0] != "A" else v for k, v in enumerate(t[1:])) for t in segs]
    kinds = {t[0] for t in segs}
    if "Z" not in kinds and kinds <= {"M", "L", "Q"} and sum(t[0] == "M" for t in segs) == 1 and (fill == "none" or len(segs) == 2):
        add_edge(svg_shapes.polyline_with_corners(segs), edge_style(s, "Q" in kinds))
        return False
    pts = svg_shapes.seg_points(segs)
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    bx, by, w, h = min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
    corners = [(t[1], t[2]) for t in segs if t[0] in ("M", "L")]
    st = {"html": "0", "fillColor": fill, **integer_stroke(s)}
    if kinds == {"M", "L", "Z"} and len(corners) == 4 and \
            {(round(p[0] - bx, 1), round(p[1] - by, 1)) for p in corners} == {(w / 2, 0), (w, h / 2), (w / 2, h), (0, h / 2)}:
        add_vertex(bx, by, w, h, {"rhombus": None, **st})
    elif kinds == {"M", "L", "Z"} and len(corners) == 6 and \
            sorted({round(p[1] - by, 1) for p in corners}) == [0, round(h / 2, 1), round(h, 1)]:
        inset = min(p[0] for p in corners if abs(p[1] - by) < 0.1) - bx
        add_vertex(bx, by, w, h, {"shape": "hexagon", "perimeter": "hexagonPerimeter2", "fixedSize": "1", "size": fmt(inset), **st})
    else:
        pad = num(s["stroke-width"], 1) + 1
        add_image(bx - pad, by - pad, w + 2 * pad, h + 2 * pad, path_png_svg(it, bx - dx, by - dy, w, h, pad))
    return False


layout = {"chips": [], "boxes": [], "runs": []}


def is_label_chip(style, height):
    """A white, borderless strip that hides a line or frame border behind a label."""
    return style.get("fillColor") == "#FFFFFF" and style.get("strokeColor") == "none" and height <= 40


def board_layout(board):
    """Original geometry the Lucid text must keep clear of: other text runs, and every vertical line
    (shape borders and connector segments) that sits clear of the text in the original."""
    ox, oy = board["left"], board["top"]
    verticals, runs = [], []  # verticals: (x, top, bottom)
    for it in board["items"]:
        s = it.get("s")
        if it["t"] == "rect" and not it["a"].get("width", "").endswith("%") and color(s["stroke"]) != "none":
            a = it["a"]
            x, y, w, h = num(a.get("x")) + ox, num(a.get("y")) + oy, num(a["width"]), num(a["height"])
            verticals += [(x, y, y + h), (x + w, y, y + h)]
        elif it["t"] == "line":
            a = it["a"]
            if num(a["x1"]) == num(a["x2"]):
                ys = sorted([num(a["y1"]) + oy, num(a["y2"]) + oy])
                verticals.append((num(a["x1"]) + ox, *ys))
        elif it["t"] == "path" and color(s["stroke"]) != "none":
            segs = svg_shapes.parse_path(it["a"]["d"])
            pts = [(x + ox, y + oy) for x, y in svg_shapes.seg_points(segs)]
            if any(t[0] == "Z" for t in segs):
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                verticals += [(min(xs), min(ys), max(ys)), (max(xs), min(ys), max(ys))]
            else:
                verticals += [(a[0], min(a[1], b[1]), max(a[1], b[1])) for a, b in zip(pts, pts[1:]) if abs(a[0] - b[0]) < 0.01]
        elif it["t"] == "text":
            _, by, _, bh = it["bb"]
            for run in it["spans"]:
                if run["text"].strip():
                    runs.append((id(run), run["x0"] + ox, run["x0"] + run["w"] + ox, by + oy, by + bh + oy))
    return {"chips": [], "verticals": verticals, "runs": runs}


def text_limits(run_key, left, right, top, bottom):
    """How far a run's text may extend: up to its neighbours on the same line and the nearest vertical line
    on either side that the original text does not touch."""
    limit_left, limit_right = float("-inf"), float("inf")
    for key, l2, r2, t2, b2 in layout["runs"]:
        if key == run_key or min(bottom, b2) - max(top, t2) < 0.3 * min(bottom - top, b2 - t2):
            continue
        if l2 >= right - 1:
            limit_right = min(limit_right, l2 - 6)
        elif r2 <= left + 1:
            limit_left = max(limit_left, r2 + 6)
    middle = (top + bottom) / 2
    for x, y1, y2 in layout["verticals"]:
        if y1 <= middle <= y2:
            if x >= right:
                limit_right = min(limit_right, x - 4)
            elif x <= left:
                limit_left = max(limit_left, x + 4)
    return limit_left, limit_right


def widen_chip(left, right, middle_x, middle_y):
    """Keep the white chip behind a label at least as wide as the label's text."""
    for chip in layout["chips"]:
        if chip["x"] <= middle_x <= chip["x"] + chip["w"] and chip["y"] <= middle_y <= chip["y"] + chip["h"]:
            new_left, new_right = min(chip["x"], left - 4), max(chip["x"] + chip["w"], right + 4)
            if (new_left, new_right) != (chip["x"], chip["x"] + chip["w"]):
                cells[chip["index"]] = re.sub(r'<mxGeometry x="[^"]+" y="([^"]+)" width="[^"]+"',
                                              lambda m: f'<mxGeometry x="{fmt(new_left)}" y="{m.group(1)}" width="{fmt(new_right - new_left)}"',
                                              cells[chip["index"]], count=1)
                chip["x"], chip["w"] = new_left, new_right - new_left
            return


def convert_text(it, ox, oy):
    s = it["s"]
    bx, by, bw, bh = it["bb"]
    anchor = s["text-anchor"]
    runs = [r for r in it["spans"] if r["text"].strip()]
    single = len(runs) == 1
    for run in runs:
        rs = run["s"]
        text = run["text"]
        leading = len(text) - len(text.lstrip(" "))
        kind = "mono" if "mono" in rs["font-family"].lower() else "sans"
        bold = num(rs["font-weight"], 400) >= 600
        # Lucid's importer may treat <word> as a tag; the look-alike angle quotes keep CLI placeholders visible
        visible = re.sub(r"<([a-zA-Z_/][^<>]*)>", "\u2039\\1\u203a", text.strip(" "))
        # width the original run gives this text, minus the leading spaces it skips over
        run_width = run["w"] * len(visible) / max(1, len(text)) if kind == "mono" else run["w"]
        x0 = run["x0"] + (run["w"] * leading / len(text) if leading else 0) + ox
        align = "center" if single and anchor == "middle" else "right" if single and anchor == "end" else "left"

        def extent(size):
            need = font(kind, bold, size).getlength(visible)
            left = {"left": x0, "center": x0 + (run_width - need) / 2, "right": x0 + run_width - need}[align]
            return need, left, left + need

        original_size = size = round(num(rs["font-size"]))
        while size > round(original_size * MIN_FONT_SCALE) and extent(size)[0] > run_width:
            size -= 1
        limit_left, limit_right = text_limits(id(run), x0, x0 + run_width, by + oy, by + bh + oy)
        while size > 6 and (extent(size)[1] < limit_left or extent(size)[2] > limit_right):
            size -= 1
        need, text_left, text_right = extent(size)
        height = max(bh, size * 1.2)
        st = {"text": None, "html": "0", "whiteSpace": "nowrap", "spacing": "0", "spacingTop": "0", "spacingBottom": "0",
              "spacingLeft": "0", "spacingRight": "0", "verticalAlign": "middle", "align": align,
              "fontFamily": MONO if kind == "mono" else SANS, "fontSize": str(size),
              "fontColor": blend(color(rs["fill"]), num(s["opacity"], 1)), "strokeColor": "none", "fillColor": "none",
              "fontStyle": str((1 if bold else 0) + (2 if rs["font-style"] == "italic" else 0))}
        add_vertex(text_left - 3, by + oy + bh / 2 - height / 2, need + 6, height, st, visible, bindable=False)
        widen_chip(text_left, text_right, x0 + run_width / 2, by + oy + bh / 2)


def utf8_text(text):
    """Write characters as UTF-8, the way draw.io itself does: Lucid shows numeric entities such as &#183; literally."""
    named = {"&": "&amp;", "<": "&lt;", '"': "&quot;"}

    def decode(match):
        code = match.group(1)
        ch = chr(int(code[1:], 16) if code[0] in "xX" else int(code))
        return named.get(ch, ch)
    return re.sub(r"&#([xX][0-9a-fA-F]+|[0-9]+);", decode, text)


def main():
    extract_path, output_path = sys.argv[1], sys.argv[2]
    data = json.load(open(extract_path))
    canvas = data["canvas"]
    BX, BY, W, H = canvas["bounds"]
    add_vertex(0, 0, W, H, {"rounded": "0", "html": "0", "fillColor": TABLE_COLOR, "strokeColor": "none", "locked": "1"},
               bindable=False)
    link_color = blend("#4A6BB0", 0.75, TABLE_COLOR)
    for link in canvas["links"]:
        (x1, y1), (x2, y2) = link["start"], link["end"]
        p1, p2 = (x1 - BX, y1 - BY), (x2 - BX, y2 - BY)
        add_edge([p1, p2], {"edgeStyle": "none", "html": "0", "endArrow": "none", "startArrow": "none", "strokeColor": link_color,
                            "strokeWidth": "7", "dashed": "1", "dashPattern": f"{fmt(26 / 7)} {fmt(16 / 7)}"})
        add_vertex(p2[0] - 34, p2[1] - 34, 68, 68, {"ellipse": None, "html": "0", "fillColor": "#4A6BB0", "strokeColor": "#FFFFFF",
                                                    "strokeWidth": "5"})
        add_vertex(p2[0] - 34, p2[1] - 34, 68, 68, {"text": None, "html": "0", "whiteSpace": "nowrap", "align": "center",
                                                    "verticalAlign": "middle", "spacing": "0", "fontFamily": SANS, "fontSize": "30",
                                                    "fontStyle": "1", "fontColor": "#FFFFFF", "strokeColor": "none", "fillColor": "none"},
                   link["number"], bindable=False)
    # links sit under the boards, so their endpoints must bind before the boards exist
    flush_edges()
    pending_edges.clear()
    for board in data["boards"]:
        first_box = len(vertex_boxes)
        convert_board_items(board)
        board_boxes = vertex_boxes[first_box:]
        # bind each board's edges only to that board's shapes
        saved = vertex_boxes[:]
        vertex_boxes[:] = board_boxes
        flush_edges()
        pending_edges.clear()
        vertex_boxes[:] = saved

    with tempfile.TemporaryDirectory() as tmp:
        jobs_path, pngs_path = os.path.join(tmp, "jobs.json"), os.path.join(tmp, "pngs.json")
        json.dump(png_jobs, open(jobs_path, "w"))
        subprocess.run(["node", os.path.join(HERE, "png_render.js"), jobs_path, pngs_path], check=True)
        pngs = json.load(open(pngs_path))
    body = "".join(cells)
    body = re.sub(r"@@(png\d+)@@", lambda m: "data:image/png," + pngs[m.group(1)], body)
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="app.diagrams.net" agent="drawio" version="29.0.3">'
           f'<diagram id="claimclarity-canvas" name="ClaimClarity architecture">'
           f'<mxGraphModel dx="0" dy="0" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" '
           f'page="1" pageScale="1" pageWidth="{W}" pageHeight="{H}" math="0" shadow="0">'
           f'<root><mxCell id="0"/><mxCell id="1" parent="0"/>{body}</root></mxGraphModel></diagram></mxfile>\n')
    open(output_path, "w", encoding="utf-8").write(utf8_text(xml))
    if "--drawio" in sys.argv:
        drawio = sys.argv[sys.argv.index("--drawio") + 1]
        subprocess.run(["xvfb-run", "-a", drawio, "-x", "-f", "xml", "--uncompressed", "-o", output_path, output_path,
                        "--no-sandbox", "--disable-gpu"], check=True, capture_output=True, env={**os.environ, "HOME": "/tmp"})
        # draw.io writes apostrophes as &#39;, which Lucid would also show literally
        exported = utf8_text(open(output_path, encoding="utf-8").read())
        open(output_path, "w", encoding="utf-8").write(exported)
    print(f"{len(cells)} cells, {len(png_jobs)} PNGs -> {output_path}")


if __name__ == "__main__":
    main()
