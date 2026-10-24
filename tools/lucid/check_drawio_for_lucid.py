# STATUS: CURRENT (tooling) - Lucid pipeline; Lucid import still unresolved | source: chat "Draw.io arrows from Zoom" (2026-10-23) | FLAG: reconstructed from Write + 1 patch
"""Check a draw.io file for features Lucidchart's draw.io importer is known to mishandle.

Rules (each one is something that renders wrong or fails in Lucid):
  file      XML declaration, ASCII-only, parses, one diagram, compressed payload decodes
  labels    no HTML labels (html=1 / tags in value) - Lucid shows the markup or drops styling
  fonts     only web-safe families (Helvetica, Arial, Courier New, ...); no fontSource web fonts
  shapes    only rect / text / rhombus / ellipse / hexagon / image(data URI PNG); no stencils
  struct    no containers or nested parents; unique ids; no negative coordinates
  strokes   integer strokeWidth; no opacity / textOpacity / fillOpacity
  edges     source + target exist, relative geometry, exit/entry inside 0..1
  images    data URIs decode to real PNGs
  text      every text cell is nowrap and wide/tall enough for its text in Lucid's font metrics

Usage: python3 check_drawio_for_lucid.py file.drawio   (exit 1 on errors)
"""
import base64
import io
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zlib

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_lucid import font  # noqa: E402

SAFE_FONTS = {"helvetica", "arial", "courier new", "times new roman", "verdana", "georgia", "liberation sans"}
SAFE_SHAPES = {"rect", "text", "rhombus", "ellipse", "hexagon", "image"}


def sdict(s):
    d = {}
    for part in (s or "").split(";"):
        if part:
            k, _, v = part.partition("=")
            d[k] = v if _ else True
    return d


def shape_of(st):
    if st.get("text") is True:
        return "text"
    for k in ("rhombus", "ellipse"):
        if st.get(k) is True:
            return k
    return st.get("shape", "rect")


def check(path):
    errs, warns = [], []
    raw = open(path, "rb").read()
    if not raw.startswith(b"<?xml"):
        warns.append("file: no <?xml ...?> declaration (unverified rule)")
    try:
        raw.decode("ascii")
    except UnicodeDecodeError:
        warns.append("file: non-ASCII bytes (unverified rule)")
    root = ET.fromstring(raw)
    diagrams = root.findall("diagram")
    if len(diagrams) != 1:
        warns.append(f"file: {len(diagrams)} pages")
    d = diagrams[0]
    model = d.find("mxGraphModel")
    if model is None:
        try:
            xml = urllib.parse.unquote(zlib.decompress(base64.b64decode(d.text), -15).decode())
            model = ET.fromstring(xml)
        except Exception as e:  # noqa: BLE001
            return [f"file: compressed diagram does not decode ({e})"], warns
    cells = list(model.iter("mxCell"))
    ids = {}
    for c in cells:
        cid = c.get("id")
        if cid in ids:
            errs.append(f"{cid}: duplicate id")
        ids[cid] = c
    for c in cells:
        cid, st, val = c.get("id"), sdict(c.get("style")), c.get("value") or ""
        if cid in ("0", "1"):
            continue
        where = cid
        if st.get("html") == "1":
            errs.append(f"{where}: html=1 label")
        if re.search(r"<[a-zA-Z/][^>]*>", val):
            errs.append(f"{where}: markup in label value")
        if "fontSource" in st:
            errs.append(f"{where}: web font (fontSource)")
        fam = st.get("fontFamily")
        if fam and fam.lower() not in SAFE_FONTS:
            errs.append(f"{where}: font '{fam}' not web-safe")
        for k in ("opacity", "textOpacity", "fillOpacity", "strokeOpacity"):
            if k in st:
                errs.append(f"{where}: {k} is not imported")
        sw = st.get("strokeWidth")
        if sw and not re.fullmatch(r"\d+", sw):
            errs.append(f"{where}: strokeWidth {sw} is not an integer")
        if c.get("parent") != "1":
            errs.append(f"{where}: nested parent {c.get('parent')} (containers/groups)")
        if st.get("container") == "1":
            errs.append(f"{where}: container")
        g = c.find("mxGeometry")
        if c.get("vertex") == "1":
            shp = shape_of(st)
            if shp not in SAFE_SHAPES:
                errs.append(f"{where}: shape '{shp}' may not map to a Lucid shape")
            x, y = float(g.get("x", 0)), float(g.get("y", 0))
            w, h = float(g.get("width", 0)), float(g.get("height", 0))
            if x < 0 or y < 0:
                errs.append(f"{where}: negative coordinates")
            if shp == "image":
                m = re.search(r"image=data:image/png,([A-Za-z0-9+/=]+)", c.get("style"))
                if not m:
                    errs.append(f"{where}: image is not an embedded PNG data URI")
                else:
                    try:
                        Image.open(io.BytesIO(base64.b64decode(m.group(1)))).verify()
                    except Exception:  # noqa: BLE001
                        errs.append(f"{where}: embedded PNG does not decode")
            if val:
                if st.get("whiteSpace") != "nowrap":
                    warns.append(f"{where}: label may wrap differently in Lucid (whiteSpace not nowrap)")
                px = float(st.get("fontSize", 12))
                kind = "mono" if (fam or "").lower() == "courier new" else "sans"
                need = font(kind, st.get("fontStyle") == "1", px).getlength(val)
                if need > w - 2:
                    errs.append(f"{where}: '{val[:30]}' needs {need:.0f}px, cell is {w:.0f}px")
                if px * 1.15 > h + 1:
                    errs.append(f"{where}: font {px}px taller than cell {h:.0f}px")
        elif c.get("edge") == "1":
            for k in ("source", "target"):
                if c.get(k) not in ids:
                    errs.append(f"{where}: {k} '{c.get(k)}' missing")
            if g is None or g.get("relative") != "1":
                errs.append(f"{where}: edge geometry must be relative")
            for k in ("exitX", "exitY", "entryX", "entryY"):
                if k in st and not 0 <= float(st[k]) <= 1:
                    errs.append(f"{where}: {k}={st[k]} outside 0..1")
    return errs, warns


if __name__ == "__main__":
    e, w = check(sys.argv[1])
    for m in e:
        print("ERROR", m)
    for m in w:
        print("WARN ", m)
    print(f"\n{len(e)} error(s), {len(w)} warning(s)")
    sys.exit(1 if e else 0)
