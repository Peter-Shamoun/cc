"""Check the Lucid-safe file's text layout against the original canvas.

For every text cell it works out where the text actually lands in Lucid's font metrics
(Liberation Sans / Mono, as tools/lucid/validate_lucid.py does) and reports
  * text that overlaps another text cell's text
  * text that spills out of the smallest shape around it
  * text that runs into an icon
  * with extract.json: connector lines and shape borders that cross the text but did not cross it in the original

Usage: python3 check_lucid_text.py lucid.drawio [extract.json]   (exit 1 on any finding)
"""
import json, os, sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "lucid"))
from validate_lucid import font  # noqa: E402


def style_dict(style):
    return dict(part.partition("=")[::2] for part in (style or "").split(";") if part)


def text_extents(drawio_path):
    extents = []
    for cell in ET.parse(drawio_path).iter("mxCell"):
        st = style_dict(cell.get("style"))
        if "text" not in st or not cell.get("value"):
            continue
        g = cell.find("mxGeometry")
        x, y, w, h = (float(g.get(k, 0)) for k in ("x", "y", "width", "height"))
        size = float(st.get("fontSize", 12))
        kind = "mono" if st.get("fontFamily", "").lower() == "courier new" else "sans"
        length = font(kind, st.get("fontStyle") in ("1", "3"), size).getlength(cell.get("value"))
        left = {"left": x, "center": x + (w - length) / 2, "right": x + w - length}[st.get("align", "center")]
        middle = y + h / 2
        extents.append((cell.get("id"), cell.get("value"), left, middle - size * 0.55, left + length, middle + size * 0.55))
    return extents


def shape_boxes(drawio_path, stroked_only=False):
    """Every non-text shape in the Lucid file: a label must stay inside the smallest one around it."""
    boxes = []
    for cell in ET.parse(drawio_path).iter("mxCell"):
        st = style_dict(cell.get("style"))
        if cell.get("vertex") != "1" or "text" in st or st.get("locked") == "1" or st.get("shape") == "image":
            continue
        g = cell.find("mxGeometry")
        x, y, w, h = (float(g.get(k, 0)) for k in ("x", "y", "width", "height"))
        if w > 4 and h > 4 and not (stroked_only and st.get("strokeColor", "none") == "none"):
            boxes.append((x, y, w, h))
    return boxes


def icon_boxes(drawio_path):
    out = []
    for cell in ET.parse(drawio_path).iter("mxCell"):
        if style_dict(cell.get("style")).get("shape") == "image":
            g = cell.find("mxGeometry")
            out.append(tuple(float(g.get(k, 0)) for k in ("x", "y", "width", "height")))
    return out


def edge_segments(drawio_path):
    segments = []
    for cell in ET.parse(drawio_path).iter("mxCell"):
        if cell.get("edge") != "1":
            continue
        g = cell.find("mxGeometry")
        ends = {p.get("as"): (float(p.get("x")), float(p.get("y"))) for p in g.findall("mxPoint")}
        mids = [(float(p.get("x")), float(p.get("y"))) for p in g.findall("Array/mxPoint")]
        pts = [ends["sourcePoint"], *mids, ends["targetPoint"]]
        segments += [(cell.get("id"), a, b) for a, b in zip(pts, pts[1:])]
    return segments


def shape_borders(drawio_path):
    """Visible vertical borders of every non-text, non-image shape, as segments."""
    out = []
    for x, y, w, h in shape_boxes(drawio_path, stroked_only=True):
        out += [("border", (x, y), (x, y + h)), ("border", (x + w, y), (x + w, y + h))]
    return out


def crosses(segment, l, t, r, b, inset=-1):  # -1: a 2px stroke touching the glyphs counts
    (x1, y1), (x2, y2) = segment
    l, t, r, b = l + inset, t + inset, r - inset, b - inset
    if x1 == x2:
        return l < x1 < r and min(y1, y2) < b and max(y1, y2) > t
    if y1 == y2:
        return t < y1 < b and min(x1, x2) < r and max(x1, x2) > l
    return False


def original_extents(extract_path):
    out = []
    for board in json.load(open(extract_path))["boards"]:
        for it in board["items"]:
            if it["t"] == "text":
                _, by, _, bh = it["bb"]
                for run in it["spans"]:
                    if run["text"].strip():
                        out.append((run["x0"] + board["left"], by + board["top"], run["x0"] + run["w"] + board["left"],
                                    by + bh + board["top"]))
    canvas = json.load(open(extract_path))["canvas"]
    bx, by = canvas["bounds"][:2]
    for link in canvas["links"]:  # badge numbers sit on the link line in the original too
        x, y = link["end"][0] - bx, link["end"][1] - by
        out.append((x - 34, y - 34, x + 34, y + 34))
    return out


def findings(drawio_path, extract_path=None):
    extents, boxes, out = text_extents(drawio_path), shape_boxes(drawio_path), []
    before = original_extents(extract_path) if extract_path else []

    def touches(box, l, t, r, b):
        x, y, w, h = box
        return min(r, x + w) - max(l, x) > 1 and min(b, y + h) - max(t, y) > 1

    for cid, value, l, t, r, b in extents:
        same_text_before = [o for o in before if min(r, o[2]) > max(l, o[0]) and min(b, o[3]) > max(t, o[1])]
        for icon in icon_boxes(drawio_path):
            # images also stand in for outlines (folders, notes) that hold text; only new contact counts
            if touches(icon, l, t, r, b) and not any(touches(icon, *o) for o in same_text_before):
                out.append(f"{cid} '{value[:40]}' runs into an image at {icon[0]:.0f},{icon[1]:.0f}")
    if extract_path:
        for edge_id, a, b_ in edge_segments(drawio_path) + shape_borders(drawio_path):
            for cid, value, l, t, r, b in extents:
                if crosses((a, b_), l, t, r, b) and not any(crosses((a, b_), *o) for o in before
                                                             if min(r, o[2]) > max(l, o[0]) and min(b, o[3]) > max(t, o[1])):
                    out.append(f"{cid} '{value[:40]}' is crossed by edge {edge_id}")
    for i, (cid, value, l, t, r, b) in enumerate(extents):
        for cid2, value2, l2, t2, r2, b2 in extents[i + 1:]:
            if min(r, r2) - max(l, l2) > 1 and min(b, b2) - max(t, t2) > 2:
                out.append(f"{cid} '{value[:40]}' overlaps {cid2} '{value2[:40]}'")
        cx, cy = (l + r) / 2, (t + b) / 2
        holders = [bx for bx in boxes if bx[0] <= cx <= bx[0] + bx[2] and bx[1] <= cy <= bx[1] + bx[3]
                   and bx[2] * bx[3] > 0]
        if holders:
            x, y, w, h = min(holders, key=lambda bx: bx[2] * bx[3])
            if l < x - 1 or r > x + w + 1:
                out.append(f"{cid} '{value[:40]}' spills out of its box ({l:.0f}..{r:.0f} vs {x:.0f}..{x + w:.0f})")
    return out


if __name__ == "__main__":
    problems = findings(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
    print("\n".join(problems) + f"\n\n{len(problems)} finding(s)")
    sys.exit(1 if problems else 0)
