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

When exporting with the draw.io desktop app, Barlow, Barlow Condensed and IBM Plex Mono must be installed where the app can find them (`$HOME/.fonts`), or the text falls back to Arial. In the browser, draw.io loads them from Google Fonts through each cell's `fontSource`.
