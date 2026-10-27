"""python3 docs/architecture/tools/drilldowns.py [nn ...]: render the six drill-downs from their board files.

The source of each drill-down is docs/architecture/drilldowns/<nn>-<name>.json, the file the diagram editor
(docs/architecture/diagram-editor.html) opens and saves. Per board this writes the production figure (ClaimClarity
skin) as <nn>-<name>.svg and .png via diagram-design's exporters, and the prototype <nn>-<name>.excalidraw in the
user's excalidraw style (except 04, whose .excalidraw is the user's own board, edited by analyzer04.py).
Needs the diagram-design skill (~/.claude/skills/diagram-design) and a Python with Playwright (PYTHON env).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ddlib import FONTS, Diagram, check, to_excalidraw, to_html  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "drilldowns"
SKILL = Path(os.environ.get("DIAGRAM_DESIGN", Path.home() / ".claude/skills/diagram-design")) / "scripts"
PY = os.environ.get("PYTHON", sys.executable)
MAX_PNG = 4000                # longest side of a drill-down PNG; the SVG keeps full detail


def board_files():
    return {path.stem[:2]: path for path in sorted(OUT.glob("[0-9][0-9]-*.json"))}


def load(path):
    """A Diagram from an editor board file ({"format": "claimclarity-diagram", "board": {...}})."""
    board = json.loads(path.read_text())["board"]
    d = Diagram(board["slug"], board["title"], board["desc"], board["w"], board["h"])
    d.zones, d.nodes, d.edges, d.gates, d.texts = (board[k] for k in ("zones", "nodes", "edges", "gates", "texts"))
    for e in d.edges:
        e["pts"] = [tuple(p) for p in e["pts"]]
        e["at"] = tuple(e["at"]) if e.get("at") else None
    d.show_legend = board.get("show_legend", True)
    d.legend_items = board.get("legend_items")
    return d


def cap_png(png):
    from PIL import Image
    im = Image.open(png)
    k = MAX_PNG / max(im.size)
    if k < 1:
        im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS).save(png, optimize=True)


def main(which):
    tmp = Path(tempfile.mkdtemp())
    files = board_files()
    for k in which or files:
        d = load(files[k])
        for prob in check(d):
            print(f"  {d.slug}: {prob}")
        if k != "04":                 # 04's .excalidraw is the source (the user's board, edited by analyzer04.py)
            (OUT / f"{d.slug}.excalidraw").write_text(to_excalidraw(d))
        html = tmp / f"{d.slug}.html"
        html.write_text(to_html(d, d.legend_items))
        svg = OUT / f"{d.slug}.svg"
        subprocess.run([PY, str(SKILL / "export_svg.py"), str(html), str(svg)], check=True)
        # the exporter imports the skill's default faces; the board uses the ClaimClarity ones
        svg.write_text(re.sub(r"@import url\('https://fonts\.googleapis\.com/css2\?[^']*'\);",
                              "@import url('" + FONTS.replace("&", "&amp;") + "');", svg.read_text()))
        png = OUT / f"{d.slug}.png"
        subprocess.run([PY, str(SKILL / "export_png.py"), str(html), str(png), str(min(2, max(1, MAX_PNG / d.w)))], check=True)
        cap_png(png)
        print(d.slug)


if __name__ == "__main__":
    main(sys.argv[1:])
