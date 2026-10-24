# STATUS: CURRENT (tooling) - Lucid pipeline; Lucid import still unresolved | source: chat "Draw.io arrows from Zoom" (2026-10-23) | FLAG: reconstructed from Write + 3 patches
"""Pre-flight validator for Lucid Standard Import (.lucid) files.

Encodes the rules published at https://lucid.readme.io/docs/standard-import/llms.txt
(fetched 2026-10-23): package layout and size limits, document/page/shape/line/group
schemas, enum values, ID and colour formats, the HTML text markup + style allowlist,
plus layout checks Lucid will not report but that show up as "errors" on the canvas
(text that will not fit in its box, dangling or misplaced line endpoints, items
off the page, non-orthogonal elbow points).

Usage:  python3 validate_lucid.py file.lucid [--json report.json]
Exit code 0 = no errors (warnings allowed), 1 = errors.
"""
import html.parser
import io
import json
import re
import sys
import zipfile

import jsonschema
from PIL import ImageFont

# ----------------------------------------------------------------------------- spec
ID = {"type": "string", "pattern": r"^[A-Za-z0-9\-_.~]{1,36}$"}
COLOR = {"type": "string", "pattern": r"^#([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$"}
NUM = {"type": "number"}
REL = {"type": "number", "minimum": 0, "maximum": 1}
BBOX = {"type": "object", "required": ["x", "y", "w", "h"], "additionalProperties": False,
        "properties": {"x": NUM, "y": NUM, "w": {"type": "number", "exclusiveMinimum": 0},
                       "h": {"type": "number", "exclusiveMinimum": 0},
                       "rotation": {"type": "number", "minimum": 0, "maximum": 360}}}
STROKE = {"type": "object", "additionalProperties": False,
          "properties": {"color": COLOR, "width": {"type": "integer", "minimum": 0},
                         "style": {"enum": ["solid", "dashed", "dotted"]}}}
IMAGEFILL = {"type": "object", "required": ["type"], "additionalProperties": False,
             "properties": {"type": {"const": "image"}, "ref": {"type": "string"}, "url": {"type": "string"},
                            "imageScale": {"enum": ["fit", "fill", "stretch", "original", "tile"]}},
             "oneOf": [{"required": ["ref"]}, {"required": ["url"]}]}
FILL = {"oneOf": [{"type": "object", "required": ["type", "color"], "additionalProperties": False,
                   "properties": {"type": {"const": "color"}, "color": COLOR}}, IMAGEFILL]}
STYLE = {"type": "object", "additionalProperties": False,
         "properties": {"fill": FILL, "stroke": STROKE, "rounding": {"type": "integer", "minimum": 0},
                        "textColor": COLOR}}
COMMON = {"id": ID, "type": {"type": "string"}, "boundingBox": BBOX, "actions": {"type": "array"},
          "customData": {"type": "array"}, "linkedData": {"type": "array"},
          "opacity": {"type": "integer", "minimum": 0, "maximum": 100}, "note": {"type": "string"},
          "zIndex": {"type": "integer"}}

STD_STYLE_TEXT = ["rectangle", "stickyNote",
                  # shape library
                  "circle", "cloud", "cross", "diamond", "doubleArrow", "flexiblePolygon", "hexagon",
                  "isoscelesTriangle", "octagon", "pentagon", "polyStar", "rightTriangle", "singleArrow",
                  # flowchart library
                  "braceNote", "connector", "data", "database", "decision", "delay", "directAccessStorage",
                  "display", "document", "internalStorage", "manualInput", "manualOperation", "merge",
                  "multipleDocuments", "note", "offPageLink", "or", "paperTape", "predefinedProcess",
                  "preparation", "process", "storedData", "summingJunction", "terminator"]
CONTAINERS = ["braceContainer", "bracketContainer", "circleContainer", "diamondContainer", "pillContainer",
              "rectangleContainer", "roundedRectangleContainer"]


def shape_schema(t):
    props = dict(COMMON)
    req = ["id", "type", "boundingBox"]
    if t in STD_STYLE_TEXT:
        props.update(style=STYLE, text={"type": "string"})
    elif t == "text":
        props.update(text={"type": "string"})
    elif t == "hotspot":
        props.update(style=STYLE)
    elif t == "image":
        props.update(stroke=STROKE, image=IMAGEFILL); req += ["stroke", "image"]
    elif t in CONTAINERS:
        props.update(style=STYLE, magnetize={"type": "boolean"}, assistedLayout={"type": "boolean"},
                     containerTitle={"type": "object", "required": ["text"], "additionalProperties": False,
                                     "properties": {"text": {"type": "string"}}})
    elif t in ("namedShape", "namedContainer"):
        props.update(className={"type": "string", "pattern": "AWS2024$"}, text={"type": "string"}, style=STYLE)
        req += ["className"]
    return {"type": "object", "required": req, "additionalProperties": False, "properties": props}


ENDPOINT_STYLES = ["none", "aggregation", "arrow", "hollowArrow", "openArrow", "async1", "async2", "closedSquare",
                   "openSquare", "bpmnConditional", "bpmnDefault", "closedCircle", "openCircle", "composition",
                   "exactlyOne", "generalization", "many", "nesting", "one", "oneOrMore", "zeroOrMore", "zeroOrOne"]
POS_ABS = {"type": "object", "required": ["x", "y"], "additionalProperties": False, "properties": {"x": NUM, "y": NUM}}
POS_REL = {"type": "object", "required": ["x", "y"], "additionalProperties": False, "properties": {"x": REL, "y": REL}}
ENDPOINT = {"oneOf": [
    {"type": "object", "required": ["type", "style", "shapeId"], "additionalProperties": False,
     "properties": {"type": {"const": "shapeEndpoint"}, "style": {"enum": ENDPOINT_STYLES}, "shapeId": ID, "position": POS_REL}},
    {"type": "object", "required": ["type", "style", "lineId", "position"], "additionalProperties": False,
     "properties": {"type": {"const": "lineEndpoint"}, "style": {"enum": ENDPOINT_STYLES}, "lineId": ID, "position": REL}},
    {"type": "object", "required": ["type", "style", "position"], "additionalProperties": False,
     "properties": {"type": {"const": "positionEndpoint"}, "style": {"enum": ENDPOINT_STYLES}, "position": POS_ABS}}]}
LINE = {"type": "object", "required": ["id", "lineType", "endpoint1", "endpoint2"], "additionalProperties": False,
        "properties": {"id": ID, "lineType": {"enum": ["straight", "elbow", "curved"]}, "endpoint1": ENDPOINT,
                       "endpoint2": ENDPOINT, "stroke": STROKE, "customData": {"type": "array"},
                       "linkedData": {"type": "array"}, "zIndex": {"type": "integer"},
                       "joints": {"type": "array", "items": POS_ABS}, "elbowControlPoints": {"type": "array", "items": POS_ABS},
                       "text": {"type": "array", "items": {"type": "object", "required": ["text"], "additionalProperties": False,
                                "properties": {"text": {"type": "string"}, "position": REL,
                                               "side": {"enum": ["top", "middle", "bottom"]}}}}}}
GROUP = {"type": "object", "required": ["id"], "additionalProperties": False,
         "properties": {"id": ID, "items": {"type": "array", "items": ID}, "note": {"type": "string"},
                        "customData": {"type": "array"}, "linkedData": {"type": "array"}, "zIndex": {"type": "integer"}}}
PAGE_SIZE = {"oneOf": [
    {"type": "object", "required": ["type"], "additionalProperties": False,
     "properties": {"type": {"enum": ["letter", "legal", "executive", "a3", "a4", "a5", "tabloid", "folio", "statement"]},
                    "format": {"enum": ["portrait", "landscape"]}}},
    {"type": "object", "required": ["type", "w", "h"], "additionalProperties": False,
     "properties": {"type": {"const": "custom"}, "w": {"type": "number", "exclusiveMinimum": 0},
                    "h": {"type": "number", "exclusiveMinimum": 0}}}]}
PAGE = {"type": "object", "required": ["id", "title"], "additionalProperties": False,
        "properties": {"id": ID, "title": {"type": "string"},
                       "settings": {"type": "object", "additionalProperties": False,
                                    "properties": {"fillColor": COLOR, "infiniteCanvas": {"type": "boolean"},
                                                   "autoTiling": {"type": "boolean"}, "size": PAGE_SIZE}},
                       "shapes": {"type": "array"}, "lines": {"type": "array", "items": LINE},
                       "groups": {"type": "array", "items": GROUP}, "layers": {"type": "array"},
                       "dataBackedShapes": {"type": "array"}, "customData": {"type": "array"}}}
DOCUMENT = {"type": "object", "required": ["version", "pages"], "additionalProperties": False,
            "properties": {"version": {"type": "number"}, "pages": {"type": "array", "minItems": 1, "items": PAGE},
                           "documentSettings": {"type": "object", "additionalProperties": False,
                                                "properties": {"units": {"enum": ["cm", "in", "pt", "px"]}}},
                           "collections": {"type": "array"}, "extensionBootstrapData": {"type": "object"}}}
KNOWN_TYPES = set(STD_STYLE_TEXT + CONTAINERS + ["text", "hotspot", "image", "namedShape", "namedContainer"])

# Documented AWS 2024 class names only (others are unverifiable and may fail).
AWS_DOCUMENTED = None  # filled from the docs file if present

# ----------------------------------------------------------------------------- text
TAGS = {"p", "br", "b", "strong", "i", "em", "s", "strike", "u", "a", "ol", "ul", "li"}
STYLE_PROPS = {"color", "font-family", "font-size", "font-style", "font-weight", "text-align",
               "text-decoration", "vertical-align"}
FONTS = {
    ("sans", False): "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ("sans", True): "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ("mono", False): "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ("mono", True): "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
}
MONO_FAMILIES = {"courier new", "courier", "liberation mono", "monospace", "source code pro", "roboto mono"}
_font_cache = {}


def font(kind, bold, px):
    key = (kind, bold, round(px * 4) / 4)
    if key not in _font_cache:
        _font_cache[key] = ImageFont.truetype(FONTS[(kind, bold)], max(1, round(px)))
    return _font_cache[key]


class TextParser(html.parser.HTMLParser):
    """Turns Lucid text HTML into paragraphs of styled runs; records markup problems."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.problems, self.paras, self.stack, self.bold = [], [], [], 0
        self.cur = None

    def _style(self, attrs):
        st = {}
        for k, v in attrs:
            if k == "style" and v:
                for decl in v.split(";"):
                    if ":" in decl:
                        a, b = decl.split(":", 1)
                        a = a.strip().lower()
                        if a not in STYLE_PROPS:
                            self.problems.append(f"style property '{a}' is ignored by Lucid")
                        st[a] = b.strip()
            elif k not in ("href", "style"):
                self.problems.append(f"attribute '{k}' is not supported")
        return st

    def handle_starttag(self, tag, attrs):
        if tag not in TAGS:
            self.problems.append(f"tag <{tag}> is not in Lucid's markup allowlist (text will show raw or be dropped)")
        st = self._style(attrs)
        if tag == "p":
            self.cur = {"style": st, "runs": []}
            self.paras.append(self.cur)
        elif tag == "br":
            self._ensure(); self.cur["runs"].append(("\n", self.bold > 0))
        elif tag in ("b", "strong"):
            self.bold += 1

    def handle_endtag(self, tag):
        if tag in ("b", "strong"):
            self.bold -= 1
        elif tag == "p":
            self.cur = None

    def _ensure(self):
        if self.cur is None:
            self.cur = {"style": {}, "runs": []}
            self.paras.append(self.cur)

    def handle_data(self, data):
        self._ensure()
        self.cur["runs"].append((data, self.bold > 0))


def parse_pt(v, default=10.0):
    m = re.match(r"^\s*([\d.]+)\s*(pt|px)?\s*$", v or "")
    if not m:
        return default
    n = float(m.group(1))
    return n * 0.75 if m.group(2) == "px" else n


def measure(text_html):
    """Return (lines, width_px, height_px, problems) for text as Lucid would lay it out (no wrapping)."""
    p = TextParser()
    p.feed(text_html)
    p.close()
    if not p.paras:
        return [], 0, 0, p.problems
    width, height, lines = 0.0, 0.0, []
    for para in p.paras:
        st = para["style"]
        pt = parse_pt(st.get("font-size"))
        px = pt * 4 / 3
        kind = "mono" if st.get("font-family", "").strip("'\" ").lower() in MONO_FAMILIES else "sans"
        pbold = st.get("font-weight", "normal") in ("bold", "600", "700", "800", "900")
        segs = [[]]
        for txt, b in para["runs"]:
            parts = txt.split("\n")
            for i, part in enumerate(parts):
                if i:
                    segs.append([])
                if part:
                    segs[-1].append((part, b or pbold))
        for seg in segs:
            w = sum(font(kind, b, px).getlength(t) for t, b in seg)
            width = max(width, w)
            height += px * 1.2
            lines.append("".join(t for t, _ in seg))
    return lines, width, height, p.problems


# ----------------------------------------------------------------------------- checks
class Report:
    def __init__(self):
        self.items = []

    def err(self, where, msg):
        self.items.append(("ERROR", where, msg))

    def warn(self, where, msg):
        self.items.append(("WARN", where, msg))

    @property
    def errors(self):
        return [i for i in self.items if i[0] == "ERROR"]


def load_aws_names():
    try:
        txt = open("/tmp/lucid-docs/aws-2024-library.md").read()
        return set(re.findall(r"`([A-Za-z0-9]+AWS2024)`", txt))
    except OSError:
        return None


def validate(path):
    r = Report()
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        r.err("package", "not a ZIP archive"); return r, None
    names = zf.namelist()
    if "document.json" not in names:
        nested = [n for n in names if n.endswith("/document.json")]
        r.err("package", "document.json must be at the ZIP root" + (f" (found {nested[0]})" if nested else ""))
        return r, None
    total = sum(i.file_size for i in zf.infolist())
    imgs = {n.split("/")[-1]: zf.getinfo(n).file_size for n in names if n.startswith("images/") and not n.endswith("/")}
    if total > 50e6: r.err("package", f"contents {total/1e6:.1f} MB > 50 MB")
    if sum(imgs.values()) > 50e6: r.err("package", "images/ > 50 MB")
    if zf.getinfo("document.json").file_size > 2e6: r.err("package", "document.json > 2 MB")
    for n in imgs:
        if not re.search(r"\.(png|jpe?g|gif|bmp|tiff|svg)$", n, re.I):
            r.err("package", f"unsupported image type {n}")
    try:
        doc = json.loads(zf.read("document.json"))
    except json.JSONDecodeError as e:
        r.err("document.json", f"invalid JSON: {e}"); return r, None

    v = jsonschema.Draft202012Validator(DOCUMENT)
    for e in sorted(v.iter_errors(doc), key=lambda e: list(e.path)):
        r.err("/".join(map(str, e.path)) or "document", e.message[:300])

    aws = load_aws_names()
    ids = {}
    for page in doc.get("pages", []):
        pid = page.get("id")
        size = (page.get("settings") or {}).get("size") or {}
        pw, ph = (size.get("w"), size.get("h")) if size.get("type") == "custom" else (None, None)
        shapes = {}
        for i, s in enumerate(page.get("shapes", [])):
            where = f"page {pid} shape {s.get('id', i)}"
            t = s.get("type")
            if s.get("id") in ids:
                r.err(where, f"duplicate id (also {ids[s['id']]})")
            ids[s.get("id")] = where
            if t not in KNOWN_TYPES:
                r.err(where, f"unknown shape type '{t}'"); continue
            for e in jsonschema.Draft202012Validator(shape_schema(t)).iter_errors(s):
                r.err(where + ("/" + "/".join(map(str, e.path)) if e.path else ""), e.message[:300])
            if t in ("namedShape", "namedContainer") and aws and s.get("className") not in aws:
                r.warn(where, f"className {s.get('className')} is not in the documented AWS 2024 list")
            if t == "image":
                ref = (s.get("image") or {}).get("ref")
                if ref and ref not in imgs:
                    r.err(where, f"image ref '{ref}' not found in images/ (import fails)")
            fillref = ((s.get("style") or {}).get("fill") or {}).get("ref")
            if fillref and fillref not in imgs:
                r.err(where, f"fill image ref '{fillref}' not found in images/")
            shapes[s.get("id")] = s
            bb = s.get("boundingBox") or {}
            if pw and (bb.get("x", 0) < 0 or bb.get("y", 0) < 0 or bb.get("x", 0) + bb.get("w", 0) > pw
                       or bb.get("y", 0) + bb.get("h", 0) > ph):
                r.warn(where, "extends outside the page")
            if "text" in s and isinstance(s["text"], str) and s["text"]:
                check_text(r, where, s, bb, t)
        for i, ln in enumerate(page.get("lines", [])):
            where = f"page {pid} line {ln.get('id', i)}"
            if ln.get("id") in ids:
                r.err(where, f"duplicate id (also {ids[ln['id']]})")
            ids[ln.get("id")] = where
            pts = []
            for k in ("endpoint1", "endpoint2"):
                ep = ln.get(k) or {}
                if ep.get("type") == "shapeEndpoint":
                    tgt = shapes.get(ep.get("shapeId"))
                    if not tgt:
                        r.err(where, f"{k} shapeId '{ep.get('shapeId')}' does not exist on this page"); continue
                    pos = ep.get("position")
                    if pos is None:
                        r.warn(where, f"{k} has no position: Lucid will auto-route it (smart line)")
                    else:
                        if 0 < pos["x"] < 1 and 0 < pos["y"] < 1:
                            r.warn(where, f"{k} attaches inside {tgt['id']} rather than on its edge")
                        b = tgt["boundingBox"]
                        pts.append((b["x"] + b["w"] * pos["x"], b["y"] + b["h"] * pos["y"]))
                elif ep.get("type") == "positionEndpoint":
                    pts.append((ep["position"]["x"], ep["position"]["y"]))
                elif ep.get("type") == "lineEndpoint":
                    if ep.get("lineId") not in {l.get("id") for l in page.get("lines", [])}:
                        r.err(where, f"{k} lineId '{ep.get('lineId')}' does not exist")
            if ln.get("lineType") == "elbow" and ln.get("elbowControlPoints") and len(pts) == 2:
                seq = [pts[0]] + [(p["x"], p["y"]) for p in ln["elbowControlPoints"]] + [pts[1]]
                for a, b in zip(seq, seq[1:]):
                    if abs(a[0] - b[0]) > 0.5 and abs(a[1] - b[1]) > 0.5:
                        r.err(where, f"elbow segment {a}->{b} is not at 90 degrees")
            if ln.get("lineType") == "straight" and len(pts) == 2:
                seq = [pts[0]] + [(p["x"], p["y"]) for p in ln.get("joints", [])] + [pts[1]]
                for a, b in zip(seq, seq[1:]):
                    if abs(a[0] - b[0]) > 0.5 and abs(a[1] - b[1]) > 0.5:
                        r.warn(where, f"diagonal segment {tuple(round(x) for x in a)}->{tuple(round(x) for x in b)}")
            for lt in ln.get("text", []):
                _, _, _, probs = measure(lt.get("text", ""))
                for pr in set(probs):
                    r.err(where + " label", pr)
        for g in page.get("groups", []):
            if g.get("id") in ids:
                r.err(f"group {g.get('id')}", "duplicate id")
            ids[g.get("id")] = "group"
            for it in g.get("items", []):
                if it not in ids:
                    r.err(f"group {g.get('id')}", f"item '{it}' does not exist (or is defined after the group)")
        check_overlaps(r, pid, page.get("shapes", []))
    return r, doc


INSET = 8  # Lucid pads text inside shapes; text blocks hug their box


def check_text(r, where, s, bb, t):
    lines, w, h, probs = measure(s["text"])
    for pr in sorted(set(probs)):
        r.err(where, pr)
    pad = 0 if t == "text" else INSET
    availw, availh = bb.get("w", 0) - 2 * pad, bb.get("h", 0) - (0 if t == "text" else 4)
    if t in ("diamond", "hexagon", "circle"):
        availw *= 0.7
    if w > availw + 0.5:
        widest = max(lines, key=lambda l: font("sans", False, 12).getlength(l)) if lines else ""
        r.err(where, f"text '{widest[:40]}' needs {w:.0f}px but box allows {availw:.0f}px (Lucid will wrap/shrink it)")
    if h > availh + 1:
        r.err(where, f"text needs {h:.0f}px height but box is {availh:.0f}px (overflows)")


def ink_box(s):
    """Where the text actually lands inside a text block (by width + alignment)."""
    bb = s["boundingBox"]
    _, w, h, _ = measure(s["text"])
    al = "left" if "text-align:left" in s["text"].replace(" ", "") else "right" if "text-align:right" in s["text"].replace(" ", "") else "center"
    x = bb["x"] if al == "left" else bb["x"] + bb["w"] - w if al == "right" else bb["x"] + (bb["w"] - w) / 2
    return {"x": x, "y": bb["y"] + (bb["h"] - h) / 2, "w": w, "h": h}


def check_overlaps(r, pid, shapes):
    texts = [s for s in shapes if s.get("type") == "text" and s.get("text")]
    boxes = {s["id"]: ink_box(s) for s in texts}
    for s in shapes:
        if s.get("type") != "image":
            continue
        I = s["boundingBox"]
        for tid, A in boxes.items():
            ox = min(A["x"] + A["w"], I["x"] + I["w"]) - max(A["x"], I["x"])
            oy = min(A["y"] + A["h"], I["y"] + I["h"]) - max(A["y"], I["y"])
            if ox > 2 and oy > 2:
                r.err(f"page {pid}", f"text of {tid} runs over icon {s['id']}")
    for i, a in enumerate(texts):
        A = boxes[a["id"]]
        for b in texts[i + 1:]:
            B = boxes[b["id"]]
            ox = min(A["x"] + A["w"], B["x"] + B["w"]) - max(A["x"], B["x"])
            oy = min(A["y"] + A["h"], B["y"] + B["h"]) - max(A["y"], B["y"])
            if ox > 2 and oy > 2:
                r.err(f"page {pid}", f"text of {a['id']} and {b['id']} overlaps on the canvas")


def main():
    path = sys.argv[1]
    rep, _ = validate(path)
    for lvl, where, msg in rep.items:
        print(f"{lvl:5} {where}: {msg}")
    n_e, n_w = len(rep.errors), len(rep.items) - len(rep.errors)
    print(f"\n{n_e} error(s), {n_w} warning(s)")
    if "--json" in sys.argv:
        json.dump([dict(level=a, where=b, msg=c) for a, b, c in rep.items], open(sys.argv[sys.argv.index("--json") + 1], "w"), indent=1)
    sys.exit(1 if n_e else 0)


if __name__ == "__main__":
    main()
