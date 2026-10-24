# STATUS: CURRENT (tooling) - Lucid pipeline; Lucid import still unresolved | source: chat "Draw.io arrows from Zoom" (2026-10-23) | FLAG: reconstructed from Write + 1 sed patch
"""Write a "Lucid-safe" draw.io file from a validated .lucid layout.

Lucid's draw.io importer mishandles HTML labels, web fonts, vendor stencil shapes,
fractional strokes and nested containers. This writer emits only plain constructs:
  * rect / rhombus / ellipse / hexagon / image(data URI) vertices, no containers
  * text as plain (html=0) cells, one cell per styled run, Helvetica / Courier New
  * integer stroke widths, explicit colours, no opacity
  * straight polyline edges bound to source/target with exit/entry points and plain labels
Every text run is positioned with the same Liberation Sans metrics the validator uses.

Usage: python3 lucid2drawio.py in.lucid out.drawio [--compressed]
"""
import base64
import html
import io
import json
import os
import sys
import urllib.parse
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from validate_lucid import TextParser, font, parse_pt, MONO_FAMILIES  # noqa: E402

SANS, MONO = "Helvetica", "Courier New"


def col(c):
    if not c:
        return "none"
    return "none" if len(c) == 9 and c[-2:] in ("00",) else c[:7]


def esc(v):
    return html.escape(v, quote=True)


def runs_layout(text_html, bb, inset=0):
    """Yield (x, y, w, h, text, px, color, bold, mono, align) per run, mirroring the preview renderer."""
    p = TextParser()
    p.feed(text_html)
    p.close()
    lines = []
    for para in p.paras:
        st = para["style"]
        px = parse_pt(st.get("font-size")) * 4 / 3
        mono = st.get("font-family", "").strip("'\" ").lower() in MONO_FAMILIES
        pb = st.get("font-weight", "normal") in ("bold", "600", "700")
        color = st.get("color", "#000000")
        al = st.get("text-align", "center")
        segs = [[]]
        for txt, b in para["runs"]:
            for i, part in enumerate(txt.split("\n")):
                if i:
                    segs.append([])
                if part:
                    segs[-1].append((part, b or pb))
        for seg in segs:
            lines.append((seg, mono, px, color, al))
    total = sum(px * 1.2 for _, _, px, _, _ in lines)
    x0, w = bb["x"] + inset, bb["w"] - 2 * inset
    y = bb["y"] + (bb["h"] - total) / 2
    out = []
    for seg, mono, px, color, al in lines:
        kind = "mono" if mono else "sans"
        widths = [font(kind, b, px).getlength(t) for t, b in seg]
        lw = sum(widths)
        x = x0 if al == "left" else x0 + w - lw if al == "right" else x0 + (w - lw) / 2
        for (t, b), tw in zip(seg, widths):
            if t.strip():
                lead = len(t) - len(t.lstrip())
                dx = font(kind, b, px).getlength(t[:lead]) if lead else 0
                out.append((x + dx, y, tw - dx + 8, px * 1.2, t.strip(), px, color, b, mono))
            x += tw
        y += px * 1.2
    return out


def convert(src, dst, compressed=False):
    zf = zipfile.ZipFile(src)
    doc = json.loads(zf.read("document.json"))
    page = doc["pages"][0]
    shapes = {s["id"]: s for s in page["shapes"]}
    items = [("s", i, s) for i, s in enumerate(page["shapes"])] + [("l", i, l) for i, l in enumerate(page.get("lines", []))]
    items.sort(key=lambda it: (it[2].get("zIndex", 0), 0 if it[0] == "s" else 1, it[1]))
    cells, n = [], [0]

    def vid(prefix):
        n[0] += 1
        return f"{prefix}{n[0]}"

    def vertex(cid, value, style, x, y, w, h):
        cells.append(f'<mxCell id="{cid}" value="{esc(value)}" style="{style}" vertex="1" parent="1">'
                     f'<mxGeometry x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" as="geometry"/></mxCell>')

    def text_cells(text_html, bb):
        for (x, y, w, h, t, px, color, b, mono) in runs_layout(text_html, bb):
            st = (f"text;html=0;whiteSpace=nowrap;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;"
                  f"spacing=0;spacingLeft=0;spacingRight=0;spacingTop=0;spacingBottom=0;"
                  f"fontFamily={MONO if mono else SANS};fontSize={round(px)};fontColor={color[:7]};fontStyle={1 if b else 0};")
            vertex(vid("t"), t, st, x, y, w, h)

    drawn = set()
    for kind, _, it in items:
        if kind == "s":
            t, bb = it["type"], it["boundingBox"]
            if t == "image":
                data = base64.b64encode(zf.read("images/" + it["image"]["ref"])).decode()
                st = f"shape=image;html=0;imageAspect=1;aspect=fixed;verticalLabelPosition=bottom;image=data:image/png,{data};"
                vertex(it["id"], "", st, bb["x"], bb["y"], bb["w"], bb["h"])
                drawn.add(it["id"])
                continue
            if t == "text":
                text_cells(it["text"], bb)
                continue
            s = it.get("style", {})
            fill = col(s.get("fill", {}).get("color", "#FFFFFF"))
            sk = s.get("stroke", {})
            sw = sk.get("width", 1)
            stroke = "none" if sw == 0 else col(sk.get("color", "#000000"))
            shape = {"diamond": "rhombus;", "circle": "ellipse;",
                     "hexagon": "shape=hexagon;perimeter=hexagonPerimeter2;size=0.22;fixedSize=0;"}.get(t, "rounded=0;")
            st = (f"{shape}html=0;whiteSpace=nowrap;fillColor={fill};strokeColor={stroke};strokeWidth={max(1, sw)};"
                  f"{'dashed=1;' if sk.get('style') == 'dashed' else ''}")
            vertex(it["id"], "", st, bb["x"], bb["y"], bb["w"], bb["h"])
            drawn.add(it["id"])
            if it.get("text"):
                text_cells(it["text"], bb)
        else:
            def end(ep):
                return {"arrow": "block", "openArrow": "open", "closedCircle": "oval", "none": "none"}.get(ep["style"], "block")
            e1, e2 = it["endpoint1"], it["endpoint2"]
            sk = it.get("stroke", {})
            st = (f"edgeStyle=none;html=0;rounded=0;strokeColor={sk.get('color', '#000000')[:7]};strokeWidth={sk.get('width', 1)};"
                  f"{'dashed=1;' if sk.get('style') == 'dashed' else ''}"
                  f"startArrow={end(e1)};startFill=1;startSize=5;endArrow={end(e2)};endFill={0 if e2['style'] == 'openArrow' else 1};endSize=7;")
            for k, e in (("exit", e1), ("entry", e2)):
                if "position" in e:
                    st += f"{k}X={e['position']['x']};{k}Y={e['position']['y']};{k}Dx=0;{k}Dy=0;"
            val, lx = "", 0
            if it.get("text"):
                tp = TextParser(); tp.feed(it["text"][0]["text"]); tp.close()
                val = "".join(t for p in tp.paras for t, _ in p["runs"]).strip()
                ps = tp.paras[0]["style"] if tp.paras else {}
                st += (f"fontFamily={SANS};fontSize={round(parse_pt(ps.get('font-size')) * 4 / 3)};"
                       f"fontColor={ps.get('color', '#000000')[:7]};labelBackgroundColor=#FFFFFF;")
                lx = it["text"][0].get("position", 0.5) * 2 - 1
                if it["text"][0].get("side") == "top":
                    st += "verticalAlign=bottom;"
            pts = "".join(f'<mxPoint x="{p["x"]}" y="{p["y"]}"/>' for p in it.get("joints", []))
            geo = f'<mxGeometry x="{lx:.3f}" relative="1" as="geometry">' + (f'<Array as="points">{pts}</Array>' if pts else "") + "</mxGeometry>"
            cells.append(f'<mxCell id="{it["id"]}" value="{esc(val)}" style="{st}" edge="1" parent="1" '
                         f'source="{e1["shapeId"]}" target="{e2["shapeId"]}">{geo}</mxCell>')

    size = page.get("settings", {}).get("size", {})
    model = (f'<mxGraphModel dx="1600" dy="900" grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
             f'fold="1" page="1" pageScale="1" pageWidth="{int(size.get("w", 6500))}" pageHeight="{int(size.get("h", 2200))}" '
             f'math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>{"".join(cells)}</root></mxGraphModel>')
    if compressed:
        c = zlib.compressobj(9, zlib.DEFLATED, -15)
        payload = base64.b64encode(c.compress(urllib.parse.quote(model, safe="").encode()) + c.flush()).decode()
    else:
        payload = model
    out = ('<?xml version="1.0" encoding="UTF-8"?>\n<mxfile host="app.diagrams.net" agent="drawio" version="29.0.3">'
           f'<diagram id="cc-backend" name="Data backend">{payload}</diagram></mxfile>\n')
    out = "".join(ch if ord(ch) < 128 else f"&#{ord(ch)};" for ch in out)
    open(dst, "w").write(out)
    return len(cells)


if __name__ == "__main__":
    print(convert(sys.argv[1], sys.argv[2], "--compressed" in sys.argv), "cells")
