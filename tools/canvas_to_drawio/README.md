# canvas_to_drawio

Converts the ClaimClarity architecture canvas (the HTML built by `tools/build_canvas.py`) into one editable draw.io page.
Every board becomes a container, and each SVG element becomes a native draw.io cell:

| SVG element | draw.io cell |
| --- | --- |
| `rect` | rectangle (absolute corner radius) |
| `text` | HTML text cell with per-`tspan` spans (font, colour, letter-spacing) |
| connector `path` (straight runs, `Q` corners) | edge with waypoints, rounded corners and arrowheads |
| cylinder `path` | `cylinder3` |
| diamond and hexagon `path` | `rhombus` and `hexagon` |
| any other `path` | an inline stencil with the exact path |
| icon (nested `svg` or `g[transform]`) | an embedded SVG image |

The canvas UI (header, board tabs, drag hint) is left out. The dashed links between boards are kept.

```bash
node tools/canvas_to_drawio/extract.js "$PWD/canvas.html" extract.json   # Playwright: exact geometry and computed styles
python3 tools/canvas_to_drawio/build.py extract.json diagrams/ClaimClarity_Architecture_Canvas.drawio
python3 tools/canvas_to_drawio/build.py extract.json one_board.drawio 00-storage   # one board at 0,0, no shadow (for pixel diffs)
```

## Lucidchart

Lucid's draw.io importer shows a blank canvas for the full-fidelity file (HTML labels, web fonts, stencils, containers
and SVG icons are all things it mishandles). `build_lucid.py` writes the same layout in the subset
`tools/lucid/check_drawio_for_lucid.py` accepts, and re-saves it through the draw.io CLI:

```bash
python3 tools/canvas_to_drawio/build_lucid.py extract.json diagrams/ClaimClarity_Architecture_Canvas_Lucid.drawio --drawio /path/to/drawio
python3 tools/lucid/check_drawio_for_lucid.py diagrams/ClaimClarity_Architecture_Canvas_Lucid.drawio   # must be 0 errors
python3 tools/canvas_to_drawio/check_lucid_text.py diagrams/ClaimClarity_Architecture_Canvas_Lucid.drawio extract.json   # must be 0 findings
python3 tools/canvas_to_drawio/check_edge_routes.py diagrams/ClaimClarity_Architecture_Canvas_Lucid.drawio /path/to/drawio   # must be 0 findings
```

`check_edge_routes.py` exports the file to SVG and checks that draw.io draws every edge along its intended corners.
`check_lucid_text.py` places every label in Lucid's font metrics. It reports labels that overlap each other, spill out of their box, run into an image, or are newly crossed by a connector or border.

What changes in the Lucid file:
- Text is plain Helvetica or Courier New, one cell per styled run. Runs that don't fit shrink to at most 85% of their size, so condensed labels run a little wider, and the white chip behind a label widens to match. Labels shrink further where they would otherwise reach a neighbouring label, a border or a connector line.
- Text is UTF-8, with no numeric character references: Lucid shows `&#183;` and `&#39;` literally.
- Icons and the folder and note outlines are PNG images. Cylinders are built from ellipses and rects.
- Stroke widths are rounded to whole pixels, and fill opacity is blended into solid colours.
- There is one flat layer. Lucid places an edge end only from the attached shape and `exitX`/`exitY`: it ignores
  `exitPerimeter=0` and the edge's own points. So each end attaches to the outline of a rectangle it touches, on the side
  the line crosses, or else to a transparent 10px anchor whose side midpoint is the exact end point. Anchors smaller
  than that make draw.io's orthogonal router detour.
- Edges are orthogonal (`orthogonalEdgeStyle`) with their original waypoints, because Lucid draws non-orthogonal lines
  straight and drops their bends. Arrows are `block` size 6, as in the known-good `diagrams/CC_Backend.drawio`.
- `<word>` placeholders in CLI text use the look-alike angle quotes ‹word›.

When exporting with the draw.io desktop app, Barlow, Barlow Condensed and IBM Plex Mono must be installed where the app can find them (`$HOME/.fonts`), or the text falls back to Arial. In the browser, draw.io loads them from Google Fonts through each cell's `fontSource`.
