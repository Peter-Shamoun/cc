"""Enhance the original icon canvas (diagrams/CC_Data_Backend_uncompressed.drawio) and write
docs/architecture/ClaimClarity_Architecture.drawio.

The main board ("ov") is kept as drawn, with these edits:
- ClaimClarity naming throughout;
- official AWS icon colours;
- the API backend is ECS on Fargate, and the Claim Query Agent sits inside it as the run worker;
- DuckDB moves out into its own query service (DuckDB Quack server), which now does the STS exchange.

The original zoom boards 00–03 are replaced by tools/boards.py (00–07), and zoom badges 04–07 are added.
"""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from boards import LAYOUT, boards  # noqa: E402
from olib import COLORS, caps, zoom  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
ORIGINAL = ROOT / "diagrams" / "CC_Data_Backend_uncompressed.drawio"
OUT = ROOT / "docs" / "architecture" / "ClaimClarity_Architecture.drawio"

tree = ET.parse(ORIGINAL)
graph_root = tree.getroot().find("./diagram/mxGraphModel/root")
cells = {c.get("id"): c for c in graph_root}

# 1. drop the original zoom boards 00-03 and everything inside them (boards.py redraws them)
parents = {cid: c.get("parent") for cid, c in cells.items()}
def inside(cid, board):
    while cid:
        if cid == board:
            return True
        cid = parents.get(cid)
    return False
for cid in [cid for cid in cells if any(inside(cid, b) for b in ("s0", "s1", "s2", "s3"))]:
    graph_root.remove(cells.pop(cid))

def value(cid, text):
    cells[cid].set("value", text)

def geometry(cid, **changes):
    g = cells[cid].find("mxGeometry")
    for k, v in changes.items():
        g.set(k, str(v))

def swap(cid, *pairs):
    v = cells[cid].get("value")
    for a, b in pairs:
        assert a in v, (cid, a)
        v = v.replace(a, b)
    cells[cid].set("value", v)

# 2. main-board edits
swap("page_title", ("CC Data Backend", "ClaimClarity Architecture"))
swap("ov_h", ("CC · Data backend", "ClaimClarity · Data backend"))
value("ov_h_t", "")
for cid in ("ov_l_bkt", "ov_l_s3r"):
    swap(cid, ("cc/", "claimclarity/"))
swap("ov_l_ec2", ("API backend · EC2", "Chat API + run workers · ECS on Fargate"))
value("ov_agent", caps("Claim Query Agent", 17, "#FFFFFF") + '<br><span style="font-size:12px;color:#FFFFFF">run worker · Claude Haiku</span>')
value("ov_duck", caps("DuckDB query service") + '<br><span style="font-size:13px">Quack server · own Fargate task · holds B\'s creds</span>')
value("ov_sts", caps("STS") + '<br><span style="font-size:14px">15-min B creds for the query service</span>')
# DuckDB leaves the API box: shrink the box, move DuckDB and STS (with gate 5) under the query service
geometry("ov_ec2", width=500)
geometry("ov_l_ec2", width=420)
geometry("ov_duck", x=1660, y=1320, width=230, height=110)
geometry("ov_sts", x=1645, y=1520, width=260, height=70)
geometry("ov_sts_i", x=1661, y=1539)
geometry("ov_g5", x=1758, y=1460)
geometry("ov_g_sts", x=1800, y=1463, width=196)
geometry("ov_g_sts_i", x=1805, y=1467)
for edge_id in ("ov_e_sts1", "ov_e_sts2"):
    e = cells[edge_id]
    e.set("source", "ov_duck" if e.get("source") == "ov_api" else e.get("source"))
    e.set("target", "ov_duck" if e.get("target") == "ov_api" else e.get("target"))
    e.set("style", re.sub(r"(exit|entry)[XY]=[^;]*;|(exit|entry)D[xy]=[^;]*;", "", e.get("style")))

text = ET.tostring(tree.getroot(), encoding="unicode")
for res, color in COLORS.items():   # official AWS icon colours
    text = text.replace(f"resIcon=mxgraph.aws4.{res};fillColor=#1C2B57;", f"resIcon=mxgraph.aws4.{res};fillColor={color};")
text = text.replace("resIcon=mxgraph.aws4.ec2;fillColor=#1C2B57;", f"resIcon=mxgraph.aws4.fargate;fillColor={COLORS['fargate']};")

# 3. new boards, zoom badges 04-07 and zoom links to every board
extra = boards()
zoom(extra, "ov_z04", "04", 1592, 1312, "ov")   # Claim Query Agent
zoom(extra, "ov_z05", "05", 1500, 1225, "ov")   # chat API + run workers
zoom(extra, "ov_z06", "06", 1110, 1625, "ov")   # network & security, beside the legend
zoom(extra, "ov_z07", "07", 1150, 1625, "ov")   # operations
extra.append('<mxCell id="ov_z67_t" value="06 network &amp; security · 07 operations" style="html=1;text;fontFamily=Barlow;fontSize=13;'
             'fontColor=#1C2B57;align=left;verticalAlign=middle;" vertex="1" parent="ov"><mxGeometry x="1186" y="1625" width="300" height="26" '
             'as="geometry"/></mxCell>')
link_style = re.search(r'<mxCell id="zl_00"[^>]*style="([^"]*)"', text).group(1)
for n in range(4, 8):
    extra.append(f'<mxCell id="zl_0{n}" value="" style="{link_style}" edge="1" parent="1" source="ov_z0{n}" target="s{n}">'
                 f'<mxGeometry relative="1" as="geometry"/></mxCell>')
text = text.replace("</root>", "".join(extra) + "</root>", 1)
assert "CC " not in re.sub(r"<[^>]+>", " ", text.replace("&lt;", "<").replace("&gt;", ">")), "a CC label is left"
OUT.write_text(text)
print("wrote", OUT, "boards:", ", ".join(LAYOUT))
