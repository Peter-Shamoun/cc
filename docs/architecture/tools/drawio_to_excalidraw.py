"""Convert the main board (cell "ov") of ClaimClarity_Architecture.drawio into an editable Excalidraw scene.

Writes docs/architecture/ClaimClarity_Architecture.excalidraw, the main-board source for the diagram editor.
Boxes become rectangles/ellipses/diamonds, AWS resource icons become image elements (tools/icons), labels become
text elements grouped with their box, and edges become arrows bound to their source and target.
"""
import base64
import html
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).parent
ARCH = HERE.parent
DRAWIO = ARCH / "ClaimClarity_Architecture.drawio"
OUT = ARCH / "ClaimClarity_Architecture.excalidraw"

ICON_FILES = {   # drawio resIcon / shape -> tools/icons file
    "mxgraph.aws4.key_management_service": "kms", "mxgraph.aws4.s3": "s3", "mxgraph.aws4.eventbridge": "eventbridge",
    "mxgraph.aws4.fargate": "fargate", "mxgraph.aws4.cognito": "cognito", "mxgraph.aws4.sts": "sts",
    "mxgraph.aws4.role": "role", "mxgraph.aws4.users": "user", "mxgraph.aws4.shield2": "shield",
    "mxgraph.aws4.identity_and_access_management": "iam",
}
SANS, MONO = 2, 3   # Excalidraw font families: Helvetica, Cascadia

root = ET.parse(DRAWIO).getroot().find("./diagram/mxGraphModel/root")
cells = {c.get("id"): c for c in root}


def style_of(cell):
    pairs = [p.split("=", 1) for p in (cell.get("style") or "").split(";") if p]
    return {p[0]: (p[1] if len(p) > 1 else True) for p in pairs}


def on_main_board(cid):
    while cid:
        if cid == "ov":
            return True
        cid = cells[cid].get("parent") if cid in cells else None
    return False


def absolute_box(cid):
    """Geometry relative to the main board's top-left corner."""
    g = cells[cid].find("mxGeometry")
    x, y = float(g.get("x", 0)), float(g.get("y", 0))
    parent = cells[cid].get("parent")
    if parent != "ov":
        px, py, _, _ = absolute_box(parent)
        x, y = x + px, y + py
    return x, y, float(g.get("width", 0)), float(g.get("height", 0))


seed_counter = [1000]


def base_element(eid, kind, x, y, w, h, **props):
    seed_counter[0] += 1
    element = {
        "id": eid, "type": kind, "x": round(x, 1), "y": round(y, 1), "width": round(w, 1), "height": round(h, 1),
        "angle": 0, "strokeColor": "#1A1D23", "backgroundColor": "transparent", "fillStyle": "solid",
        "strokeWidth": 1, "strokeStyle": "solid", "roughness": 0, "opacity": 100, "groupIds": [], "frameId": None,
        "roundness": None, "seed": seed_counter[0], "version": 1, "versionNonce": seed_counter[0] * 7,
        "isDeleted": False, "boundElements": None, "updated": 1, "link": None, "locked": False,
    }
    element.update(props)
    return element


def colour(value, default="transparent"):
    return default if value in (None, "none") else value


def label_lines(value, style):
    """Split a drawio HTML label into lines of (text, fontSize, colour, fontFamily)."""
    default_size = float(style.get("fontSize", 12))
    default_colour = style.get("fontColor", "#1A1D23")
    default_family = MONO if "Mono" in style.get("fontFamily", "") else SANS
    lines = []
    for chunk in re.split(r"<br\s*/?>|</div>", value):
        size, col, family, upper = default_size, default_colour, default_family, False
        first_span = re.search(r'style="([^"]*)"', chunk)
        if first_span:
            css = first_span.group(1)
            if m := re.search(r"font-size:\s*([\d.]+)px", css):
                size = float(m.group(1))
            if m := re.search(r"(?<!-)color:\s*(#[0-9A-Fa-f]{3,6})", css):
                col = m.group(1)
            upper = "uppercase" in css
        # monospace only when the whole line is monospace; a mono name followed by a plain note reads as plain text
        outside_mono = re.sub(r'<span style="[^"]*Mono[^"]*">.*?</span>', "", chunk)
        if "Mono" in chunk and not re.sub(r"<[^>]+>|&nbsp;|\s", "", outside_mono):
            family = MONO
        elif "Mono" in chunk:
            family = SANS
        text = html.unescape(re.sub(r"<[^>]+>", "", chunk)).replace("\xa0", " ").strip()
        lines.append((text.upper() if upper else text, size, col, family) if text else None)
    while lines and lines[-1] is None:
        lines.pop()
    return lines


def text_width(text, size, family):
    return len(text) * size * (0.6 if family == MONO else 0.52)


elements, files = [], {}
groups = {}   # cell id -> group id (box + its label + icons inside it)


def add_label(cid, x, y, w, h, style, group):
    lines = label_lines(cells[cid].get("value") or "", style)
    if not lines:
        return
    align = style.get("align", "center")
    vertical = style.get("verticalAlign", "middle")
    left = float(style.get("spacingLeft", 0)) + float(style.get("spacing", 2) if "spacing" in style else 2)
    right = float(style.get("spacingRight", 0)) + 2
    top = float(style.get("spacingTop", 0)) + 2
    available = max(w - left - right, 20) * (0.62 if style.get("shape") == "hexagon" else 1)
    wrapped = []
    for line in lines:
        if line is None:
            wrapped.append(None)
            continue
        text, size, col, family = line
        rows, row = [], ""
        for word in text.split(" "):
            if row and text_width(f"{row} {word}", size, family) > available:
                rows.append(row)
                row = word
            else:
                row = f"{row} {word}" if row else word
        rows.append(row)
        wrapped.append(("\n".join(rows), size, col, family, len(rows)))
    heights = [6 if line is None else line[1] * 1.25 * line[4] for line in wrapped]
    cursor = y + top if vertical == "top" else y + (h - sum(heights)) / 2
    for n, line in enumerate(wrapped):
        if line is None:
            cursor += heights[n]
            continue
        text, size, col, family, _ = line
        width = max(text_width(row, size, family) for row in text.split("\n"))
        tx = {"left": x + left, "right": x + w - right - width}.get(align, x + (w - width) / 2)
        elements.append(base_element(
            f"{cid}__t{n}", "text", tx, cursor, width, heights[n], strokeColor=col, groupIds=group,
            text=text, originalText=text, fontSize=size, fontFamily=family, textAlign=align if align in ("left", "right") else "center",
            verticalAlign="top", containerId=None, autoResize=True, lineHeight=1.25))
        cursor += heights[n]


def hexagon_points(w, h, size=0.22):
    inset = w * size
    return [[inset, 0], [w - inset, 0], [w, h / 2], [w - inset, h], [inset, h], [0, h / 2], [inset, 0]]


vertex_ids = [cid for cid, c in cells.items() if c.get("vertex") and cid != "ov" and on_main_board(cid)]

for cid in vertex_ids:
    style = style_of(cells[cid])
    x, y, w, h = absolute_box(cid)
    shape = style.get("shape", "")
    res_icon = style.get("resIcon", shape)
    stroke = colour(style.get("strokeColor"), "#1A1D23" if "strokeColor" not in style else "transparent")
    fill = colour(style.get("fillColor"), "#FFFFFF" if "fillColor" not in style else "transparent")
    dashed = "dashed" if style.get("dashed") == "1" else "solid"
    stroke_width = float(style.get("strokeWidth", 1))
    if cid.endswith("_i") and cid[:-2] in cells:
        group = groups.setdefault(cid[:-2], [f"g_{cid[:-2]}"])
    else:
        group = groups.setdefault(cid, [f"g_{cid}"])

    if res_icon in ICON_FILES:
        file_id = f"icon-{ICON_FILES[res_icon]}"
        svg = (HERE / "icons" / f"{ICON_FILES[res_icon]}.svg").read_bytes()
        files[file_id] = {"id": file_id, "mimeType": "image/svg+xml", "created": 1,
                          "dataURL": "data:image/svg+xml;base64," + base64.b64encode(svg).decode()}
        elements.append(base_element(cid, "image", x, y, w, h, strokeColor="transparent", groupIds=group,
                                     fileId=file_id, status="saved", scale=[1, 1], crop=None))
        continue
    if shape in ("mxgraph.aws4.folder", "mxgraph.weblogos.github"):   # no icon file: a small filled tile
        elements.append(base_element(cid, "rectangle", x, y + h * 0.15, w, h * 0.7, strokeColor="transparent",
                                     backgroundColor=fill, groupIds=group, roundness={"type": 3}))
        continue

    kind = "rectangle"
    if "ellipse" in style:
        kind = "ellipse"
    elif "rhombus" in style:
        kind = "diamond"
    is_text_only = "text" in style and fill == "transparent" and stroke == "transparent"
    if shape == "hexagon":
        elements.append(base_element(cid, "line", x, y, w, h, strokeColor=stroke, backgroundColor=fill,
                                     groupIds=group, points=hexagon_points(w, h, float(style.get("size", 0.22))),
                                     lastCommittedPoint=None, startBinding=None, endBinding=None,
                                     startArrowhead=None, endArrowhead=None, polygon=True))
    elif not is_text_only:
        elements.append(base_element(cid, kind, x, y, w, h, strokeColor=stroke, backgroundColor=fill,
                                     strokeStyle=dashed, strokeWidth=stroke_width, groupIds=group))
    add_label(cid, x, y, w, h, style, group)


def anchor(cid, fx, fy, toward):
    x, y, w, h = absolute_box(cid)
    if fx is not None:
        return x + w * fx, y + h * fy
    tx, ty = toward
    cx, cy = x + w / 2, y + h / 2   # no fixed anchor: leave from the side facing the other end
    if abs(tx - cx) * h > abs(ty - cy) * w:
        return (x + w if tx > cx else x), cy
    return cx, (y + h if ty > cy else y)


def centre(cid):
    x, y, w, h = absolute_box(cid)
    return x + w / 2, y + h / 2


bound = {}
for cid, cell in cells.items():
    if not (cell.get("edge") and on_main_board(cid)):
        continue
    style = style_of(cell)
    source, target = cell.get("source"), cell.get("target")
    get = lambda k: float(style[k]) if k in style else None   # noqa: E731
    start = anchor(source, get("exitX"), get("exitY"), centre(target))
    end = anchor(target, get("entryX"), get("entryY"), centre(source))
    waypoints = [(float(p.get("x")), float(p.get("y"))) for p in cell.findall("./mxGeometry/Array/mxPoint")]
    route = [start, *waypoints, end]
    if not waypoints and start[0] != end[0] and start[1] != end[1]:   # orthogonal elbow
        horizontal_exit = get("exitX") in (0.0, 1.0) or (get("exitX") is None and abs(end[0] - start[0]) > abs(end[1] - start[1]))
        if horizontal_exit and get("entryY") in (0.0, 1.0):
            route = [start, (end[0], start[1]), end]
        elif horizontal_exit:
            mid = (start[0] + end[0]) / 2
            route = [start, (mid, start[1]), (mid, end[1]), end]
        else:
            mid = (start[1] + end[1]) / 2
            route = [start, (start[0], mid), (end[0], mid), end]
    xs, ys = [p[0] for p in route], [p[1] for p in route]
    label = html.unescape(re.sub(r"<[^>]+>", " ", cell.get("value") or "")).strip()
    arrow = base_element(
        cid, "arrow", route[0][0], route[0][1], max(xs) - min(xs), max(ys) - min(ys),
        strokeColor=style.get("strokeColor", "#4A6BB0"), strokeWidth=float(style.get("strokeWidth", 1)),
        strokeStyle="dashed" if style.get("dashed") == "1" else "solid",
        points=[[round(px - route[0][0], 1), round(py - route[0][1], 1)] for px, py in route],
        lastCommittedPoint=None, startArrowhead=None, endArrowhead="triangle", elbowed=False,
        startBinding={"elementId": source, "focus": 0, "gap": 1},
        endBinding={"elementId": target, "focus": 0, "gap": 1},
        boundElements=[{"type": "text", "id": f"{cid}__label"}] if label else None)
    elements.append(arrow)
    for end_id in (source, target):
        bound.setdefault(end_id, []).append({"type": "arrow", "id": cid})
    if label:
        size = float(style.get("fontSize", 13))
        elements.append(base_element(
            f"{cid}__label", "text", (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, text_width(label, size, SANS),
            size * 1.25, strokeColor=style.get("fontColor", "#3A5A9E"), text=label, originalText=label,
            fontSize=size, fontFamily=SANS, textAlign="center", verticalAlign="middle", containerId=cid,
            autoResize=True, lineHeight=1.25))

for element in elements:
    if element["id"] in bound:
        element["boundElements"] = (element["boundElements"] or []) + bound[element["id"]]
# arrows can only bind to shapes that exist in the scene
ids = {e["id"] for e in elements}
for element in elements:
    if element["type"] == "arrow":
        for side in ("startBinding", "endBinding"):
            if element[side]["elementId"] not in ids:
                element[side] = None

# white board background behind everything
_, _, board_w, board_h = (0, 0, *[float(cells["ov"].find("mxGeometry").get(k)) for k in ("width", "height")])
elements.insert(0, base_element("ov", "rectangle", 0, 0, board_w, board_h, strokeColor="#C9CBCE",
                                backgroundColor="#FFFFFF", locked=True))

scene = {"type": "excalidraw", "version": 2, "source": "ClaimClarity drawio main board",
         "elements": elements, "appState": {"viewBackgroundColor": "#FFFFFF", "gridSize": None}, "files": files}
OUT.write_text(json.dumps(scene, indent=1))
print("wrote", OUT, len(elements), "elements,", len(files), "icons")
