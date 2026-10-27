"""Export the main board (cell "ov") of ClaimClarity_Architecture.drawio as a clean, editable SVG.

Writes docs/architecture/ClaimClarity_Architecture.svg: only the main board's cells, HTML labels kept as
foreignObject (no PNG fallbacks), light colours only, cropped to the board.
"""
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ARCH = Path(__file__).resolve().parent.parent
DRAWIO_CLI = os.environ.get("DRAWIO_CLI", "/tmp/claude-0/-home-user-cc/0e6bb8ac-ae6e-51d1-adaa-a5d39fba27c7/scratchpad/squashfs-root/drawio")
OUT = ARCH / "ClaimClarity_Architecture.svg"
SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

raw = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if raw is None:
    raw = Path(tempfile.mkdtemp()) / "board.svg"
    subprocess.run(["xvfb-run", "-a", DRAWIO_CLI, "-x", "-f", "svg", "-o", str(raw), str(ARCH / "ClaimClarity_Architecture.drawio"),
                    "--no-sandbox", "--disable-gpu"], check=True, env={**os.environ, "HOME": tempfile.mkdtemp()})

root = ET.parse(raw).getroot()
q = lambda tag: f"{{{SVG_NS}}}{tag}"   # noqa: E731
parents = {child: parent for parent in root.iter() for child in parent}

board = next(g for g in root.iter(q("g")) if g.get("data-cell-id") == "ov")
board_rect = board.find(f"./{q('g')}/{q('rect')}")
x, y, w, h = (float(board_rect.get(k)) for k in ("x", "y", "width", "height"))

# keep only the main board's cell groups: drop every sibling cell group under "1" that is not the board
layer = parents[board]
for cell in list(layer):
    if cell is not board and not (cell.get("data-cell-id") or "").startswith("ov"):
        layer.remove(cell)
# drop the PNG/text fallbacks drawio puts next to each HTML label
for switch in root.iter(q("switch")):
    for child in list(switch):
        if child.tag != q("foreignObject"):
            switch.remove(child)

root.set("viewBox", f"{x - 1} {y - 1} {w + 2} {h + 2}")
root.set("width", str(int(w + 2)))
root.set("height", str(int(h + 2)))
root.set("style", "background: #FFFFFF; color-scheme: light;")
text = ET.tostring(root, encoding="unicode")
text = text.replace("light-dark(", "ld(")   # drawio writes light-dark() colours; keep the light value
import re   # noqa: E402
text = re.sub(r"ld\((rgb\([^)]*\)|#[0-9a-fA-F]+|[a-z]+), (rgb\([^)]*\)|#[0-9a-fA-F]+|[a-z]+)\)", r"\1", text)
OUT.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + text)
print("wrote", OUT, f"{len(text) // 1024} KB", "leftover ld(:", text.count("ld("))
