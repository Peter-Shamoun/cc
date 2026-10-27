"""Overview (00) and the six area diagrams (10–60): flows read left to right, names only, drill-down badges."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from dlib import DECISION, EXT, NOTE, PHI, STEPBOX, CYL, TEXT, LANE, Diagram, badge, chain, group, legend_drilldown, strip

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

# 40 analyzer: code inside the run worker -----------------------------------------------------------------
d = Diagram("40-analyzer", "Analyzer", 1560, 520)
d.title("CLAIMCLARITY · ANALYZER", "code inside the run worker · every model call goes to Amazon Bedrock")
strip(d, "s1", 70, 380, "HOW ONE QUESTION IS ANSWERED")
d.add("q", "<b>Question</b>", NOTE, 60, 236, 110, 50)
d.add("g", "<b>Standard KPI<br>fits?</b>", DECISION, 240, 206, 150, 110); badge(d, "g-b", "41", 362, 206)
d.add("k", "<b>Standard KPI query</b>", STEPBOX, 500, 120, 200, 56); badge(d, "k-b", "42", 688, 108)
d.add("c", "<b>Custom analysis</b>", STEPBOX, 500, 346, 200, 56); badge(d, "c-b", "43", 688, 334)
d.add("gd", "<b>SQL guard</b>", STEPBOX, 780, 346, 160, 56); badge(d, "gd-b", "44", 928, 334)
d.add("r", "<b>Reader session</b><br>B's gold only", CYL, 1040, 216, 130, 90)
d.add("a", "<b>Answer</b><br>+ SQL + rows", NOTE, 1280, 236, 130, 56)
d.edge("e-q-g", "q", "g")
d.edge("e-g-k", "g", "k", "yes", exit_=(.5, 0), entry=(0, .5))
d.edge("e-g-c", "g", "c", "no", exit_=(.5, 1), entry=(0, .5))
d.edge("e-k-r", "k", "r", "", exit_=(1, .5), entry=(.5, 0))
d.edge("e-c-gd", "c", "gd")
d.edge("e-gd-r", "gd", "r", "", exit_=(1, .5), entry=(.5, 1))
d.edge("e-r-a", "r", "a")
d.icon("bed", "bedrock", "Amazon Bedrock", "", 1360, 400, label="left", size=44); badge(d, "bed-b", "45", 1376, 366)
legend_drilldown(d, 1290, 480)
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
