"""Tiny drawio writer: AWS icons, shapes and straight edges, with ClaimClarity's colours."""
from xml.sax.saxutils import escape

FONT = "fontFamily=Helvetica;fontSize=11;fontColor=#232F3E;html=1;"
LABEL = {"below": "verticalLabelPosition=bottom;verticalAlign=top;align=center;",
         "above": "verticalLabelPosition=top;verticalAlign=bottom;align=center;",
         "right": "labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingLeft=4;",
         "left": "labelPosition=left;verticalLabelPosition=middle;align=right;verticalAlign=middle;spacingRight=4;"}
COLORS = {"s3": "#7AA116", "transfer_family": "#7AA116", "key_management_service": "#DD344C", "sts": "#DD344C",
          "cognito": "#DD344C", "waf": "#DD344C", "identity_and_access_management": "#DD344C", "eventbridge": "#E7157B",
          "sqs": "#E7157B", "cloudwatch_2": "#E7157B", "cloudtrail": "#E7157B", "fargate": "#ED7100", "ecs": "#ED7100",
          "ecr": "#ED7100", "cloudfront": "#8C4FFF", "elastic_load_balancing": "#8C4FFF", "vpc": "#8C4FFF", "bedrock": "#01A88D"}
EXT = "rounded=0;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#8A8E95;dashed=1;fontColor=#1A1D23;align=center;verticalAlign=middle;fontSize=11;fontFamily=Helvetica;"
BOX = "rounded=0;whiteSpace=wrap;html=1;fillColor=#EAF0F9;strokeColor=#4A6BB0;fontColor=#1A1D23;align=center;verticalAlign=middle;fontSize=11;fontFamily=Helvetica;"
AGENT = "shape=hexagon;perimeter=hexagonPerimeter2;size=0.12;whiteSpace=wrap;html=1;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;fontFamily=Helvetica;fontSize=11;"
PHI = "rounded=0;html=1;fillColor=none;strokeColor=#1C2B57;dashed=1;dashPattern=6 4;strokeWidth=1.5;align=left;verticalAlign=top;spacingLeft=10;spacingTop=4;fontSize=11;fontColor=#1C2B57;fontFamily=Helvetica;"
TEXT = "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#1A1D23;fontFamily=Helvetica;whiteSpace=wrap;"
LANE = "text;html=1;align=left;verticalAlign=middle;fontSize=12;fontColor=#1C2B57;fontStyle=1;fontFamily=Helvetica;"
STEP = "ellipse;html=1;fillColor=#1C2B57;strokeColor=none;fontColor=#FFFFFF;fontStyle=1;fontSize=11;fontFamily=Helvetica;aspect=fixed;"
CYL = "shape=cylinder3;whiteSpace=wrap;html=1;boundedLbl=1;backgroundOutline=1;size=8;fillColor=#EAF0F9;strokeColor=#4A6BB0;fontSize=11;fontFamily=Helvetica;"
NOTE = "shape=note;size=10;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#4A6BB0;fontSize=10;fontFamily=Helvetica;"
FOLDER = "shape=mxgraph.aws4.folder;html=1;fillColor=#4A6BB0;strokeColor=none;aspect=fixed;" + FONT
EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;orthogonalLoop=1;jettySize=auto;html=1;strokeColor=#4A6BB0;strokeWidth=1.5;endArrow=block;endFill=1;endSize=6;fontSize=11;fontColor=#3A5A9E;labelBackgroundColor=#FFFFFF;fontFamily=Helvetica;"


def group(kind):
    icon, stroke, fill, font = {"cloud": ("group_aws_cloud_alt", "#232F3E", "none", "#232F3E"),
                                "vpc": ("group_vpc2", "#8C4FFF", "none", "#8C4FFF"),
                                "subnet": ("group_security_group", "#00A4A6", "#E6F6F7", "#147EBA"),
                                "region": ("group_region", "#00A4A6", "none", "#147EBA")}[kind]
    dashed = "dashed=1;" if kind == "region" else "dashed=0;"
    return (f"points=[[0,0]];outlineConnect=0;html=1;whiteSpace=wrap;fontSize=12;shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.{icon};"
            f"strokeColor={stroke};fillColor={fill};verticalAlign=top;align=left;spacingLeft=30;fontColor={font};{dashed}fontFamily=Helvetica;"
            + ("grStroke=0;" if kind == "subnet" else ""))


class Diagram:
    def __init__(self, diagram_id, name, width, height):
        self.id, self.name, self.width, self.height, self.cells = diagram_id, name, width, height, []

    def add(self, cid, value, style, x, y, w, h):
        self.cells.append(f'<mxCell id="{cid}" value="{escape(value, {chr(34): "&quot;"})}" style="{style}" vertex="1" parent="1">'
                          f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return cid

    def icon(self, cid, res, title, line, cx, cy, label="below", size=52, tile=True, color=None):
        color = color or COLORS.get(res, "#232F3E")
        shape = (f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{res};strokeColor=#ffffff;" if tile
                 else f"shape=mxgraph.aws4.{res};strokeColor=none;")
        value = f"<b>{title}</b>" + (f"<br>{line}" if line else "")
        return self.add(cid, value, f"sketch=0;outlineConnect=0;dashed=0;aspect=fixed;fillColor={color};{shape}{FONT}{LABEL[label]}",
                        cx - size / 2, cy - size / 2, size, size)

    def edge(self, cid, src, dst, label="", exit_=(1, .5), entry=(0, .5), both=False, dashed=False, waypoints=()):
        style = EDGE + (f"exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0;entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
                        + ("startArrow=block;startFill=1;" if both else "") + ("dashed=1;" if dashed else ""))
        points = "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in waypoints)
        geometry = f'<mxGeometry relative="1" as="geometry">{"<Array as=" + chr(34) + "points" + chr(34) + ">" + points + "</Array>" if points else ""}</mxGeometry>'
        self.cells.append(f'<mxCell id="{cid}" value="{escape(label)}" style="{style}" edge="1" parent="1" source="{src}" target="{dst}">{geometry}</mxCell>')

    def line(self, cid, x1, y1, x2, y2, label="", dashed=False, arrow=True):
        style = EDGE + ("dashed=1;" if dashed else "") + ("" if arrow else "endArrow=none;")
        self.cells.append(f'<mxCell id="{cid}" value="{escape(label)}" style="{style}" edge="1" parent="1"><mxGeometry relative="1" as="geometry">'
                          f'<mxPoint x="{x1}" y="{y1}" as="sourcePoint"/><mxPoint x="{x2}" y="{y2}" as="targetPoint"/></mxGeometry></mxCell>')

    def title(self, text, sub=""):
        self.add("title", f"<b style='font-size:20px'>{text}</b>" + (f"&nbsp;&nbsp;&nbsp;{sub}" if sub else ""),
                 LANE + "fontStyle=0;", 20, 14, self.width - 40, 36)

    def xml(self):
        return (f'<mxfile host="drawio"><diagram id="{self.id}" name="{escape(self.name)}"><mxGraphModel dx="{self.width}" dy="{self.height}" grid="0" '
                f'gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{self.width}" '
                f'pageHeight="{self.height}" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
                + "".join(self.cells) + "</root></mxGraphModel></diagram></mxfile>\n")


STRIP = "rounded=1;arcSize=3;html=1;fillColor=#F6F8FC;strokeColor=#D5DCE8;"
BADGE = "ellipse;html=1;fillColor=#1C2B57;strokeColor=#FFFFFF;strokeWidth=2;fontColor=#FFFFFF;fontStyle=1;fontSize=10;fontFamily=Helvetica;aspect=fixed;"
DECISION = "rhombus;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#1C2B57;strokeWidth=1.5;fontSize=11;fontFamily=Helvetica;fontColor=#1A1D23;"
STEPBOX = "rounded=1;arcSize=12;whiteSpace=wrap;html=1;fillColor=#EAF0F9;strokeColor=#4A6BB0;fontSize=11;fontFamily=Helvetica;fontColor=#1A1D23;"


def strip(d, key, y, h, title, width=None):
    d.add(f"{key}", "", STRIP, 20, y, (width or d.width) - 40, h)
    d.add(f"{key}-t", title, LANE, 36, y + 6, 1000, 24)


def badge(d, cid, page, cx, cy):
    """Small navy circle with the drill-down page number, at an item's top right."""
    d.add(cid, page, BADGE, cx, cy, 22, 22)


def chain(d, prefix, items, y, x0=120, step=200):
    """items: (res, title, page or None, tile) in a row, joined by arrows. Returns the ids."""
    ids = []
    for i, item in enumerate(items):
        res, title, page, tile = item[:4]
        label = item[4] if len(item) > 4 else "below"
        cid, cx = f"{prefix}{i}", x0 + i * step
        d.icon(cid, res, title, "", cx, y, tile=tile, label=label)
        if page:
            badge(d, f"{cid}-b", page, *((cx + 20, y - 42) if label != "above" else (cx + 30, y - 58)))
        if ids:
            d.edge(f"e-{ids[-1]}-{cid}", ids[-1], cid)
        ids.append(cid)
    return ids


def legend_drilldown(d, x, y):
    badge(d, "leg-b", "11", x, y)
    d.add("leg-b-t", "page with the detail", TEXT + "fontColor=#3A5A9E;", x + 28, y - 2, 200, 26)
