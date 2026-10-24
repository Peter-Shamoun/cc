# STATUS: CURRENT (tooling) - Lucid pipeline; Lucid import still unresolved | source: chat "Draw.io arrows from Zoom" (2026-10-23) | FLAG: reconstructed from Write + 4 patches; hard-coded /tmp paths
"""Convert the (uncompressed) ClaimClarity draw.io diagram into a Lucid Standard Import (.lucid) file.

Handles the subset of draw.io used by gen.py: nested containers, rectangles, text,
rhombus, ellipse, hexagon, icon shapes (rendered to PNG with the draw.io CLI and
embedded as image blocks), HTML labels (converted to Lucid's <p style> + <b>/<br>
markup), and edges with exit/entry constraints, waypoints and labels.

Usage: python3 drawio2lucid.py in.drawio out.lucid
"""
import hashlib
import html
import html.parser
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_lucid import measure, font  # noqa: E402

DRAWIO = "/tmp/drawio-bin/squashfs-root/drawio"
ICON_DIR = "/tmp/work/lucid/icons"
TEXT_PAD = 4          # safety margin per side for Lucid text blocks
MIN_SHRINK = 0.72     # never shrink box text below this share of the source size


def sdict(style):
    d = {}
    for part in (style or "").split(";"):
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            d[k] = v
        else:
            d[part] = True
    return d


def color(c, default=None):
    if not c or c == "none":
        return default
    c = c.upper()
    if re.fullmatch(r"#[0-9A-F]{3}", c):
        c = "#" + "".join(ch * 2 for ch in c[1:])
    return c if re.fullmatch(r"#[0-9A-F]{6}([0-9A-F]{2})?", c) else default


CLEAR = "#FFFFFF00"

# ----------------------------------------------------------------------------- labels


class Label(html.parser.HTMLParser):
    """draw.io HTML label -> lines of runs with resolved style."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.stack = [base]
        self.lines = [[]]

    def handle_starttag(self, tag, attrs):
        st = dict(self.stack[-1])
        if tag == "br":
            self.lines.append([])
            return
        if tag in ("b", "strong"):
            st["bold"] = True
        for k, v in attrs:
            if k == "style" and v:
                for decl in v.split(";"):
                    if ":" not in decl:
                        continue
                    a, b = (x.strip() for x in decl.split(":", 1))
                    if a == "font-size":
                        st["px"] = float(re.sub(r"[^\d.]", "", b))
                    elif a == "color":
                        st["color"] = b
                    elif a == "font-weight":
                        st["bold"] = b in ("bold", "600", "700") or (b == "500" and st.get("bold"))
                    elif a == "text-transform" and b == "uppercase":
                        st["upper"] = True
                    elif a == "font-family":
                        st["mono"] = "mono" in b.lower()
                        if "condensed" in b.lower():
                            st["bold"] = True
        self.stack.append(st)

    def handle_endtag(self, tag):
        if tag != "br" and len(self.stack) > 1:
            self.stack.pop()

    def handle_data(self, data):
        st = self.stack[-1]
        txt = data.replace("\xa0", " ")
        if st.get("upper"):
            txt = txt.upper()
        self.lines[-1].append((txt, st))


def label_html(value, base, scale=1.0, maxw=None):
    """Return (lucid_html, meta) for a draw.io label. One <p> per visual line."""
    p = Label(base)
    p.feed(value)
    p.close()
    out = []
    for runs in p.lines:
        runs = [(t, s) for t, s in runs if t]
        if not runs:
            px = base["px"] * scale
            out.append(f'<p style="font-size:{px * 0.75:.1f}pt"> </p>')
            continue
        lead = next((s for t, s in runs if t.strip()), runs[0][1])
        if any(s.get("mono") for t, s in runs if t.strip()) and not all(s.get("mono") for t, s in runs if t.strip()):
            # Lucid styles whole paragraphs: keep the prose style, bold the code part instead of mono
            lead = next(s for t, s in runs if t.strip() and not s.get("mono"))
            runs = [(t, dict(s, bold=True, mono=False) if s.get("mono") else s) for t, s in runs]
        pbold = all(s.get("bold") for t, s in runs if t.strip())
        if maxw:
            fnt = font("mono" if lead.get("mono") else "sans", pbold, lead["px"] * scale)
            full = "".join(t for t, _ in runs)
            if fnt.getlength(full) > maxw:
                words, cur, wrapped = full.split(" "), "", []
                for wd in words:
                    trial = (cur + " " + wd).strip()
                    if cur and fnt.getlength(trial) > maxw:
                        wrapped.append(cur); cur = wd
                    else:
                        cur = trial
                wrapped.append(cur)
                sub = dict(lead)
                for part in wrapped:
                    css = [f"font-size:{lead['px'] * scale * 0.75:.1f}pt", f"text-align:{base['align']}"]
                    c = color(lead.get("color")) or base.get("color")
                    if c: css.append(f"color:{c}")
                    if lead.get("mono"): css.append("font-family:Courier New")
                    if pbold: css.append("font-weight:bold")
                    out.append(f'<p style="{";".join(css)}">{html.escape(part, quote=False)}</p>')
                continue
        css = [f"font-size:{lead['px'] * scale * 0.75:.1f}pt", f"text-align:{base['align']}"]
        c = color(lead.get("color")) or base.get("color")
        if c:
            css.append(f"color:{c}")
        if lead.get("mono"):
            css.append("font-family:Courier New")
        if pbold:
            css.append("font-weight:bold")
        body = ""
        for t, s in runs:
            t = html.escape(t, quote=False).replace("  ", " &nbsp;")
            body += f"<b>{t}</b>" if s.get("bold") and not pbold else t
        out.append(f'<p style="{";".join(css)}">{body.strip() or " "}</p>')
    return "".join(out)


# ----------------------------------------------------------------------------- icons
ICON_SHAPES = ("mxgraph.aws4.resourceIcon", "mxgraph.weblogos.github", "mxgraph.aws4.folder",
               "mxgraph.aws4.shield2", "cylinder3", "note")


def is_icon(st, value):
    return not value and st.get("shape") in ICON_SHAPES


def render_icon(style, w, h):
    os.makedirs(ICON_DIR, exist_ok=True)
    key = hashlib.sha1(f"{style}|{w}|{h}".encode()).hexdigest()[:12]
    png = os.path.join(ICON_DIR, f"icon_{key}.png")
    if not os.path.exists(png):
        k = 4
        xml = (f'<mxfile><diagram id="i" name="i"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
               f'<mxCell id="2" value="" style="{html.escape(style)}" vertex="1" parent="1">'
               f'<mxGeometry x="0" y="0" width="{w * k}" height="{h * k}" as="geometry"/></mxCell></root></mxGraphModel></diagram></mxfile>')
        src = png.replace(".png", ".drawio")
        open(src, "w").write(xml)
        env = dict(os.environ, HOME="/tmp")
        subprocess.run(["xvfb-run", "-a", DRAWIO, "-x", "-f", "png", "-t", "--border", "0", "-o", png, src,
                        "--no-sandbox", "--disable-gpu"], env=env, capture_output=True, timeout=120)
        if not os.path.exists(png):
            raise RuntimeError(f"icon render failed for {style}")
    return os.path.basename(png)


# ----------------------------------------------------------------------------- convert


def convert(src, out):
    root = ET.parse(src).getroot()
    model = root.find(".//mxGraphModel")
    cells, order = {}, []
    for c in model.iter("mxCell"):
        cid = c.get("id")
        g = c.find("mxGeometry")
        cells[cid] = dict(id=cid, value=c.get("value") or "", st=sdict(c.get("style")), parent=c.get("parent"),
                          vertex=c.get("vertex") == "1", edge=c.get("edge") == "1", src=c.get("source"),
                          tgt=c.get("target"), g=g)
        order.append(cid)

    absb = {}

    def abs_of(cid):
        if cid in ("0", "1") or cid is None:
            return (0.0, 0.0)
        if cid in absb:
            return absb[cid][:2]
        c = cells[cid]
        px, py = abs_of(c["parent"])
        g = c["g"]
        x, y = px + float(g.get("x", 0)), py + float(g.get("y", 0))
        absb[cid] = (x, y, float(g.get("width", 0)), float(g.get("height", 0)))
        return (x, y)

    shapes, lines, groups, images, notes = [], [], [], {}, []
    z = 0
    zpos = {cid: i * 3 for i, cid in enumerate(order)}
    lid = {}  # draw.io id -> lucid shape id

    def stroke_of(st, default_w=1):
        if st.get("strokeColor") == "none":
            return {"color": CLEAR, "width": 0, "style": "solid"}
        w = float(st.get("strokeWidth", default_w))
        return {"color": color(st.get("strokeColor"), "#000000"), "width": max(1, int(w + 0.5)),
                "style": "dashed" if st.get("dashed") in ("1", True) else "solid"}

    def text_block(cid, bb, value, st, bold_default=False):
        """Place a Lucid text block for a draw.io label inside bb (abs x,y,w,h)."""
        nonlocal z
        x, y, w, h = bb
        sl = float(st.get("spacingLeft", 0)) + float(st.get("spacing", 0) if "spacing" in st else 0)
        sr = float(st.get("spacingRight", 0))
        stp = float(st.get("spacingTop", 0))
        align = st.get("align", "center")
        base = {"px": float(st.get("fontSize", 12)), "align": align, "color": color(st.get("fontColor")),
                "bold": bold_default or st.get("fontStyle") == "1",
                "mono": "Mono" in st.get("fontFamily", "")}
        if "Condensed" in st.get("fontFamily", ""):
            base["bold"] = True
        is_text_cell = st.get("text") is True
        tx, tw = x + sl, w - sl - sr
        scale = 1.0
        txt = label_html(value, base, scale)
        _, mw, mh, _ = measure(txt)
        if is_text_cell and mw > tw - 2 * TEXT_PAD:
            grow = mw + 2 * TEXT_PAD - tw
            if align == "right":
                tx -= grow
            elif align == "center":
                tx -= grow / 2
            tw += grow
            notes.append(f"widened label {cid} by {grow:.0f}px")
        if mw > tw - 2 * TEXT_PAD and not is_text_cell:
            wtxt = label_html(value, base, 1.0, maxw=tw - 2 * TEXT_PAD - 2)
            _, ww, wh, _ = measure(wtxt)
            if ww <= tw - 2 * TEXT_PAD and wh <= h - 6:
                txt, mw, mh = wtxt, ww, wh
                notes.append(f"wrapped text of {cid}")
        while mw > tw - 2 * TEXT_PAD and scale > MIN_SHRINK:
            scale -= 0.04
            txt = label_html(value, base, scale)
            _, mw, mh, _ = measure(txt)
        if scale < 1:
            notes.append(f"shrank text of {cid} to {scale:.0%}")
        if st.get("verticalAlign") == "top":
            ty, th = y + stp + 2, mh + 4
        else:
            th = max(h, mh + 4)
            ty = y + (h - th) / 2
        z += 1  # just above its own box
        tid = f"{cid}-t"[:36]
        shapes.append({"id": tid, "type": "text", "boundingBox": {"x": r(tx), "y": r(ty), "w": r(tw), "h": r(th)},
                       "text": txt, "zIndex": z})
        return tid

    def r(v):
        return round(v, 1)

    for cid in order:
        c = cells[cid]
        if not c["vertex"]:
            continue
        x, y = abs_of(cid)
        _, _, w, h = absb[cid]
        st, val = c["st"], c["value"]
        bb = {"x": r(x), "y": r(y), "w": r(w), "h": r(h)}
        z = zpos[cid]
        if is_icon(st, val):
            ref = render_icon(";".join(f"{k}={v}" if v is not True else k for k, v in st.items()), w, h)
            images[ref] = os.path.join(ICON_DIR, ref)
            shapes.append({"id": cid, "type": "image", "boundingBox": bb,
                           "stroke": {"color": CLEAR, "width": 0, "style": "solid"},
                           "image": {"type": "image", "ref": ref, "imageScale": "fit"}, "zIndex": z})
            lid[cid] = cid
            continue
        if st.get("text") is True and color(st.get("fillColor")) is None:
            # pure label: text block only
            lid[cid] = text_block(cid, (x, y, w, h), val, st)
            continue
        t = "rectangle"
        if st.get("rhombus"):
            t = "diamond"
        elif st.get("ellipse"):
            t = "circle"
        elif st.get("shape") == "hexagon":
            t = "hexagon"
        elif st.get("container") == "1":
            t = "rectangleContainer"
        fillc = CLEAR if st.get("fillColor") == "none" else color(st.get("fillColor"), CLEAR if t == "rectangleContainer" else "#FFFFFF")
        style = {"fill": {"type": "color", "color": fillc}, "stroke": stroke_of(st)}
        if st.get("line"):
            # vertical rule drawn as a thin bar
            bb = {"x": r(x), "y": r(y), "w": 1.5, "h": r(h)}
            style = {"fill": {"type": "color", "color": color(st.get("strokeColor"), "#000000")},
                     "stroke": {"color": CLEAR, "width": 0, "style": "solid"}}
        shp = {"id": cid, "type": t, "boundingBox": bb, "style": style, "zIndex": z}
        if t == "rectangleContainer":
            shp["magnetize"] = True
        shapes.append(shp)
        lid[cid] = cid
        if val:
            tid = text_block(cid, (x, y, w, h), val, st)
            groups.append({"id": f"{cid}-g"[:36], "items": [cid, tid], "zIndex": shp["zIndex"]})

    # pull each "<id>_i" icon into its owner's group
    gmap = {g["items"][0]: g for g in groups}
    for cid in order:
        if cid.endswith("_i") and cid[:-2] in gmap and cid in lid:
            gmap[cid[:-2]]["items"].append(cid)

    ends = {"block": "arrow", "classic": "arrow", "open": "openArrow", "oval": "closedCircle", "none": "none"}
    for cid in order:
        c = cells[cid]
        if not c["edge"]:
            continue
        st = c["st"]
        z = zpos[cid]
        px, py = abs_of(c["parent"]) if c["parent"] not in ("0", "1") else (0.0, 0.0)

        def ep(which, sid, kx, ky, arrowkey, default):
            pos = None
            if kx in st:
                pos = {"x": float(st[kx]), "y": float(st[ky])}
            e = {"type": "shapeEndpoint", "style": ends.get(st.get(arrowkey, default), "arrow"), "shapeId": lid[sid]}
            if pos:
                e["position"] = pos
            return e

        line = {"id": cid, "lineType": "straight",
                "endpoint1": ep(1, c["src"], "exitX", "exitY", "startArrow", "none"),
                "endpoint2": ep(2, c["tgt"], "entryX", "entryY", "endArrow", "classic"),
                "stroke": stroke_of(st, 1), "zIndex": z}
        if st.get("opacity"):
            # Lucid lines have no opacity: pre-blend the colour onto white
            o = float(st["opacity"]) / 100
            col = line["stroke"]["color"]
            rgb = [int(col[i:i + 2], 16) for i in (1, 3, 5)]
            line["stroke"]["color"] = "#" + "".join(f"{round(v * o + 255 * (1 - o)):02X}" for v in rgb)
        g = c["g"]
        arr = g.find("Array") if g is not None else None
        if arr is not None:
            line["joints"] = [{"x": r(float(p.get("x")) + px), "y": r(float(p.get("y")) + py)} for p in arr.iter("mxPoint")]
        if c["value"]:
            lx = float(g.get("x", 0)) if g is not None else 0
            ly = float(g.get("y", 0)) if g is not None else 0
            base = {"px": float(st.get("fontSize", 12)), "align": "center", "color": color(st.get("fontColor"))}
            txt = label_html(c["value"], base)
            line["text"] = [{"text": txt, "position": round((lx + 1) / 2, 3), "side": "top" if ly < 0 else "middle"}]
        lines.append(line)

    maxx = max(s["boundingBox"]["x"] + s["boundingBox"]["w"] for s in shapes) + 60
    maxy = max(s["boundingBox"]["y"] + s["boundingBox"]["h"] for s in shapes) + 60
    doc = {"version": 1, "documentSettings": {"units": "px"},
           "pages": [{"id": "page1", "title": "Data backend",
                      "settings": {"fillColor": "#FFFFFF", "infiniteCanvas": False, "autoTiling": True,
                                   "size": {"type": "custom", "w": r(maxx), "h": r(maxy)}},
                      "shapes": shapes, "lines": lines, "groups": groups}]}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("document.json", json.dumps(doc, indent=1))
        for ref, p in images.items():
            zf.write(p, f"images/{ref}")
    return notes, len(shapes), len(lines), len(groups), len(images)


if __name__ == "__main__":
    notes, *counts = convert(sys.argv[1], sys.argv[2])
    print("shapes/lines/groups/images:", counts)
    for n in notes:
        print(" note:", n)
