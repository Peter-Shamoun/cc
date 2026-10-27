"""python3 docs/architecture/tools/build.py: render every board and build ClaimClarity_Architecture.html and .pdf
from ClaimClarity_Architecture.md.

The boards' sources are drilldowns/main.json (the main board) and drilldowns/<nn>-<name>.json, the files the diagram
editor (diagram-editor.html) opens and saves. drilldowns.py renders them to PNG and SVG; set PYTHON to a Python with
Playwright. The editor is rebuilt too, so it opens the same boards.
"""
import base64, io, os, re, subprocess, sys
from pathlib import Path
from PIL import Image

V2 = Path(__file__).resolve().parent.parent
TOOLS = V2 / "tools"
WIDE = 3.0   # a figure wider than 3:1 is split into two stacked halves in the document, so its text stays readable in print


def data_uri(png: Path):
    im = Image.open(png)
    if im.width / im.height <= WIDE:
        return [base64.b64encode(png.read_bytes()).decode()]
    halves = []
    for box in ((0, 0, im.width // 2, im.height), (im.width // 2, 0, im.width, im.height)):
        buf = io.BytesIO(); im.crop(box).save(buf, "PNG", optimize=True)
        halves.append(base64.b64encode(buf.getvalue()).decode())
    return halves


def image(m):
    alt, src = m.group(1), m.group(2)
    return "\n\n".join(f"![{alt}](data:image/png;base64,{b})" for b in data_uri(V2 / src))


def main():
    subprocess.run([sys.executable, str(TOOLS / "drilldowns.py")], check=True)
    subprocess.run([sys.executable, str(TOOLS / "build_editor.py")], check=True)
    md = (V2 / "ClaimClarity_Architecture.md").read_text()
    md = re.sub(r"!\[([^\]]*)\]\(([^)]+\.png)\)", image, md)
    body = subprocess.run(["pandoc", "-f", "gfm", "-t", "html"], input=md, capture_output=True, text=True, check=True).stdout
    css = ("body{font:14px/1.5 Barlow,Helvetica,Arial,sans-serif;color:#1A1D23;max-width:1200px;margin:24px auto;padding:0 16px;background:#fff}"
           "h1,h2{color:#1C2B57;font-family:'Barlow Condensed',Barlow,sans-serif;line-height:1.15} h1{font-size:34px;margin:0 0 12px} h2{font-size:24px;margin:28px 0 10px} img{width:100%;border:1px solid #D5DCE8}"
           "table{border-collapse:collapse;width:100%} th,td{border:1px solid #D5DCE8;padding:5px 8px;text-align:left;vertical-align:top}"
           "th{background:#EAF0F9} code{font-family:'IBM Plex Mono',monospace;font-size:0.92em}"
           "@media print{body{font-size:11.5px;line-height:1.4} th,td{padding:3px 6px} h2{break-before:page;margin:0 0 6px} img{width:100%;height:auto;max-height:104mm;object-fit:contain} tr{break-inside:avoid}}")
    html = V2 / "ClaimClarity_Architecture.html"
    html.write_text(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                    f"<title>ClaimClarity Architecture</title><style>{css}</style>{body}")
    js = (f"const {{chromium}}=require('playwright');(async()=>{{const b=await chromium.launch();const p=await b.newPage();"
          f"await p.goto('file://{html}');await p.pdf({{path:'{html.with_suffix('.pdf')}',format:'A4',landscape:true,printBackground:true,"
          f"margin:{{top:'10mm',bottom:'10mm',left:'10mm',right:'10mm'}}}});await b.close();}})();")
    subprocess.run(["node", "-e", js], check=True, env=dict(os.environ, NODE_PATH=os.environ.get("NODE_PATH", "/opt/node22/lib/node_modules")))


if __name__ == "__main__":
    main()
