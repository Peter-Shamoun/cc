"""Overview (00) and the six area diagrams (10–60): flows read left to right, names only, drill-down badges."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from dlib import AGENT, DECISION, EXT, NOTE, PHI, STEPBOX, CYL, TEXT, LANE, Diagram, badge, chain, group, legend_drilldown, strip

SRC = Path(__file__).resolve().parent.parent / "src"
T, F = True, False

def save(d):
    (SRC / f"{d.id}.drawio").write_text(d.xml())

# 00 overview ---------------------------------------------------------------------------------------------
d = Diagram("00-overview", "Overview", 1560, 760)
d.title("CLAIMCLARITY · HOW IT WORKS")
strip(d, "s1", 70, 180, "1 · DATA IN")
chain(d, "a", [("corporate_data_center", "Customer lab", None, F), ("transfer_family", "SFTP intake", "10", T), ("s3", "S3 · raw", None, T),
               ("eventbridge", "EventBridge", None, T), ("fargate", "Parser task", None, T), ("s3", "S3 · gold", None, T)], 160)
strip(d, "s2", 270, 160, "2 · SIGN-IN")
d.icon("b0", "users", "XiFin user", "", 120, 350, tile=F)
d.add("b1", "<b>Microsoft Entra ID</b>", EXT, 240, 326, 160, 48)
d.icon("b2", "cognito", "Amazon Cognito", "", 520, 350); badge(d, "b2-b", "20", 540, 308)
d.add("b3", "<b>ID token</b><br>names one customer", NOTE, 660, 318, 140, 64)
d.edge("e-b0-b1", "b0", "b1"); d.edge("e-b1-b2", "b1", "b2"); d.edge("e-b2-b3", "b2", "b3")
strip(d, "s3", 450, 250, "3 · A QUESTION")
ids = chain(d, "c", [("users", "XiFin user", None, F), ("cloudfront", "CloudFront + WAF", None, T), ("ecs", "Chat API", "30", T),
                     ("sqs", "Run queue", None, T), ("fargate", "Run worker", "40", T), ("s3", "S3 · gold", None, T)], 540)
d.icon("c6", "bedrock", "Amazon Bedrock", "", 1020, 655, label="right")
d.icon("c7", "sts", "STS", "", 820, 655, label="left")
d.edge("e-c4-c6", "c4", "c6", "", exit_=(1, .85), entry=(.5, 0))
d.edge("e-c4-c7", "c4", "c7", "", exit_=(0, .85), entry=(.5, 0))
d.add("x", "<b>Across all three:</b> network &amp; security <b>50</b> · operations <b>60</b>", TEXT + "fontColor=#3A5A9E;", 1250, 590, 290, 30)
legend_drilldown(d, 1290, 715)
save(d)

# 10 ingestion --------------------------------------------------------------------------------------------
d = Diagram("10-ingestion", "Ingestion & lake", 1560, 420)
d.title("CLAIMCLARITY · INGESTION &amp; LAKE")
strip(d, "s1", 70, 220, "A LAB'S DAILY FILES BECOME QUERYABLE TABLES")
d.add("phi", "Customer B only: own prefix, KMS key and role", PHI, 460, 108, 1060, 168)
chain(d, "a", [("corporate_data_center", "Customer lab", None, F), ("transfer_family", "SFTP intake", "11", T), ("s3", "S3 · raw", "12", T),
               ("eventbridge", "EventBridge", "13", T), ("fargate", "Parser task", "14", T), ("s3", "S3 · silver", "12", T),
               ("s3", "S3 · gold", "15", T)], 200, step=210)
legend_drilldown(d, 1290, 330)
save(d)

# 20 identity ---------------------------------------------------------------------------------------------
d = Diagram("20-identity", "Identity & access", 1560, 560)
d.title("CLAIMCLARITY · IDENTITY &amp; ACCESS")
strip(d, "s1", 70, 180, "SIGN-IN · once per session")
d.icon("a0", "users", "XiFin user", "", 120, 160, tile=F)
d.add("a1", "<b>Microsoft Entra ID</b>", EXT, 240, 136, 160, 48)
d.icon("a2", "cognito", "Amazon Cognito", "", 520, 160); badge(d, "a2-b", "21", 540, 118)
d.add("a3", "<b>ID token</b><br>in session cookie", NOTE, 660, 128, 140, 64)
d.edge("e-a0-a1", "a0", "a1"); d.edge("e-a1-a2", "a1", "a2"); d.edge("e-a2-a3", "a2", "a3")
strip(d, "s2", 270, 220, "EVERY QUESTION · token to data")
d.add("b0", "<b>ID token</b>", NOTE, 70, 336, 100, 50)
d.icon("b1", "ecs", "Chat API checks it", "", 320, 360); badge(d, "b1-b", "22", 340, 318)
d.icon("b2", "sts", "STS", "", 520, 360); badge(d, "b2-b", "22", 540, 318)
d.icon("b3", "role", "Customer B's role", "", 720, 360, tile=F, color="#DD344C"); badge(d, "b3-b", "23", 740, 318)
d.icon("b4", "data_encryption_key", "Customer B's key", "", 920, 360, tile=F, color="#DD344C"); badge(d, "b4-b", "52", 940, 318)
d.icon("b5", "s3", "S3 · B's gold only", "", 1120, 360); badge(d, "b5-b", "23", 1140, 318)
for a, b in [("b0", "b1"), ("b1", "b2"), ("b2", "b3"), ("b3", "b4"), ("b4", "b5")]:
    d.edge(f"e-{a}-{b}", a, b)
legend_drilldown(d, 1290, 520)
save(d)

# 30 chat backend -----------------------------------------------------------------------------------------
d = Diagram("30-chat-backend", "Chat backend", 1560, 480)
d.title("CLAIMCLARITY · CHAT BACKEND")
strip(d, "s1", 70, 330, "A QUESTION IN, AN ANSWER STREAMED BACK")
chain(d, "a", [("users", "XiFin user", None, F), ("cloudfront", "CloudFront + WAF", "31", T), ("elastic_load_balancing", "Load balancer", "31", T),
               ("ecs", "Chat API", "32", T, "above"), ("sqs", "Run queue", "34", T), ("fargate", "Run worker", "40", T, "above"), ("bedrock", "Amazon Bedrock", "45", T)],
      190, step=200)
d.icon("a7", "s3", "S3 · chat state", "", 720, 320, label="right"); badge(d, "a7-b", "33", 740, 278)
d.icon("a8", "s3", "S3 · gold", "", 1120, 320, label="right")
d.edge("e-a3-a7", "a3", "a7", "", exit_=(.5, 1), entry=(.5, 0))
d.edge("e-a5-a8", "a5", "a8", "", exit_=(.5, 1), entry=(.5, 0))
legend_drilldown(d, 1290, 440)
save(d)

# 40 analyzer: the excalidraw (diagrams/agent_analyzer.excalidraw) in our format, plus the KPI gate and presets
d = Diagram("40-analyzer", "Analyzer", 2720, 800)
d.title("CLAIMCLARITY · ANALYZER")
PANEL = "rounded=1;arcSize=3;html=1;fillColor=none;strokeColor=#1A1D23;strokeWidth=1.5;"
CAP = "text;html=1;align=left;verticalAlign=bottom;fontSize=12;fontColor=#1A1D23;fontFamily=Helvetica;whiteSpace=wrap;"
SMALL = "text;html=1;align=left;verticalAlign=top;fontSize=11;fontColor=#3A5A9E;fontFamily=Helvetica;whiteSpace=wrap;"
RED = "strokeColor=#C0392B;fontColor=#C0392B;"
def table(cid, title, rows, x, y, w, extra=""):
    d.add(cid, title, "swimlane;fontStyle=1;childLayout=stackLayout;horizontal=1;startSize=26;fillColor=#EAF0F9;strokeColor=#4A6BB0;horizontalStack=0;resizeParent=1;resizeLast=0;collapsible=0;marginBottom=0;html=1;fontSize=12;fontFamily=Helvetica;" + extra, x, y, w, 26 + 22 * len(rows))
    for k, r in enumerate(rows):
        d.cells.append(f'<mxCell id="{cid}-r{k}" value="{r}" style="text;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;spacingLeft=8;html=1;fontSize=11;fontFamily=Helvetica;" vertex="1" parent="{cid}"><mxGeometry y="{26 + 22 * k}" width="{w}" height="22" as="geometry"/></mxCell>')

# frontend
d.add("fe-cap", "Frontend user interface · shared between apps", CAP, 20, 150, 330, 24)
d.add("fe", "", PANEL, 20, 180, 300, 400)
d.icon("fe-i", "client", "Chat app", "", 170, 370, tile=F, size=80)

# NEW: the KPI gate and the preset queries
d.add("gate", "<b>KPI gate</b><br>does a preset<br>answer it?", DECISION, 420, 270, 150, 120)
d.add("pre-cap", "<b>NEW · Preset KPI queries</b>", CAP, 640, 32, 500, 24)
d.add("pre", "<b>16 presets</b> · fixed SQL, reviewed once<br>the gate fills the <b>parameters</b>: payer · payer class · month · CPT · CARC", STEPBOX + "align=left;spacingLeft=12;", 640, 60, 520, 60)

# Claim Query Agent
d.add("ag-cap", "Claim Query Agent · backend for ClaimClarity, and part of the backend for Agentic Next Best Actions", CAP, 640, 128, 560, 24)
d.add("ag", "", PANEL, 640, 156, 560, 540)
d.add("bank", "<b>Solved example<br>question bank</b><br>(training set)", STEPBOX, 660, 180, 120, 490)
d.add("ref-cap", "References", "text;html=1;align=center;verticalAlign=middle;fontSize=12;fontColor=#1A1D23;fontFamily=Helvetica;", 840, 172, 340, 22)
d.add("refs", "", "rounded=1;arcSize=6;html=1;fillColor=none;strokeColor=#1A1D23;", 830, 170, 355, 140)
d.add("r1", "<b>Playbook</b><br>14 rules from<br>past mistakes", STEPBOX, 845, 200, 100, 90)
d.add("r2", "<b>EDI business<br>glossary</b>", STEPBOX, 957, 200, 100, 90)
d.add("r3", "<b>Gold schema</b><br>dictionary +<br>allowed values", STEPBOX, 1069, 200, 100, 90)
d.add("llm", "<b>Claude Haiku</b><br>Strands agent on Amazon Bedrock<br>one agent", AGENT, 915, 370, 240, 130)
d.add("ll-s", "<b>Full lossless EDI schema</b><br><br>only after the agent escalates<br>with a reason (logged, shown to user)", NOTE + "dashed=1;fontSize=11;", 930, 560, 210, 110)
d.edge("e-r1", "r1", "llm", "", exit_=(.5, 1), entry=(.2, 0)); d.edge("e-r2", "r2", "llm", "", exit_=(.5, 1), entry=(.5, 0)); d.edge("e-r3", "r3", "llm", "", exit_=(.5, 1), entry=(.8, 0))
d.edge("e-bank", "bank", "llm", "K = 3 most similar<br>question + SQL<br>+ answer", exit_=(1, .5), entry=(0, .5))
d.edge("e-ll", "llm", "ll-s", "", exit_=(.5, 1), entry=(.5, 0), dashed=True)

# frontend <-> gate / agent
d.edge("e-q", "fe", "gate", "user question", exit_=(1, .25), entry=(0, .5))
d.edge("e-f", "fe", "ag", "follow-up or clarify answers", exit_=(1, .6), entry=(0, .45), waypoints=[(380, 420), (600, 420)])
d.edge("e-ans", "ag", "fe", "answer + formatting + live steps, or clarifying questions", exit_=(0, .85), entry=(1, .85))
d.edge("e-yes", "gate", "pre", "yes", exit_=(.5, 0), entry=(0, .5))
d.edge("e-no", "gate", "ag", "no", exit_=(1, .5), entry=(0, .27))

# cli + guard
d.add("cli", "<b>cc CLI<br>+ SQL guard</b>", STEPBOX, 1430, 370, 120, 130)
d.edge("e-pre-cli", "pre", "cli", "preset SQL", exit_=(1, .5), entry=(.5, 0))
d.edge("e-c1", "llm", "cli", "agent writes CLI<br>calls with SQL", exit_=(1, .2), entry=(0, .2))
d.edge("e-c2", "cli", "llm", "iterative querying<br>+ result streaming", exit_=(0, .5), entry=(1, .5))
d.edge("e-c3", "llm", "cli", "final query + answer<br>(re-run by the server)", exit_=(1, .8), entry=(0, .8))
d.cells[-1] = d.cells[-1].replace("strokeColor=#4A6BB0;", "strokeColor=#C0392B;").replace("fontColor=#3A5A9E;", "fontColor=#C0392B;")

# core principle
d.add("cp-cap", "<b>Core principle: enforce the semantic layer.</b> A schema of curated tables the agent can query to answer nearly every question. The full lossless schema is the exception. This saves tokens and keeps queries simple.", CAP, 1640, 92, 1060, 44)
d.add("cp", "", PANEL, 1640, 140, 1060, 600)
d.add("duck", "<b>DuckDB reader session</b><br>one per run<br>read-only SELECT · row, time and cost caps", CYL, 1860, 370, 240, 130)
d.edge("e-cli-duck", "cli", "duck", "", exit_=(1, .5), entry=(0, .5))
d.add("n1", "SQL over a per-run channel; the agent never holds credentials", SMALL, 1655, 330, 190, 50)
d.add("n2", "temporary (15-min) AWS credentials for the signed-in customer", SMALL, 1655, 455, 190, 50)
d.add("pfx", "", "rounded=1;arcSize=2;html=1;fillColor=none;strokeColor=#7AA116;dashed=1;strokeWidth=2;", 2160, 170, 520, 540)
d.icon("pfx-i", "s3", "", "", 2190, 200, size=28)
d.cells[-1] = d.cells[-1].replace('value="&lt;b&gt;&lt;/b&gt;"', 'value=""')
d.add("pfx-t", "<b>Customer's S3 prefix</b><br>semantic_layer/ (gold)", "text;html=1;align=left;verticalAlign=top;fontSize=13;fontColor=#1A1D23;fontFamily=Helvetica;", 2212, 186, 320, 44)
d.add("sem-n", "Curated tables built from silver by the parser at load time; simple to understand and query", SMALL, 2200, 250, 250, 40)
table("sem", "sem.* · semantic layer", ["claims", "remit_claims", "remit_lines", "adjustments", "…"], 2220, 300, 210, extra="strokeColor=#E8A0A0;")
table("ll", "lossless.* · EDI tables", ["c837_claim", "r835_claim_payment", "…"], 2440, 470, 210)
d.add("ll-n", "Every field from the raw EDI files, structured, patient identifiers removed. Opened only when the agent decides the curated tables cannot answer.", SMALL, 2430, 560, 240, 80)
GATE = "rhombus;html=1;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;fontStyle=1;fontSize=12;fontFamily=Helvetica;"
d.add("g1", "", GATE, 2120, 375, 28, 28); d.add("g2", "", GATE, 2120, 496, 28, 28)
d.edge("e-duck-g1", "duck", "g1", "", exit_=(1, .2), entry=(0, .5)); d.edge("e-g1-sem", "g1", "sem", "", exit_=(1, .5), entry=(0, .45))
d.edge("e-duck-g2", "duck", "g2", "", exit_=(1, .9), entry=(0, .5)); d.edge("e-g2-ll", "g2", "ll", "ONLY AFTER A REASONED ESCALATION", exit_=(1, .5), entry=(0, .45))
d.edge("e-built", "ll", "sem", "built from", exit_=(.5, 0), entry=(1, .5), dashed=True)
d.cells[-1] = d.cells[-1].replace("strokeColor=#4A6BB0;", "strokeColor=#E07A7A;")
d.add("g-t", "IAM + KMS gate: this customer only", "text;html=1;align=left;verticalAlign=middle;fontSize=12;fontColor=#1A1D23;fontFamily=Helvetica;", 1960, 600, 260, 24)
d.edge("e-gt", "g-t", "g2", "", exit_=(.7, 0), entry=(.5, 1))
save(d)

# 50 network & security -----------------------------------------------------------------------------------
d = Diagram("50-network-security", "Network & security", 1560, 560)
d.title("CLAIMCLARITY · NETWORK &amp; SECURITY")
d.add("cloud", "AWS Cloud", group("cloud"), 230, 70, 1300, 400)
d.icon("u", "users", "XiFin user", "", 110, 270, tile=F)
d.icon("cf", "cloudfront", "CloudFront + WAF", "", 340, 270); badge(d, "cf-b", "51", 360, 228)
d.add("vpc", "VPC", group("vpc"), 450, 110, 620, 330)
d.add("sub", "Private subnets", group("subnet"), 480, 160, 380, 250)
d.icon("alb", "elastic_load_balancing", "Load balancer", "", 570, 270)
d.icon("ecs", "fargate", "ECS tasks", "", 760, 270); badge(d, "ecs-b", "51", 780, 228)
d.icon("vpe", "endpoints", "VPC endpoints", "", 970, 270, tile=F, color="#8C4FFF"); badge(d, "vpe-b", "51", 990, 228)
for i, (res, name, page) in enumerate([("s3", "S3", "52"), ("key_management_service", "KMS", "52"), ("bedrock", "Bedrock", None), ("cloudtrail", "CloudTrail", "53")]):
    d.icon(f"m{i}", res, name, "", 1160 + (i % 2) * 180, 190 + (i // 2) * 170, size=48)
    if page: badge(d, f"m{i}-b", page, 1180 + (i % 2) * 180, 152 + (i // 2) * 170)
d.add("man", "", "rounded=0;html=1;fillColor=none;strokeColor=#D5DCE8;dashed=1;", 1100, 120, 400, 320)
d.edge("e-u-cf", "u", "cf"); d.edge("e-cf-alb", "cf", "alb"); d.edge("e-alb-ecs", "alb", "ecs"); d.edge("e-ecs-vpe", "ecs", "vpe")
d.edge("e-vpe-man", "vpe", "man", "private", exit_=(1, .5), entry=(0, .469))
legend_drilldown(d, 1290, 510)
save(d)

# 60 operations -------------------------------------------------------------------------------------------
d = Diagram("60-operations", "Operations", 1560, 620)
d.title("CLAIMCLARITY · OPERATIONS")
strip(d, "s1", 70, 160, "SHIP A CHANGE")
d.add("g0", "<b>Git</b>", EXT, 70, 136, 100, 48)
d.add("ci", "<b>CI</b><br>build + tests", EXT, 260, 136, 120, 48)
chain(d, "a", [("ecr", "Amazon ECR", "61", T), ("ecs", "ECS services", "61", T)], 160, x0=520)
d.edge("e-g0-ci", "g0", "ci"); d.edge("e-ci-a0", "ci", "a0")
strip(d, "s2", 250, 160, "WATCH IT")
chain(d, "b", [("fargate", "ECS tasks", None, T), ("cloudwatch_2", "CloudWatch", "62", T)], 340, x0=120)
d.add("b2", "<b>Alarm</b> → on-call", NOTE, 420, 310, 140, 56); d.edge("e-b1-b2", "b1", "b2")
strip(d, "s3", 430, 160, "KEEP ANSWERS RIGHT")
d.add("c0", "<b>Thumbs-down +<br>sampled chats</b>", NOTE, 60, 486, 140, 64)
d.add("c1", "<b>Regression set</b><br>with answer keys", NOTE, 280, 486, 150, 64); badge(d, "c1-b", "63", 418, 474)
d.add("c2", "<b>Ship only if<br>accuracy holds</b>", DECISION, 520, 470, 160, 96)
d.edge("e-c0-c1", "c0", "c1"); d.edge("e-c1-c2", "c1", "c2")
legend_drilldown(d, 1290, 580)
save(d)
