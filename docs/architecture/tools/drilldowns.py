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


def lift(d, dy, below=100):
    """Move everything under y = below up by dy (to close an empty band under the title)."""
    for coll in (d.zones, d.nodes, d.gates, d.texts):
        for e in coll:
            if e["y"] > below:
                e["y"] -= dy
    for e in d.edges:
        e["pts"] = [(x, y - dy) for x, y in e["pts"]]
        if e["at"]:
            e["at"] = (e["at"][0], e["at"][1] - dy)
        if e.get("xat"):
            e["xat"] = (e["xat"][0], e["xat"][1] - dy)
    d.h -= dy


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
    d.edge([(670, cy), (920, cy)], "parse", (795, cy - GR_ - 14))
    d.gate(3, 795, cy, G3, "below", "iam")
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
                "The user signs in at Microsoft Entra ID; Amazon Cognito federates the sign-in, maps the user's assigned group to exactly one customer and issues the ID token that gate 4 checks on every request.",
                2400, 1200)
    d.text(40, 56, "02 · Login & customer mapping", "title")
    d.text(40, 86, "Sign-in order: user → Entra ID → Cognito → token. The token is the only place the customer comes from.", "subtitle")
    d.zone(40, 160, 380, 860, "Browser", "plain")
    d.node("user", 80, 230, 300, 100, "User", "company account", "users")
    d.node("cookie", 80, 790, 300, 130, "Session cookie", "ID token · httpOnly · Secure", shape="file", style="white")
    d.zone(540, 160, 580, 520, "Microsoft Entra ID tenant", "plain")
    d.node("entra", 580, 230, 500, 100, "Microsoft Entra ID", "company sign-in", "entra", style="external")
    d.node("group", 580, 470, 500, 120, "Assigned group", "ClaimClarity · customer B", "users", style="external")
    d.edge([(380, 280), (580, 280)], "sign in", (480, 268))
    d.edge([(830, 330), (830, 470)], "member of", (842, 404), lpos="right")
    d.zone(1240, 160, 660, 860, "Amazon Cognito", "dashed", "cognito")
    d.node("pool", 1280, 230, 580, 100, "User pool", "federated with Entra ID", "cognito")
    d.edge([(1080, 280), (1280, 280)], "federate", (1180, 268))
    t = d.node("map", 1280, 420, 580, 220, "Group → customer", shape="table", style="white")
    t["inner"] = [["Assigned group", "Customer"], ["ClaimClarity · customer A", "customer_a"], ["ClaimClarity · customer B", "customer_b"]]
    t["colw"] = 330
    d.edge([(1080, 530), (1280, 530)], "group claim", (1180, 518))
    d.edge([(1570, 330), (1570, 420)], "look up", (1582, 384), lpos="right")
    d.node("token", 1380, 720, 380, 130, "ID token", "customer = B · 60 min", shape="token")
    d.edge([(1570, 640), (1570, 720)], "issue", (1582, 688), lpos="right")
    d.edge([(1380, 820), (380, 820)], "set cookie", (880, 808), style="dashed")
    d.node("api", 2080, 840, 260, 100, "Chat API", "Fargate", "fargate")
    d.edge([(380, 890), (2080, 890)], "every request", (880, 878))
    d.gate(4, 1990, 890, G4, "above", "cognito")
    d.node("reject", 1880, 1030, 220, 80, "Rejected", "sign in again", style="denied")
    d.edge([(1990, 926), (1990, 1030)], "invalid", (2002, 984), lpos="right", style="deny")
    return d


def d03():
    d = Diagram("03-query-path", "03 · Query path",
                "The run worker sends SQL over Quack to the DuckDB query service, which alone exchanges the token at STS for customer B's 15-minute credentials and reads only customer B's gold; customer A's prefix is denied.",
                2620, 1000)
    d.text(40, 56, "03 · Query path", "title")
    d.text(40, 86, "Only the DuckDB query service touches the data, with 15-minute credentials for one customer.", "subtitle")
    d.zone(40, 300, 660, 520, "Run worker · Fargate", "worker", "fargate")
    a = d.node("agent", 80, 380, 580, 120, "Claim Query Agent", "Claude Haiku")
    a["shape"] = "agent"
    d.node("cli", 80, 600, 250, 110, "cli.py", "ClaimClarity CLI", "terminal")
    d.node("guard", 410, 600, 250, 110, "SQL guard", "read-only", "shieldcheck")
    d.edge([(205, 500), (205, 600)])
    d.edge([(330, 655), (410, 655)])
    d.text(80, 780, "the agent never holds AWS credentials", "note")
    d.zone(860, 300, 560, 520, "DuckDB query service · Fargate", "worker", "fargate")
    d.node("qs", 900, 480, 400, 200, "Quack server", "customer B · 15-min credentials", "duck")
    d.edge([(660, 630), (900, 630)], "sql · per-run token", (780, 618))
    d.edge([(900, 680 - 10), (660, 680 - 10)], "rows", (780, 658))
    d.node("sts", 1060, 120, 280, 100, "AWS STS", "reader role B", "sts")
    d.edge([(1150, 480), (1150, 220)], "token", (1138, 420), lpos="left")
    d.gate(5, 1150, 260, G5, "left", "sts")
    d.edge([(1260, 220), (1260, 480)], "15-min credentials", (1272, 420), lpos="right")
    d.zone(1760, 160, 820, 740, "ClaimClarity data bucket", "bucket", "s3")
    d.zone(1790, 220, 760, 450, "customer_b/", "phi")
    d.node("gold", 1960, 280, 480, 120, "semantic_layer/", "gold · current version", shape="db", style="white")
    d.node("lossless", 1960, 500, 480, 120, "lossless.*", "PHI-stripped", shape="db", style="optional")
    d.edge([(1300, 500), (1520, 500), (1520, 340), (1960, 340)], "read-only", (1805, 328), lpos="right")
    d.gate(6, 1520, 420, G6, "right", "iam")
    d.edge([(1300, 560), (1960, 560)], "after escalation", (1805, 548), lpos="right", style="dashed")
    d.gate(6, 1650, 560, G6, "below", "iam")
    d.zone(1790, 710, 760, 160, "customer_a/  ·  denied by IAM + KMS", "denied")
    d.node("gold_a", 1960, 760, 480, 90, "semantic_layer/", shape="db", style="denied")
    d.edge([(1300, 650), (1460, 650), (1460, 805), (1790, 805)], "denied", (1650, 793), style="deny")
    d.edges[-1]["xat"] = (1540, 805)
    return d


def d04():
    d = Diagram("04-analyzer", "04 · Analyzer",
                "Inside the run worker, the KPI gate answers with a preset query when one fits; otherwise the Claim Query Agent, fed by its example bank, cached references and prompts, writes CLI calls with SQL that pass the SQL guard on the way to the DuckDB query service.",
                2960, 1290)
    d.text(40, 56, "04 · Analyzer", "title")
    d.text(40, 86, "The Claim Query Agent is the run worker: one Fargate task per run, Claude Haiku on Amazon Bedrock.", "subtitle")
    # frontend
    d.node("ui", 40, 330, 260, 400, "ClaimClarity", "chat in the browser", shape="ui", style="white")
    d.edge([(300, 440), (480, 440)], "question", (390, 428))
    d.edge([(480, 620), (300, 620)], "answer · sse", (390, 608), style="dashed")
    d.text(312, 652, "live steps, or a\nclarifying question", "note")
    # run worker = KPI gate + Claim Query Agent
    d.zone(480, 140, 1600, 1060, "Run worker · Fargate", "worker", "fargate")
    d.zone(510, 200, 300, 820, "KPI gate", "plain")
    d.node("kpi", 545, 360, 230, 160, "Preset fits?", shape="decision")
    d.node("preset", 550, 640, 220, 110, "Preset KPI queries", "16 · fixed SQL", shape="file")
    d.edge([(660, 520), (660, 640)], "yes · fill params", (672, 586), lpos="right")
    d.zone(840, 200, 1210, 820, "Claim Query Agent", "dashed")
    d.node("bank", 870, 330, 190, 520, "Solved example\nbank", "questions · SQL · answers", shape="db", style="white")
    d.zone(1180, 250, 580, 210, "References · prompt cached", "plain")
    d.node("rules", 1200, 300, 170, 130, "Rules", "past mistakes", shape="file", style="white")
    d.node("gloss", 1385, 300, 170, 130, "EDI glossary", shape="file", style="white")
    d.node("schema", 1570, 300, 170, 130, "Semantic schema", "+ allowed values", shape="file", style="white")
    d.node("sys", 1800, 250, 220, 120, "System prompt\n+ history", "cached", shape="file", style="white")
    d.node("newest", 1800, 380, 220, 80, "Newest prompt", shape="file", style="white")
    a = d.node("agent", 1150, 560, 870, 150, "Claim Query Agent", "Claude Haiku · first: answer, clarify or escalate", "bedrock")
    a["shape"] = "agent"
    for x in (1285, 1470, 1655):
        d.edge([(x, 430), (x, 560)])
    d.edge([(1910, 460), (1910, 560)])
    d.edge([(775, 440), (812, 440), (812, 268), (1110, 268), (1110, 600), (1162, 600)], "no", (960, 256))
    d.edge([(1060, 660), (1162, 660)], "k = 3", (1106, 648))
    d.node("tools", 870, 890, 190, 110, "Tool & CLI\nreference", "prompt cached", shape="file", style="white")
    d.edge([(1060, 945), (1220, 945), (1220, 710)])
    d.node("lossless", 1420, 820, 330, 150, "Full lossless schema", "only after the agent\nescalates with a reason", shape="db", style="optional")
    d.edge([(1585, 710), (1585, 820)], "escalate", (1597, 770), lpos="right", style="dashed")
    # tools: cli.py + SQL guard
    d.zone(2280, 400, 640, 400, "Tools", "dashed")
    d.node("cli", 2320, 560, 190, 150, "cli.py", "ClaimClarity CLI", "terminal")
    d.node("guard", 2600, 545, 280, 180, "SQL guard", "read-only SELECT\nfails fast on the schema", "shieldcheck")
    d.edge([(2510, 635), (2600, 635)])
    d.edge([(2000, 590), (2320, 590)], "cli calls with sql", (2180, 578))
    d.edge([(2320, 635), (2000, 635)], "results · iterate", (2180, 623))
    d.edge([(2000, 680), (2320, 680)], "final query + answer", (2180, 668), style="final")
    d.text(2180, 712, "re-run by the server", "note", "middle")
    d.edge([(660, 750), (660, 1110), (2740, 1110), (2740, 725)], "preset sql", (1700, 1098))
    d.node("qs", 2600, 180, 280, 100, "DuckDB query service", "Quack server", "fargate")
    d.edge([(2740, 545), (2740, 280)], "sql over quack", (2752, 420), lpos="right")
    d.text(2752, 446, "per-run token", "note")
    return d


def d05():
    d = Diagram("05-chat-backend", "05 · Chat backend",
                "A question passes CloudFront and WAF, the internal load balancer and gate 4 at the chat API, waits in the SQS run queue, and is answered by the run worker, which calls Bedrock and the DuckDB query service; the answer streams back live over SSE.",
                2520, 1110)
    d.text(40, 56, "05 · Chat backend", "title")
    d.text(40, 86, "One run per question. The answer streams back live.", "subtitle")
    d.node("user", 40, 375, 200, 100, "User", "browser", "users")
    d.zone(320, 290, 330, 290, "Edge", "plain")
    d.node("cf", 350, 355, 270, 140, "CloudFront + WAF", "public entry", "cloudfront")
    d.edge([(240, 405), (350, 405)], "question", (295, 393))
    d.edge([(350, 455), (240, 455)], "answer · sse", (295, 487), style="dashed")
    d.zone(740, 270, 760, 560, "Chat service", "worker", "fargate")
    d.node("alb", 780, 360, 230, 130, "Load balancer", "internal", "elb")
    d.node("api", 1180, 350, 290, 150, "Chat API", "3 unfinished runs per user", "fargate")
    d.edge([(620, 405), (780, 405)])
    d.edge([(780, 455), (620, 455)], style="dashed")
    d.edge([(1010, 405), (1180, 405)])
    d.gate(4, 1095, 405, G4, "above", "cognito")
    d.edge([(1180, 455), (1010, 455)], style="dashed")
    d.node("sqs", 1180, 640, 290, 110, "Run queue", "Amazon SQS", "sqs")
    d.edge([(1325, 500), (1325, 640)], "enqueue", (1337, 576), lpos="right")
    d.zone(1620, 270, 560, 560, "Run worker · Fargate", "worker", "fargate")
    a = d.node("agent", 1660, 620, 480, 150, "Claim Query Agent", "110 s run deadline")
    a["shape"] = "agent"
    d.edge([(1470, 695), (1672, 695)], "one run", (1560, 683))
    d.edge([(1900, 620), (1900, 425), (1470, 425)], "answer stream", (1685, 413), style="dashed")
    d.node("bedrock", 2260, 640, 230, 110, "Amazon Bedrock", "Claude Haiku", "bedrock")
    d.edge([(2128, 670), (2260, 670)], "prompt", (2194, 658))
    d.edge([(2260, 720), (2128, 720)], "tokens", (2194, 752), style="dashed")
    d.node("qs", 1720, 920, 360, 100, "DuckDB query service", "Quack server", "fargate")
    d.edge([(1840, 770), (1840, 920)], "sql · quack", (1828, 876), lpos="left")
    d.edge([(1960, 920), (1960, 770)], "rows", (1972, 876), lpos="right")
    lift(d, 120)
    return d


DIAGRAMS = {"00": d00, "01": d01, "02": d02, "03": d03, "05": d05}   # 04: the user excalidraw, edited by analyzer04.py


SKILL = Path(os.environ.get("DIAGRAM_DESIGN", Path.home() / ".claude/skills/diagram-design")) / "scripts"
PY = os.environ.get("PYTHON", sys.executable)


MAX_PNG = 4000                # longest side of a drill-down PNG; the SVG keeps full detail


def cap_png(png):
    from PIL import Image
    im = Image.open(png)
    k = MAX_PNG / max(im.size)
    if k < 1:
        im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS).save(png, optimize=True)


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
        png = OUT / f"{d.slug}.png"
        subprocess.run([PY, str(SKILL / "export_png.py"), str(html), str(png), str(min(2, max(1, MAX_PNG / d.w)))], check=True)
        cap_png(png)
        print(d.slug)


if __name__ == "__main__":
    main(sys.argv[1:])
