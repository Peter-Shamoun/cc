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
G1, G2, G3, G4, G5, G6 = ("Rule matches\ncustomer B", "PassRole\nparser role B", "IAM + KMS\nparser role B",
                          "Token valid", "Trust policy\nallows this token", "IAM + KMS\nreader role B")


def d00():
    d = Diagram("00-storage", "00 · Storage & isolation",
                "Customer B's prefixes in the one ClaimClarity data bucket, the roles and keys that guard them, and Customer A denied.",
                2320, 970)
    d.text(40, 56, "00 · Storage & isolation", "title")
    d.text(40, 86, "One bucket, one prefix per customer. Shown for customer B; every customer gets the same.", "subtitle")
    d.zone(360, 130, 1420, 720, "ClaimClarity data bucket", "bucket", "s3")
    d.zone(390, 180, 1360, 640, "customer_b/   ·   bucket policy: customer B roles only", "phi")
    y1, h1 = 250, 120
    cy = y1 + h1 // 2
    d.node("sftp", 40, cy - 45, 230, 90, "Transfer Family", "SFTP copy role B", "transfer")
    d.node("landing", 430, y1, 200, h1, "landing/", "SFTP copy role", shape="folder")
    d.node("raw", 740, y1, 210, h1, "raw/", "bronze · Object Lock", shape="db")
    d.node("silver", 1200, y1, 200, h1, "parquet/", "silver · parser role", shape="db")
    d.node("gold", 1510, y1, 200, h1, "semantic_layer/", "gold · CURRENT", shape="db", style="white")
    d.edge([(270, cy), (430, cy)], "write", (350, cy - 12))
    d.edge([(630, cy), (740, cy)], "copy", (685, cy - 12))
    d.edge([(950, cy), (1200, cy)], "parse", (1075, cy + GR_ + 34))
    d.gate(3, 1075, cy, G3, "above", "iam")
    d.edge([(1400, cy), (1510, cy)], "publish", (1455, cy - 12))
    d.node("qs", 2010, cy - 45, 270, 90, "DuckDB query service", "reader role B", "fargate")
    d.edge([(1710, cy), (2010, cy)], "read", (1970, cy - 12))
    d.gate(6, 1880, cy, G6, "below", "iam")
    y2, h2 = 500, 110
    d.node("audit", 430, y2, 200, h2, "audit/", "write-once · audit role", shape="folder")
    d.node("quar", 740, y2, 210, h2, "quarantine/", "bad files · 1 yr", shape="folder")
    d.node("staging", 1200, y2, 200, h2, "staging/", "in-progress load", shape="folder")
    d.node("chat", 1510, y2, 200, h2, "conversations/\nrun-events/", "transcripts role", shape="folder")
    d.edge([(845, y1 + h1), (845, y2)], "bad files", (857, (y1 + h1 + y2) // 2 + 6), lpos="right", style="dashed")
    d.edge([(1300, y2), (1300, y1 + h1)], "load", (1312, (y1 + h1 + y2) // 2 + 6), lpos="right", style="dashed")
    y3 = 700
    d.text(430, y3 - 18, "KMS keys", "eyebrow")
    d.node("k_audit", 430, y3, 220, 80, "Query audit key", "platform · B statement", "kms", style="white")
    d.node("k_lake", 880, y3, 380, 80, "Lake key", "customer B · all data prefixes", "kms", style="white")
    d.node("k_tr", 1510, y3, 200, 80, "Transcript key", "customer B · chat", "kms", style="white")
    d.node("custA", 40, 595, 230, 90, "Customer A roles", "bucket policy + KMS", "role", style="denied")
    d.edge([(270, 640), (390, 640)], "denied", (330, 616), style="deny")
    return d


def d01():
    d = Diagram("01-ingestion", "01 · Ingestion & parse",
                "Lab files arrive over SFTP, are copied to raw, and the _SUCCESS marker triggers one Fargate parser task that writes silver and publishes a new gold version.",
                2440, 980)
    d.text(40, 56, "01 · Ingestion & parse", "title")
    d.text(40, 86, "One batch for customer B, from the lab's upload to a new CURRENT gold version.", "subtitle")
    d.zone(630, 140, 1790, 200, "S3 · customer_b/", "phi", "s3")
    y1, h1 = 200, 120
    c1 = y1 + h1 // 2
    d.node("lab", 40, c1 - 45, 200, 90, "Lab", "customer B", "building", style="external")
    d.node("sftp", 340, c1 - 45, 240, 90, "Transfer Family", "SFTP endpoint", "transfer")
    d.node("landing", 670, y1, 190, h1, "landing/", shape="folder")
    d.node("raw", 940, y1, 270, h1, "raw/", "837 · 835 · 277 · _SUCCESS last", shape="db")
    d.node("silver", 1700, y1, 200, h1, "parquet/", "silver", shape="db")
    d.node("gold", 1990, y1, 210, h1, "semantic_layer/", "gold · new version", shape="db", style="white")
    d.node("current", 2270, c1 - 40, 120, 80, "CURRENT", shape="file", style="white")
    d.edge([(240, c1), (340, c1)], "port 22", (290, c1 - 12))
    d.edge([(580, c1), (670, c1)], "write", (625, c1 - 12))
    d.edge([(860, c1), (940, c1)], "copy", (900, c1 - 12))
    d.edge([(2200, c1), (2270, c1)])
    d.text(2330, c1 + 70, "compare-and-set", "note", "middle")
    # trigger chain
    y2, h2 = 570, 100
    c2 = y2 + h2 // 2
    pe, pr = 1000, 1150           # raw bottom ports: event, read
    d.node("eb", pe - 110, y2, 220, h2, "EventBridge rule", "customer B", "eventbridge")
    d.node("run", 1200, y2, 200, h2, "ECS RunTask", "launcher", "ecs")
    d.node("task", 1580, 530, 680, 180, "Fargate parser task · one per batch", icon="fargate", shape="steps",
           inner=["Start", "Parse", "Silver", "Gold", "Exit"])
    d.edge([(pe, y1 + h1), (pe, y2)], "_success", (pe + 12, 372), lpos="right")
    d.gate(1, pe, 470, G1, "left", "eventbridge")
    d.edge([(pe + 110, c2), (1200, c2)])
    d.edge([(1400, c2), (1580, c2)])
    d.gate(2, 1490, c2, G2, "below", "iam")
    d.edge([(pr, y1 + h1), (pr, 450), (1630, 450), (1630, 530)], "read", (1500, 438))
    d.gate(3, 1300, 450, G3, "above", "iam")
    d.edge([(1800, 530), (1800, y1 + h1)], "write", (1812, 506), lpos="right")
    d.gate(3, 1800, 425, G3, "right", "iam")
    d.edge([(2095, 530), (2095, y1 + h1)], "write", (2107, 506), lpos="right")
    d.gate(3, 2095, 425, G3, "right", "iam")
    d.node("dlq", pe - 110, 790, 220, 80, "Dead-letter queue", icon="sqs")
    d.edge([(pe, y2 + h2), (pe, 790)], "after 3 retries", (pe + 12, 736), lpos="right", style="dashed")
    return d


DIAGRAMS = {"00": d00, "01": d01}


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
