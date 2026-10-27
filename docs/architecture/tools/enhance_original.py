"""Enhance the original icon drawio (diagrams/CC_Data_Backend_uncompressed.drawio) in place, cell by cell, and add the
analyzer page. Writes v2/ClaimClarity_Architecture.drawio."""
import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ORIGINAL = ROOT / "diagrams" / "CC_Data_Backend_uncompressed.drawio"
OUT = ROOT / "docs" / "architecture" / "ClaimClarity_Architecture.drawio"

text = ORIGINAL.read_text()

def set_value(cell_id, change):
    global text
    pattern = re.compile(r'(<mxCell id="%s" value=")([^"]*)(")' % re.escape(cell_id))
    match = pattern.search(text)
    assert match, cell_id
    old = html.unescape(match.group(2))
    new = change(old)
    assert new != old, cell_id
    text = text[:match.start(2)] + html.escape(new, quote=True) + text[match.end(2):]

def swap(cell_id, *pairs):
    def change(value):
        for a, b in pairs:
            assert a in value, (cell_id, a)
            value = value.replace(a, b)
        return value
    set_value(cell_id, change)

swap("ov_h", ("CC · Data backend", "ClaimClarity · Data backend"))
swap("page_title", ("CC Data Backend", "ClaimClarity Architecture"))
set_value("ov_h_t", lambda v: "")
for cid in ("ov_l_bkt", "ov_l_s3r", "s0_l_b", "s3_l2"):
    swap(cid, ("cc/", "claimclarity/"))
for cid in ("ov_l_ec2", "s3_l1"):
    swap(cid, ("EC2", "ECS on Fargate"))
for cid in ("ov_agent", "s3_agent"):
    swap(cid, ("Claude agent", "Analyzer"),
         ("Bedrock · BAA", 'KPI gate + agent</span><br><span style="font-size:12px;color:#FFFFFF">Claude Haiku · Bedrock'))
swap("ov_duck", ("per request", "per run"))
swap("s3_duck", ("per request", "per run"))
swap("s3_comp", ("Metric compiler", "cc CLI + SQL guard"), ("Writes SQL over semantic_layer/ tables", "Preset KPI SQL or agent SQL · read-only"))
swap("s3_e7", ("Metrics by name", "preset or agent SQL"))
# zoom badge 04 on the analyzer, same style as 00-03, pointing to the new analyzer page
badge = re.search(r'<mxCell id="ov_z00".*?</mxCell>', text, re.S).group(0)
badge04 = (badge.replace('id="ov_z00"', 'id="ov_z04"').replace('value="00"', 'value="04"')
           .replace('x="1207" y="307"', 'x="1608" y="1316"'))
text = text.replace(badge, badge + badge04, 1)
# the API backend runs on Fargate: swap its icons
text = text.replace("resIcon=mxgraph.aws4.ec2;", "resIcon=mxgraph.aws4.fargate;")

import sys
sys.path.insert(0, str(Path(__file__).parent))
from boards import boards
from olib import COLORS, zoom
# official AWS colours for every icon on the original boards
for res, color in COLORS.items():
    text = text.replace(f"resIcon=mxgraph.aws4.{res};fillColor=#1C2B57;", f"resIcon=mxgraph.aws4.{res};fillColor={color};")
extra = boards()
zoom(extra, "ov_z05", "05", 1255, 1030, "ov")   # chat backend, on the API backend box
text = text.replace("</root>", "".join(extra) + "</root>", 1)
OUT.write_text(text)
print("wrote", OUT)
