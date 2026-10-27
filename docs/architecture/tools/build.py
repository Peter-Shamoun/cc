"""python3 docs/architecture/tools/build.py: render the enhanced canvas, cut one PNG per board, and build ClaimClarity_Architecture.html and .pdf."""
import base64, os, re, subprocess
from pathlib import Path
from PIL import Image

V2 = Path(__file__).resolve().parent.parent
DRAWIO = os.environ.get("DRAWIO", "/tmp/claude-0/-home-user-cc/0e6bb8ac-ae6e-51d1-adaa-a5d39fba27c7/scratchpad/squashfs-root/drawio")
import sys
sys.path.insert(0, str(Path(__file__).parent))
from boards import LAYOUT  # noqa: E402
FILES = {"s0": "01-storage", "s1": "02-ingestion", "s2": "03-login", "s3": "04-query-path", "s4": "05-analyzer",
         "s5": "06-chat-backend", "s6": "07-network-security", "s7": "08-operations"}
BOARDS = {"00-overview": (1900, 160, 2460, 1700), **{FILES[k]: v[2:] for k, v in LAYOUT.items()}}
ORIGIN = (min(x for x, _, _, _ in BOARDS.values()) - 2, 48)   # canvas position of the exported image's top-left corner


def main():
    subprocess.run(["python3", str(V2 / "tools" / "enhance_original.py")], check=True)
    full = V2 / "full.png"
    env = dict(os.environ, HOME="/tmp/drawio-home-build"); Path(env["HOME"]).mkdir(exist_ok=True)
    subprocess.run(["xvfb-run", "-a", DRAWIO, "-x", "-f", "png", "-p", "1", "-s", "1", "-b", "0", "-o", str(full),
                    str(V2 / "ClaimClarity_Architecture.drawio"), "--no-sandbox", "--disable-gpu"], env=env, check=True, capture_output=True)
    im = Image.open(full)
    for name, (x, y, w, h) in BOARDS.items():
        im.crop((max(0, x - ORIGIN[0] - 16), max(0, y - ORIGIN[1] - 16), min(im.width, x - ORIGIN[0] + w + 16),
                 min(im.height, y - ORIGIN[1] + h + 16))).save(V2 / f"{name}.png")
    im.save(V2 / "ClaimClarity_Architecture.png")
    full.unlink()
    md = (V2 / "ClaimClarity_Architecture.md").read_text()
    md = re.sub(r"!\[([^\]]*)\]\(([^)]+\.png)\)", lambda m: f"![{m.group(1)}](data:image/png;base64,"
                f"{base64.b64encode((V2 / m.group(2)).read_bytes()).decode()})", md)
    body = subprocess.run(["pandoc", "-f", "gfm", "-t", "html"], input=md, capture_output=True, text=True, check=True).stdout
    css = ("body{font:14px/1.5 Helvetica,Arial,sans-serif;color:#1A1D23;max-width:1200px;margin:24px auto;padding:0 16px;background:#fff}"
           "h1,h2{color:#1C2B57} img{width:100%;border:1px solid #D5DCE8} table{border-collapse:collapse;width:100%}"
           "th,td{border:1px solid #D5DCE8;padding:5px 8px;text-align:left;vertical-align:top} th{background:#EAF0F9}"
           "@media print{h2{break-before:page} img{width:100%;height:auto;max-height:135mm;object-fit:contain} tr{break-inside:avoid}}")
    html = V2 / "ClaimClarity_Architecture.html"
    html.write_text(f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                    f"<title>ClaimClarity Architecture</title><style>{css}</style>{body}")
    js = (f"const {{chromium}}=require('playwright');(async()=>{{const b=await chromium.launch();const p=await b.newPage();"
          f"await p.goto('file://{html}');await p.pdf({{path:'{html.with_suffix('.pdf')}',format:'A4',landscape:true,printBackground:true,"
          f"margin:{{top:'10mm',bottom:'10mm',left:'10mm',right:'10mm'}}}});await b.close();}})();")
    subprocess.run(["node", "-e", js], check=True)


if __name__ == "__main__":
    main()
