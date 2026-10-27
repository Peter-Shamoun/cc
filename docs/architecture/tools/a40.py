"""Analyzer sub-pages 41–45: zoom-ins of png/40-analyzer.png (tools/l1.py, "# 40 analyzer"), same boxes and names."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from dlib import AGENT, CYL, DECISION, NOTE, STEPBOX, TEXT, LANE, Diagram, badge, legend_drilldown, strip

SRC = Path(__file__).resolve().parent.parent / "src"
F = False
PANEL = "rounded=1;arcSize=3;html=1;fillColor=none;strokeColor=#1A1D23;strokeWidth=1.5;"
CAP = "text;html=1;align=left;verticalAlign=bottom;fontSize=12;fontColor=#1A1D23;fontFamily=Helvetica;whiteSpace=wrap;"
CRUMB = "text;html=1;align=left;verticalAlign=middle;fontSize=11;fontColor=#3A5A9E;fontFamily=Helvetica;"
GATE = "rhombus;html=1;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;fontStyle=1;fontSize=12;fontFamily=Helvetica;"
PREFIX = "rounded=1;arcSize=2;html=1;fillColor=none;strokeColor=#7AA116;dashed=1;strokeWidth=2;"
PREFIX_T = "text;html=1;align=left;verticalAlign=top;fontSize=13;fontColor=#1A1D23;fontFamily=Helvetica;"
REFS = "rounded=1;arcSize=6;html=1;fillColor=none;strokeColor=#1A1D23;"
CENTER = "text;html=1;align=center;verticalAlign=middle;fontSize=12;fontColor=#1A1D23;fontFamily=Helvetica;"


def save(d):
    (SRC / f"{d.id}.drawio").write_text(d.xml())


def header(d, title, crumb):
    d.title(f"CLAIMCLARITY · {title}")
    d.add("crumb", f"Overview › Analyzer › {crumb}", CRUMB, 20, 50, 600, 20)


def table(d, cid, title, rows, x, y, w, extra=""):
    d.add(cid, title, "swimlane;fontStyle=1;childLayout=stackLayout;horizontal=1;startSize=26;fillColor=#EAF0F9;strokeColor=#4A6BB0;horizontalStack=0;resizeParent=1;resizeLast=0;collapsible=0;marginBottom=0;html=1;fontSize=12;fontFamily=Helvetica;" + extra, x, y, w, 26 + 22 * len(rows))
    for k, r in enumerate(rows):
        d.cells.append(f'<mxCell id="{cid}-r{k}" value="{r}" style="text;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;spacingLeft=8;html=1;fontSize=11;fontFamily=Helvetica;" vertex="1" parent="{cid}"><mxGeometry y="{26 + 22 * k}" width="{w}" height="22" as="geometry"/></mxCell>')


def frontend(d):
    d.add("fe-cap", "Frontend user interface · shared between apps", CAP, 20, 150, 330, 24)
    d.add("fe", "", PANEL, 20, 180, 300, 400)
    d.icon("fe-i", "client", "Chat app", "", 170, 370, tile=F, size=80)


def red(d):
    d.cells[-1] = d.cells[-1].replace("strokeColor=#4A6BB0;", "strokeColor=#C0392B;").replace("fontColor=#3A5A9E;", "fontColor=#C0392B;")


def prefix(d, x, y, w, h):
    d.add("pfx", "", PREFIX, x, y, w, h)
    d.icon("pfx-i", "s3", "", "", x + 30, y + 30, size=28)
    d.cells[-1] = d.cells[-1].replace('value="&lt;b&gt;&lt;/b&gt;"', 'value=""')
    d.add("pfx-t", "<b>Customer's S3 prefix</b><br>semantic_layer/ (gold)", PREFIX_T, x + 52, y + 16, 320, 44)


SEM_ROWS = ["claims", "remit_claims", "remit_lines", "adjustments", "…"]

# 41 KPI gate ---------------------------------------------------------------------------------------------
d = Diagram("41-kpi-gate", "KPI gate", 1320, 680)
header(d, "KPI GATE", "KPI gate")
frontend(d)
d.add("n-cache", "<b>Cached</b><br>16 presets · allowed values", NOTE + "fontSize=11;", 360, 140, 180, 56)
d.add("n-turn", "<b>Each turn</b><br>history · date · data coverage", NOTE + "fontSize=11;", 560, 140, 200, 56)
d.add("gate", "<b>KPI gate</b><br>does a preset<br>answer it?", DECISION, 420, 300, 150, 120)
d.icon("bedrock", "bedrock", "Claude Haiku on Amazon Bedrock", "", 495, 520)
d.add("check", "<b>Values allowed?</b>", DECISION, 720, 300, 150, 120)
d.add("pre-cap", "<b>Preset KPI queries</b>", CAP, 900, 62, 380, 24)
d.add("pre", "<b>16 presets</b> · fixed SQL", STEPBOX, 900, 90, 380, 60)
badge(d, "pre-b", "42", 1268, 78)
d.add("ag-cap", "Claim Query Agent", CAP, 900, 388, 380, 24)
d.add("ag", "", PANEL, 900, 415, 380, 200)
d.add("llm", "<b>Claude Haiku</b><br>Strands agent", AGENT, 1000, 470, 180, 90)
badge(d, "ag-b", "43", 1268, 403)
d.edge("e-q", "fe", "gate", "user question", exit_=(1, .45), entry=(0, .5))
d.edge("e-cache", "n-cache", "gate", "", exit_=(.5, 1), entry=(.5, 0))
d.edge("e-turn", "n-turn", "gate", "", exit_=(.5, 1), entry=(.5, 0))
d.edge("e-model", "gate", "bedrock", "", exit_=(.5, 1), entry=(.5, 0), both=True, dashed=True)
d.edge("e-fits", "gate", "check", "fits", exit_=(1, .5), entry=(0, .5))
d.edge("e-yes", "check", "pre", "yes", exit_=(.5, 0), entry=(0, .5))
d.edge("e-no1", "check", "ag", "no", exit_=(.5, 1), entry=(0, .325))
d.edge("e-no2", "gate", "ag", "no", exit_=(.75, .75), entry=(0, .825), waypoints=[(532, 450), (650, 450), (650, 580)])
legend_drilldown(d, 1060, 640)
save(d)

# 42 preset KPI queries -----------------------------------------------------------------------------------
d = Diagram("42-standard-kpi-path", "Preset KPI queries", 1980, 660)
header(d, "PRESET KPI QUERIES", "Preset KPI queries")
frontend(d)
d.add("gate", "<b>KPI gate</b>", DECISION, 420, 210, 150, 100)
badge(d, "gate-b", "41", 540, 206)
d.add("disp", "<b>Display SQL</b><br>values inlined", NOTE + "fontSize=11;", 690, 90, 200, 50)
d.add("pre-cap", "<b>Preset KPI queries</b>", CAP, 640, 188, 300, 24)
d.add("pre", "<b>16 presets</b> · fixed SQL", STEPBOX, 640, 215, 300, 90)
d.add("cli", "<b>cc CLI<br>+ SQL guard</b>", STEPBOX, 1030, 195, 120, 130)
badge(d, "cli-b", "44", 1138, 183)
d.add("duck", "<b>DuckDB reader session</b><br>read-only", CYL, 1240, 195, 240, 130)
prefix(d, 1560, 120, 380, 300)
table(d, "sem", "sem.* · semantic layer", SEM_ROWS, 1660, 200, 230, extra="strokeColor=#E8A0A0;")
d.add("g1", "", GATE, 1516, 246, 28, 28)
d.add("fmt", "<b>Number formatting</b><br>in code", STEPBOX, 1240, 470, 240, 60)
d.add("writer", "<b>Claude Haiku</b><br>reply writer", AGENT, 900, 455, 240, 90)
d.edge("e-q", "fe", "gate", "user question", exit_=(1, .2), entry=(0, .5))
d.edge("e-yes", "gate", "pre", "yes", exit_=(1, .5), entry=(0, .5))
d.edge("e-disp", "pre", "disp", "", exit_=(.5, 0), entry=(.5, 1))
d.edge("e-disp-fe", "disp", "fe", "SQL panel", exit_=(0, .5), entry=(1, .1), waypoints=[(360, 115), (360, 220)])
d.edge("e-pre-cli", "pre", "cli", "bound values", exit_=(1, .5), entry=(0, .5))
d.edge("e-cli-duck", "cli", "duck", "", exit_=(1, .5), entry=(0, .5))
d.edge("e-duck-g1", "duck", "g1", "", exit_=(1, .5), entry=(0, .5))
d.edge("e-g1-sem", "g1", "sem", "", exit_=(1, .5), entry=(0, .441))
d.edge("e-rows", "duck", "fmt", "rows", exit_=(.5, 1), entry=(.5, 0))
d.edge("e-fmt-writer", "fmt", "writer", "", exit_=(0, .5), entry=(1, .5))
d.edge("e-ans", "writer", "fe", "answer", exit_=(0, .5), entry=(1, .8))
legend_drilldown(d, 1720, 610)
save(d)

# 43 custom analysis agent --------------------------------------------------------------------------------
d = Diagram("43-custom-analysis-agent", "Claim Query Agent", 1900, 760)
header(d, "CLAIM QUERY AGENT", "Claim Query Agent")
frontend(d)
d.add("gate", "<b>KPI gate</b>", DECISION, 400, 300, 160, 90)
badge(d, "gate-b", "41", 530, 296)
d.add("ag-cap", "Claim Query Agent", CAP, 620, 128, 560, 24)
d.add("ag", "", PANEL, 620, 156, 560, 540)
d.add("bank", "<b>EACH TURN</b><br><br><b>Solved example<br>question bank</b><br><br>+ today&#39;s date<br>+ data coverage<br>+ conversation<br>history", STEPBOX, 640, 180, 120, 490)
d.add("ref-cap", "<b>CACHED</b> · same for every question", CENTER, 820, 172, 340, 22)
d.add("refs", "", REFS, 810, 170, 355, 140)
d.add("r1", "<b>Playbook</b>", STEPBOX, 825, 200, 100, 90)
d.add("r2", "<b>EDI business<br>glossary</b>", STEPBOX, 937, 200, 100, 90)
d.add("r3", "<b>Gold schema</b>", STEPBOX, 1049, 200, 100, 90)
d.add("llm", "<b>Claude Haiku</b><br>Strands agent on Amazon Bedrock", AGENT, 895, 370, 240, 130)
d.add("ll-s", "<b>Full lossless EDI schema</b><br><br>only after a reasoned escalation", NOTE + "dashed=1;fontSize=11;", 910, 560, 210, 110)
d.add("cli", "<b>cc CLI<br>+ SQL guard</b>", STEPBOX, 1410, 370, 120, 130)
badge(d, "cli-b", "44", 1518, 358)
table(d, "cmds", "cc commands", ["sql", "lookup", "escalate", "list · describe", "answer · refuse · ask"], 1375, 560, 190)
d.add("duck", "<b>DuckDB reader session</b>", CYL, 1620, 370, 240, 130)
d.edge("e-q", "fe", "gate", "question", exit_=(1, .4125), entry=(0, .5))
d.edge("e-no", "gate", "ag", "no", exit_=(1, .5), entry=(0, .3510))
d.edge("e-f", "fe", "ag", "follow-up", exit_=(1, .6), entry=(0, .4889))
d.edge("e-ans", "ag", "fe", "answer", exit_=(0, .7111), entry=(1, .9))
d.edge("e-r1", "r1", "llm", "", exit_=(.5, 1), entry=(.2, 0)); d.edge("e-r2", "r2", "llm", "", exit_=(.5, 1), entry=(.5, 0)); d.edge("e-r3", "r3", "llm", "", exit_=(.5, 1), entry=(.8, 0))
d.edge("e-bank", "bank", "llm", "3 examples", exit_=(1, .5204), entry=(0, .5))
d.edge("e-ll", "llm", "ll-s", "", exit_=(.5, 1), entry=(.5, 0), dashed=True)
d.edge("e-c1", "llm", "cli", "SQL calls", exit_=(1, .2), entry=(0, .2))
d.edge("e-c2", "cli", "llm", "results", exit_=(0, .5), entry=(1, .5))
d.edge("e-c3", "llm", "cli", "final query", exit_=(1, .8), entry=(0, .8)); red(d)
d.edge("e-cmds", "cli", "cmds", "", exit_=(.5, 1), entry=(.5, 0), dashed=True)
d.edge("e-cli-duck", "cli", "duck", "", exit_=(1, .5), entry=(0, .5))
legend_drilldown(d, 1640, 720)
save(d)

# 44 SQL guard and escalation -----------------------------------------------------------------------------
d = Diagram("44-sql-guard-escalation", "cc CLI + SQL guard", 2080, 740)
header(d, "CC CLI + SQL GUARD", "cc CLI + SQL guard")
d.add("pre", "<b>Preset KPI queries</b>", STEPBOX, 40, 140, 220, 60)
badge(d, "pre-b", "42", 248, 128)
d.add("llm", "<b>Claude Haiku</b><br>Strands agent", AGENT, 40, 300, 220, 110)
badge(d, "llm-b", "43", 248, 290)
d.add("cli-cap", "<b>cc CLI + SQL guard</b> · every query", CAP, 460, 232, 500, 24)
d.add("cli", "", PANEL, 330, 260, 760, 190)
d.add("d1", "<b>One read-only<br>SELECT?</b>", DECISION, 350, 305, 140, 100)
d.add("d2", "<b>Allowed tables<br>and functions?</b>", DECISION, 530, 305, 140, 100)
d.add("d3", "<b>Join cost<br>OK?</b>", DECISION, 710, 305, 140, 100)
d.add("d4", "<b>Query time<br>left?</b>", DECISION, 890, 305, 140, 100)
d.add("duck", "<b>DuckDB reader session</b><br>read-only · row caps", CYL, 1160, 290, 240, 130)
d.add("esc1", "<b>escalate</b> · reason", NOTE + "fontSize=11;", 330, 560, 220, 60)
d.add("esc2", "<b>Reason masked, logged,<br>shown to the user</b>", STEPBOX, 640, 560, 300, 60)
prefix(d, 1480, 150, 560, 520)
table(d, "sem", "sem.* · semantic layer", SEM_ROWS, 1560, 230, 210, extra="strokeColor=#E8A0A0;")
table(d, "ll", "lossless.* · EDI tables", ["c837_claim", "r835_claim_payment", "…"], 1800, 380, 210)
d.add("g1", "", GATE, 1440, 316, 28, 28)
d.add("g2", "", GATE, 1440, 396, 28, 28)
d.add("g-t", "IAM + KMS: this customer only", TEXT + "fontSize=12;", 1310, 480, 180, 40)
d.edge("e-pre", "pre", "d1", "", exit_=(1, .5), entry=(.5, 0), waypoints=[(420, 170)])
d.edge("e-llm", "llm", "d1", "", exit_=(1, .5), entry=(0, .5))
d.edge("e-12", "d1", "d2", "yes", exit_=(1, .5), entry=(0, .5))
d.edge("e-23", "d2", "d3", "yes", exit_=(1, .5), entry=(0, .5))
d.edge("e-34", "d3", "d4", "yes", exit_=(1, .5), entry=(0, .5))
d.edge("e-4d", "d4", "duck", "yes", exit_=(1, .5), entry=(0, .5))
d.edge("e-duck-g1", "duck", "g1", "", exit_=(1, .3077), entry=(0, .5))
d.edge("e-g1-sem", "g1", "sem", "", exit_=(1, .5), entry=(0, .7353))
d.edge("e-duck-g2", "duck", "g2", "", exit_=(1, .9231), entry=(0, .5))
d.edge("e-g2-ll", "g2", "ll", "after escalation", exit_=(1, .5), entry=(0, .3261))
d.edge("e-gt", "g-t", "g2", "", exit_=(.8, 0), entry=(.5, 1))
d.edge("e-esc", "llm", "esc1", "escalate", exit_=(.5, 1), entry=(0, .5), waypoints=[(150, 590)])
d.edge("e-esc12", "esc1", "esc2", "", exit_=(1, .5), entry=(0, .5))
d.edge("e-esc-duck", "esc2", "duck", "opens lossless.*", exit_=(1, .5), entry=(.5, 1), waypoints=[(1280, 590)])
legend_drilldown(d, 1820, 700)
save(d)

# 45 model calls ------------------------------------------------------------------------------------------
d = Diagram("45-model-calls", "Model calls", 1240, 640)
header(d, "MODEL CALLS", "Model calls")
strip(d, "s1", 80, 160, "EVERY QUESTION")
d.add("gate", "<b>KPI gate</b>", DECISION, 140, 120, 150, 100)
badge(d, "gate-b", "41", 262, 116)
d.icon("b1", "bedrock", "Claude Haiku on Amazon Bedrock", "", 620, 170)
d.add("c1", "<b>Cached</b><br>16 presets · allowed values", NOTE + "fontSize=11;", 880, 140, 220, 60)
strip(d, "s2", 260, 160, "PRESET ANSWER")
d.add("writer", "<b>Claude Haiku</b><br>reply writer", AGENT, 120, 305, 190, 80)
badge(d, "writer-b", "42", 296, 296)
d.icon("b2", "bedrock", "Claude Haiku on Amazon Bedrock", "", 620, 345)
strip(d, "s3", 440, 160, "AGENT ANSWER")
d.add("agent", "<b>Claude Haiku</b><br>Strands agent", AGENT, 120, 485, 190, 80)
badge(d, "agent-b", "43", 296, 476)
d.icon("b3", "bedrock", "Claude Haiku on Amazon Bedrock", "", 620, 525)
d.add("c3", "<b>Cached</b><br>playbook · glossary · gold schema", NOTE + "fontSize=11;", 880, 495, 220, 60)
d.edge("e1", "gate", "b1", "1 call", exit_=(1, .5), entry=(0, .5))
d.edge("e1c", "b1", "c1", "", exit_=(1, .5), entry=(0, .5), dashed=True)
d.edge("e2", "writer", "b2", "1 call", exit_=(1, .5), entry=(0, .5))
d.edge("e3", "agent", "b3", "per step", exit_=(1, .5), entry=(0, .5))
d.edge("e3c", "b3", "c3", "", exit_=(1, .5), entry=(0, .5), dashed=True)
legend_drilldown(d, 980, 612)
save(d)
