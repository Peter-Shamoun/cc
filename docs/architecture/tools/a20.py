"""Identity (21–23) and network & security (51–53) detail diagrams, in the style of tools/l1.py.

    python3 docs/architecture/tools/a20.py      # writes src/<id>.drawio for the six pages
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from dlib import (CYL, EXT, LANE, NOTE, PHI, STEP, STEPBOX, TEXT, DECISION, Diagram, badge, group, legend_drilldown,
                  strip)

SRC = Path(__file__).resolve().parent.parent / "src"
GATE = "rhombus;html=1;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;fontStyle=1;fontSize=12;fontFamily=Helvetica;"
GATE_Q = "text;html=1;align=center;verticalAlign=bottom;fontSize=11;fontColor=#1C2B57;fontFamily=Helvetica;whiteSpace=wrap;"
RED_KEY = "#DD344C"


def save(d):
    (SRC / f"{d.id}.drawio").write_text(d.xml())


def gate(d, cid, number, question, cx, cy):
    """AWS gate: navy diamond with its number, a short plain question above it."""
    d.add(cid, str(number), GATE, cx - 15, cy - 15, 30, 30)
    d.add(f"{cid}-q", question, GATE_Q, cx - 60, cy - 52, 120, 34)


# 21 sign-in: sequence layout, user → Entra ID → Cognito → chat API ----------------------------------------
d = Diagram("21-sign-in", "Sign-in", 1180, 640)
d.title("CLAIMCLARITY · SIGN-IN")
lifelines = {"user": 140, "entra": 400, "cognito": 660, "api": 920}
d.icon("user", "users", "XiFin user", "", lifelines["user"], 100, tile=False)
d.add("entra", "<b>Microsoft Entra ID</b>", EXT, lifelines["entra"] - 80, 76, 160, 48)
d.icon("cognito", "cognito", "Amazon Cognito", "", lifelines["cognito"], 100)
d.icon("api", "ecs", "Chat API", "", lifelines["api"], 100); badge(d, "api-b", "22", lifelines["api"] + 20, 58)
for key, x in lifelines.items():
    d.line(f"{key}-life", x, 150, x, 560, dashed=True, arrow=False)
messages = [("user", "entra", "sign in", 200), ("entra", "cognito", "Entra token", 300), ("cognito", "api", "code", 400),
            ("api", "cognito", "exchange code", 450), ("cognito", "api", "ID token", 500), ("api", "user", "session cookie", 540)]
for k, (a, b, text, y) in enumerate(messages):
    xa, xb = lifelines[a], lifelines[b]
    d.line(f"m{k}", xa, y, xb, y, "", dashed=xb < xa)
    left = min(xa, xb)
    d.add(f"m{k}-n", str(k + 1), STEP, left + 10, y - 24, 18, 18)
    d.add(f"m{k}-t", text, TEXT, left + 34, y - 27, 200, 22)
d.add("q-entra", "assigned<br>group?", DECISION, lifelines["entra"] - 45, 218, 90, 62)
d.add("q-cognito", "customer<br>mapped?", DECISION, lifelines["cognito"] - 45, 318, 90, 62)
d.add("token", "<b>ID token</b><br>in session cookie", NOTE, lifelines["user"] - 70, 572, 140, 52)
legend_drilldown(d, 960, 600)
save(d)

# 22 token to credentials: gates 4, 5, 6 ------------------------------------------------------------------
d = Diagram("22-token-to-credentials", "Token to credentials", 1560, 560)
d.title("CLAIMCLARITY · TOKEN TO CREDENTIALS")
strip(d, "s1", 70, 250, "EVERY QUESTION · token to data")
Y = 180
d.add("phi", "Customer B's data", PHI, 1000, 100, 520, 200)
d.add("tok", "<b>ID token</b>", NOTE, 60, Y - 25, 100, 50)
gate(d, "g4", 4, "Token valid?", 240, Y)
d.icon("api", "ecs", "Chat API", "", 380, Y); badge(d, "api-b", "32", 400, Y - 42)
d.icon("wrk", "fargate", "Run worker", "", 560, Y); badge(d, "wrk-b", "40", 580, Y - 42)
gate(d, "g5", 5, "Trust policy allows?", 700, Y)
d.icon("sts", "sts", "STS", "", 840, Y)
d.add("rdr", "<b>Reader session</b><br>15-min credentials", CYL, 1040, Y - 40, 160, 80); badge(d, "rdr-b", "44", 1188, Y - 52)
gate(d, "g6", 6, "IAM + KMS allow?", 1270, Y)
d.icon("gold", "s3", "S3 · B's gold only", "", 1420, Y); badge(d, "gold-b", "15", 1440, Y - 42)
for a, b in [("tok", "g4"), ("g4", "api"), ("api", "wrk"), ("wrk", "g5"), ("g5", "sts"), ("sts", "rdr"), ("rdr", "g6"), ("g6", "gold")]:
    d.edge(f"e-{a}-{b}", a, b)
strip(d, "s2", 340, 170, "CHECKED AGAINST")
d.icon("jwks", "cognito", "Cognito signing keys", "", 240, 430)
d.icon("role", "role", "Customer B's role", "", 700, 430, tile=False, color=RED_KEY)
d.icon("key", "data_encryption_key", "Customer B's key", "", 1270, 430, tile=False, color=RED_KEY); badge(d, "key-b", "52", 1290, 388)
d.edge("e-g4-jwks", "g4", "jwks", "", exit_=(.5, 1), entry=(.5, 0), dashed=True)
d.edge("e-g5-role", "g5", "role", "", exit_=(.5, 1), entry=(.5, 0), dashed=True)
d.edge("e-g6-key", "g6", "key", "", exit_=(.5, 1), entry=(.5, 0), dashed=True)
legend_drilldown(d, 1290, 530)
save(d)

# 23 customer isolation: A's own path on top, every path to B refused below ---------------------------------
d = Diagram("23-customer-isolation", "Customer isolation", 1560, 600)
d.title("CLAIMCLARITY · CUSTOMER ISOLATION")
strip(d, "s1", 70, 230, "CUSTOMER A'S SESSION · allowed")
Y = 180
d.add("tok", "<b>ID token</b><br>customer A", NOTE, 60, Y - 28, 110, 56)
gate(d, "g4", 4, "Token valid?", 250, Y)
d.icon("api", "ecs", "Chat API", "", 390, Y)
gate(d, "g5", 5, "Trust policy allows?", 540, Y)
d.icon("sts", "sts", "STS", "", 690, Y)
d.icon("role", "role", "Customer A's role", "", 850, Y, tile=False, color=RED_KEY)
d.add("rdr", "<b>Reader session</b>", CYL, 980, Y - 35, 150, 70); badge(d, "rdr-b", "44", 1118, Y - 47)
gate(d, "g6", 6, "IAM + KMS allow?", 1240, Y)
d.icon("gold", "s3", "S3 · A's gold", "", 1400, Y)
for a, b in [("tok", "g4"), ("g4", "api"), ("api", "g5"), ("g5", "sts"), ("sts", "role"), ("role", "rdr"), ("rdr", "g6"), ("g6", "gold")]:
    d.edge(f"e-{a}-{b}", a, b)
strip(d, "s2", 330, 220, "CUSTOMER B · every path refused")
d.add("phi", "Customer B's data", PHI, 300, 370, 1220, 160)
d.icon("b-role", "role", "Customer B's role", "", 540, 450, tile=False, color=RED_KEY)
d.icon("b-files", "s3", "S3 · B's prefix", "", 1055, 450)
d.icon("b-key", "data_encryption_key", "Customer B's key", "", 1240, 450, tile=False, color=RED_KEY); badge(d, "b-key-b", "52", 1260, 408)
d.edge("e-g5-brole", "g5", "b-role", "denied", exit_=(.5, 1), entry=(.5, 0), dashed=True)
d.edge("e-rdr-bfiles", "rdr", "b-files", "denied", exit_=(.5, 1), entry=(.5, 0), dashed=True)
d.edge("e-g6-bkey", "g6", "b-key", "denied", exit_=(.5, 1), entry=(.5, 0), dashed=True)
legend_drilldown(d, 1290, 570)
save(d)

# 51 network ---------------------------------------------------------------------------------------------
d = Diagram("51-network", "Network", 1560, 760)
d.title("CLAIMCLARITY · NETWORK")
d.add("cloud", "AWS Cloud", group("cloud"), 210, 70, 1320, 640)
d.icon("u", "users", "XiFin user", "", 100, 250, tile=False)
d.icon("cf", "cloudfront", "CloudFront + WAF", "", 310, 250); badge(d, "cf-b", "31", 330, 208)
d.icon("cog", "cognito", "Amazon Cognito", "", 310, 590); badge(d, "cog-b", "21", 330, 548)
d.add("vpc", "VPC · two AZs", group("vpc"), 410, 110, 640, 570)
d.add("sub", "Private subnets", group("subnet"), 430, 150, 600, 370)
d.icon("alb", "elastic_load_balancing", "Load balancer", "", 520, 250)
d.icon("api", "ecs", "Chat API", "", 700, 250); badge(d, "api-b", "32", 720, 208)
d.icon("wrk", "fargate", "Run workers", "", 700, 350); badge(d, "wrk-b", "40", 720, 308)
d.icon("prs", "fargate", "Parser", "", 700, 450); badge(d, "prs-b", "14", 720, 408)
d.icon("vpe", "endpoints", "VPC endpoints", "", 940, 350, tile=False, color="#8C4FFF")
d.add("egr", "Egress subnet", group("subnet"), 430, 540, 600, 120)
d.icon("nat", "vpc", "NAT + Network Firewall", "", 520, 600)
d.add("man", "", "rounded=0;html=1;fillColor=none;strokeColor=#D5DCE8;dashed=1;", 1090, 110, 410, 570)
for i, (res, name, page) in enumerate([("s3", "S3", "52"), ("key_management_service", "KMS", "52"), ("sts", "STS", "22"),
                                       ("bedrock", "Bedrock", "45"), ("ecr", "ECR", "61"), ("sqs", "SQS", "34"),
                                       ("cloudwatch_2", "CloudWatch Logs", "53"), ("secrets_manager", "Secrets Manager", None)]):
    cx, cy = 1190 + (i % 2) * 210, 180 + (i // 2) * 130
    d.icon(f"m{i}", res, name, "", cx, cy, size=48)
    if page:
        badge(d, f"m{i}-b", page, cx + 18, cy - 40)
d.edge("e-u-cf", "u", "cf")
d.edge("e-cf-alb", "cf", "alb")
d.edge("e-alb-api", "alb", "api")
d.edge("e-api-vpe", "api", "vpe", "", exit_=(1, .5), entry=(0, .2))
d.edge("e-wrk-vpe", "wrk", "vpe", "", exit_=(1, .5), entry=(0, .5))
d.edge("e-prs-vpe", "prs", "vpe", "", exit_=(1, .5), entry=(0, .8))
d.edge("e-vpe-man", "vpe", "man", "private", exit_=(1, .5), entry=(0, .421))
d.edge("e-api-nat", "api", "nat", "", exit_=(0, .85), entry=(1, .5), waypoints=[(610, 268), (610, 600)])
d.edge("e-nat-cog", "nat", "cog", "Cognito only", exit_=(0, .5), entry=(1, .5))
legend_drilldown(d, 1290, 730)
save(d)

# 52 encryption keys: one strip per kind of data --------------------------------------------------------------
d = Diagram("52-encryption-keys", "Encryption keys", 1560, 620)
d.title("CLAIMCLARITY · ENCRYPTION KEYS")
rows = [("lake", "LAKE", "Customer B's lake roles", "S3 · B's raw, silver, gold", "12", "Customer B's lake key"),
        ("chat", "TRANSCRIPTS", "Customer B's transcript roles", "S3 · B's transcripts", "33", "Customer B's transcript key"),
        ("audit", "QUERY AUDIT", "Customer B's audit roles", "S3 · B's query audit", "53", "Query-audit key")]
for i, (key, title, role, prefix, page, kms) in enumerate(rows):
    y0 = 70 + i * 170
    cy = y0 + 85
    strip(d, f"s{i}", y0, 150, title)
    d.add(f"phi-{key}", "", PHI, 560, y0 + 30, 300, 110)
    d.icon(f"r-{key}", "role", role, "", 300, cy, tile=False, color=RED_KEY)
    d.icon(f"p-{key}", "s3", prefix, "", 710, cy); badge(d, f"p-{key}-b", page, 730, cy - 42)
    d.icon(f"k-{key}", "data_encryption_key", kms, "", 1120, cy, tile=False, color=RED_KEY)
    d.edge(f"e-r-{key}", f"r-{key}", f"p-{key}", "TLS")
    d.edge(f"e-p-{key}", f"p-{key}", f"k-{key}", "SSE-KMS")
legend_drilldown(d, 1290, 590)
save(d)

# 53 audit logging: one strip per record ----------------------------------------------------------------
d = Diagram("53-audit-logging", "Audit logging", 1560, 620)
d.title("CLAIMCLARITY · AUDIT LOGGING")
strip(d, "s0", 70, 150, "AWS CALLS")
d.icon("a0", "role", "Every role", "", 300, 155, tile=False, color=RED_KEY)
d.icon("a1", "cloudtrail", "CloudTrail", "", 710, 155)
d.icon("a2", "s3", "S3 · trail logs", "", 1120, 155)
d.edge("e-a0-a1", "a0", "a1"); d.edge("e-a1-a2", "a1", "a2")
strip(d, "s1", 240, 150, "AGENT SQL")
d.add("phi", "", PHI, 970, 270, 300, 110)
d.icon("b0", "fargate", "Run worker", "", 300, 325); badge(d, "b0-b", "44", 320, 283)
d.add("b1", "<b>Agent audit</b><br>hash-chained", STEPBOX, 640, 297, 140, 56)
d.icon("b2", "s3", "S3 · B's query audit", "", 1120, 325)
d.edge("e-b0-b1", "b0", "b1"); d.edge("e-b1-b2", "b1", "b2")
strip(d, "s2", 410, 150, "APP LOGS")
d.icon("c0", "ecs", "Chat API + workers", "", 300, 495)
d.add("c1", "<b>PHI log filter</b>", STEPBOX, 640, 467, 140, 56)
d.icon("c2", "cloudwatch_2", "CloudWatch Logs", "", 1120, 495); badge(d, "c2-b", "62", 1140, 453)
d.edge("e-c0-c1", "c0", "c1"); d.edge("e-c1-c2", "c1", "c2")
legend_drilldown(d, 1290, 590)
save(d)
print("ok")
