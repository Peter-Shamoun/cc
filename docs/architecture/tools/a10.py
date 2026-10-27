"""Area 10 sub-pages (11–15): SFTP intake, S3 layout, parse trigger, parser task, gold publish.

Same look as tools/l1.py: strips that read left to right, names only, drill-down badges.
    python3 docs/architecture/tools/a10.py      # writes src/11-…drawio to src/15-…drawio
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from dlib import (CYL, DECISION, EXT, FOLDER, LABEL, NOTE, PHI, STEPBOX, TEXT, Diagram, badge,  # noqa: E402
                  legend_drilldown, strip)

SRC = Path(__file__).resolve().parent.parent / "src"
GATE = "rhombus;html=1;fillColor=#1C2B57;strokeColor=#1C2B57;fontColor=#FFFFFF;fontStyle=1;fontSize=12;fontFamily=Helvetica;"
CENTER = TEXT.replace("align=left", "align=center")
SIZE = {"icon": (52, 52), "note": (130, 56), "step": (130, 56), "decision": (130, 84), "gate": (30, 30),
        "folder": (56, 46), "cyl": (130, 72), "ext": (200, 48)}


def save(d):
    (SRC / f"{d.id}.drawio").write_text(d.xml())


def item(d, cid, kind, name, cx, cy, page=None, res=None, tile=True, color=None, label="below"):
    """One item centred at (cx, cy): its name only, plus a badge when it has a detail page."""
    w, h = SIZE[kind]
    x, y = cx - w / 2, cy - h / 2
    if kind == "icon":
        d.icon(cid, res, name, "", cx, cy, tile=tile, color=color, label=label)
    elif kind == "folder":
        d.add(cid, f"<b>{name}</b>", FOLDER + LABEL["below"], x, y, w, h)
    elif kind == "gate":
        d.add(cid, name, GATE, x, y, w, h)
    else:
        style = {"note": NOTE + "fontSize=11;", "step": STEPBOX, "decision": DECISION, "cyl": CYL, "ext": EXT}[kind]
        d.add(cid, f"<b>{name}</b>", style, x, y, w, h)
    if page:
        badge(d, f"{cid}-b", page, cx + w / 2 - 6, cy - h / 2 - 16)
    return cid


def gate(d, cid, number, question, cx, cy):
    item(d, cid, "gate", number, cx, cy)
    d.add(f"{cid}-t", question, CENTER + "fontColor=#1C2B57;", cx - 70, cy + 18, 140, 22)
    return cid


def link(d, a, b, label="", exit_=(1, .5), entry=(0, .5)):
    d.edge(f"e-{a}-{b}", a, b, label, exit_=exit_, entry=entry)


def flow(d, ids, labels=()):
    labels = list(labels) + [""] * len(ids)
    for i in range(len(ids) - 1):
        link(d, ids[i], ids[i + 1], labels[i])


# 11 SFTP intake ------------------------------------------------------------------------------------------
d = Diagram("11-sftp-intake", "SFTP intake", 1560, 490)
d.title("CLAIMCLARITY · SFTP INTAKE")
strip(d, "s1", 70, 210, "A LAB'S FILES LAND IN RAW")
d.add("phi", "Customer B's prefix", PHI, 400, 104, 920, 160)
y = 185
flow(d, [item(d, "a0", "icon", "Customer lab", 120, y, res="corporate_data_center", tile=False),
         item(d, "a1", "icon", "AWS Transfer Family", 300, y, res="transfer_family"),
         item(d, "a2", "icon", "S3 · landing", 500, y, page="12", res="s3"),
         item(d, "a3", "step", "Copy step", 700, y),
         item(d, "a4", "icon", "S3 · raw", 900, y, page="12", res="s3"),
         item(d, "a5", "note", "_SUCCESS", 1100, y),
         item(d, "a6", "icon", "EventBridge", 1420, y, page="13", res="eventbridge")], ["SFTP"])
strip(d, "s2", 300, 130, "CHECKS ON EVERY RAW WRITE")
y = 370
item(d, "b0", "icon", "B's SFTP copy role", 300, y, res="role", tile=False, color="#DD344C")
item(d, "b1", "icon", "B's lake key", 500, y, page="52", res="data_encryption_key", tile=False, color="#DD344C")
item(d, "b2", "note", "Bucket policy", 700, y, page="23")
item(d, "b3", "icon", "CloudTrail", 900, y, page="53", res="cloudtrail")
legend_drilldown(d, 1290, 448)
save(d)

# 12 S3 layout --------------------------------------------------------------------------------------------
d = Diagram("12-s3-layout", "S3 layout", 1560, 490)
d.title("CLAIMCLARITY · S3 LAYOUT")
strip(d, "s1", 70, 210, "ONE BUCKET · CUSTOMER B'S PREFIX")
d.add("phi", "customer_b/", PHI, 60, 104, 1240, 160)
y = 180
flow(d, [item(d, "a0", "folder", "landing/", 140, y, page="11"),
         item(d, "a1", "folder", "raw/ · bronze", 320, y, page="11"),
         item(d, "a2", "folder", "parquet/ · silver", 500, y, page="14"),
         item(d, "a3", "folder", "semantic_layer/ · gold", 700, y, page="15")], ["copy", "parse", "publish"])
item(d, "a4", "folder", "staging/ · lease", 900, y, page="14")
item(d, "a5", "folder", "quarantine/", 1050, y, page="14")
item(d, "a6", "folder", "transcripts/ · audit/", 1200, y, page="33")
item(d, "a7", "ext", "customer_a/ · same layout", 1425, y)
strip(d, "s2", 300, 130, "WHO MAY TOUCH IT")
y = 365
item(d, "b0", "icon", "SFTP copy role · landing, raw", 320, y, res="role", tile=False, color="#DD344C")
item(d, "b1", "icon", "Parser role · derived zones", 560, y, page="14", res="role", tile=False, color="#DD344C")
item(d, "b2", "icon", "Reader role · gold only", 800, y, page="22", res="role", tile=False, color="#DD344C")
item(d, "b3", "icon", "B's lake key", 1040, y, page="52", res="data_encryption_key", tile=False, color="#DD344C")
legend_drilldown(d, 1290, 448)
save(d)

# 13 parse trigger ----------------------------------------------------------------------------------------
d = Diagram("13-parse-trigger", "Parse trigger", 1560, 630)
d.title("CLAIMCLARITY · PARSE TRIGGER")
strip(d, "s1", 70, 190, "A COMPLETE DAY STARTS ONE TASK")
y = 170
a0 = item(d, "a0", "note", "_SUCCESS", 140, y)
g1 = gate(d, "g1", "1", "Rule matches B?", 330, y)
a1 = item(d, "a1", "icon", "EventBridge rule", 520, y, res="eventbridge")
g2 = gate(d, "g2", "2", "PassRole allowed?", 710, y)
a2 = item(d, "a2", "icon", "Amazon ECS", 900, y, res="ecs")
a3 = item(d, "a3", "icon", "Parser task", 1100, y, page="14", res="fargate")
flow(d, [a0, g1, a1, g2, a2, a3], ["", "", "RunTask", "", "starts"])
strip(d, "s2", 280, 150, "IF THE LAUNCH FAILS")
y = 365
flow(d, [item(d, "b0", "icon", "EventBridge rule", 140, y, res="eventbridge"),
         item(d, "b1", "icon", "SQS · dead letters", 420, y, res="sqs"),
         item(d, "b2", "step", "Re-run the day", 700, y)], ["3 tries", "redrive"])
strip(d, "s3", 450, 130, "ROLES USED")
y = 510
item(d, "c0", "icon", "Launcher role", 520, y, res="role", tile=False, color="#DD344C")
item(d, "c1", "icon", "Task execution role", 900, y, res="role", tile=False, color="#DD344C")
item(d, "c2", "icon", "B's parser role", 1100, y, page="14", res="role", tile=False, color="#DD344C")
legend_drilldown(d, 1290, 590)
save(d)

# 14 parser task ------------------------------------------------------------------------------------------
d = Diagram("14-parser-task", "Parser task", 1560, 700)
d.title("CLAIMCLARITY · PARSER TASK")
strip(d, "s1", 70, 330, "ONE BATCH, FIVE STEPS")
d.add("phi", "Fargate task · B's parser role", PHI, 60, 104, 1100, 110)
xs = [160, 380, 600, 820, 1040]
names = ["1 · Start", "2 · Parse", "3 · Silver", "4 · Gold", "5 · Exit"]
steps = [item(d, f"a{i}", "step", n, x, 165, page="15" if i == 3 else None) for i, (n, x) in enumerate(zip(names, xs))]
flow(d, steps)
y = 300
item(d, "b0", "folder", "staging/ · lease", xs[0], y)
item(d, "b1", "folder", "raw/", xs[1], y, page="11")
item(d, "b2", "folder", "parquet/ · silver", xs[2], y, page="12")
item(d, "b3", "folder", "semantic_layer/ · gold", xs[3], y, page="15")
item(d, "b4", "icon", "CloudWatch Logs", xs[4], y, res="cloudwatch_2")
down, up = ((.5, 1), (.5, 0)), ((.5, 0), (.5, 1))
link(d, "a0", "b0", "lease", *down)
link(d, "b1", "a1", "read", *up)
link(d, "a2", "b2", "write", *down)
link(d, "a3", "b3", "publish", *down)
link(d, "a4", "b4", "summary", *down)
gate(d, "g3", "3", "IAM + KMS allow? (every read and write)", 1220, 240)
strip(d, "s2", 420, 210, "A FILE THAT FAILS TO PARSE")
y = 505
c0 = item(d, "c0", "note", "X12 file", 160, y)
c1 = item(d, "c1", "decision", "Envelope valid?", 380, y)
c2 = item(d, "c2", "step", "Load to silver", 640, y)
c3 = item(d, "c3", "folder", "quarantine/", 860, 575, page="12")
link(d, c0, c1)
link(d, c1, c2, "yes")
d.edge("e-c1-c3", c1, c3, "no", exit_=(.5, 1), entry=(0, .5), waypoints=[(380, 575)])
legend_drilldown(d, 1290, 660)
save(d)

# 15 gold publish -----------------------------------------------------------------------------------------
d = Diagram("15-gold-publish", "Gold publish", 1560, 630)
d.title("CLAIMCLARITY · GOLD PUBLISH")
strip(d, "s1", 70, 170, "PUBLISH: FILES, THEN MANIFEST, THEN CURRENT")
d.add("phi1", "B's semantic_layer/", PHI, 300, 100, 720, 120)
y = 165
flow(d, [item(d, "a0", "step", "Gold build", 160, y, page="14"),
         item(d, "a1", "note", "Data files", 420, y),
         item(d, "a2", "note", "Manifest v=&lt;run&gt;", 660, y),
         item(d, "a3", "note", "CURRENT", 900, y)], ["", "", "compare-and-set"])
strip(d, "s2", 260, 170, "READ: CURRENT, THEN MANIFEST, THEN FILES")
d.add("phi2", "B's semantic_layer/", PHI, 60, 290, 720, 120)
y = 355
flow(d, [item(d, "b0", "note", "CURRENT", 160, y),
         item(d, "b1", "note", "Manifest v=&lt;run&gt;", 420, y),
         item(d, "b2", "note", "Data files", 660, y),
         item(d, "b3", "cyl", "Reader session", 900, y, page="32")])
strip(d, "s3", 450, 130, "OLD VERSIONS")
y = 515
flow(d, [item(d, "c0", "note", "Retired files", 160, y),
         item(d, "c1", "decision", "Older than 20 min?", 420, y),
         item(d, "c2", "step", "Deleted", 660, y)], ["", "yes"])
legend_drilldown(d, 1290, 590)
save(d)
