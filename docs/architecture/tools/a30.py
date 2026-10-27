"""Chat backend (31–34) and operations (61–63) sub-pages: strips, names only, drill-down badges."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from dlib import CYL, DECISION, EXT, LANE, NOTE, PHI, STEP, STEPBOX, TEXT, Diagram, badge, legend_drilldown, strip

SRC = Path(__file__).resolve().parent.parent / "src"
T, F = True, False
KEY_RED = "#DD344C"


def save(d):
    (SRC / f"{d.id}.drawio").write_text(d.xml())


def icon(d, cid, res, name, cx, cy, page=None, tile=T, label="below", color=None):
    d.icon(cid, res, name, "", cx, cy, tile=tile, label=label, color=color)
    if page:
        badge(d, f"{cid}-b", page, *((cx + 20, cy - 42) if label != "above" else (cx + 30, cy - 58)))
    return cid


def box(d, cid, style, html, x, y, w, h, page=None):
    d.add(cid, html, style, x, y, w, h)
    if page:
        badge(d, f"{cid}-b", page, x + w - 12, y - 12)
    return cid


def table(d, cid, title, rows, x, y, w):
    d.add(cid, title, "swimlane;fontStyle=1;childLayout=stackLayout;horizontal=1;startSize=26;fillColor=#EAF0F9;"
          "strokeColor=#4A6BB0;horizontalStack=0;resizeParent=1;resizeLast=0;collapsible=0;marginBottom=0;html=1;"
          "fontSize=12;fontFamily=Helvetica;", x, y, w, 26 + 22 * len(rows))
    for k, r in enumerate(rows):
        d.cells.append(f'<mxCell id="{cid}-r{k}" value="{r}" style="text;strokeColor=none;fillColor=none;align=left;'
                       f'verticalAlign=middle;spacingLeft=8;html=1;fontSize=11;fontFamily=Helvetica;" vertex="1" '
                       f'parent="{cid}"><mxGeometry y="{26 + 22 * k}" width="{w}" height="22" as="geometry"/></mxCell>')
    return cid


# 31 edge -------------------------------------------------------------------------------------------------
d = Diagram("31-edge", "Edge", 1560, 780)
d.title("CLAIMCLARITY · EDGE")
strip(d, "s1", 70, 200, "THE WEB APP")
icon(d, "a0", "users", "XiFin user", 120, 165, tile=F)
icon(d, "a1", "cloudfront", "CloudFront + WAF", 320, 165)
box(d, "a2", STEPBOX, "<b>Nonce function</b><br>Lambda@Edge", 480, 141, 150, 48)
icon(d, "a3", "s3", "S3 · web app", 820, 165, page="61")
d.edge("e-a0-a1", "a0", "a1"); d.edge("e-a1-a2", "a1", "a2", "HTML"); d.edge("e-a2-a3", "a2", "a3")
d.edge("e-a1-a3", "a1", "a3", "assets", exit_=(1, .85), entry=(0, .85),
       waypoints=[(420, 183), (420, 240), (740, 240), (740, 183)])
strip(d, "s2", 290, 200, "API CALLS")
icon(d, "b0", "users", "XiFin user", 120, 385, tile=F)
icon(d, "b1", "cloudfront", "CloudFront + WAF", 320, 385)
icon(d, "b2", "elastic_load_balancing", "Load balancer", 520, 385)
icon(d, "b3", "ecs", "Chat API", 720, 385, page="32")
icon(d, "b4", "cognito", "Amazon Cognito", 920, 385, page="22")
d.edge("e-b0-b1", "b0", "b1"); d.edge("e-b1-b2", "b1", "b2", "/api/*"); d.edge("e-b2-b3", "b2", "b3")
d.edge("e-b3-b4", "b3", "b4", "keys")
strip(d, "s3", 510, 200, "ACCESS LOGS")
icon(d, "c0", "cloudfront", "CloudFront + WAF", 320, 600)
icon(d, "c1", "elastic_load_balancing", "Load balancer", 520, 600)
icon(d, "c2", "s3", "S3 · access logs", 920, 600, page="53")
d.edge("e-c1-c2", "c1", "c2")
d.edge("e-c0-c2", "c0", "c2", "", exit_=(1, .85), entry=(0, .85),
       waypoints=[(420, 618), (420, 672), (840, 672), (840, 618)])
legend_drilldown(d, 1290, 740)
save(d)

# 32 chat turn (sequence) ---------------------------------------------------------------------------------
d = Diagram("32-chat-turn", "Chat turn", 1480, 1000)
d.title("CLAIMCLARITY · CHAT TURN")
lanes = [("l-user", "users", "Browser", F, None), ("l-api", "ecs", "Chat API", T, None),
         ("l-sqs", "sqs", "Run queue", T, "34"), ("l-worker", "fargate", "Run worker", T, "40"),
         ("l-sts", "sts", "STS", T, "22"), ("l-bedrock", "bedrock", "Amazon Bedrock", T, "45"),
         ("l-s3", "s3", "S3 · customer prefix", T, "33")]
xs = {}
for i, (cid, res, name, tile, page) in enumerate(lanes):
    x = 110 + i * 200
    xs[cid] = x
    icon(d, cid, res, name, x, 100, page=page, tile=tile)
    d.line(f"{cid}-life", x, 150, x, 960, dashed=True, arrow=False)
steps = [("l-user", "l-api", "POST question"), ("l-api", "l-s3", "save question"), ("l-api", "l-sqs", "queue run"),
         ("l-sqs", "l-worker", "take run"), ("l-worker", "l-sts", "token → credentials"), ("l-worker", "l-s3", "read gold"),
         ("l-worker", "l-bedrock", "model calls"), ("l-worker", "l-s3", "append events"), ("l-api", "l-s3", "read events"),
         ("l-api", "l-user", "stream events"), ("l-worker", "l-s3", "save answer"),
         None, ("l-user", "l-api", "GET + Last-Event-ID"), ("l-api", "l-user", "replay, then live"),
         None, ("l-user", "l-api", "POST stop"), ("l-api", "l-s3", "cancel flag"), ("l-api", "l-user", "aborted end")]
y, n = 200, 0
sections = iter(["RECONNECT", "STOP"])
for step in steps:
    if step is None:
        d.add(f"sec-{n}", next(sections), LANE, 20, y - 10, 200, 24)
        y += 30
        continue
    a, b, text = step
    back = xs[b] < xs[a]
    d.line(f"m{n}", xs[a], y, xs[b], y, "", dashed=back)
    lx = min(xs[a], xs[b])
    d.add(f"m{n}-n", str(n + 1), STEP, lx + 8, y - 22, 18, 18)
    d.add(f"m{n}-t", text, TEXT, lx + 30, y - 25, 180, 22)
    y += 44
    n += 1
legend_drilldown(d, 1220, 965)
save(d)

# 33 state ------------------------------------------------------------------------------------------------
d = Diagram("33-state", "State", 1560, 800)
d.title("CLAIMCLARITY · CHAT STATE")
strip(d, "s1", 70, 230, "SAVE A CONVERSATION")
icon(d, "a0", "ecs", "Chat API", 120, 185, page="32")
d.add("a-phi", "Customer B's prefix", PHI, 230, 100, 640, 185)
table(d, "a1", "Conversation", ["title", "messages", "pinned · archived", "feedback"], 300, 128, 200)
icon(d, "a2", "data_encryption_key", "User data key", 720, 185, tile=F, color=KEY_RED)
icon(d, "a3", "key_management_service", "Customer B's transcript key", 1060, 185, page="52")
d.edge("e-a0-a1", "a0", "a1", "If-Match"); d.edge("e-a1-a2", "a1", "a2", "sealed by")
d.edge("e-a2-a3", "a2", "a3", "wrapped by")
strip(d, "s2", 320, 200, "STREAM AN ANSWER")
icon(d, "b0", "fargate", "Run worker", 120, 420, page="40")
d.add("b-phi", "Customer B's prefix", PHI, 230, 350, 640, 155)
box(d, "b1", NOTE, "<b>Run-event chunks</b>", 450, 392, 180, 56)
icon(d, "b2", "ecs", "Chat API", 1060, 420, page="32")
d.edge("e-b0-b1", "b0", "b1", "write once"); d.edge("e-b1-b2", "b1", "b2", "replay")
strip(d, "s3", 540, 200, "AUDIT EVERY QUERY")
icon(d, "c0", "fargate", "Run worker", 120, 640)
d.add("c-phi", "Customer B's prefix", PHI, 230, 570, 640, 155)
box(d, "c1", NOTE, "<b>Agent audit record</b>", 450, 612, 180, 56, page="53")
icon(d, "c2", "key_management_service", "Query audit key", 1060, 640, page="52")
d.edge("e-c0-c1", "c0", "c1", "write once"); d.edge("e-c1-c2", "c1", "c2", "encrypted by")
legend_drilldown(d, 1290, 765)
save(d)

# 34 limits -----------------------------------------------------------------------------------------------
d = Diagram("34-limits", "Limits", 1560, 700)
d.title("CLAIMCLARITY · LIMITS")
strip(d, "s1", 70, 280, "EVERY QUESTION")
icon(d, "a0", "users", "XiFin user", 120, 170, tile=F)
icon(d, "a1", "cloudfront", "CloudFront + WAF", 320, 170, page="31")
icon(d, "a2", "ecs", "Chat API", 520, 170, page="32", label="above")
box(d, "a3", DECISION, "<b>Under limits?</b>", 650, 125, 140, 90)
icon(d, "a4", "sqs", "Run queue", 920, 170)
box(d, "a5", NOTE, "<b>429 · Retry-After</b>", 650, 260, 140, 50)
box(d, "a6", NOTE, "<b>S3 · limits object</b>", 450, 260, 140, 50, page="33")
d.edge("e-a0-a1", "a0", "a1"); d.edge("e-a1-a2", "a1", "a2", "rate rule"); d.edge("e-a2-a3", "a2", "a3")
d.edge("e-a3-a4", "a3", "a4", "yes"); d.edge("e-a3-a5", "a3", "a5", "no", exit_=(.5, 1), entry=(.5, 0))
d.edge("e-a2-a6", "a2", "a6", "count", exit_=(.5, 1), entry=(.5, 0))
strip(d, "s2", 370, 260, "EVERY RUN")
icon(d, "b0", "sqs", "Run queue", 120, 470)
icon(d, "b1", "fargate", "Run worker", 320, 470, page="40")
box(d, "b2", DECISION, "<b>Budget left?</b>", 450, 425, 140, 90)
icon(d, "b3", "bedrock", "Amazon Bedrock", 720, 470, page="45")
box(d, "b4", NOTE, "<b>Answer or error</b>", 450, 560, 140, 50)
d.edge("e-b0-b1", "b0", "b1"); d.edge("e-b1-b2", "b1", "b2"); d.edge("e-b2-b3", "b2", "b3", "yes")
d.edge("e-b2-b4", "b2", "b4", "no", exit_=(.5, 1), entry=(.5, 0))
legend_drilldown(d, 1290, 665)
save(d)

# 61 deploy -----------------------------------------------------------------------------------------------
d = Diagram("61-deploy", "Deploy", 1560, 720)
d.title("CLAIMCLARITY · DEPLOY")


def git_ci(prefix, y, page=None):
    box(d, f"{prefix}g", EXT, "<b>Git</b>", 60, y - 24, 100, 48)
    box(d, f"{prefix}c", EXT, "<b>CI</b><br>build + tests", 240, y - 24, 130, 48, page=page)
    d.edge(f"e-{prefix}g-{prefix}c", f"{prefix}g", f"{prefix}c")


strip(d, "s1", 70, 230, "IMAGES")
git_ci("a", 165, page="63")
icon(d, "a1", "ecr", "Amazon ECR", 520, 165)
icon(d, "a2", "ecs", "ECS services", 720, 165)
box(d, "a3", DECISION, "<b>Healthy?</b>", 850, 125, 140, 80)
box(d, "a4", NOTE, "<b>New tasks live</b>", 1070, 141, 140, 48)
box(d, "a5", NOTE, "<b>Roll back</b>", 850, 230, 140, 44)
d.edge("e-ac-a1", "ac", "a1", "push"); d.edge("e-a1-a2", "a1", "a2", "roll"); d.edge("e-a2-a3", "a2", "a3")
d.edge("e-a3-a4", "a3", "a4", "yes"); d.edge("e-a3-a5", "a3", "a5", "no", exit_=(.5, 1), entry=(.5, 0))
strip(d, "s2", 320, 170, "WEB APP")
git_ci("b", 400)
icon(d, "b1", "s3", "S3 · web app", 520, 400)
icon(d, "b2", "cloudfront", "CloudFront", 720, 400, page="31")
d.edge("e-bc-b1", "bc", "b1", "upload"); d.edge("e-b1-b2", "b1", "b2", "invalidate")
strip(d, "s3", 510, 170, "VIEW SQL")
git_ci("c", 590)
icon(d, "c1", "fargate", "Parser task", 520, 590, page="14")
icon(d, "c2", "s3", "S3 · gold", 720, 590, page="15")
d.edge("e-cc-c1", "cc", "c1", "image"); d.edge("e-c1-c2", "c1", "c2", "publish")
legend_drilldown(d, 1290, 690)
save(d)

# 62 monitoring -------------------------------------------------------------------------------------------
d = Diagram("62-monitoring", "Monitoring", 1560, 790)
d.title("CLAIMCLARITY · MONITORING")
strip(d, "s1", 70, 180, "APP LOGS")
icon(d, "a0", "fargate", "ECS tasks", 120, 155)
icon(d, "a1", "cloudwatch_2", "CloudWatch Logs", 320, 155)
icon(d, "a2", "cloudwatch_2", "CloudWatch metrics", 520, 155)
d.edge("e-a0-a1", "a0", "a1"); d.edge("e-a1-a2", "a1", "a2", "filters")
strip(d, "s2", 270, 280, "SERVICE METRICS")
icon(d, "b0", "sqs", "Run queue", 300, 330, label="left")
icon(d, "b1", "elastic_load_balancing", "Load balancer", 300, 410, label="left")
icon(d, "b2", "bedrock", "Amazon Bedrock", 300, 490, label="left")
icon(d, "b3", "cloudwatch_2", "CloudWatch metrics", 620, 410)
d.edge("e-b0-b3", "b0", "b3", "", entry=(0, .25), waypoints=[(460, 330), (460, 397)])
d.edge("e-b1-b3", "b1", "b3")
d.edge("e-b2-b3", "b2", "b3", "", entry=(0, .75), waypoints=[(460, 490), (460, 423)])
strip(d, "s3", 570, 180, "ALARMS")
icon(d, "c0", "cloudwatch_2", "CloudWatch alarms", 120, 660)
box(d, "c1", DECISION, "<b>Over threshold?</b>", 250, 615, 160, 90)
box(d, "c2", NOTE, "<b>Notify on-call</b>", 500, 636, 140, 48)
d.edge("e-c0-c1", "c0", "c1"); d.edge("e-c1-c2", "c1", "c2", "yes")
save(d)

# 63 eval regression --------------------------------------------------------------------------------------
d = Diagram("63-eval-regression", "Eval regression", 1560, 620)
d.title("CLAIMCLARITY · EVAL REGRESSION")
strip(d, "s1", 70, 200, "COLLECT")
box(d, "a0", NOTE, "<b>Thumbs-down +<br>sampled chats</b>", 60, 133, 150, 64)
icon(d, "a1", "s3", "S3 · de-identified exports", 360, 165)
box(d, "a2", NOTE, "<b>Candidate question</b>", 500, 141, 150, 48)
box(d, "a3", CYL, "<b>Regression set</b><br>answer keys", 760, 125, 160, 80)
d.edge("e-a0-a1", "a0", "a1"); d.edge("e-a1-a2", "a1", "a2"); d.edge("e-a2-a3", "a2", "a3", "review")
strip(d, "s2", 290, 270, "EVERY ANALYZER CHANGE")
box(d, "b0", EXT, "<b>Analyzer change</b>", 60, 376, 150, 48)
box(d, "b1", EXT, "<b>CI</b>", 330, 376, 100, 48, page="61")
icon(d, "b2", "fargate", "Run worker · new image", 840, 400, page="40")
box(d, "b3", STEPBOX, "<b>Score</b>", 1000, 376, 120, 48)
box(d, "b4", DECISION, "<b>Accuracy holds?</b>", 1190, 355, 160, 90)
box(d, "b5", NOTE, "<b>Ship</b>", 1420, 376, 100, 48, page="61")
box(d, "b6", NOTE, "<b>Blocked</b>", 1220, 480, 100, 44)
d.edge("e-b0-b1", "b0", "b1"); d.edge("e-b1-b2", "b1", "b2"); d.edge("e-b2-b3", "b2", "b3", "answers")
d.edge("e-a3-b2", "a3", "b2", "questions", exit_=(.5, 1), entry=(.5, 0))
d.edge("e-b3-b4", "b3", "b4"); d.edge("e-b4-b5", "b4", "b5", "yes")
d.edge("e-b4-b6", "b4", "b6", "no", exit_=(.5, 1), entry=(.5, 0))
legend_drilldown(d, 1290, 590)
save(d)
