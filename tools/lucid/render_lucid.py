"""Approximate preview of a .lucid file, drawn with Lucid's default font (Liberation Sans).

Not a pixel-exact Lucid renderer: it exists to catch layout problems (text overflow,
misplaced endpoints, overlaps, z-order) before uploading.
Usage: python3 render_lucid.py file.lucid out.png [--scale 0.5] [--crop x,y,w,h]
"""
import io
import json
import math
import sys
import zipfile

from PIL import Image, ImageDraw

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from validate_lucid import INSET, TextParser, font, parse_pt, MONO_FAMILIES  # noqa: E402


def rgba(c, default=(0, 0, 0, 255)):
    if not c:
        return default
    c = c.lstrip("#")
    if len(c) == 6:
        c += "ff"
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4, 6))


def dashed(draw, pts, color, width, style):
    if style == "solid":
        draw.line(pts, fill=color, width=width)
        return
    on, off = (8, 6) if style == "dashed" else (2, 4)
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        L = math.hypot(x2 - x1, y2 - y1)
        d = 0
        while d < L:
            e = min(d + on, L)
            draw.line([(x1 + (x2 - x1) * d / L, y1 + (y2 - y1) * d / L), (x1 + (x2 - x1) * e / L, y1 + (y2 - y1) * e / L)],
                      fill=color, width=width)
            d += on + off


def outline(t, x, y, w, h):
    if t == "diamond":
        return [(x + w / 2, y), (x + w, y + h / 2), (x + w / 2, y + h), (x, y + h / 2)]
    if t == "hexagon":
        k = w * 0.22
        return [(x + k, y), (x + w - k, y), (x + w, y + h / 2), (x + w - k, y + h), (x + k, y + h), (x, y + h / 2)]
    return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


def draw_text(img, draw, s, bb, S, inset):
    p = TextParser()
    p.feed(s)
    p.close()
    lines = []
    for para in p.paras:
        st = para["style"]
        px = parse_pt(st.get("font-size")) * 4 / 3
        kind = "mono" if st.get("font-family", "").strip("'\" ").lower() in MONO_FAMILIES else "sans"
        pb = st.get("font-weight", "normal") in ("bold", "600", "700")
        col = rgba(st.get("color"), (0, 0, 0, 255)) if st.get("color", "").startswith("#") else (0, 0, 0, 255)
        al = st.get("text-align", "center")
        segs = [[]]
        for txt, b in para["runs"]:
            for i, part in enumerate(txt.split("\n")):
                if i:
                    segs.append([])
                if part:
                    segs[-1].append((part, b or pb))
        for seg in segs:
            lines.append((seg, kind, px, col, al))
    total = sum(px * 1.2 for _, _, px, _, _ in lines)
    x0, w = bb["x"] + inset, bb["w"] - 2 * inset
    y = bb["y"] + (bb["h"] - total) / 2
    for seg, kind, px, col, al in lines:
        lw = sum(font(kind, b, px).getlength(t) for t, b in seg)
        x = x0 if al == "left" else x0 + w - lw if al == "right" else x0 + (w - lw) / 2
        for t, b in seg:
            f = font(kind, b, px * S)
            draw.text((x * S, (y + px * 0.1) * S), t, font=f, fill=col)
            x += font(kind, b, px).getlength(t)
        y += px * 1.2


def arrow(draw, a, b, style, color, S):
    if style == "none":
        return
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    L = 9 * S
    p1 = (b[0] - L * math.cos(ang - 0.45), b[1] - L * math.sin(ang - 0.45))
    p2 = (b[0] - L * math.cos(ang + 0.45), b[1] - L * math.sin(ang + 0.45))
    if style in ("arrow",):
        draw.polygon([b, p1, p2], fill=color)
    else:
        draw.line([p1, b, p2], fill=color, width=max(1, round(S)))


def render(path, out, S=0.5, crop=None):
    zf = zipfile.ZipFile(path)
    doc = json.loads(zf.read("document.json"))
    page = doc["pages"][0]
    size = page.get("settings", {}).get("size", {})
    W, H = int(size.get("w", 4000)), int(size.get("h", 3000))
    img = Image.new("RGBA", (int(W * S), int(H * S)), rgba(page.get("settings", {}).get("fillColor", "#FFFFFF")))
    draw = ImageDraw.Draw(img)
    shapes = {s["id"]: s for s in page.get("shapes", [])}
    items = [("s", i, s) for i, s in enumerate(page.get("shapes", []))] + [("l", i, l) for i, l in enumerate(page.get("lines", []))]
    items.sort(key=lambda it: (it[2].get("zIndex", 0), 0 if it[0] == "s" else 1, it[1]))
    for kind, _, it in items:
        if kind == "s":
            t, bb = it["type"], it["boundingBox"]
            x, y, w, h = bb["x"], bb["y"], bb["w"], bb["h"]
            if t == "image":
                ref = it["image"].get("ref")
                if ref:
                    im = Image.open(io.BytesIO(zf.read("images/" + ref))).convert("RGBA")
                    im.thumbnail((max(1, int(w * S)), max(1, int(h * S))))
                    img.alpha_composite(im, (int(x * S + (w * S - im.width) / 2), int(y * S + (h * S - im.height) / 2)))
                continue
            if t != "text":
                st = it.get("style", {})
                fill = rgba(st.get("fill", {}).get("color"), (255, 255, 255, 255))
                sk = st.get("stroke", {})
                sc, sw, ss = rgba(sk.get("color")), max(0, round(sk.get("width", 1) * S)) or 1, sk.get("style", "solid")
                pts = [(px * S, py * S) for px, py in outline(t, x, y, w, h)]
                if t == "circle":
                    if fill[3]:
                        draw.ellipse([x * S, y * S, (x + w) * S, (y + h) * S], fill=fill, outline=None)
                    if sk.get("width", 1) and sc[3]:
                        draw.ellipse([x * S, y * S, (x + w) * S, (y + h) * S], outline=sc, width=sw)
                else:
                    if fill[3]:
                        draw.polygon(pts, fill=fill)
                    if sk.get("width", 1) and sc[3]:
                        dashed(draw, pts + [pts[0]], sc, sw, ss)
            if it.get("text"):
                draw_text(img, draw, it["text"], bb, S, 0 if t == "text" else INSET)
        else:
            pts = []
            for k in ("endpoint1", "endpoint2"):
                ep = it[k]
                if ep["type"] == "shapeEndpoint":
                    b = shapes[ep["shapeId"]]["boundingBox"]
                    pos = ep.get("position", {"x": 0.5, "y": 0.5})
                    pts.append((b["x"] + b["w"] * pos["x"], b["y"] + b["h"] * pos["y"]))
                else:
                    pts.append((ep["position"]["x"], ep["position"]["y"]))
            mids = [(p["x"], p["y"]) for p in it.get("joints", []) + it.get("elbowControlPoints", [])]
            path_ = [pts[0]] + mids + [pts[1]]
            P = [(a * S, b * S) for a, b in path_]
            sk = it.get("stroke", {})
            col = rgba(sk.get("color"))
            dashed(draw, P, col, max(1, round(sk.get("width", 1) * S)), sk.get("style", "solid"))
            arrow(draw, P[-2], P[-1], it["endpoint2"]["style"], col, S)
            arrow(draw, P[1], P[0], it["endpoint1"]["style"], col, S)
            segl = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(path_, path_[1:])]
            for lt in it.get("text", []):
                target, acc = sum(segl) * lt.get("position", 0.5), 0
                for (a, b), L in zip(zip(path_, path_[1:]), segl):
                    if acc + L >= target:
                        f = (target - acc) / L if L else 0
                        cx, cy = a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f
                        break
                    acc += L
                tp = TextParser(); tp.feed(lt["text"]); tp.close()
                txt = "".join(t for p in tp.paras for t, _ in p["runs"])
                st = tp.paras[0]["style"] if tp.paras else {}
                px = parse_pt(st.get("font-size")) * 4 / 3
                fnt = font("sans", False, px)
                tw = fnt.getlength(txt)
                box = [(cx - tw / 2 - 3) * S, (cy - px * 0.6) * S, (cx + tw / 2 + 3) * S, (cy + px * 0.6) * S]
                draw.rectangle(box, fill=(255, 255, 255, 255))
                draw.text(((cx - tw / 2) * S, (cy - px * 0.5) * S), txt, font=font("sans", False, px * S),
                          fill=rgba(st.get("color")) if st.get("color", "").startswith("#") else (0, 0, 0, 255))
    if crop:
        x, y, w, h = crop
        img = img.crop((int(x * S), int(y * S), int((x + w) * S), int((y + h) * S)))
    img.convert("RGB").save(out)


if __name__ == "__main__":
    a = sys.argv
    S = float(a[a.index("--scale") + 1]) if "--scale" in a else 0.5
    crop = tuple(map(float, a[a.index("--crop") + 1].split(","))) if "--crop" in a else None
    render(a[1], a[2], S, crop)
