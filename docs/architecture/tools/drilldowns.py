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
from ddlib import DENY, GR as GR_, _tw, Diagram, check, to_excalidraw, to_html  # noqa: E402

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
    d.edge([(1120, cy), (1230, cy)], "built from", (1175, cy - 12), style="built")
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
                2200, 1330)
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
    # how gold is built: the parser's gold step
    zy = 780
    d.zone(40, zy, 2120, 460, "How gold is built · the parser's gold step", "dashed")
    gs = 1320 + 50 + (680 - 100) * 3 / 4          # the "Gold" step on the parser timeline
    d.edge([(gs, 710), (gs, zy)], "gold step", (gs + 12, 752), lpos="right", style="dashed")
    sv = d.node("silver_t", 80, zy + 60, 460, 200, "parquet/ · silver", shape="table", style="white")
    sv["inner"] = [["schema", "tables (lossless, one per EDI structure)"], ["edi.*", "source_file · segment · entity · amount …"],
                   ["c837.*", "claim · service_line · hl · other_payer …"], ["r835.*", "payment · claim_payment · remark …"]]
    sv["colw"] = 90
    vs = d.node("views", 640, zy + 90, 300, 120, "View SQL", "semantic_layer_views.sql\nversioned in Git · every batch", "git", shape="file", style="white")
    rf = d.node("ref", 640, zy + 290, 300, 130, "Reference seeds", shape="table", style="white")
    rf["inner"] = [["ref.*"], ["payer_alias · denial_carc"], ["claim_status …"]]
    si = d.node("semint", 1040, zy + 60, 300, 200, "sem_int.* · intermediate", shape="table", style="white")
    si["inner"] = [["views"], ["submission · remit_event"], ["claim_cas · line_match"], ["payer_835 · payer_837"]]
    se = d.node("sem", 1440, zy + 60, 320, 200, "sem.* · 10 gold tables", shape="table", style="white")
    se["inner"] = [["materialized as Parquet"], ["claims · claim_lines"], ["remit_claims · remit_lines"], ["adjustments · payments …"]]
    d.node("vn", 1840, zy + 100, 190, 120, "semantic_layer/vN", "new version", shape="db", style="white")
    d.node("cur2", 2060, zy + 120, 80, 80, "CURRENT", shape="file", style="white")
    d.node("lossless_t", 80, zy + 300, 460, 120, "lossless.*", "c837.claim · c837.service_line · c837.hl\nr835.claim_payment · PHI-stripped", shape="db", style="optional")
    c = zy + 160
    d.edge([(540, c), (640, c)], "built from", (590, c - 12), style="built")
    d.edge([(790, zy + 290), (790, zy + 210)])
    d.edge([(940, c), (1040, c)], style="built")
    d.edge([(1340, c), (1440, c)], style="built")
    d.edge([(1760, c), (1840, c)], "write", (1800, c - 12))
    d.edge([(2030, c), (2060, c)])
    d.text(2100, zy + 236, "manifest, then\ncompare-and-set", "note", "middle")
    d.edge([(310, zy + 260), (310, zy + 300)], "phi-stripped copy", (322, zy + 286), lpos="right", style="built")
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
    """04 keeps the user's excalidraw: content and layout come from drilldowns/04-analyzer.excalidraw (the user's
    board, edited by analyzer04.py); every element is mapped to the house shape at its own position."""
    import json
    src = json.loads((OUT / "04-analyzer.excalidraw").read_text())
    E = {e["id"][:10]: e for e in src["elements"] if not e.get("isDeleted")}
    kpi = sorted((e for e in src["elements"] if e["id"].startswith("kpi_")), key=lambda e: e["id"])
    OX, OY = 5390, -600                             # excalidraw canvas -> board pixels
    X = lambda v: round(v + OX)
    Y = lambda v: round(v + OY)

    def box(k):
        e = E[k]
        return X(e["x"]), Y(e["y"]), round(e["width"]), round(e["height"])

    def words(k):
        tid = next(e["id"][:10] for e in E.values() if (e.get("containerId") or "")[:10] == k)
        return [l for l in E[tid]["text"].split("\n") if l.strip()]

    def kpi_el(typ, text_prefix=None):
        for e in kpi:
            if e["type"] == typ and (text_prefix is None or e.get("text", "").startswith(text_prefix)):
                return e

    d = Diagram("04-analyzer", "04 · Analyzer",
                "The user's analyzer board: the frontend sends a question to the KPI gate, which answers with a preset query when one fits; otherwise the Claim Query Agent, fed by its example bank, cached references and prompts, writes CLI calls with SQL that pass the SQL guard to the DuckDB query service, which reads only this customer's semantic layer.",
                5940, 1200)
    d.text(40, 56, "04 · Analyzer", "title")
    d.text(40, 86, "The Claim Query Agent is the run worker. The DuckDB query service holds the credentials.", "subtitle")
    # zones
    d.zone(*box("rL8BxLtQ6X"), "", "worker")
    d.zone(*box("RY0gNU6VDh"), "References (prompt cached)", "plain")
    d.zone(*box("8DqM00NEH5"), "", "dashed")
    d.zone(*box("z9Lwryh5kO"), "", "aws")
    d.zone(*box("24W6ljDbGQ"), "Customer's S3 prefix · semantic_layer/ (gold)", "phi", "s3")
    d.zone(*box("YcJQqyJI9K"), "", "accent")
    # nodes
    f = d.node("ui", *box("KyHx9De5HC"), "Frontend", shape="image")
    f["href"] = src["files"][E["IIx3z_anQB"]["fileId"]]["dataURL"]
    kr = kpi_el("rectangle"); kd = kpi_el("diamond")
    d.node("preset", X(kr["x"]), Y(kr["y"]), round(kr["width"]), round(kr["height"]), "Preset KPI queries",
           "16 presets, fixed SQL · the gate fills\npayer, payer class, month, CPT, CARC", shape="file", style="white")
    d.node("kpi", X(kd["x"]), Y(kd["y"]), round(kd["width"]), round(kd["height"]), "KPI gate", "does a preset\nanswer it?", shape="decision")
    d.node("bank", *box("BInIQNotFm"), "Solved Example\nQuestion Bank", shape="db", style="white")
    d.node("tools", *box("nPO3M3NYGI"), "Tool & CLI\nreference", "(prompt cached)", shape="file", style="white")
    d.node("rules", *box("6r2Y4_6Ln_"), "Rules from\npast mistakes", shape="file", style="white")
    d.node("gloss", *box("NjiWEu8of1"), "EDI Business\nGlossary", shape="file", style="white")
    d.node("schema", *box("SR-Lrc45uK"), "Semantic layer", "schema + allowed values", shape="file", style="white")
    o = d.node("prompt_outer", *box("g53ojIwuNb"), "User Newest Prompt", shape="box", style="white")
    o["valign"] = "bottom"
    d.node("sysprompt", *box("dTyUfzF_bj"), "System Prompt +\nConversation History", "(prompt cached)", shape="file", style="white")
    a_ = d.node("agent", *box("jybGrI_Izu"), "Claude Haiku", "one agent · Strands on\nAmazon Bedrock", "bedrock")
    a_["shape"] = "agent"
    d.node("lossless_schema", *box("3iBkr6axdX"), "Full lossless EDI schema",
           "only after the agent\nescalates with a reason (logged)", shape="db", style="optional")
    d.node("qs", *box("visPhHm5ok"), "DuckDB query service",
           "Quack server, own Fargate service\none session per run\nread-only SELECT, row/time/cost caps", "fargate")
    d.node("sem", *box("asHDa6G9m3"), "Semantic Layer", "(sem.*) curated tables", shape="db", style="white")
    d.node("lossless", *box("l9RAy2prZT"), "Lossless EDI", "tables (lossless.*)", shape="db", style="white")

    def ln(k):
        e = E[k]
        return [(X(e["x"] + px), Y(e["y"] + py)) for px, py in e["points"]]

    def flat(k, style="solid"):                      # a straight excalidraw arrow, made exactly horizontal
        (x0, y0), (x1, _) = ln(k)[0], ln(k)[-1]
        d.edge([(x0, y0), (x1, y0)], style=style)

    for k in ("liKA4lL-bL", "qI9MfLE4fF", "TvL2CjKkfN", "5BvdWNqcaP"):
        flat(k)
    # the tools box, redrawn to show what happens there: the CLI's commands, then the SQL guard's checks
    CL = (-2400, 1140, 310, 240)                    # in excalidraw coordinates, inside the dashed tools box
    cli = d.node("cli", X(CL[0]), Y(CL[1]), CL[2], CL[3], "cli.py · ClaimClarity CLI", "the agent's one tool", shape="code")
    cli["inner"] = ['sql "SELECT …"', "lookup <dimension> <text>", "describe <relation>", "list",
                    'escalate "<reason>"', 'answer --sql … --text …', "ask · refuse"]
    for k, style in (("XIV8gXseuu", "solid"), ("rddg7eDfdV", "final")):      # agent -> CLI
        (x0, y0) = ln(k)[0]
        d.edge([(x0, y0), (X(CL[0]), y0)], style=style)
    (x0, y0) = ln("lUFxtPcBGz")[-1]                                         # CLI -> agent (results)
    d.edge([(X(CL[0]), y0), (x0, y0)])
    checks = [("Read-only SELECT", "one statement"), ("Allowed tables + functions", "sem.* · lossless.* after escalate"),
              ("Valid against the schema", "would it fail? stop it here"), ("Join cost", "no keyless joins > 100 M row pairs"),
              ("Time + row limits", "25 s · 60 s per question · 1,000 rows")]
    GX, GW, GY, GH, GS = -2050, 280, 1080, 64, 80
    d.text(X(GX), Y(GY) - 16, "SQL guard", "heading")
    d.texts[-1]["icon"] = "shieldcheck"
    for i, (nm, sb) in enumerate(checks):
        d.node(f"check{i}", X(GX), Y(GY + i * GS), GW, GH, nm, sb, style="white")
        if i:
            d.edge([(X(GX + GW / 2), Y(GY + i * GS - GS + GH)), (X(GX + GW / 2), Y(GY + i * GS))])
    last = GY + 4 * GS
    qs_left = X(E["visPhHm5ok"]["x"])
    d.edge([(X(GX + GW), Y(last + GH / 2)), (X(-1730), Y(last + GH / 2)), (X(-1730), Y(1303)), (qs_left, Y(1303))],
           "passes", (X(-1718), Y(1395)), lpos="right")
    d.edge([(X(CL[0] + CL[2]), Y(1200)), (X(-2070), Y(1200)), (X(-2070), Y(GY + GH / 2)), (X(GX), Y(GY + GH / 2))])
    hx_, hy_, hw_, hh_ = box("jybGrI_Izu")
    d.edge([(X(GX), Y(1440)), (hx_ + hw_ - 40, Y(1440)), (hx_ + hw_ - 40, hy_ + hh_)], "fails a check: error to the agent",
           (X(-2240), Y(1428)), style="dashed")
    for e in kpi:                                   # the KPI gate's arrows; the preset path ends on the guard's first check
        if e["type"] == "arrow":
            pts = [(X(e["x"] + px), Y(e["y"] + py)) for px, py in e["points"]]
            if len(pts) > 2:                        # leave the presets right of the box title, then over to the guard
                pts = [(X(-3900), pts[0][1]), (X(-3900), pts[1][1]), (X(-1880), pts[1][1]), (X(-1880), Y(GY))]
            d.edge(pts)
    # references and the tool reference flow into Haiku with right-angle elbows
    hx, hy, hw, hh = box("jybGrI_Izu")
    rb = Y(E["RY0gNU6VDh"]["y"] + E["RY0gNU6VDh"]["height"])
    mid = (rb + hy) // 2
    for k, tx in (("eM4_BLOWAe", hx + 74), ("MujbKXp24X", hx + hw - 74)):
        sx = ln(k)[0][0]
        d.edge([(sx, rb), (sx, mid), (tx, mid), (tx, hy)])
    gx = ln("uHzQHsbzJl")[0][0]
    d.edge([(gx, rb), (gx, hy)])
    tb = box("nPO3M3NYGI")
    d.edge([(tb[0] + tb[2], ln("B-wjkiTGqr")[0][1]), (tb[0] + tb[2] + 50, ln("B-wjkiTGqr")[0][1]),
            (tb[0] + tb[2] + 50, hy + hh - 24), (hx + 6, hy + hh - 24)])
    d.edge(ln("fPXVIk0UzP"), style="dashed")
    # the two reads, each with a big gate 6 on its arrow
    q = box("visPhHm5ok")
    (s1x, s1y), (e1x, _) = ln("qMsg83z7QP")[0], ln("qMsg83z7QP")[-1]
    d.edge([(q[0] + q[2], s1y), (e1x, s1y)])
    (s2x, s2y), (e2x, _) = ln("rOkWFwHs5r")[0], ln("rOkWFwHs5r")[-1]
    d.edge([(q[0] + q[2], s2y), (e2x, s2y)], "only after a reasoned escalation", (X(E["kYzu4znOfs"]["x"] + 206), s2y - 12), style="solid")
    for k, yy in (("499pBNb1Mh", s1y), ("AeKE4Dl3tF", s2y)):
        dm = E[k]
        d.gate(6, X(dm["x"] + dm["width"] / 2), yy, "", "above")
    gtip = X(E["AeKE4Dl3tF"]["x"] + E["AeKE4Dl3tF"]["width"] / 2)
    t = E["80_0YFkhw-"]
    note = "GATE 6 · IAM + KMS: this customer only"
    tx0, ty0 = X(t["x"]), Y(t["y"]) + 26
    d.text(tx0, ty0, note, "gatenote")
    tend = tx0 + _tw(note, 18, "sans") + 14
    d.edge([(tend, ty0 - 6), (gtip, ty0 - 6), (gtip, s2y + GR_)], style="navy")
    # lossless tables -> semantic layer, the pink dashed hint, as an elbow
    l0 = ln("QKNyGbgAu6")[0]; l1 = ln("QKNyGbgAu6")[-1]
    hl = box("YcJQqyJI9K")
    d.edge([(l0[0], Y(E["l9RAy2prZT"]["y"])), (l0[0], l1[1]), (hl[0] + hl[2], l1[1])], style="dashed")
    # the user's words: arrow labels, notes and headings, at their own positions
    def put(k, role, dy=0, anchor="start", text=None):
        e = E[k]
        d.text(X(e["x"]), Y(e["y"]) + {"label": 15, "note": 15, "heading": 17}[role] + dy, text or e["text"], role, anchor)
    for k in ("k7SIsJ3M_h", "ZmjSuQpPF_", "Y-9ygs7teF", "Zaky5DrGkd", "YMAUWRuvhl", "EWI5qb2A46", "jDqN4Xr_II"):
        put(k, "label")
    put("h0yKh0RZM8", "label", dy=-40)               # above its arrow, not on it
    put("X0Pit0Z_6d", "label", dy=31)                # under the final-query arrow, not on it
    for e in kpi:
        if e["type"] == "text" and e["text"] in ("yes", "no") or e.get("text", "").startswith("preset SQL"):
            d.text(X(e["x"]), Y(e["y"]) + 15, e["text"], "label")
    put("-A8dMCxTwW", "heading")
    put("qHtTjH_-AM", "heading", text="Claim Query Agent · run worker on Fargate")
    d.texts[-1]["icon"] = "fargate"
    d.text(X(-3850), Y(E["qHtTjH_-AM"]["y"]) + 17, "the backend for ClaimClarity, and part of the backend for Agentic Next Best Actions", "heading")
    put("9kVIq4ZuaA", "heading")
    put("6sE7--PXrr", "note")
    put("Hli_0rnnE0", "note")
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


DIAGRAMS = {"00": d00, "01": d01, "02": d02, "03": d03, "04": d04, "05": d05}


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
        if k != "04":                 # 04's .excalidraw is the source (the user's board, edited by analyzer04.py)
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
