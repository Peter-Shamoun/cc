"""Build the architecture docs: renders, one multi-page .drawio, one HTML file and one PDF.

    python3 docs/architecture/build.py            # everything
    python3 docs/architecture/build.py --no-render # reuse png/ and svg/

Inputs: src/<id>.drawio (one diagram each), pages/<id>.md, cards/<service>.md.
Outputs: png/, svg/, CC_Architecture.drawio (all diagrams as pages), pages/90-service-catalog.md (from the cards),
CC_Architecture.html (self-contained, inline SVG, page tree on the left) and CC_Architecture.pdf.
"""
from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DRAWIO = Path(os.environ.get("DRAWIO", "/tmp/claude-0/-home-user-cc/0e6bb8ac-ae6e-51d1-adaa-a5d39fba27c7/scratchpad/squashfs-root/drawio"))
CARD_ORDER = ["transfer-family", "s3", "eventbridge", "ecs-fargate", "ecr", "sqs", "cloudfront", "waf", "alb", "cognito",
              "entra-id", "sts", "iam", "kms", "bedrock", "vpc", "secrets-manager", "cloudwatch", "cloudtrail"]


def page_ids() -> list[str]:
    return sorted(p.stem for p in (HERE / "pages").glob("*.md"))


def render(diagram_id: str) -> None:
    env = dict(os.environ, HOME="/tmp/drawio-home-build")
    Path(env["HOME"]).mkdir(exist_ok=True)
    source = HERE / "src" / f"{diagram_id}.drawio"
    for fmt, extra in (("png", ["--width", "2000"]), ("svg", [])):
        subprocess.run(["xvfb-run", "-a", str(DRAWIO), "-x", "-f", fmt, *extra, "-o", str(HERE / fmt / f"{diagram_id}.{fmt}"),
                        str(source), "--no-sandbox", "--disable-gpu"], env=env, check=True, capture_output=True, timeout=180)


def merge_drawio(diagram_ids: list[str]) -> None:
    pages = []
    for diagram_id in diagram_ids:
        text = (HERE / "src" / f"{diagram_id}.drawio").read_text()
        pages += re.findall(r"<diagram\b.*?</diagram>", text, re.S)
    (HERE / "CC_Architecture.drawio").write_text('<mxfile host="drawio">\n' + "\n".join(pages) + "\n</mxfile>\n")


def service_catalog() -> None:
    cards = {p.stem: p.read_text().strip() for p in (HERE / "cards").glob("*.md")}
    ordered = [cards.pop(name) for name in CARD_ORDER if name in cards] + [cards[name] for name in sorted(cards)]
    (HERE / "pages" / "90-service-catalog.md").write_text(
        "# AWS service catalog\n\n[Overview](00-overview.md) › Service catalog\n\n"
        "One card per AWS service CC uses: what it does in CC, what it talks to, who may do what, encryption, data "
        "held, limits, failure behaviour and logging. Each card links to the pages that use it.\n\n" + "\n\n".join(ordered) + "\n")


def title_of(markdown: str, fallback: str) -> str:
    match = re.search(r"^# (.+)$", markdown, re.M)
    return match.group(1).strip() if match else fallback


def build_html(ids: list[str]) -> Path:
    sections, nav = [], []
    for page_id in ids:
        markdown = (HERE / "pages" / f"{page_id}.md").read_text()
        svg_path = HERE / "svg" / f"{page_id}.svg"

        def inline_image(match):   # the PNG reference becomes the inline SVG (crisp, searchable text)
            if svg_path.exists():
                svg = re.sub(r"<\?xml[^>]*>|<!DOCTYPE[^>]*>", "", svg_path.read_text())
                return f'\n\n<div class="diagram">{svg}</div>\n\n'
            return match.group(0)
        markdown = re.sub(r"!\[[^\]]*\]\(\.\./png/[^)]+\)", inline_image, markdown)
        markdown = re.sub(r"\]\((\d\d-[a-z0-9-]+)\.md(#[^)]*)?\)", lambda m: f"](#{m.group(1)})", markdown)
        body = subprocess.run(["pandoc", "-f", "gfm", "-t", "html"], input=markdown, capture_output=True, text=True,
                              check=True).stdout
        sections.append(f'<section id="{page_id}">{body}</section>')
        depth = 0 if page_id.startswith("00") or page_id[1] == "0" else 1
        nav.append(f'<a class="d{depth}" href="#{page_id}">{html.escape(title_of(markdown, page_id))}</a>')
    out = HERE / "CC_Architecture.html"
    out.write_text(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CC Architecture</title>
<style>
:root {{ --ink:#1A1D23; --navy:#1C2B57; --blue:#4A6BB0; --tint:#EAF0F9; --line:#D5DCE8; --paper:#FFFFFF; }}
body {{ margin:0; font:15px/1.55 Helvetica, Arial, sans-serif; color:var(--ink); background:var(--paper); }}
nav {{ position:fixed; top:0; left:0; bottom:0; width:260px; overflow:auto; padding:20px 16px; background:#F3F5F9;
       border-right:1px solid var(--line); box-sizing:border-box; }}
nav b {{ display:block; color:var(--navy); margin-bottom:12px; letter-spacing:.04em; }}
nav a {{ display:block; color:var(--navy); text-decoration:none; padding:3px 0; font-size:13px; }}
nav a.d1 {{ padding-left:14px; color:#3A5A9E; }}
main {{ margin-left:260px; padding:24px 40px 80px; max-width:1200px; }}
section {{ border-bottom:1px solid var(--line); padding:12px 0 36px; }}
h1 {{ color:var(--navy); font-size:26px; }} h2 {{ color:var(--navy); font-size:18px; margin-top:28px; }}
a {{ color:#2F55A4; }}
table {{ border-collapse:collapse; margin:12px 0; font-size:13.5px; width:100%; }}
th, td {{ border:1px solid var(--line); padding:6px 9px; text-align:left; vertical-align:top; }}
th {{ background:var(--tint); }}
code {{ background:#F1F3F7; padding:1px 4px; border-radius:3px; font-size:13px; }}
pre {{ background:#F1F3F7; padding:10px; overflow:auto; }}
.diagram {{ border:1px solid var(--line); padding:8px; background:#fff; overflow:auto; }}
.diagram svg {{ width:100%; height:auto; max-width:100%; }}
@media (max-width: 800px) {{ nav {{ position:static; width:auto; height:auto; }} main {{ margin-left:0; padding:16px; }} }}
@media print {{ nav {{ display:none; }} main {{ margin:0; padding:0; max-width:none; }}
  section {{ break-before:page; border:0; padding:0; }} .diagram {{ border:0; padding:0; break-inside:avoid; }}
  .diagram svg {{ width:100%; height:auto; max-height:160mm; display:block; margin:0 auto; }}
  h1, h2 {{ break-after:avoid; }} tr {{ break-inside:avoid; }} body {{ font-size:12px; }} }}
</style></head><body>
<nav><b>CC ARCHITECTURE</b>{''.join(nav)}</nav>
<main>{''.join(sections)}</main></body></html>""")
    return out


def build_pdf(html_path: Path) -> None:
    script = f"""
const {{ chromium }} = require('playwright');
(async () => {{
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto('file://{html_path}');
  await page.pdf({{ path: '{html_path.with_suffix(".pdf")}', format: 'A4', landscape: true, printBackground: true,
                   margin: {{ top: '12mm', bottom: '12mm', left: '12mm', right: '12mm' }} }});
  await browser.close();
}})();"""
    subprocess.run(["node", "-e", script], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-render", action="store_true")
    args = parser.parse_args()
    diagrams = sorted(p.stem for p in (HERE / "src").glob("*.drawio"))
    if not args.no_render:
        for diagram_id in diagrams:
            render(diagram_id)
    merge_drawio(diagrams)
    service_catalog()
    html_path = build_html(page_ids())
    build_pdf(html_path)
    print(f"{len(diagrams)} diagrams, {len(page_ids())} pages -> {html_path.name}, {html_path.with_suffix('.pdf').name}")


if __name__ == "__main__":
    main()
