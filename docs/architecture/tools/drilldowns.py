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
from ddlib import DENY, Diagram, to_excalidraw, to_html  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "drilldowns"
G1, G2, G3, G4, G5, G6 = ("Rule matches\ncustomer B", "PassRole\nparser role B", "IAM + KMS\nparser role B",
                          "Token valid", "Trust policy\nallows this token", "IAM + KMS\nreader role B")


def d00():
    d = Diagram("00-storage", "00 · Storage & isolation",
                "Customer B's prefixes in the one ClaimClarity data bucket, the roles and keys that guard them, and Customer A denied.",
                1960, 960)
    d.text(40, 56, "00 · Storage & isolation", "title")
    d.text(40, 84, "One bucket, one prefix per customer. Shown for customer B; every customer gets the same.", "subtitle")
    ox = 80
    d.zone(280 + ox, 120, 1200, 720, "ClaimClarity data bucket", "bucket", "s3")
    d.zone(310 + ox, 170, 1140, 640, "customer_b/   ·   bucket policy: customer B roles only", "phi")
    y1, h1 = 240, 120
    cy = y1 + h1 // 2
    d.node("sftp", 40, cy - 45, 200, 90, "Transfer Family", "SFTP copy role B", "transfer")
    d.node("landing", 340 + ox, y1, 180, h1, "landing/", "SFTP copy role", shape="folder")
    d.node("raw", 610 + ox, y1, 200, h1, "raw/", "bronze · Object Lock", shape="db")
    d.node("silver", 960 + ox, y1, 190, h1, "parquet/", "silver · parser role", shape="db")
    d.node("gold", 1240 + ox, y1, 180, h1, "semantic_layer/", "gold · CURRENT", shape="db", style="white")
    d.edge([(240, cy), (340 + ox, cy)], "write", (300, cy - 12))
    d.edge([(520 + ox, cy), (610 + ox, cy)], "copy", (565 + ox, cy - 12))
    d.edge([(810 + ox, cy), (960 + ox, cy)], "parse", (885 + ox, cy + 56))
    d.gate(3, 885 + ox, cy, G3, "above", "iam")
    d.edge([(1150 + ox, cy), (1240 + ox, cy)], "publish", (1195 + ox, cy - 12))
    # reader, outside the bucket
    d.node("qs", 1640 + ox, cy - 45, 220, 90, "DuckDB query service", "reader role B", "fargate")
    d.edge([(1420 + ox, cy), (1640 + ox, cy)], "read", (1610 + ox, cy - 12))
    d.gate(6, 1530 + ox, cy, G6, "below", "iam")
    # row 2
    y2, h2 = 480, 110
    d.node("audit", 340 + ox, y2, 180, h2, "audit/", "write-once · audit role", shape="folder")
    d.node("quar", 610 + ox, y2, 200, h2, "quarantine/", "bad files · 1 yr", shape="folder")
    d.node("staging", 960 + ox, y2, 190, h2, "staging/", "in-progress load", shape="folder")
    d.node("chat", 1240 + ox, y2, 180, h2, "conversations/\nrun-events/", "transcripts role", shape="folder")
    d.edge([(710 + ox, y1 + h1), (710 + ox, y2)], "bad files", (722 + ox, (y1 + h1 + y2) // 2 + 6), lpos="right", style="dashed")
    d.edge([(1055 + ox, y2), (1055 + ox, y1 + h1)], "load", (1067 + ox, (y1 + h1 + y2) // 2 + 6), lpos="right", style="dashed")
    # keys row
    y3 = 660
    d.node("k_audit", 340 + ox, y3, 180, 80, "Query audit key", "platform · B statement", "kms", style="white")
    d.node("k_lake", 700 + ox, y3, 360, 80, "Lake key B", "landing · raw · silver · gold · staging · quarantine", "kms", style="white")
    d.node("k_tr", 1240 + ox, y3, 180, 80, "Transcript key B", "chat state", "kms", style="white")
    d.text(340 + ox, y3 - 16, "KMS keys", "eyebrow")
    # Customer A denied
    d.node("custA", 40, 560, 200, 90, "Customer A roles", "bucket policy + KMS", "role", style="denied")
    d.edge([(240, 605), (310 + ox, 605)], "denied", (310, 590), style="deny")
    return d


DIAGRAMS = {"00": d00}


SKILL = Path(os.environ.get("DIAGRAM_DESIGN", Path.home() / ".claude/skills/diagram-design")) / "scripts"
PY = os.environ.get("PYTHON", sys.executable)


def main(which):
    OUT.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    for k in which or DIAGRAMS:
        d = DIAGRAMS[k]()
        (OUT / f"{d.slug}.excalidraw").write_text(to_excalidraw(d))
        html = tmp / f"{d.slug}.html"
        html.write_text(to_html(d, getattr(d, "legend_items", None)))
        subprocess.run([PY, str(SKILL / "export_svg.py"), str(html), str(OUT / f"{d.slug}.svg")], check=True)
        subprocess.run([PY, str(SKILL / "export_png.py"), str(html), str(OUT / f"{d.slug}.png"), "2"], check=True)
        print(d.slug)


if __name__ == "__main__":
    main(sys.argv[1:])
