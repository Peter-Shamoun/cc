"""python3 docs/architecture/tools/build_canvas.py: write docs/architecture/ClaimClarity_Canvas.html.

One pan-and-zoom canvas with every board on it: the main board in the middle, each drill-down placed beside the zoom
badge that points to it (with a dashed connector), and the access matrix with the gates key at the top left.
It embeds the SVGs drilldowns.py renders, so run that first (build.py does both).
"""
import json
import re
from pathlib import Path

ARCH = Path(__file__).resolve().parent.parent
BOARDS = ARCH / "drilldowns"
OUT = ARCH / "ClaimClarity_Canvas.html"
GAP = 400

# where each board goes, relative to the main board at (0, 0); "side" is the edge its connector reaches
PLACEMENT = {
    "00-storage": dict(x=-300, y=-1300, side="bottom"),
    "01-ingestion": dict(x=2300, y=-1340, side="bottom"),
    "02-login": dict(x=-2900, y=975, side="right"),
    "03-query-path": dict(x=3475, y=975, side="left"),
    "04-analyzer": dict(x=-964, y=2654, side="top"),
    "05-access-matrix": dict(x=-3120, y=-1340, side=None),
}


def board(slug):
    return json.loads((BOARDS / f"{slug}.json").read_text())["board"]


def inline_svg(slug):
    svg = (BOARDS / f"{slug}.svg").read_text()
    svg = re.sub(r"<\?xml[^>]*\?>|<!DOCTYPE[^>]*>", "", svg).strip()
    return re.sub(r'<svg ', '<svg preserveAspectRatio="xMidYMid meet" ', svg, count=1)


def main():
    main_board = board("main")
    badges = {n["name"]: (n["x"] + n["w"] / 2, n["y"] + n["h"] / 2) for n in main_board["nodes"] if n["shape"] == "badge"}
    items = [dict(slug="main", title=main_board["title"], x=0, y=0, w=main_board["w"], h=main_board["h"], svg=inline_svg("main"))]
    links = []
    for slug, place in PLACEMENT.items():
        b = board(slug)
        x, y, w, h = place["x"], place["y"], b["w"], b["h"]
        items.append(dict(slug=slug, title=b["title"], x=x, y=y, w=w, h=h, svg=inline_svg(slug)))
        number = slug[:2]
        if place["side"] and number in badges:
            bx, by = badges[number]
            side = place["side"]
            if side in ("top", "bottom"):
                end = (min(max(bx, x + 40), x + w - 40), y if side == "top" else y + h)
                start = (bx, by)
            else:
                end = (x if side == "left" else x + w, min(max(by, y + 40), y + h - 40))
                start = (bx, by)
            links.append(dict(number=number, start=start, end=end))
    xs = [i["x"] for i in items] + [i["x"] + i["w"] for i in items]
    ys = [i["y"] for i in items] + [i["y"] + i["h"] for i in items]
    bounds = [min(xs) - GAP, min(ys) - GAP, max(xs) - min(xs) + 2 * GAP, max(ys) - min(ys) + 2 * GAP]
    page = (Path(__file__).parent / "canvas_template.html").read_text()
    data = dict(items=[{k: v for k, v in i.items() if k != "svg"} for i in items], links=links, bounds=bounds)
    page = page.replace('"__CANVAS__"', json.dumps(data))
    page = page.replace("<!--__BOARDS__-->", "\n".join(
        f'<div class="board" data-slug="{i["slug"]}" style="left:{i["x"] - bounds[0]}px;top:{i["y"] - bounds[1]}px;'
        f'width:{i["w"]}px;height:{i["h"]}px">{i["svg"]}</div>' for i in items))
    OUT.write_text(page)
    print(f"{OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
