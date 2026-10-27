"""Enhance the original icon canvas (diagrams/CC_Data_Backend_uncompressed.drawio) and write
docs/architecture/ClaimClarity_Architecture.drawio.

The main board ("ov") is kept as drawn, with these edits:
- ClaimClarity naming throughout;
- official AWS icon colours;
- the API backend is ECS on Fargate, and the Claim Query Agent sits inside it as the run worker;
- DuckDB moves out into its own query service (DuckDB Quack server), which now does the STS exchange.

The original zoom boards 00–03 are replaced by tools/boards.py (00–07), and zoom badge 04 is added.
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
swap("ov_api", ("Verifies token, gets creds", "Verifies token, queues the run"))
# sign-in: Cognito maps the Entra ID group to one customer (session handling is IT's design)
swap("ov_cog", ("Issues token", "Maps group → customer"))
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

# the query audit: an audit/ row in the bucket, its JSON Lines record, and an edge from the query service that writes it
geometry("ov_zs3r", height=440)
def add_cell(cid, value_text, style, x, y, w, h, parent="ov"):
    c = ET.SubElement(graph_root, "mxCell", id=cid, value=value_text, style=style, vertex="1", parent=parent)
    ET.SubElement(c, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), **{"as": "geometry"})
add_cell("ov_aud", "customer_b/audit/ · one JSON line per SQL", cells["ov_s3b"].get("style"), 2020, 1432, 360, 44)
add_cell("ov_aud_json", '{"ts":"2026-10-27T14:03:11Z",<br>&nbsp;"customer":"customer_b","user":"ana@xifin.example",<br>'
         '&nbsp;"run_id":"r_7f3c","seq":3,<br>&nbsp;"sql":"SELECT payer_name, … (first 500 chars)",<br>'
         '&nbsp;"tables":["sem.remit_claims"],"rows":12,"ms":184,<br>&nbsp;"escalation_reason":null,"prev_hash":"9c1e…","hash":"4b7a…"}',
         "html=1;whiteSpace=wrap;rounded=0;fillColor=#1A1D23;strokeColor=none;fontColor=#E6E8EB;fontFamily=IBM Plex Mono;"
         "fontSize=10;align=left;verticalAlign=top;spacingLeft=10;spacingTop=6;", 2020, 1486, 360, 122)
e = ET.SubElement(graph_root, "mxCell", id="ov_e_aud", value="", edge="1", parent="ov", source="ov_duck", target="ov_aud",
                  style=re.sub(r"(exit|entry)[XY]=[^;]*;|(exit|entry)D[xy]=[^;]*;", "", cells["ov_e_duck"].get("style"))
                  + "exitX=1;exitY=0.9;exitDx=0;exitDy=0;entryX=0;entryY=0.5;entryDx=0;entryDy=0;")
ET.SubElement(e, "mxGeometry", relative="1", **{"as": "geometry"})

text = ET.tostring(tree.getroot(), encoding="unicode")
for res, color in COLORS.items():   # official AWS icon colours
    text = text.replace(f"resIcon=mxgraph.aws4.{res};fillColor=#1C2B57;", f"resIcon=mxgraph.aws4.{res};fillColor={color};")
text = text.replace("resIcon=mxgraph.aws4.ec2;fillColor=#1C2B57;", f"resIcon=mxgraph.aws4.fargate;fillColor={COLORS['fargate']};")

# 3. new boards, zoom badge 04 and zoom links to every board
extra = boards()
zoom(extra, "ov_z04", "04", 1592, 1312, "ov")   # Claim Query Agent
link_style = re.search(r'<mxCell id="zl_00"[^>]*style="([^"]*)"', text).group(1)
for n in range(4, 5):
    extra.append(f'<mxCell id="zl_0{n}" value="" style="{link_style}" edge="1" parent="1" source="ov_z0{n}" target="s{n}">'
                 f'<mxGeometry relative="1" as="geometry"/></mxCell>')
text = text.replace("</root>", "".join(extra) + "</root>", 1)
assert "CC " not in re.sub(r"<[^>]+>", " ", text.replace("&lt;", "<").replace("&gt;", ">")), "a CC label is left"
OUT.write_text(text)
print("wrote", OUT, "boards:", ", ".join(LAYOUT))
