"""python3 docs/architecture/tools/build_editor.py: write docs/architecture/diagram-editor.html.

The editor is one self-contained HTML page (open it straight from disk). This script fills its template
(tools/diagram_editor_template.html) with the same AWS icons, glyphs and drill-down layouts that ddlib.py renders,
so the shapes, arrows and gates in the editor match the published boards 00-05.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ddlib import GLYPHS, ICONS, icon_svg  # noqa: E402
from drilldowns import DIAGRAMS  # noqa: E402

TOOLS = Path(__file__).parent
TEMPLATE = TOOLS / "diagram_editor_template.html"
OUT = TOOLS.parent / "diagram-editor.html"


def board_json(diagram):
    return dict(slug=diagram.slug, title=diagram.title, desc=diagram.desc, w=diagram.w, h=diagram.h,
                zones=diagram.zones, nodes=diagram.nodes, edges=diagram.edges, gates=diagram.gates, texts=diagram.texts,
                legend_items=getattr(diagram, "legend_items", None))


def main():
    aws_icons = {path.stem: icon_svg(path.stem) for path in sorted(ICONS.glob("*.svg"))}
    boards = [board_json(make_diagram()) for make_diagram in DIAGRAMS.values()]
    page = TEMPLATE.read_text()
    for placeholder, value in (("__AWS_ICONS__", aws_icons), ("__GLYPHS__", GLYPHS), ("__BOARDS__", boards)):
        page = page.replace(f'"{placeholder}"', json.dumps(value, ensure_ascii=False).replace("</", "<\\/"))
    OUT.write_text(page)
    print(f"{OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
