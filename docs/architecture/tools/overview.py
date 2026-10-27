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

v("title", "<b style='font-size:20px'>CLAIMCLARITY · ARCHITECTURE OVERVIEW</b>&nbsp;&nbsp;&nbsp;claims analytics chat for clinical labs · numbers link to the area pages", TEXT + "fontColor=#1C2B57;", 20, 14, 1100, 36)
v("grp-aws", "", GROUP, 320, 80, 1240, 830)
v("lbl-aws", "<b>AWS ACCOUNT · CLAIMCLARITY</b>&nbsp; all tasks on Amazon ECS on AWS Fargate in private subnets", "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#1A1D23;fontFamily=Helvetica;", 334, 88, 700, 24)

v("ext-labs", box(1, "CUSTOMER LABS", "Daily 837 · 835 · 277 files over SFTP"), EXT, 40, 140, 230, 72)
v("ext-users", box(2, "XIFIN USERS", "Browser · React chat app"), EXT, 40, 484, 230, 72)
v("ext-entra", box(3, "MICROSOFT ENTRA ID", "Company sign-in"), EXT, 40, 664, 230, 72)

v("svc-ingest", box(4, "INGESTION &amp; LAKE", "Transfer Family → raw → parser task (Fargate)<br>→ lossless (silver) → semantic (gold) · gates 1–3"), SVC, 380, 136, 390, 80)
v("phi-bucket", "", PHI, 860, 116, 660, 120)
v("svc-bucket", box(5, "CLAIMCLARITY BUCKET (S3)", "One prefix, one KMS key and one IAM role per customer<br>raw · silver · gold · conversations · run events · audit"), SVC, 880, 136, 620, 80)
v("lbl-phi", "PHI BOUNDARY · per customer", "text;html=1;align=left;verticalAlign=middle;fontSize=10;fontColor=#1C2B57;fontFamily=Helvetica;fontStyle=1;", 866, 238, 260, 18)

v("svc-edge", box(6, "EDGE", "CloudFront + WAF · static app from S3<br>load balancer → chat API"), SVC, 380, 480, 270, 80)
v("svc-chat", box(7, "CHAT BACKEND", "Chat API · SQS run queue · run workers<br>streams each answer (SSE)"), SVC, 710, 480, 300, 80)
v("agt-analyzer", "<b>8 · ANALYZER</b><br>Strands agent in the run worker<br>KPI gate → standard KPI or custom analysis", AGENT, 1060, 466, 260, 108)
v("svc-bedrock", box(9, "AMAZON BEDROCK", "Claude Haiku"), SVC, 1370, 480, 160, 80)
v("svc-identity", box(10, "IDENTITY &amp; ACCESS", "Cognito id token names the customer<br>STS → that customer's role, 15 min"), SVC, 710, 655, 300, 90)

v("svc-netsec", box(11, "NETWORK &amp; SECURITY", "Private subnets · VPC endpoints · KMS per customer<br>CloudTrail · hash-chained agent audit"), SVC, 380, 800, 540, 80)
v("svc-ops", box(12, "OPERATIONS", "ECR · CI deploys · CloudWatch alarms<br>regression set re-run on every analyzer change"), SVC, 980, 800, 550, 80)

e("e-labs-ingest", "ext-labs", "svc-ingest", "SFTP", (1, .5), (0, .5))
e("e-ingest-bucket", "svc-ingest", "svc-bucket", "write", (1, .5), (0, .5))
e("e-users-edge", "ext-users", "svc-edge", "HTTPS", (1, .5), (0, .5))
e("e-users-entra", "ext-users", "ext-entra", "sign in", (.5, 1), (.5, 0))
e("e-entra-identity", "ext-entra", "svc-identity", "federation", (1, .5), (0, .5))
e("e-edge-chat", "svc-edge", "svc-chat", "", (1, .5), (0, .5))
e("e-chat-identity", "svc-chat", "svc-identity", "verify · creds", (.5, 1), (.5, 0), both=True)
e("e-chat-analyzer", "svc-chat", "agt-analyzer", "", (1, .5), (0, .5))
e("e-analyzer-bedrock", "agt-analyzer", "svc-bedrock", "", (1, .5), (0, .5))
e("e-analyzer-bucket", "agt-analyzer", "svc-bucket", "read gold", (.5, 0), (.5, 1))

v("gate-4", "4", GATE, 905, 590, 30, 30)
v("gate-4-lbl", "Token valid? · 5 Trust allows?", "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#1C2B57;fontFamily=Helvetica;", 941, 590, 200, 30)
v("gate-6", "6", GATE, 1225, 330, 30, 30)
v("gate-6-lbl", "IAM + KMS allow?", "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#1C2B57;fontFamily=Helvetica;", 1261, 330, 140, 30)

lx, ly = 900, 940
for i, (style, w, h, label) in enumerate([(AGENT, 26, 18, "AGENT"), (SVC.replace("spacingLeft=14;", ""), 26, 18, "SERVICE"), (EXT, 26, 18, "EXTERNAL"),
                                          (PHI, 26, 18, "PHI BOUNDARY"), (GATE, 20, 20, "AWS GATE")]):
    v(f"leg-{i}", "", style, lx + i * 130, ly, w, h)
    v(f"leg-{i}-t", f"<b>{label}</b>", "text;html=1;align=left;verticalAlign=middle;fontSize=10;fontColor=#1A1D23;fontFamily=Helvetica;", lx + i * 130 + w + 6, ly - 2, 96, 22)

xml = ('<mxfile host="drawio"><diagram id="00-overview" name="Overview"><mxGraphModel dx="1600" dy="1000" grid="0" gridSize="10" '
       'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1600" pageHeight="1000" '
       'math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
Path(__file__).resolve().parent.parent.joinpath("src", "00-overview.drawio").write_text(xml)
