"""Three alternative structures for the overview, for the user to choose from."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from dlib import AGENT, BOX, CYL, EXT, LANE, NOTE, PHI, STEP, TEXT, Diagram, group

OUT = Path(__file__).resolve().parent.parent / "variants"
OUT.mkdir(exist_ok=True)

# ---- B: three flows, each one straight line --------------------------------------------------------------
d = Diagram("variant-b", "B · three flows", 1560, 860)
d.title("CLAIMCLARITY · HOW IT WORKS", "three flows, each read left to right")
STRIP = "rounded=1;arcSize=4;html=1;fillColor=#F6F8FC;strokeColor=#D5DCE8;"
for i, (y, h, text) in enumerate([(70, 190, "1 · DATA IN: a lab's daily files become queryable tables"),
                                  (280, 170, "2 · SIGN-IN: a XiFin user gets a token that names one customer"),
                                  (470, 330, "3 · A QUESTION: from the chat box to an answer computed by the database")]):
    d.add(f"strip{i}", "", STRIP, 20, y, 1520, h)
    d.add(f"strip{i}-t", text, LANE, 36, y + 6, 900, 24)
y = 165
d.icon("b1", "corporate_data_center", "Customer lab", "837 · 835 · 277", 120, y, tile=False)
d.icon("b2", "transfer_family", "SFTP intake", "Transfer Family", 340, y)
d.icon("b3", "s3", "Raw prefix", "files as received", 560, y)
d.icon("b4", "eventbridge", "EventBridge", "batch complete", 780, y)
d.icon("b5", "fargate", "Parser task", "runs as the customer's role", 1000, y)
d.icon("b6", "s3", "Lake prefix", "silver · gold (sem.*)", 1220, y)
for a, b in [("b1", "b2"), ("b2", "b3"), ("b3", "b4"), ("b4", "b5"), ("b5", "b6")]:
    d.edge(f"e-{a}-{b}", a, b)
d.add("b-key", "Each customer: own prefix, own KMS key, own IAM role", TEXT + "fontColor=#3A5A9E;", 1290, 150, 230, 40)
y = 370
d.icon("s1", "users", "XiFin user", "browser", 120, y, tile=False)
d.icon("s2", "cognito", "Amazon Cognito", "issues the id token", 340, y)
d.add("s3", "<b>Microsoft Entra ID</b><br>company sign-in", EXT, 480, y - 26, 180, 52)
d.add("s4", "<b>id token</b><br>names the customer<br>httpOnly session cookie", NOTE, 760, y - 34, 150, 68)
d.edge("e-s1-s2", "s1", "s2", "sign in")
d.edge("e-s2-s3", "s2", "s3", "federation", both=True)
d.edge("e-s3-s4", "s3", "s4", "")
y = 580
d.icon("q1", "users", "XiFin user", "asks a question", 120, y, tile=False)
d.icon("q2", "cloudfront", "CloudFront + WAF", "app and API entry", 300, y)
d.icon("q3", "ecs", "Chat API", "checks the token", 480, y)
d.icon("q4", "sqs", "Run queue", "one run per question", 660, y)
d.icon("q5", "fargate", "Run worker", "", 840, y)
d.add("q6", "<b>ANALYZER</b><br>KPI gate → standard KPI<br>or custom analysis", AGENT, 960, y - 40, 190, 80)
d.icon("q7", "s3", "Lake prefix", "reads gold only", 1270, y)
d.icon("q8", "bedrock", "Amazon Bedrock", "Claude Haiku", 1055, 720, label="right")
d.icon("q9", "sts", "STS", "15-min role creds", 840, 720, label="right")
for a, b in [("q1", "q2"), ("q2", "q3"), ("q3", "q4"), ("q4", "q5"), ("q5", "q6"), ("q6", "q7")]:
    d.edge(f"e-{a}-{b}", a, b)
d.edge("e-q6-q8", "q6", "q8", "", exit_=(.5, 1), entry=(.5, 0))
d.edge("e-q5-q9", "q5", "q9", "", exit_=(.5, 1), entry=(.5, 0))
d.add("q-note", "The answer streams back to the browser (SSE). Conversation and run events are saved in the customer's prefix.",
      TEXT + "fontColor=#3A5A9E;", 1340, 640, 190, 90)
(OUT / "variant-b.drawio").write_text(d.xml())

# ---- C: one question as a numbered sequence -------------------------------------------------------------
d = Diagram("variant-c", "C · one question, step by step", 1500, 820)
d.title("CLAIMCLARITY · ONE QUESTION, STEP BY STEP", "time runs down the page")
lanes = [("c-browser", "users", "Browser", False), ("c-edge", "cloudfront", "CloudFront + WAF", True),
         ("c-api", "ecs", "Chat API", True), ("c-sqs", "sqs", "Run queue (SQS)", True), ("c-worker", "fargate", "Run worker + analyzer", True),
         ("c-sts", "sts", "STS", True), ("c-bedrock", "bedrock", "Bedrock (Haiku)", True), ("c-s3", "s3", "S3 · customer prefix", True)]
xs = {}
for i, (cid, res, name, tile) in enumerate(lanes):
    x = 110 + i * 180
    xs[cid] = x
    d.icon(cid, res, name, "", x, 100, tile=tile)
    d.line(f"{cid}-life", x, 150, x, 790, dashed=True, arrow=False)
steps = [("c-browser", "c-edge", "POST question + session cookie"), ("c-edge", "c-api", "forward (WAF rules passed)"),
         ("c-api", "c-sqs", "token valid → queue the run"), ("c-api", "c-browser", "open answer stream (SSE)"),
         ("c-sqs", "c-worker", "worker takes the run"), ("c-worker", "c-sts", "token → customer role"),
         ("c-sts", "c-worker", "15-minute credentials"), ("c-worker", "c-bedrock", "KPI gate, then KPI or custom path"),
         ("c-worker", "c-s3", "read current gold version"), ("c-worker", "c-s3", "write run events + audit"),
         ("c-api", "c-browser", "stream steps, SQL and answer")]
for k, (a, b, text) in enumerate(steps):
    y = 200 + k * 52
    back = xs[b] < xs[a]
    d.line(f"m{k}", xs[a], y, xs[b], y, "", dashed=back)
    lx = min(xs[a], xs[b])
    d.add(f"m{k}-n", str(k + 1), STEP, lx + 8, y - 22, 18, 18)
    d.add(f"m{k}-t", text, TEXT + "fontSize=11;", lx + 30, y - 25, 330, 22)
(OUT / "variant-c.drawio").write_text(d.xml())

# ---- D: where everything runs (boundaries) --------------------------------------------------------------
d = Diagram("variant-d", "D · where it runs", 1500, 900)
d.title("CLAIMCLARITY · WHERE IT RUNS", "network boundaries, left = internet, right = AWS managed services")
d.add("lane-net", "INTERNET", LANE, 40, 70, 200, 24)
d.icon("d-users", "users", "XiFin users", "browser", 110, 190, tile=False)
d.icon("d-labs", "corporate_data_center", "Customer labs", "SFTP", 110, 420, tile=False)
d.add("d-entra", "<b>Microsoft Entra ID</b><br>company sign-in", EXT, 30, 620, 160, 52)
d.add("d-cloud", "AWS Cloud · ClaimClarity account", group("cloud"), 250, 60, 1230, 820)
d.icon("d-cf", "cloudfront", "CloudFront + WAF", "", 370, 190)
d.icon("d-tf", "transfer_family", "Transfer Family", "", 370, 420)
d.icon("d-cog", "cognito", "Cognito", "", 370, 646)
d.add("d-vpc", "VPC", group("vpc"), 480, 110, 520, 700)
d.add("d-sub", "Private subnets · no public IPs", group("subnet"), 510, 160, 460, 520)
d.icon("d-alb", "elastic_load_balancing", "Load balancer", "internal", 620, 190)
d.icon("d-api", "ecs", "Chat API", "ECS service", 830, 190)
d.icon("d-wrk", "fargate", "Run workers", "ECS service · analyzer", 830, 370)
d.icon("d-prs", "fargate", "Parser task", "one task per batch", 830, 560)
d.icon("d-vpe", "vpc", "VPC endpoints", "private access to AWS", 740, 750, label="right")
d.add("d-man", "", "rounded=0;html=1;fillColor=#F6F8FC;strokeColor=#D5DCE8;", 1060, 110, 390, 700)
d.add("d-man-t", "AWS MANAGED SERVICES", LANE, 1076, 116, 300, 24)
for i, (res, name, line) in enumerate([("s3", "S3", "lake + chat state"), ("key_management_service", "KMS", "one key per customer"),
                                       ("sts", "STS", "role credentials"), ("sqs", "SQS", "run queue"), ("eventbridge", "EventBridge", "batch trigger"),
                                       ("bedrock", "Bedrock", "Claude Haiku"), ("cloudwatch_2", "CloudWatch", "logs + alarms"),
                                       ("cloudtrail", "CloudTrail", "audit")]):
    col, row = i % 2, i // 2
    d.icon(f"d-m{i}", res, name, line, 1130 + col * 190, 200 + row * 160, label="below", size=48)
d.edge("e-u-cf", "d-users", "d-cf", "HTTPS")
d.edge("e-cf-alb", "d-cf", "d-alb")
d.edge("e-alb-api", "d-alb", "d-api")
d.edge("e-api-wrk", "d-api", "d-wrk", "", exit_=(.5, 1), entry=(.5, 0))
d.edge("e-l-tf", "d-labs", "d-tf", "SFTP")
d.edge("e-e-cog", "d-entra", "d-cog", "", both=True)
d.edge("e-vpe-man", "d-vpe", "d-man", "", exit_=(1, .5), entry=(0, .918))
(OUT / "variant-d.drawio").write_text(d.xml())
