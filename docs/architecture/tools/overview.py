"""Writes src/00-overview.drawio (the L0 diagram). Hand-placed: ~11 boxes, 10 edges."""
from pathlib import Path
from xml.sax.saxutils import escape

SVC = "rounded=0;whiteSpace=wrap;html=1;fillColor=#EAF0F9;strokeColor=#4A6BB0;fontColor=#1A1D23;align=left;spacingLeft=14;spacingRight=10;verticalAlign=middle;fontSize=12;fontFamily=Helvetica;"
EXT = "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#8A8E95;dashed=1;fontColor=#1A1D23;align=left;spacingLeft=14;spacingRight=10;verticalAlign=middle;fontSize=12;fontFamily=Helvetica;"
GROUP = "rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#4A6BB0;strokeWidth=1.5;"
PHI = "rounded=0;whiteSpace=wrap;html=1;fillColor=none;strokeColor=#1C2B57;dashed=1;dashPattern=6 4;strokeWidth=1.5;"
AGENT = "shape=hexagon;perimeter=hexagonPerimeter2;size=0.12;whiteSpace=wrap;html=1;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;fontFamily=Helvetica;fontSize=11;"
GATE = "rhombus;html=1;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;fontStyle=1;fontSize=13;fontFamily=Helvetica;"
TEXT = "text;html=1;align=left;verticalAlign=middle;fontSize=12;fontColor=#1C2B57;fontFamily=Helvetica;"
EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#4A6BB0;strokeWidth=1.5;endArrow=block;endFill=1;endSize=6;fontSize=11;fontColor=#3A5A9E;labelBackgroundColor=#FFFFFF;fontFamily=Helvetica;"

cells = []
def v(cid, value, style, x, y, w, h):
    cells.append(f'<mxCell id="{cid}" value="{escape(value, {chr(34): "&quot;"})}" style="{style}" vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
def e(cid, src, dst, label, exit_, entry, both=False):
    style = EDGE + f"exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0;entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;" + ("startArrow=block;startFill=1;" if both else "")
    cells.append(f'<mxCell id="{cid}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{src}" target="{dst}"><mxGeometry relative="1" as="geometry"/></mxCell>')
def box(n, title, lines): return f"<b>{n} · {title}</b><br>{lines}"

v("title", "<b style='font-size:20px'>CLAIMCLARITY · ARCHITECTURE OVERVIEW</b>&nbsp;&nbsp;&nbsp;numbers link to the area pages", TEXT, 20, 14, 900, 36)
LANE = "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#3A5A9E;fontStyle=1;fontFamily=Helvetica;"
v("grp-aws", "", GROUP, 380, 70, 1030, 650)
v("lbl-aws", "<b>AWS · CLAIMCLARITY ACCOUNT</b>", "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#1A1D23;fontFamily=Helvetica;", 394, 76, 400, 22)
v("lane-data", "DATA IN", LANE, 20, 132, 90, 20)
v("lane-q", "QUESTIONS", LANE, 20, 472, 90, 20)
v("lane-signin", "SIGN-IN", LANE, 20, 652, 90, 20)

v("ext-labs", box(1, "CUSTOMER LABS", "daily 837 · 835 · 277 files"), EXT, 130, 110, 210, 64)
v("svc-sftp", box(2, "SFTP INTAKE", "AWS Transfer Family"), SVC, 420, 110, 220, 64)
v("svc-parser", box(3, "PARSER TASK", "ECS on Fargate · one per batch"), SVC, 800, 110, 240, 64)
v("phi-bucket", "", PHI, 410, 252, 760, 90)
v("svc-bucket", box(4, "CLAIMCLARITY BUCKET (S3)", "lake and chat state · own prefix and key per customer"), SVC, 420, 262, 740, 70)

v("ext-users", box(5, "XIFIN USERS", "browser chat app"), EXT, 130, 450, 210, 64)
v("svc-edge", box(6, "CLOUDFRONT + WAF", "web app and API entry"), SVC, 420, 450, 200, 64)
v("svc-chat", box(7, "CHAT API", "ECS on Fargate · queues runs"), SVC, 680, 450, 210, 64)
v("agt-analyzer", "<b>8 · ANALYZER</b><br>runs in the run workers", AGENT, 950, 442, 200, 80)
v("svc-bedrock", box(9, "AMAZON BEDROCK", "Claude Haiku"), SVC, 1210, 450, 180, 64)

v("ext-entra", box(10, "MICROSOFT ENTRA ID", "company sign-in"), EXT, 130, 630, 210, 64)
v("svc-cognito", box(11, "AMAZON COGNITO", "issues the customer token"), SVC, 680, 630, 210, 64)

v("lane-all", "ACROSS<br>EVERYTHING", LANE, 20, 808, 90, 40)
v("svc-netsec", box(12, "NETWORK &amp; SECURITY", "private subnets · KMS · audit"), SVC, 420, 790, 320, 56)
v("svc-ops", box(13, "OPERATIONS", "deploys · monitoring · regression tests"), SVC, 780, 790, 320, 56)

e("e-labs-sftp", "ext-labs", "svc-sftp", "SFTP", (1, .5), (0, .5))
e("e-sftp-bucket", "svc-sftp", "svc-bucket", "raw files", (.5, 1), (.1486, 0))
e("e-parser-bucket", "svc-parser", "svc-bucket", "silver · gold", (.5, 1), (.6757, 0), both=True)
e("e-users-edge", "ext-users", "svc-edge", "HTTPS", (1, .5), (0, .5))
e("e-edge-chat", "svc-edge", "svc-chat", "", (1, .5), (0, .5))
e("e-chat-analyzer", "svc-chat", "agt-analyzer", "", (1, .5), (0, .5))
e("e-analyzer-bedrock", "agt-analyzer", "svc-bedrock", "", (1, .5), (0, .5))
e("e-analyzer-bucket", "agt-analyzer", "svc-bucket", "read gold", (.5, 0), (.8514, 1))
e("e-users-entra", "ext-users", "ext-entra", "sign in", (.5, 1), (.5, 0))
e("e-entra-cognito", "ext-entra", "svc-cognito", "federation", (1, .5), (0, .5))
e("e-cognito-chat", "svc-cognito", "svc-chat", "token", (.5, 0), (.5, 1))

lx, ly = 760, 880
for i, (style, w, h, label) in enumerate([(AGENT, 26, 18, "AGENT"), (SVC.replace("spacingLeft=14;", ""), 26, 18, "SERVICE"), (EXT, 26, 18, "EXTERNAL"),
                                          (PHI, 26, 18, "PHI BOUNDARY")]):
    v(f"leg-{i}", "", style, lx + i * 130, ly, w, h)
    v(f"leg-{i}-t", f"<b>{label}</b>", "text;html=1;align=left;verticalAlign=middle;fontSize=10;fontColor=#1A1D23;fontFamily=Helvetica;", lx + i * 130 + w + 6, ly - 2, 96, 22)

xml = ('<mxfile host="drawio"><diagram id="00-overview" name="Overview"><mxGraphModel dx="1600" dy="1000" grid="0" gridSize="10" '
       'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1420" pageHeight="920" '
       'math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
Path(__file__).resolve().parent.parent.joinpath("src", "00-overview.drawio").write_text(xml)
