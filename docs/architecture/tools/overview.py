"""Writes src/00-overview.drawio (the L0 diagram): AWS icons, three lanes, every edge straight or one elbow."""
from pathlib import Path
from xml.sax.saxutils import escape

FONT = "fontFamily=Helvetica;fontSize=11;fontColor=#232F3E;html=1;"
EXT = "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#8A8E95;dashed=1;fontColor=#1A1D23;align=center;verticalAlign=middle;fontSize=11;fontFamily=Helvetica;"
PHI = "rounded=0;html=1;fillColor=none;strokeColor=#1C2B57;dashed=1;dashPattern=6 4;strokeWidth=1.5;align=left;verticalAlign=top;spacingLeft=34;spacingTop=4;fontSize=11;fontColor=#1C2B57;fontFamily=Helvetica;"
CLOUD = "points=[[0,0]];outlineConnect=0;html=1;whiteSpace=wrap;fontSize=12;container=0;shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;verticalAlign=top;align=left;spacingLeft=30;fontColor=#232F3E;dashed=0;fontFamily=Helvetica;"
AGENT = "shape=hexagon;perimeter=hexagonPerimeter2;size=0.12;whiteSpace=wrap;html=1;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;fontFamily=Helvetica;fontSize=11;"
LANE = "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#3A5A9E;fontStyle=1;fontFamily=Helvetica;"
EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#4A6BB0;strokeWidth=1.5;endArrow=block;endFill=1;endSize=6;fontSize=11;fontColor=#3A5A9E;labelBackgroundColor=#FFFFFF;fontFamily=Helvetica;"
LABEL_AT = {"below": "verticalLabelPosition=bottom;verticalAlign=top;align=center;",
            "above": "verticalLabelPosition=top;verticalAlign=bottom;align=center;",
            "right": "labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingLeft=4;"}

cells = []
def add(cid, value, style, x, y, w, h):
    cells.append(f'<mxCell id="{cid}" value="{escape(value, {chr(34): "&quot;"})}" style="{style}" vertex="1" parent="1"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
def icon(cid, n, title, line, res, color, cx, cy, label="below", tile=True, size=56):
    shape = (f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{res};strokeColor=#ffffff;" if tile
             else f"shape=mxgraph.aws4.{res};strokeColor=none;")
    add(cid, f"<b>{n} · {title}</b><br>{line}", f"sketch=0;outlineConnect=0;dashed=0;aspect=fixed;fillColor={color};{shape}{FONT}{LABEL_AT[label]}",
        cx - size / 2, cy - size / 2, size, size)
def edge(cid, src, dst, label, exit_, entry, both=False):
    style = EDGE + f"exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0;entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;" + ("startArrow=block;startFill=1;" if both else "")
    cells.append(f'<mxCell id="{cid}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{src}" target="{dst}"><mxGeometry relative="1" as="geometry"/></mxCell>')

add("title", "<b style='font-size:20px'>CLAIMCLARITY · ARCHITECTURE OVERVIEW</b>&nbsp;&nbsp;&nbsp;numbers link to the area pages", LANE + "fontColor=#1C2B57;fontStyle=0;fontSize=12;", 20, 14, 900, 36)
add("grp-cloud", "AWS Cloud · ClaimClarity account", CLOUD, 360, 70, 1010, 690)
add("lane-data", "DATA IN", LANE, 20, 140, 90, 20)
add("lane-q", "QUESTIONS", LANE, 20, 490, 90, 20)
add("lane-signin", "SIGN-IN", LANE, 20, 650, 90, 20)

# data in
icon("ext-labs", 1, "Customer labs", "daily 837 · 835 · 277", "corporate_data_center", "#232F3E", 200, 150, tile=False)
icon("svc-sftp", 2, "SFTP intake", "AWS Transfer Family", "transfer_family", "#7AA116", 480, 150, label="above")
icon("svc-parser", 3, "Parser task", "Fargate · one task per batch", "fargate", "#ED7100", 1000, 150, label="above")
# the bucket: one PHI boundary per customer, three prefixes
add("phi-bucket", "<b>4 · ClaimClarity bucket (S3)</b><br>per customer: own prefix, KMS key and IAM role", PHI.replace("spacingLeft=34", "spacingLeft=150"), 400, 252, 900, 128)
icon("ico-s3", "", "", "", "s3", "#7AA116", 534, 274, tile=True, size=26)
cells[-1] = cells[-1].replace('value="&lt;b&gt; · &lt;/b&gt;&lt;br&gt;"', 'value=""')
FOLDER = "shape=mxgraph.aws4.folder;html=1;fillColor=#4A6BB0;strokeColor=none;aspect=fixed;" + FONT + LABEL_AT["right"]
add("fld-raw", "<b>raw</b><br>files as received", FOLDER, 462, 320, 36, 30)
add("fld-chat", "<b>chat state</b><br>conversations · run events", FOLDER, 702, 320, 36, 30)
add("fld-lake", "<b>lake</b><br>silver · gold (sem.*)", FOLDER, 982, 320, 36, 30)
# questions
icon("ext-users", 5, "XiFin users", "browser chat app", "users", "#232F3E", 200, 500, label="above", tile=False)
icon("svc-edge", 6, "CloudFront + WAF", "web app and API entry", "cloudfront", "#8C4FFF", 480, 500)
icon("svc-chat", 7, "Chat API", "ECS on Fargate", "ecs", "#ED7100", 720, 500)
add("agt-analyzer", "<b>8 · ANALYZER</b><br>in the run workers<br>KPI gate → KPI or custom", AGENT, 905, 460, 190, 80)
icon("svc-bedrock", 9, "Amazon Bedrock", "Claude Haiku", "bedrock", "#01A88D", 1240, 500)
# sign-in
icon("svc-cognito", 10, "Amazon Cognito", "token names the customer", "cognito", "#DD344C", 480, 660, label="right")
add("ext-entra", "<b>11 · Microsoft Entra ID</b><br>company sign-in", EXT, 390, 790, 180, 50)
# across everything
add("lane-all", "ACROSS<br>EVERYTHING", LANE, 20, 905, 90, 40)
icon("svc-netsec", 12, "Network &amp; security", "private subnets · KMS · audit", "vpc", "#8C4FFF", 480, 925, label="right", size=48)
icon("svc-ops", 13, "Operations", "deploys · monitoring · tests", "cloudwatch_2", "#E7157B", 820, 925, label="right", size=48)

edge("e-labs-sftp", "ext-labs", "svc-sftp", "SFTP", (1, .5), (0, .5))
edge("e-sftp-parser", "svc-sftp", "svc-parser", "batch ready", (1, .5), (0, .5))
edge("e-sftp-raw", "svc-sftp", "fld-raw", "", (.5, 1), (.5, 0))
edge("e-parser-lake", "svc-parser", "fld-lake", "", (.5, 1), (.5, 0))
edge("e-users-edge", "ext-users", "svc-edge", "HTTPS", (1, .5), (0, .5))
edge("e-edge-chat", "svc-edge", "svc-chat", "", (1, .5), (0, .5))
edge("e-chat-state", "svc-chat", "fld-chat", "", (.5, 0), (.5, 1))
edge("e-chat-analyzer", "svc-chat", "agt-analyzer", "", (1, .5), (0, .5))
edge("e-analyzer-lake", "agt-analyzer", "fld-lake", "read gold", (.5, 0), (.5, 1))
edge("e-analyzer-bedrock", "agt-analyzer", "svc-bedrock", "", (1, .5), (0, .5))
edge("e-users-cognito", "ext-users", "svc-cognito", "sign in", (.5, 1), (0, .5), both=True)
edge("e-cognito-entra", "svc-cognito", "ext-entra", "federation", (.5, 1), (.5, 0), both=True)

xml = ('<mxfile host="drawio"><diagram id="00-overview" name="Overview"><mxGraphModel dx="1400" dy="900" grid="0" gridSize="10" '
       'guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1400" pageHeight="980" '
       'math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>\n")
Path(__file__).resolve().parent.parent.joinpath("src", "00-overview.drawio").write_text(xml)
