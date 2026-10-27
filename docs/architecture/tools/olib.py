"""Cells in the original board style (diagrams/CC_Data_Backend.drawio), with official AWS icon colours."""
from xml.sax.saxutils import escape

FONTSRC = "fontSource=https%3A%2F%2Ffonts.googleapis.com%2Fcss%3Ffamily%3DBarlow;"
CONDSRC = "fontSource=https%3A%2F%2Ffonts.googleapis.com%2Fcss%3Ffamily%3DBarlow%2BCondensed%3Awght%40600;"
BASE = "html=1;whiteSpace=wrap;fontFamily=Barlow;" + FONTSRC + "fontColor=#1A1D23;"
BOARD = BASE + "rounded=0;container=1;pointerEvents=0;collapsible=0;fillColor=#FFFFFF;strokeColor=#C9CBCE;"
NUM = BASE + "fillColor=#4A6BB0;strokeColor=none;fontColor=#FFFFFF;fontFamily=Barlow Condensed;" + CONDSRC + "fontStyle=1;fontSize=16;"
ZOOM = BASE + "ellipse;fillColor=#4A6BB0;strokeColor=none;fontColor=#FFFFFF;fontFamily=Barlow Condensed;" + CONDSRC + "fontStyle=1;fontSize=13;"
TEXT = BASE + "text;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;spacing=0;"
TAG = BASE + "rounded=0;fillColor=#FFFFFF;strokeColor=#9A9CA1;fontSize=12;align=center;verticalAlign=middle;spacing=0;"
GROUP = BASE + "rounded=0;fillColor=none;strokeColor=#4A6BB0;strokeWidth=1.5;container=0;"
PHI = BASE + "rounded=0;fillColor=none;strokeColor=#1C2B57;dashed=1;dashPattern=8 4;strokeWidth=1.5;"
BOX = BASE + "rounded=0;fillColor=#EAF0F9;strokeColor=#4A6BB0;align=left;verticalAlign=middle;spacingRight=18;fontSize=14;"
WHITE = BOX.replace("#EAF0F9", "#FFFFFF")
DASHED = BASE + "rounded=0;fillColor=#FFFFFF;strokeColor=#1A1D23;dashed=1;dashPattern=4 3;align=left;verticalAlign=middle;spacingLeft=18;spacingRight=18;fontSize=14;"
MONO = BASE + "rounded=0;fillColor=#EAF0F9;strokeColor=#4A6BB0;align=left;verticalAlign=middle;spacingLeft=18;fontSize=14;fontFamily=IBM Plex Mono;"
AGENT = BASE + "shape=hexagon;perimeter=hexagonPerimeter2;size=0.22;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;"
DECIDE = BASE + "rhombus;fillColor=#FFFFFF;strokeColor=#1C2B57;strokeWidth=1.5;fontSize=14;"
GATE = BASE + "rhombus;fillColor=#1C2B57;strokeColor=none;fontColor=#FFFFFF;fontFamily=Barlow Condensed;" + CONDSRC + "fontStyle=1;fontSize=14;"
GATELABEL = BASE + "text;fillColor=#FFFFFF;strokeColor=none;align=left;verticalAlign=middle;fontSize=13;"
EDGE = ("edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;endSize=5;strokeColor=#4A6BB0;strokeWidth=1.5;"
        "fontFamily=Barlow Condensed;" + CONDSRC + "fontSize=13;fontColor=#3A5A9E;labelBackgroundColor=#FFFFFF;")
COLORS = {"s3": "#7AA116", "transfer_family": "#7AA116", "key_management_service": "#DD344C", "sts": "#DD344C",
          "cognito": "#DD344C", "waf": "#DD344C", "identity_and_access_management": "#DD344C", "secrets_manager": "#DD344C",
          "role": "#DD344C", "eventbridge": "#E7157B", "sqs": "#E7157B", "cloudwatch_2": "#E7157B", "cloudtrail": "#E7157B",
          "fargate": "#ED7100", "ecs": "#ED7100", "ecr": "#ED7100", "cloudfront": "#8C4FFF", "elastic_load_balancing": "#8C4FFF",
          "vpc": "#8C4FFF", "bedrock": "#01A88D", "users": "#232F3E"}


def caps(text, size=18, color="#1C2B57"):
    return (f'<span style="font-family:Barlow Condensed;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;'
            f'font-size:{size}px;color:{color}">{text}</span>')


class Board:
    def __init__(self, cells, bid, num, title, x, y, w, h, tag="Shown for Customer B"):
        self.cells, self.bid = cells, bid
        self._raw(bid, "", BOARD, x, y, w, h, parent="1")
        self._raw(f"{bid}_n", num, NUM, 40, 30, 36, 28)
        self._raw(f"{bid}_h", caps(title, 26, "#1A1D23"), TEXT + "fontSize=26;", 90, 28, 900, 32)
        if tag:
            self._raw(f"{bid}_t", tag, TAG, w - 198, 34, 158, 20)

    def _raw(self, cid, value, style, x, y, w, h, parent=None):
        self.cells.append(f'<mxCell id="{cid}" value="{escape(value, {chr(34): "&quot;"})}" style="{style}" vertex="1" '
                          f'parent="{parent or self.bid}"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')
        return cid

    def icon(self, cid, res, x, y, size=40):
        self._raw(cid, "", f"sketch=0;outlineConnect=0;dashed=0;html=1;aspect=fixed;shape=mxgraph.aws4.resourceIcon;"
                  f"resIcon=mxgraph.aws4.{res};fillColor={COLORS.get(res, '#1C2B57')};strokeColor=#FFFFFF;", x, y, size, size)

    def box(self, cid, title, line, x, y, w=260, h=90, res=None, white=False):
        style = (WHITE if white else BOX) + ("spacingLeft=72;" if res else "spacingLeft=18;")
        self._raw(cid, caps(title) + (f'<br><span style="font-size:14px">{line}</span>' if line else ""), style, x, y, w, h)
        if res:
            self.icon(f"{cid}_i", res, x + 18, y + h / 2 - 20)
        return cid

    def mono(self, cid, text, x, y, w, h, badge=None):
        self._raw(cid, text, MONO, x, y, w, h)
        return cid

    def note(self, cid, text, x, y, w, h):
        return self._raw(cid, text, DASHED, x, y, w, h)

    def text(self, cid, text, x, y, w, h, size=14, color="#1A1D23"):
        return self._raw(cid, text, TEXT + f"fontSize={size};fontColor={color};", x, y, w, h)

    def group(self, cid, title, x, y, w, h, res=None, phi=False):
        self._raw(cid, "", PHI if phi else GROUP, x, y, w, h)
        self._raw(f"{cid}_l", caps(title, 14), TEXT + "fontSize=14;", x + (58 if res else 20), y + 10, w - 80, 24)
        if res:
            self.icon(f"{cid}_i", res, x + 20, y + 8, 28)

    def agent(self, cid, title, line, x, y, w=240, h=120):
        return self._raw(cid, caps(title, 22, "#FFFFFF") + f'<br><span style="font-size:13px;color:#FFFFFF">{line}</span>', AGENT, x, y, w, h)

    def decide(self, cid, text, x, y, w=170, h=110):
        return self._raw(cid, text, DECIDE, x, y, w, h)

    def gate(self, cid, n, label, x, y, res="identity_and_access_management"):
        self._raw(cid, str(n), GATE, x, y, 34, 34)
        self._raw(f"{cid}_l", f'<span style="font-weight:500;color:#1C2B57">{label}</span>', GATELABEL + "spacingLeft=28;", x - 57, y - 61, 190, 26)
        self.icon(f"{cid}_li", res, x - 52, y - 57, 18)

    def edge(self, cid, src, dst, label="", exit_=(1, .5), entry=(0, .5), dashed=False, red=False):
        style = EDGE + (f"exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0;entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
                        + ("dashed=1;" if dashed else "") + ("strokeColor=#C0392B;fontColor=#C0392B;" if red else ""))
        value = (f'<span style="font-family:Barlow Condensed;font-weight:500;text-transform:uppercase;letter-spacing:0.06em">{label}</span>'
                 if label else "")
        self.cells.append(f'<mxCell id="{cid}" value="{escape(value, {chr(34): "&quot;"})}" style="{style}" edge="1" parent="{self.bid}" '
                          f'source="{src}" target="{dst}"><mxGeometry relative="1" as="geometry"/></mxCell>')


def zoom(cells, cid, num, x, y, parent):
    cells.append(f'<mxCell id="{cid}" value="{num}" style="{ZOOM}" vertex="1" parent="{parent}"><mxGeometry x="{x}" y="{y}" '
                 f'width="26" height="26" as="geometry"/></mxCell>')


BIGGATE = BASE + "rhombus;fillColor=#1C2B57;strokeColor=#FFFFFF;strokeWidth=3;fontColor=#FFFFFF;fontFamily=Barlow Condensed;" + CONDSRC + "fontStyle=1;fontSize=22;"
DENYGATE = BIGGATE.replace("#1C2B57", "#C0392B")
GATECAP = BASE + "text;fillColor=#FFF4F4;strokeColor=#C0392B;rounded=1;arcSize=20;align=center;verticalAlign=middle;fontSize=13;spacing=4;"


def big_gate(board, cid, number, question, detail, x, y, deny=False, label_below=False):
    """A gate drawn ON the arrow: a 64 px diamond the arrow passes through, with a boxed caption beside it.
    (x, y) is the diamond's top-left corner. Connect with board.edge(src, cid) and board.edge(cid, dst)."""
    board._raw(cid, "✕" if deny else str(number), DENYGATE if deny else BIGGATE, x, y, 64, 64)
    caption = (f'<b style="color:#1C2B57">GATE {number}</b> · {question}' if not deny else f'<b style="color:#C0392B">DENIED</b> · {question}')
    if detail:
        caption += f'<br><span style="font-size:12px">{detail}</span>'
    board._raw(f"{cid}_c", caption, GATECAP, x - 98, y + (74 if label_below else -58), 260, 48)
    return cid
