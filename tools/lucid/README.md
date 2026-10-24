# Lucid pre-flight loop

Three scripts, Python 3 + `jsonschema` + `Pillow`:

| Step | Script | What it does |
|---|---|---|
| 1 | `drawio2lucid.py in.drawio out.lucid` | Converts the draw.io diagram to Lucid Standard Import (`document.json` + `images/`). Icons are rendered to PNG with the draw.io CLI. Labels become Lucid `<p style>` markup; text that won't fit is wrapped, then shrunk (floor 72%). |
| 2 | `validate_lucid.py file.lucid` | Checks the package against the published Standard Import spec (layout, size limits, schemas, enums, ID/colour formats, integer stroke widths, markup + style allowlist) and the canvas: text fit measured in Liberation Sans (Lucid's default font), text colliding with text or icons, dangling/inside endpoints, diagonal or non-90° segments, items off the page. Exit 1 on any error. |
| 3 | `render_lucid.py file.lucid preview.png [--scale 0.5] [--crop x,y,w,h]` | Draws an approximate Lucid preview for visual inspection. |

Loop: edit `gen.py` → `drawio2lucid.py` → `validate_lucid.py` (must be 0 errors) → `render_lucid.py` → inspect → repeat.

The validator only knows what Lucid documents; a real import is the final check.
Spec source: https://lucid.readme.io/docs/standard-import/llms.txt

## Lucidchart web import (draw.io route)

Lucid's UI does not accept `.lucid`; it imports draw.io XML. Two more steps:

| Step | Script | What it does |
|---|---|---|
| 4 | `lucid2drawio.py backend.lucid out.drawio --compressed` | Writes a Lucid-safe draw.io file from the validated layout: plain-text cells (one per styled run), Helvetica / Courier New, PNG icons as data URIs, integer strokes, no containers or HTML. |
| 5 | `check_drawio_for_lucid.py out.drawio` | Flags what Lucid's draw.io importer mishandles: HTML labels, web fonts, stencil shapes, containers/nesting, fractional strokes, opacity, broken edges/images, text that won't fit. Exit 1 on errors. |

Full loop: `gen.py` → `drawio2lucid.py` → `validate_lucid.py` → `lucid2drawio.py` → `check_drawio_for_lucid.py` → render (`drawio -x -f png`) → inspect.
