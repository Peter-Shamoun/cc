"""python3 docs/architecture/tools/drilldowns.py [nn ...]: lay out the eight drill-downs and write, per drill-down,
docs/architecture/drilldowns/<nn>-<name>.excalidraw (prototype, user's excalidraw style), and the production figure
(ClaimClarity skin) as an HTML page in a temporary folder, which diagram-design's exporters turn into <nn>-<name>.svg
and .png. Needs the diagram-design skill (~/.claude/skills/diagram-design) and a Python with Playwright (PYTHON env).

The Mermaid files next to them (<nn>-<name>.mmd) are the structural source of truth; this file is the layout.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ddlib import DENY, GR as GR_, Diagram, check, to_excalidraw, to_html  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "drilldowns"
AUDIT = ['{', '  "ts": "2026-10-27T14:03:11Z",', '  "customer": "customer_b",', '  "user": "ana@xifin.example",',
         '  "run_id": "r_7f3c",  "seq": 3,', '  "sql": "SELECT payer_name, avg(is_denied::INT) …',
         '          (first 500 chars)",', '  "tables": ["sem.remit_claims"],  "rows": 12,  "ms": 184,',
         '  "escalation_reason": null,', '  "prev_hash": "9c1e…",  "hash": "4b7a…"', '}']
G1, G2, G3, G4, G5, G6 = ("Rule matches\ncustomer B", "PassRole\nparser role B", "IAM + KMS\nparser role B",
                          "Token valid", "Trust policy\nallows this token", "IAM + KMS\nreader role B")


def d00():
    d = Diagram("00-storage", "00 · Storage & isolation",
                "Customer B's prefixes in the one ClaimClarity data bucket, the roles and keys that guard them, and Customer A denied.",
                2300, 970)
    d.text(40, 56, "00 · Storage & isolation", "title")
    d.text(40, 86, "One bucket, one prefix per customer. Shown for customer B; every customer gets the same.", "subtitle")
    d.zone(360, 130, 1160, 720, "ClaimClarity data bucket", "bucket", "s3")
    d.zone(390, 180, 1100, 640, "customer_b/   ·   bucket policy: customer B roles only", "phi")
    y1, h1 = 250, 120
    cy = y1 + h1 // 2
    d.node("sftp", 40, cy - 45, 230, 90, "Transfer Family", "SFTP copy role B", "transfer")
    d.node("raw", 440, y1, 230, h1, "raw/", "bronze · Object Lock", shape="db")
    d.node("silver", 920, y1, 200, h1, "parquet/", "silver · parser role", shape="db")
    d.node("gold", 1230, y1, 220, h1, "semantic_layer/", "gold · CURRENT", shape="db", style="white")
    d.edge([(270, cy), (440, cy)], "write", (350, cy - 12))
    d.edge([(670, cy), (920, cy)], "parse", (795, cy + GR_ + 34))
    d.gate(3, 795, cy, G3, "above", "iam")
    d.edge([(1120, cy), (1230, cy)], "publish", (1175, cy - 12))
    d.node("qs", 1750, cy - 45, 270, 90, "DuckDB query service", "reader role B", "fargate")
    d.edge([(1450, cy), (1750, cy)], "read", (1710, cy - 12))
    d.gate(6, 1620, cy, G6, "below", "iam")
    y2, h2 = 500, 110
    d.node("quar", 440, y2, 230, h2, "quarantine/", "bad files · 1 yr", shape="folder")
    d.node("audit", 1210, y2, 240, h2, "audit/", "write-once · 6 yr", shape="folder")
    c = d.node("rec", 1560, 450, 700, 320, "Audit record", "JSON Lines · one per SQL · hash-chained", shape="code")
    c["inner"] = AUDIT
    d.edge([(1450, 555), (1560, 555)], style="dashed", end=False)
    d.edge([(555, y1 + h1), (555, y2)], "bad files", (567, (y1 + h1 + y2) // 2 + 6), lpos="right", style="dashed")
    y3 = 700
    d.text(440, y3 - 18, "KMS keys", "eyebrow")
    d.node("k_cust", 440, y3, 680, 80, "Customer key B", "raw · parquet · semantic_layer · quarantine", "kms", style="white")
    d.node("k_audit", 1210, y3, 240, 80, "Platform audit key", "statement for B", "kms", style="white")
    d.node("custA", 40, 595, 230, 90, "Customer A roles", "bucket policy + KMS", "role", style="denied")
    d.edge([(270, 640), (390, 640)], "denied", (330, 616), style="deny")
    return d


def d01():
    d = Diagram("01-ingestion", "01 · Ingestion & parse",
                "Lab files arrive over SFTP straight into raw, and the _SUCCESS marker triggers one Fargate parser task that writes silver and publishes a new gold version.",
                2200, 840)
    d.text(40, 56, "01 · Ingestion & parse", "title")
    d.text(40, 86, "One batch for customer B, from the lab's upload to a new CURRENT gold version.", "subtitle")
    X = -260                      # everything right of the SFTP endpoint
    d.zone(640, 140, 1530, 200, "S3 · customer_b/", "phi", "s3")
    y1, h1 = 200, 120
    c1 = y1 + h1 // 2
    d.node("lab", 40, c1 - 45, 200, 90, "Lab", "customer B", "building", style="external")
    d.node("sftp", 340, c1 - 45, 240, 90, "Transfer Family", "SFTP endpoint", "transfer")
    d.node("raw", 940 + X, y1, 270, h1, "raw/", "837 · 835 · 277 · _SUCCESS last", shape="db")
    d.node("silver", 1700 + X, y1, 200, h1, "parquet/", "silver", shape="db")
    d.node("gold", 1990 + X, y1, 210, h1, "semantic_layer/", "gold · new version", shape="db", style="white")
    d.node("current", 2270 + X, c1 - 40, 120, 80, "CURRENT", shape="file", style="white")
    d.edge([(240, c1), (340, c1)], "port 22", (290, c1 - 12))
    d.edge([(580, c1), (940 + X, c1)], "write", (608, c1 - 12))
    d.edge([(2200 + X, c1), (2270 + X, c1)])
    d.text(2330 + X, c1 + 70, "compare-and-set", "note", "middle")
    y2, h2 = 570, 100
    c2 = y2 + h2 // 2
    pe, pr = 1000 + X, 1150 + X   # raw bottom ports: event, read
    d.node("eb", pe - 110, y2, 220, h2, "EventBridge rule", "3 launch retries", "eventbridge")
    d.node("run", 1200 + X, y2, 200, h2, "ECS RunTask", "launcher", "ecs")
    t = d.node("task", 1580 + X, 530, 680, 180, "Fargate parser task · one per batch", icon="fargate", shape="steps",
               inner=["Start", "Parse", "Silver", "Gold", "Exit"])
    t["badge"] = ("lock", "Batch lease")
    d.edge([(pe, y1 + h1), (pe, y2)], "_success", (pe + 12, 372), lpos="right")
    d.gate(1, pe, 470, G1, "left", "eventbridge")
    d.edge([(pe + 110, c2), (1200 + X, c2)])
    d.edge([(1400 + X, c2), (1580 + X, c2)])
    d.gate(2, 1490 + X, c2, G2, "below", "iam")
    d.edge([(pr, y1 + h1), (pr, 450), (1630 + X, 450), (1630 + X, 530)], "read", (1500 + X, 438))
    d.gate(3, 1300 + X, 450, G3, "above", "iam")
    d.edge([(1800 + X, 530), (1800 + X, y1 + h1)], "write", (1812 + X, 506), lpos="right")
    d.gate(3, 1800 + X, 425, G3, "right", "iam")
    d.edge([(2095 + X, 530), (2095 + X, y1 + h1)], "write", (2107 + X, 506), lpos="right")
    d.gate(3, 2095 + X, 425, G3, "right", "iam")
    return d


def d02():
    d = Diagram("02-login", "02 · Login & customer mapping",
                "Sign-in goes from the user to Microsoft Entra ID to Amazon Cognito, which maps the user's group to one customer and issues the token that gate 4 checks.",
                2320, 720)
    d.text(40, 56, "02 · Login & customer mapping", "title")
    d.text(40, 86, "The token is the only place the customer comes from.", "subtitle")
    cy = 300
    d.node("user", 40, cy - 45, 180, 90, "User", "browser", "users")
    d.node("entra", 310, cy - 50, 270, 100, "Microsoft Entra ID", "company sign-in", "entra", style="external")
    d.node("group", 670, cy - 50, 260, 100, "Assigned group", "ClaimClarity · customer B", "users", style="external")
    d.node("cognito", 1030, cy - 50, 260, 100, "Amazon Cognito", "group → one customer", "cognito")
    d.node("token", 1390, cy - 55, 230, 110, "ID token", "customer = B · 60 min", shape="token")
    d.node("cookie", 1710, cy - 45, 190, 90, "Cookie", "httpOnly · Secure", shape="file", style="white")
    d.node("api", 2100, cy - 45, 190, 90, "Chat API", "Fargate", "fargate")
    d.edge([(220, cy), (310, cy)], "sign in", (265, cy - 12))
    d.edge([(580, cy), (670, cy)], "member", (625, cy - 12))
    d.edge([(930, cy), (1030, cy)], "federate", (980, cy - 12))
    d.edge([(1290, cy), (1390, cy)], "issue", (1340, cy - 12))
    d.edge([(1620, cy), (1710, cy)], "store", (1665, cy - 12))
    d.edge([(1900, cy), (2100, cy)], "every request", (2000, cy - GR_ - 16))
    d.gate(4, 2000, cy, G4, "below", "cognito")
    d.node("noacc", 330, 520, 230, 90, "No access", "not in an assigned group", style="denied")
    d.edge([(445, cy + 50), (445, 520)], "denied", (457, 420), lpos="right", style="deny")
    d.zone(290, 180, 670, 200, "Microsoft Entra ID tenant", "plain")
    return d


def d03():
    d = Diagram("03-query-path", "03 · Query path",
                "The run worker sends SQL over Quack to the DuckDB query service, which alone holds customer B's 15-minute STS credentials and reads only customer B's gold; Customer A is denied by the API and by IAM.",
                2400, 1040)
    d.text(40, 56, "03 · Query path", "title")
    d.text(40, 86, "The agent never holds AWS credentials. Only the query service does, for 15 minutes.", "subtitle")
    cy = 330
    d.zone(330, 190, 820, 280, "Run worker · Fargate · no AWS credentials", "worker", "fargate")
    d.zone(1330, 190, 360, 280, "DuckDB query service · Fargate", "worker", "fargate")
    d.zone(1900, 140, 460, 800, "ClaimClarity data bucket", "bucket", "s3")
    d.node("api", 40, cy - 45, 210, 90, "Chat API", "customer from token", "fargate")
    d.node("agent", 370, cy - 50, 250, 100, "Claim Query Agent", "run worker")
    d.nodes[-1]["shape"] = "agent"
    d.node("cli", 690, cy - 45, 220, 90, "ClaimClarity CLI", "cli.py", "terminal")
    d.node("guard", 980, cy - 45, 140, 90, "SQL guard", shape="box")
    d.node("qs", 1370, cy - 60, 290, 120, "Quack server", "customer B · 15-min credentials", "duck")
    d.edge([(250, cy), (370, cy)], "run", (310, cy - 12))
    d.edge([(620, cy), (690, cy)])
    d.edge([(910, cy), (980, cy)])
    d.edge([(1120, cy), (1370, cy)], "sql over quack\nper-run token", (1245, cy - 30))
    # STS (gate 5)
    d.node("sts", 1400, 680, 230, 100, "AWS STS", "reader role B · 15 min", "sts")
    d.edge([(1515, cy + 60), (1515, 680)], "token", (1503, 510), lpos="left", start=True)
    d.gate(5, 1515, 590, G5, "right", "sts")
    # reads and the audit write
    d.node("gold", 1950, 230, 370, 130, "customer_b/\nsemantic_layer/", "current gold · read-only", shape="db", style="white")
    d.node("lossless", 1950, 450, 370, 110, "lossless.*", "after a reasoned escalation", shape="db", style="optional")
    d.node("audit", 1950, 620, 370, 100, "customer_b/audit/", "write-once · audit key · 6 yr", shape="folder")
    d.node("custa", 1950, 790, 370, 110, "customer_a/", shape="db", style="denied")
    d.edge([(1660, 295), (1950, 295)], "read", (1910 - 40, 283))
    d.gate(6, 1790, 295, G6, "above", "iam")
    d.edge([(1660, 320), (1890, 320), (1890, 505), (1950, 505)], style="dashed")
    d.edge([(1660, 350), (1860, 350), (1860, 670), (1950, 670)], "record", (1872, 600), lpos="right")
    d.edge([(1660, 380), (1830, 380), (1830, 845), (1950, 845)], "denied\niam + kms", (1818, 830), lpos="left", style="deny")
    d.edges[-1]["xat"] = (1895, 845)
    c = d.node("rec", 330, 600, 760, 320, "Audit record · customer_b/audit/", "one per SQL · hash-chained", shape="code")
    c["inner"] = AUDIT
    # Customer A at the API
    d.node("reqa", 40, 560, 210, 90, "Request for A", "token names B", style="denied")
    d.edge([(145, 560), (145, cy + 45)], "denied by api", (157, 470), lpos="right", style="deny")
    return d


def d04():
    d = Diagram("04-analyzer", "04 · Analyzer",
                "Inside the run worker, the KPI gate answers with a preset query when one fits; otherwise the Claim Query Agent, given its cached references and examples, decides to answer, clarify or escalate and sends SQL through cli.py and the SQL guard.",
                2440, 1160)
    d.text(40, 56, "04 · Analyzer", "title")
    d.text(40, 86, "The Claim Query Agent is the run worker: one Fargate task per run, Claude Haiku on Amazon Bedrock.", "subtitle")
    d.zone(400, 130, 1700, 950, "Run worker · Fargate  =  Claim Query Agent", "worker", "fargate")
    d.node("ui", 40, 330, 240, 240, "ClaimClarity", "chat in the browser", shape="ui", style="white")
    d.node("kpi", 440, 330, 200, 160, "KPI gate", "preset fits?", shape="decision")
    d.edge([(280, 410), (440, 410)], "question", (340, 398))
    d.edge([(400, 500), (280, 500)], "answer · sse", (340, 488), style="dashed")
    xs = [(740, 230), (1000, 330), (1360, 230), (1620, 250), (1900, 180)]
    names = [("Example bank", "K = 3 similar", "db"), ("References", "cached · rules · glossary · schema", "file"),
             ("Tool & CLI reference", "cached", "file"), ("System prompt + history", "cached", "file"),
             ("Newest prompt", "this turn", "file")]
    for (x, w), (nm, sb, sh) in zip(xs, names):
        d.node(nm, x, 170, w, 110, nm, sb, shape=sh, style="white")
        d.edge([(x + w // 2, 280), (x + w // 2, 360)])
    a = d.node("agent", 720, 360, 1380, 100, "Claim Query Agent", "Claude Haiku · Amazon Bedrock · Strands", "bedrock")
    a["shape"] = "agent"
    d.edge([(640, 410), (720, 410)], "no", (680, 398))
    d.node("preset", 430, 600, 220, 100, "Preset KPI queries", "16 · fixed SQL", shape="file")
    d.edge([(540, 490), (540, 600)], "yes", (552, 556), lpos="right")
    d.node("decide", 780, 560, 260, 200, "Answer · Clarify", "or escalate", shape="decision")
    d.edge([(910, 460), (910, 560)], "first", (922, 516), lpos="right")
    d.node("cli", 1130, 615, 220, 90, "ClaimClarity CLI", "cli.py", "terminal")
    d.node("guard", 1440, 615, 200, 90, "SQL guard", "read-only SELECT", "shieldcheck")
    d.node("qs", 2160, 615, 260, 90, "DuckDB query service", "Quack", "fargate")
    d.edge([(1040, 660), (1130, 660)], "answer", (1085, 648))
    d.edge([(1350, 660), (1440, 660)])
    d.edge([(1640, 660), (2160, 660)], "sql over quack", (1900, 648))
    d.node("lossless", 800, 840, 220, 100, "Full lossless schema", "after escalation only", shape="db", style="optional")
    d.edge([(910, 760), (910, 840)], "escalate", (922, 806), lpos="right", style="dashed")
    d.edge([(540, 700), (540, 1010), (1540, 1010), (1540, 705)], "preset sql", (1290, 998))
    return d


def d05():
    d = Diagram("05-chat-backend", "05 · Chat backend",
                "A question enters through CloudFront and WAF, passes gate 4 at the chat API, waits in the SQS run queue, and is answered by a run worker that calls Bedrock and the DuckDB query service; the answer streams back live over SSE.",
                2400, 880)
    d.text(40, 56, "05 · Chat backend", "title")
    d.text(40, 86, "One run per question. The answer streams back live.", "subtitle")
    cy = 380
    d.node("user", 40, cy - 45, 180, 90, "User", "browser", "users")
    d.node("cf", 300, cy - 50, 270, 100, "CloudFront + WAF", "public entry", "cloudfront")
    d.node("alb", 650, cy - 50, 250, 100, "Load balancer", "internal", "elb")
    d.node("api", 1120, cy - 50, 240, 100, "Chat API", "3 open runs per user", "fargate")
    d.node("sqs", 1440, cy - 50, 220, 100, "Run queue", "Amazon SQS", "sqs")
    d.node("worker", 1740, cy - 50, 280, 100, "Run worker", "Claim Query Agent · 110 s", "fargate")
    d.node("bedrock", 2120, cy - 50, 240, 100, "Amazon Bedrock", "Claude Haiku", "bedrock")
    d.node("qs", 1740, 620, 280, 100, "DuckDB query service", "Quack server", "fargate")
    d.edge([(220, cy), (300, cy)], "https", (260, cy - 12))
    d.edge([(570, cy), (650, cy)])
    d.edge([(900, cy), (1120, cy)])
    d.gate(4, 1010, cy, G4, "below", "cognito")
    d.edge([(1360, cy), (1440, cy)], "enqueue", (1400, cy - 12))
    d.edge([(1660, cy), (1740, cy)])
    d.edge([(2020, cy), (2120, cy)])
    d.edge([(1880, cy + 50), (1880, 620)], "sql over quack", (1892, 540), lpos="right", start=True)
    d.edge([(1880, cy - 50), (1880, 220), (130, 220), (130, cy - 45)], "answer · sse · live", (1000, 208), style="dashed")
    return d


DIAGRAMS = {"00": d00, "01": d01, "02": d02, "03": d03, "04": d04, "05": d05}


SKILL = Path(os.environ.get("DIAGRAM_DESIGN", Path.home() / ".claude/skills/diagram-design")) / "scripts"
PY = os.environ.get("PYTHON", sys.executable)


def main(which):
    OUT.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    for k in which or DIAGRAMS:
        d = DIAGRAMS[k]()
        for prob in check(d):
            print(f"  {d.slug}: {prob}")
        (OUT / f"{d.slug}.excalidraw").write_text(to_excalidraw(d))
        html = tmp / f"{d.slug}.html"
        html.write_text(to_html(d, getattr(d, "legend_items", None)))
        subprocess.run([PY, str(SKILL / "export_svg.py"), str(html), str(OUT / f"{d.slug}.svg")], check=True)
        subprocess.run([PY, str(SKILL / "export_png.py"), str(html), str(OUT / f"{d.slug}.png"), "2"], check=True)
        print(d.slug)


if __name__ == "__main__":
    main(sys.argv[1:])
