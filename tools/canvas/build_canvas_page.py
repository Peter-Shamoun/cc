"""Bundle the data-backend canvas boards into one self-contained HTML page.

Reads diagrams/data-backend-canvas/{canvas.json,*.dc.html,_blob/*.svg} and writes
docs/data-backend/canvas.html: every board laid out at its canvas position, icons
inlined as data URIs, SVG marker ids made unique per board, plus pan/zoom controls.

    python tools/canvas/build_canvas_page.py
"""
import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "diagrams" / "data-backend-canvas"
OUT = ROOT / "docs" / "data-backend" / "canvas.html"

ZOOM_TARGETS = {"00": "Z00-Storage.dc.html", "01": "Z01-Trigger.dc.html",
                "02": "Z02-Login.dc.html", "03": "Z03-Query.dc.html"}


def board_body(name: str, prefix: str) -> str:
    html = (SRC / name).read_text()
    body = re.search(r"</helmet>(.*)</x-dc>", html, re.S).group(1).strip()

    def blob(m):
        svg = (SRC / "_blob" / f"{m.group(1)}.svg").read_bytes()
        return 'src="data:image/svg+xml;base64,' + base64.b64encode(svg).decode() + '"'

    body = re.sub(r'src="/_blob/([0-9a-f]{32})"', blob, body)
    body = re.sub(r'id="(dc-[^"]+)"', lambda m: f'id="{prefix}-{m.group(1)}"', body)
    body = re.sub(r"url\(#(dc-[^)]+)\)", lambda m: f"url(#{prefix}-{m.group(1)})", body)
    if name == "Main.dc.html":  # make the numbered zoom-in circles jump to their board
        for num, target in ZOOM_TARGETS.items():
            body = body.replace(
                f'border-radius: 50%; background: #4A6BB0; color: #FFFFFF; display: flex; align-items: center; '
                f"justify-content: center; font-family: 'Barlow Condensed', sans-serif; font-weight: 600; "
                f'font-size: 13px">{num}</div>',
                f'border-radius: 50%; background: #4A6BB0; color: #FFFFFF; display: flex; align-items: center; '
                f"justify-content: center; font-family: 'Barlow Condensed', sans-serif; font-weight: 600; "
                f'font-size: 13px" class="zoomlink" data-go="{target}" role="button" tabindex="0" '
                f'title="Open zoom-in {num}">{num}</div>')
    if "/_blob/" in body:
        sys.exit(f"{name}: unresolved blob reference")
    return body


def main() -> None:
    canvas = json.loads((SRC / "canvas.json").read_text())
    boards, notes = canvas["boards"], canvas["notes"]
    xs = [b["x"] for b in boards.values()] + [n["x"] for n in notes.values()]
    ys = [b["y"] for b in boards.values()] + [n["y"] for n in notes.values()]
    ox, oy = -min(xs) + 80, -min(ys) + 80
    width = max(b["x"] + b["w"] for b in boards.values()) + ox + 80
    height = max(b["y"] + b["h"] for b in boards.values()) + oy + 80

    parts = []
    for key, n in notes.items():
        parts.append(f'<div class="note" style="left:{n["x"] + ox}px;top:{n["y"] + oy}px">{n["text"]}</div>')
    nav = []
    for i, name in enumerate(canvas["order"]):
        b = boards[name]
        bid = name.removesuffix(".dc.html")
        parts.append(
            f'<section class="board" id="{bid}" data-board="{name}" '
            f'style="left:{b["x"] + ox}px;top:{b["y"] + oy}px;width:{b["w"]}px;height:{b["h"]}px">'
            f'<div class="btitle">{b["title"]}</div>{board_body(name, f"b{i}")}</section>')
        nav.append(f'<button type="button" id="go-{bid}" data-go="{name}">{b["title"]}</button>')

    template = (Path(__file__).parent / "canvas_template.html").read_text()
    page = (template.replace("{{TITLE}}", canvas["title"])
            .replace("{{W}}", str(width)).replace("{{H}}", str(height))
            .replace("{{NAV}}", "\n      ".join(nav)).replace("{{BOARDS}}", "\n".join(parts)))
    OUT.write_text(page)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(page) // 1024} KB)")


if __name__ == "__main__":
    main()
