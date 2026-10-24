"""Generate ClaimClarity Data Backend canvas as an editable draw.io file."""
from xml.sax.saxutils import escape
import sys

NAVY, MID, DEEP, INK, GRAY, TINT = "#1C2B57", "#4A6BB0", "#3A5A9E", "#1A1D23", "#5A606B", "#EAF0F9"
FS_B = "fontSource=https%3A%2F%2Ffonts.googleapis.com%2Fcss%3Ffamily%3DBarlow;"
FS_C = "fontSource=https%3A%2F%2Ffonts.googleapis.com%2Fcss%3Ffamily%3DBarlow%2BCondensed%3Awght%40600;"
FS_M = "fontSource=https%3A%2F%2Ffonts.googleapis.com%2Fcss%3Ffamily%3DIBM%2BPlex%2BMono;"
BASE = f"html=1;whiteSpace=wrap;fontFamily=Barlow;{FS_B}fontColor={INK};"
COND = "font-family:Barlow Condensed;font-weight:600;text-transform:uppercase;letter-spacing:0.04em;"
MONO = "font-family:IBM Plex Mono,monospace;"

cells = []
edges_late = []


def add(cid, value, style, x, y, w, h, parent="1"):
    cells.append(f'<mxCell id="{cid}" value="{escape(value, {chr(34): "&quot;"})}" style="{style}" '
                 f'vertex="1" parent="{parent}"><mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


DX, DY = 40, 160

def frame(cid, x, y, w, h):
    add(cid, "", f"{BASE}rounded=0;container=1;pointerEvents=0;collapsible=0;fillColor=#FFFFFF;strokeColor=#C9CBCE;", x + DX, y + DY, w, h)


def zone(cid, p, x, y, w, h, kind="zone"):
    st = {"zone": f"strokeColor={MID};strokeWidth=1.5;",
          "phi": f"strokeColor={NAVY};strokeWidth=1.5;dashed=1;dashPattern=6 4;",
          "gray": "strokeColor=#9A9CA1;strokeWidth=1.5;dashed=1;dashPattern=6 4;",
          "loop": f"strokeColor={MID};dashed=1;dashPattern=5 4;fillColor=#F6F8FC;",
          "white": f"strokeColor={MID};fillColor=#FFFFFF;"}[kind]
    fill = "" if kind in ("loop", "white") else "fillColor=none;"
    add(cid, "", f"{BASE}rounded=0;{fill}{st}container=1;pointerEvents=0;collapsible=0;", x, y, w, h, p)


def title_html(t, sub=None, color=NAVY, size=18):
    s = f'<span style="{COND}font-size:{size}px;color:{color}">{t}</span>'
    if sub:
        s += f'<br><span style="font-size:14px">{sub}</span>'
    return s


def box(cid, p, x, y, w, h, html, kind="svc", icon=None, isz=36, pad=18, align="left"):
    st = {"svc": f"fillColor={TINT};strokeColor={MID};",
          "ext": "fillColor=#FFFFFF;strokeColor=#9A9CA1;dashed=1;dashPattern=4 3;",
          "white": f"fillColor=#FFFFFF;strokeColor={MID};",
          "black": f"fillColor=#FFFFFF;strokeColor={INK};dashed=1;dashPattern=4 3;"}[kind]
    left = pad + (isz + 14 if icon else 0)
    add(cid, html, f"{BASE}rounded=0;{st}align={align};verticalAlign=middle;spacingLeft={left};spacingRight={pad};fontSize=14;", x, y, w, h, p)
    if icon:
        ico(cid + "_i", p, x + pad, y + (h - isz) / 2, isz, icon)


def ico(cid, p, x, y, s, name):
    if name == "github":
        st = f"shape=mxgraph.weblogos.github;html=1;fillColor={NAVY};strokeColor=none;"
    elif name == "folder":
        st = f"shape=mxgraph.aws4.folder;html=1;fillColor={MID};strokeColor=none;aspect=fixed;"
    elif name == "doc":
        st = f"shape=note;size=8;html=1;fillColor=#FFFFFF;strokeColor={MID};"
    elif name == "db":
        st = f"shape=cylinder3;size=5;html=1;fillColor=#FFFFFF;strokeColor={MID};"
    elif name == "shield":
        st = f"shape=mxgraph.aws4.shield2;html=1;fillColor={MID};strokeColor=none;aspect=fixed;"
    else:
        st = (f"sketch=0;outlineConnect=0;dashed=0;html=1;aspect=fixed;shape=mxgraph.aws4.resourceIcon;"
              f"resIcon=mxgraph.aws4.{name};fillColor={NAVY};strokeColor=#FFFFFF;")
    add(cid, "", st, x, y, s, s, p)


def text(cid, p, x, y, w, h, html, align="left", size=14, bg=None, color=INK, cond=False):
    st = f"{BASE}text;strokeColor=none;fillColor={bg or 'none'};align={align};verticalAlign=middle;fontSize={size};fontColor={color};spacing=0;"
    if cond:
        st += f"fontFamily=Barlow Condensed;{FS_C}fontStyle=1;"
    add(cid, html, st, x, y, w, h, p)


def label(cid, p, x, y, t, size=14, color=NAVY, w=None):
    """Small-caps zone label (Barlow Condensed uppercase)."""
    w = w or int(len(t) * size * 0.8) + 20
    text(cid, p, x, y, w, 20, f'<span style="{COND}letter-spacing:0.06em">{t}</span>', size=size, color=color)


def iconlabel(cid, p, x, y, icon, t, isz=28, size=14):
    ico(cid + "_i", p, x, y + (isz - 20) / 2 if isz > 20 else y, isz, icon)
    label(cid, p, x + isz + 10, y + max(0, (isz - 20) / 2), t, size=size)


def gatelabel(cid, p, cx, y, icon, t, center=True, right=None):
    w = int(len(t) * 7.2) + 38
    x = cx - w / 2 if center else cx
    if right is not None:
        x = right - w
    add(cid, f'<span style="font-weight:500;color:{NAVY}">{t}</span>',
        f"{BASE}text;fillColor=#FFFFFF;strokeColor=none;align=left;verticalAlign=middle;fontSize=13;spacingLeft=28;", x, y, w, 26, p)
    ico(cid + "_i", p, x + 5, y + 4, 18, icon)


def gate(cid, p, x, y, n, s=34):
    add(cid, str(n), f"{BASE}rhombus;fillColor={NAVY};strokeColor=none;fontColor=#FFFFFF;fontFamily=Barlow Condensed;{FS_C}fontStyle=1;fontSize=14;", x, y, s, s, p)


def zoom(cid, p, x, y, n):
    add(cid, n, f"{BASE}ellipse;fillColor={MID};strokeColor=none;fontColor=#FFFFFF;fontFamily=Barlow Condensed;{FS_C}fontStyle=1;fontSize=13;", x, y, 26, 26, p)


def tag(cid, p, x, y, t, kind="outline", w=None, mono=False):
    w = w or int(len(t) * (8.2 if mono else 7)) + 18
    st = {"outline": f"fillColor=#FFFFFF;strokeColor={MID};fontColor={NAVY};",
          "solid": f"fillColor={NAVY};strokeColor=none;fontColor=#FFFFFF;",
          "mid": f"fillColor={MID};strokeColor=none;fontColor=#FFFFFF;",
          "ink": f"fillColor=#FFFFFF;strokeColor={INK};fontColor={INK};",
          "gray": "fillColor=#FFFFFF;strokeColor=#9A9CA1;fontColor=#1A1D23;",
          "tint": f"fillColor={TINT};strokeColor={MID};fontColor={NAVY};"}[kind]
    v = f'<span style="{MONO}">{t}</span>' if mono else t
    add(cid, v, f"{BASE}rounded=0;{st}fontSize={13 if mono else 12};align=center;verticalAlign=middle;spacing=0;", x, y, w, 20, p)
    return w


def header(cid, p, num, t, right_tag):
    x = 40
    if num:
        add(cid + "_n", num, f"{BASE}fillColor={MID};strokeColor=none;fontColor=#FFFFFF;fontFamily=Barlow Condensed;{FS_C}fontStyle=1;fontSize=16;", 40, 30, 36, 28, p)
        x = 90
    text(cid, p, x, 28, 700, 32, f'<span style="{COND}">{t}</span>', size=26)
    if right_tag:
        w = int(len(right_tag) * 7) + 18
        tag(cid + "_t", p, FW[p] - 40 - w, 34, right_tag, "gray", w=w)


def edge(cid, src, tgt, p="1", ex=None, ey=None, nx=None, ny=None, pts=None, label=None, extra="", lx=None, ly=0):
    st = (f"edgeStyle=orthogonalEdgeStyle;rounded=0;html=1;endArrow=block;endFill=1;endSize=5;strokeColor={MID};strokeWidth=1.5;"
          f"fontFamily=Barlow Condensed;{FS_C}fontSize=13;fontColor={DEEP};labelBackgroundColor=#FFFFFF;")
    if ex is not None: st += f"exitX={ex};exitY={ey};exitDx=0;exitDy=0;"
    if nx is not None: st += f"entryX={nx};entryY={ny};entryDx=0;entryDy=0;"
    st += extra
    v = f'<span style="{COND}letter-spacing:0.06em;font-weight:500">{label}</span>' if label else ""
    geo = '<mxGeometry relative="1" as="geometry">'
    if lx is not None:
        geo = f'<mxGeometry x="{lx}" y="{ly}" relative="1" as="geometry">'
    if pts:
        geo += '<Array as="points">' + "".join(f'<mxPoint x="{a}" y="{b}"/>' for a, b in pts) + "</Array>"
    geo += "</mxGeometry>"
    cells.append(f'<mxCell id="{cid}" value="{escape(v, {chr(34): "&quot;"})}" style="{st}" edge="1" parent="{p}" source="{src}" target="{tgt}">{geo}</mxCell>')


FW = {}

# ============================ OVERVIEW (abs 1860,0) ============================
O = "ov"; OX, OY = 1860, 0; FW[O] = 2460
frame(O, OX, OY, 2460, 1700)
header("ov_h", O, None, "ClaimClarity · Data backend", "Draft")
zone("ov_zsftp", O, 40, 100, 1180, 160)
zone("ov_zbkt", O, 40, 320, 1180, 800)
zone("ov_phiA", O, 80, 390, 520, 700, "phi")
zone("ov_phiB", O, 667, 390, 520, 700, "phi")
zone("ov_zcw", O, 1280, 320, 640, 800)
zone("ov_farg", O, 1320, 700, 560, 350, "white")
zone("ov_zacc", O, 40, 1180, 1000, 300)
zone("ov_zcr", O, 1100, 1180, 840, 420)
zone("ov_ec2", O, 1130, 1240, 780, 220, "white")
zone("ov_zs3r", O, 2000, 1180, 400, 300)
label("ov_l_sftp", O, 60, 110, "SFTP")
iconlabel("ov_l_bkt", O, 60, 330, "s3", "Prod S3 bucket | claimclarity/")
label("ov_l_cw", O, 1300, 334, "Compute · write")
text("ov_l_cw2", O, 1580, 334, 320, 20, "Shown for Customer B · same per customer", align="right", size=13)
label("ov_l_acc", O, 60, 1194, "Access")
label("ov_l_cr", O, 1120, 1194, "Compute · read")
iconlabel("ov_l_s3r", O, 2020, 1190, "s3", "Prod S3 bucket | claimclarity/", isz=24)

for c, x in (("A", 80), ("B", 660)):
    box(f"ov_sftp{c}", O, x, 150, 520, 84, title_html(f"Customer {c}", "Daily 837 · 835 · 277", color=INK), "ext", icon="folder", isz=30)
for c, x in (("A", 100), ("B", 680)):
    label(f"ov_hdr{c}", O, x, 404, f"Customer {c}", size=18)
    gatelabel(f"ov_key{c}", O, 0, 400, "key_management_service", f"Key {c} · Role {c}", right=x + 480)
    raw = (f'<span style="{MONO}font-size:17px;font-weight:500;color:{NAVY}">raw/</span><br>'
           f'<span style="{MONO}font-size:14px">&nbsp;&nbsp;received_date=YYYY-MM-DD/</span> '
           f'<span style="color:{GRAY}">· raw EDI | bronze layer</span>')
    add(f"ov_raw{c}", raw, f"{BASE}rounded=0;fillColor={TINT};strokeColor={MID};align=left;verticalAlign=top;spacingLeft=18;spacingTop=12;fontSize=14;", x, 450, 480, 200, O)
    tx = x + 44
    for i, t in enumerate(["837/", "835/", "277/"]):
        tag(f"ov_raw{c}_t{i}", O, tx, 530, t, "outline", w=50, mono=True); tx += 56
    tag(f"ov_raw{c}_ts", O, tx, 530, "_SUCCESS", "solid", w=84, mono=True)
    lines = [("parquet/", "lossless parquet tables | silver layer"), ("semantic_layer/", "all sem.* views as Parquet | gold layer"),
             ("staging/", "· in-progress load"), ("quarantine/", "· failed files")]
    html = f'<span style="{COND}font-size:13px;letter-spacing:0.06em;color:{DEEP}">After parsing</span><br><br>' + "<br><br>".join(
        f'<span style="{MONO}">&nbsp;&nbsp;{a}</span> <span style="color:{GRAY}">{b}</span>' for a, b in lines)
    add(f"ov_aft{c}", html, f"{BASE}rounded=0;fillColor={TINT};strokeColor={MID};align=left;verticalAlign=top;spacingLeft=18;spacingTop=14;fontSize=14;", x, 760, 480, 230, O)

box("ov_eb", O, 1320, 420, 560, 80, title_html("EventBridge", "On _SUCCESS"), icon="eventbridge", isz=40)
box("ov_launch", O, 1320, 560, 560, 70, title_html("Launcher · ECS RunTask", "Starts the task as Role B · can't read data itself"), icon="role", isz=36)
iconlabel("ov_l_farg", O, 1336, 710, "fargate", "Fargate task · lives for one batch", isz=26, size=16)

steps = [("1", "<b>Start</b> · ECS hands it Role B temp creds", TINT, INK),
         ("2", "<b>Read</b> raw/ · parse each file", "#FFFFFF", INK),
         ("3", "<b>Write</b> parquet/ · rebuild semantic_layer/ once per batch", "#FFFFFF", INK),
         ("4", "<b>Exit</b> · creds expire, container destroyed", NAVY, "#FFFFFF")]
add("ov_steps", "", f"{BASE}rounded=0;fillColor=#FFFFFF;strokeColor={MID};container=1;pointerEvents=0;collapsible=0;", 1340, 750, 520, 280, O)
for i, (n, t, bg, fc) in enumerate(steps):
    add(f"ov_st{n}", t, f"{BASE}rounded=0;fillColor={bg};strokeColor=#C9CBCE;fontColor={fc};align=left;verticalAlign=middle;spacingLeft=50;fontSize=15;", 0, i * 70, 520, 70, "ov_steps")
    add(f"ov_st{n}_n", n, f"{BASE}ellipse;fillColor=none;strokeColor={'#FFFFFF' if bg == NAVY else MID};fontColor={fc if bg == NAVY else NAVY};fontFamily=Barlow Condensed;{FS_C}fontStyle=1;fontSize=13;", 14, i * 70 + 23, 24, 24, "ov_steps")

box("ov_user", O, 70, 1330, 200, 90, title_html("Xifin user", color=INK), "ext", icon="users", isz=34, pad=16)
box("ov_entra", O, 310, 1330, 220, 90, title_html("Microsoft Entra ID", "Company login", color=INK), "ext", icon="shield", isz=28, pad=16)
box("ov_cog", O, 570, 1330, 200, 90, title_html("Cognito", "Issues token"), icon="cognito", isz=32, pad=16)
box("ov_tok", O, 810, 1330, 200, 90, title_html("Signed token", "Names the customer"), pad=16)
iconlabel("ov_l_ec2", O, 1146, 1250, "ec2", "API backend · EC2", isz=30, size=16)
box("ov_api", O, 1160, 1330, 240, 90, title_html("API server", "Verifies token, gets creds"), pad=16)
agent_html = (f'<span style="{COND}font-size:19px;color:#FFFFFF">Claude agent</span><br>'
              f'<span style="font-size:12px;color:#FFFFFF">Bedrock · BAA</span>')
add("ov_agent", agent_html, f"{BASE}shape=hexagon;perimeter=hexagonPerimeter2;size=0.22;fixedSize=0;fillColor={NAVY};strokeColor={NAVY};fontColor=#FFFFFF;", 1440, 1325, 180, 100, O)
box("ov_duck", O, 1660, 1320, 200, 110, title_html("DuckDB", "Opened per request with Role B creds · closed after"), pad=16)
box("ov_sts", O, 1160, 1500, 240, 70, title_html("STS", "Temporary Role B creds"), icon="sts", isz=32, pad=16)
box("ov_gh", O, 1980, 860, 400, 100, title_html("View SQL · GitHub", f'<span style="{MONO}font-size:13px">semantic/views/</span> · code only · run by the parser each batch'), "white", icon="github", isz=36)
box("ov_s3a", O, 2020, 1240, 360, 60, f'<span style="{MONO}color:{GRAY}">customer_a/</span>', "ext", pad=16)
tag("ov_s3a_t", O, 2290, 1260, "No access", "ink", w=76)
box("ov_s3b", O, 2020, 1330, 360, 90, f'<span style="{MONO}font-size:13px;font-weight:500;color:{NAVY}">customer_b/ parquet + semantic_layer</span>', pad=16)
tag("ov_s3b_t", O, 2320, 1365, "Read", "mid", w=46)

# overview edges (drawn before gates so gates sit on top)
edge("ov_e1", "ov_sftpA", "ov_rawA", O, 0.769, 1, 0.792, 0, label="Copy script")
edge("ov_e2", "ov_sftpB", "ov_rawB", O, 0.769, 1, 0.792, 0, label="Copy script")
edge("ov_e3", "ov_rawB", "ov_eb", O, 1, 0.05, 0, 0.5)
edge("ov_e4", "ov_eb", "ov_launch", O, 0.5, 1, 0.5, 0)
edge("ov_e5", "ov_launch", "ov_farg", O, 0.5, 1, 0.5, 0)
edge("ov_e6", "ov_rawB", "ov_steps", O, 1, 0.75, 0, 0.375, pts=[(1220, 600), (1220, 855)], label="Read")
edge("ov_e7", "ov_steps", "ov_aftB", O, 0, 0.625, 1, 0.717, label="Write", lx=0.72)
edge("ov_e8", "ov_gh", "ov_steps", O, 0, 0.65, 1, 0.625, label="Materialize sem.* views", lx=0, ly=-14)
for a, b in (("user", "entra"), ("entra", "cog"), ("cog", "tok"), ("tok", "api"), ("api", "agent"), ("agent", "duck"), ("duck", "s3b")):
    edge(f"ov_e_{a}", f"ov_{a}", f"ov_{b}", O, 1, 0.5, 0, 0.5 if b != "s3b" else 0.5)
edge("ov_e_sts1", "ov_api", "ov_sts", O, 0.333, 1, 0.333, 0)
edge("ov_e_sts2", "ov_sts", "ov_api", O, 0.667, 0, 0.667, 1)

gatelabel("ov_g_eb", O, 1240, 400, "eventbridge", "Rule matches B?")
gatelabel("ov_g_pr", O, 1625, 650, "identity_and_access_management", "PassRole allowed?", center=False)
gatelabel("ov_g_kmsA", O, 0, 718, "key_management_service", "IAM + KMS allow?", right=1180)
gatelabel("ov_g_kmsB", O, 1110, 958, "key_management_service", "IAM + KMS allow?", center=False)
gatelabel("ov_g_tok", O, 1085, 1300, "cognito", "Token valid?")
gatelabel("ov_g_sts", O, 1340, 1450, "sts", "Trust policy allows B?", center=False)
gatelabel("ov_g_kms3", O, 1940, 1300, "key_management_service", "IAM + KMS allow?")
for gid, x, y, n in (("g1", 1223, 443, 1), ("g2", 1583, 648, 2), ("g3a", 1203, 713, 3), ("g3b", 1233, 908, 3),
                     ("g4", 1068, 1358, 4), ("g5", 1223, 1443, 5), ("g6", 1923, 1358, 6)):
    gate(f"ov_{gid}", O, x, y, n)
for n, x, y in (("00", 1207, 307), ("01", 1907, 307), ("02", 1027, 1167), ("03", 1927, 1167)):
    zoom(f"ov_z{n}", O, x, y, n)

# legend
lx = 1620
leg = [("hex", "Agent"), ("svc", "Service"), ("ext", "External"), ("phi", "PHI boundary"), ("gate", "AWS gate"), ("zoom", "Zoom-in")]
for i, (k, t) in enumerate(leg):
    sid = f"ov_leg{i}"
    if k == "hex":
        add(sid, "", f"shape=hexagon;perimeter=hexagonPerimeter2;size=0.22;fillColor={NAVY};strokeColor={NAVY};html=1;", lx, 1642, 26, 16, O)
    elif k == "svc":
        add(sid, "", f"fillColor={TINT};strokeColor={MID};html=1;", lx, 1642, 24, 15, O)
    elif k == "ext":
        add(sid, "", "fillColor=none;strokeColor=#9A9CA1;dashed=1;html=1;", lx, 1642, 24, 15, O)
    elif k == "phi":
        add(sid, "", f"fillColor=none;strokeColor={NAVY};strokeWidth=1.5;dashed=1;html=1;", lx, 1642, 24, 15, O)
    elif k == "gate":
        add(sid, "", f"rhombus;fillColor={NAVY};strokeColor=none;html=1;", lx, 1640, 20, 20, O)
    else:
        add(sid, "", f"ellipse;fillColor={MID};strokeColor=none;html=1;", lx, 1641, 18, 18, O)
    text(sid + "_t", O, lx + 32, 1640, 110, 20, f'<span style="{COND}font-weight:500;letter-spacing:0.06em">{t}</span>', size=13)
    lx += 32 + int(len(t) * 8.5) + 30

# ============================ 00 STORAGE (abs 120,0) ============================
S = "s0"; FW[S] = 1600
frame(S, 120, 0, 1600, 880)
header("s0_h", S, "00", "Storage &amp; isolation".replace("&amp;", "&"), "Shown for Customer B")
zone("s0_zb", S, 40, 100, 900, 740)
zone("s0_phi", S, 80, 160, 820, 520, "phi")
zone("s0_za", S, 80, 720, 820, 90, "gray")
zone("s0_zac", S, 1000, 100, 560, 740)
iconlabel("s0_l_b", S, 60, 108, "s3", "Prod S3 bucket | claimclarity/")
label("s0_l_ac", S, 1020, 114, "Access controls")
label("s0_hdr", S, 100, 176, "Customer B · PHI boundary", size=18)
gatelabel("s0_key", S, 0, 172, "key_management_service", "Key B · Role B", right=880)
add("s0_tree", f'<span style="{MONO}font-size:17px;font-weight:500;color:{NAVY}">customer_b/</span>',
    f"{BASE}rounded=0;fillColor={TINT};strokeColor={MID};align=left;verticalAlign=top;spacingLeft=20;spacingTop=16;fontSize=14;", 100, 220, 780, 440, S)
add("s0_tree_ln", "", f"line;direction=south;strokeColor={MID};strokeWidth=1;fillColor=none;html=1;", 125, 270, 1, 360, S)
rows = [("raw/received_date=YYYY-MM-DD/", "Raw EDI | bronze layer · never edited", 285),
        ("staging/{run_id}/", "In-progress load · deleted on failure", 385),
        ("parquet/", "Lossless parquet tables | silver layer", 445),
        ("semantic_layer/", "All sem.* views as Parquet | gold layer", 505),
        ("quarantine/", "Files that failed to parse", 565)]
for i, (a, b, y) in enumerate(rows):
    text(f"s0_r{i}", S, 146, y, 380, 22, f'<span style="{MONO}font-size:15px">{a}</span>')
    text(f"s0_r{i}d", S, 520, y, 340, 22, b, align="right", color=GRAY)
tx = 146
for i, t in enumerate(["837/", "835/", "277/"]):
    tag(f"s0_t{i}", S, tx, 318, t, "outline", w=50, mono=True); tx += 56
tag("s0_ts", S, tx, 318, "_SUCCESS", "solid", w=84, mono=True)
label("s0_ca", S, 100, 755, "Customer A", size=18, color=GRAY)
text("s0_ca2", S, 240, 755, 320, 20, f'<span style="{MONO}">customer_a/ · Key A · Role A</span>', color=GRAY)
tag("s0_ca_t", S, 726, 755, "No access for Role B", "ink", w=154)
cards = [("kms", "key_management_service", "KMS key B", "Key policy trusts only Role B", 170),
         ("iam", "identity_and_access_management", "IAM role B", f'Read and write <span style="{MONO}font-size:13px">customer_b/</span> only', 300),
         ("bp", "s3", "Bucket policy", "Denies any role outside its own prefix", 430),
         ("ct", "cloudtrail", "Audit logs", "CloudTrail data events · KMS decrypt logs", 560)]
for cid, ic, t, sub, y in cards:
    box(f"s0_{cid}", S, 1040, y, 480, 96, title_html(t, sub), icon=ic, isz=40)
box("s0_dbl", S, 1040, 700, 480, 100, title_html("Double security layer", "Reading PHI needs the role's access and the key's decrypt. One alone gets nothing.", color=INK, size=16), "ext")
edge("s0_e1", "s0_kms", "s0_phi", S, 0, 0.5, 1, 0.111, label="Encrypts")
edge("s0_e2", "s0_iam", "s0_phi", S, 0, 0.5, 1, 0.362, label="Grants")
edge("s0_e3", "s0_bp", "s0_phi", S, 0, 0.5, 1, 0.612, label="Limits")
edge("s0_e4", "s0_phi", "s0_ct", S, 1, 0.8615, 0, 0.5, label="Logged")

# ============================ 01 INGESTION (abs 4460,0) ============================
I = "s1"; FW[I] = 1900
frame(I, 4460, 0, 1900, 1030)
header("s1_h", I, "01", "Ingestion & parse", "Shown for Customer B · same per customer")
zone("s1_zt", I, 40, 100, 1820, 220)
zone("s1_phi", I, 40, 380, 1820, 520, "phi")
zone("s1_each", I, 330, 450, 500, 320, "loop")
zone("s1_batch", I, 845, 450, 995, 320, "loop")
zone("s1_za", I, 40, 930, 1820, 56, "gray")
label("s1_l_t", I, 60, 114, "Trigger")
iconlabel("s1_l_f", I, 240, 388, "fargate", "Parser · Fargate task · lives for one batch")
text("s1_l_phi", I, 1500, 396, 340, 20, f'<span style="{COND}letter-spacing:0.06em">Customer B · PHI boundary</span>', align="right", color=NAVY)
label("s1_l_e", I, 342, 458, "For each file", size=13)
text("s1_l_b", I, 1620, 458, 200, 20, f'<span style="{COND}letter-spacing:0.06em">Once per batch</span>', align="right", size=13, color=NAVY)
box("s1_sftp", I, 70, 160, 250, 110, title_html("SFTP", "Customer placed files", color=INK), "ext", icon="folder", isz=32)
box("s1_copy", I, 380, 160, 290, 110, title_html("Copy script", "Copies new files, writes _SUCCESS last"))
box("s1_raw", I, 730, 160, 300, 110, title_html("S3 · raw/", "_SUCCESS lists each file + hash"), icon="s3", isz=44)
box("s1_eb", I, 1090, 160, 300, 110, title_html("EventBridge", "Fires on _SUCCESS · customer from the path"), icon="eventbridge", isz=44)
box("s1_ln", I, 1450, 160, 380, 110, title_html("Launcher · ECS RunTask", "Starts the task with Role B · can't read data itself"), icon="role", isz=40)
box("s1_rs", I, 80, 515, 240, 90, title_html("Read _SUCCESS", "File list + hashes"), icon="doc", isz=30)
add("s1_d1", "", f"rhombus;html=1;fillColor=#FFFFFF;strokeColor={MID};strokeWidth=1.5;", 375, 515, 90, 90, I)
box("s1_parse", I, 520, 515, 300, 90, title_html("Parse EDI", f'Write to <span style="{MONO}font-size:13px">staging/{{run_id}}/</span>'), icon="fargate", isz=40)
add("s1_d2", "", f"rhombus;html=1;fillColor=#FFFFFF;strokeColor={MID};strokeWidth=1.5;", 915, 515, 90, 90, I)
box("s1_prom", I, 1060, 515, 300, 90, title_html("Promote to parquet/", "Whole batch visible at once"), icon="s3", isz=40)
box("s1_reb", I, 1460, 515, 360, 90, title_html("Rebuild semantic_layer/", "Runs all sem.* view SQL from GitHub"), icon="s3", isz=40)
box("s1_skip", I, 340, 680, 160, 60, title_html("Skip file", color=INK, size=17), "ext", align="center", pad=8)
box("s1_q", I, 860, 680, 200, 72, title_html("Quarantine", '<span style="font-size:13px">Delete staging</span>', color=INK, size=17), "ext", align="center", pad=8)
text("s1_q1", I, 360, 482, 120, 20, f'<span style="{COND}font-weight:500;letter-spacing:0.06em">Already loaded?</span>', align="center", size=13, color=DEEP)
text("s1_q2", I, 890, 482, 140, 20, f'<span style="{COND}font-weight:500;letter-spacing:0.06em">Every file parsed?</span>', align="center", size=13, color=DEEP)
edge("s1_e1", "s1_sftp", "s1_copy", I, 1, 0.5, 0, 0.5)
edge("s1_e2", "s1_copy", "s1_raw", I, 1, 0.5, 0, 0.5)
edge("s1_e3", "s1_raw", "s1_eb", I, 1, 0.5, 0, 0.5)
edge("s1_e4", "s1_eb", "s1_ln", I, 1, 0.5, 0, 0.5)
edge("s1_e5", "s1_ln", "s1_rs", I, 0.5, 1, 0.5, 0, pts=[(1640, 345), (200, 345)], label="Starts task", lx=-0.15)
edge("s1_e6", "s1_rs", "s1_d1", I, 1, 0.5, 0, 0.5)
edge("s1_e7", "s1_d1", "s1_parse", I, 1, 0.5, 0, 0.5, label="No")
edge("s1_e8", "s1_parse", "s1_d2", I, 1, 0.5, 0, 0.5)
edge("s1_e9", "s1_d2", "s1_prom", I, 1, 0.5, 0, 0.5, label="Yes")
edge("s1_e10", "s1_prom", "s1_reb", I, 1, 0.5, 0, 0.5)
edge("s1_e11", "s1_d1", "s1_skip", I, 0.5, 1, 0.5, 0, label="Yes")
edge("s1_e12", "s1_d2", "s1_q", I, 0.5, 1, 0.5, 0, label="No")
add("s1_bar", "", f"{BASE}rounded=0;fillColor=#FFFFFF;strokeColor={MID};container=1;pointerEvents=0;collapsible=0;", 80, 800, 1740, 70, I)
segs = [("<b>Start</b> · ECS issues Role B temp creds, rotated automatically", TINT, INK, 18),
        ("Every S3 read and write passes IAM + KMS for Role B", "#FFFFFF", INK, 56),
        ("<b>Exit</b> · creds expire, container and disk destroyed", NAVY, "#FFFFFF", 18)]
for i, (t, bg, fc, sl) in enumerate(segs):
    add(f"s1_seg{i}", t, f"{BASE}rounded=0;fillColor={bg};strokeColor={MID};fontColor={fc};align=left;verticalAlign=middle;spacingLeft={sl};fontSize=15;", i * 580, 0, 580, 70, "s1_bar")
gate("s1_g3", "s1_bar", 580 + 18, 22, 3, s=26)
text("s1_ca", I, 60, 944, 1780, 28, f'<span style="{COND}font-size:16px;color:{GRAY}">Customer A</span>&nbsp;&nbsp;&nbsp;Same chain, separate task, runs as Role A. One task never holds two customers\' roles.', color=GRAY)
gatelabel("s1_gl1", I, 1060, 128, "eventbridge", "Rule matches B?")
gatelabel("s1_gl2", I, 1300, 362, "identity_and_access_management", "PassRole allowed?")
gate("s1_g1", I, 1043, 198, 1)
gate("s1_g2", I, 1283, 328, 2)

# ============================ 02 LOGIN (abs 0,1100) ============================
L = "s2"; FW[L] = 1720
frame(L, 0, 1100, 1720, 600)
header("s2_h", L, "02", "Login & customer mapping", None)
zone("s2_z", L, 40, 100, 1640, 240)
label("s2_l", L, 60, 114, "Access")
box("s2_user", L, 70, 170, 240, 100, title_html("Xifin user", "Opens the app", color=INK), "ext", icon="users", isz=38)
box("s2_entra", L, 370, 170, 280, 100, title_html("Microsoft Entra ID", "Company login + the user's groups", color=INK), "ext", icon="shield", isz=32)
box("s2_cog", L, 710, 170, 280, 100, title_html("Cognito", "Issues the app's token"), icon="cognito", isz=44)
box("s2_tok", L, 1050, 170, 300, 100, "")
text("s2_tok_l", L, 1068, 190, 260, 24, title_html("Signed token"))
tag("s2_tok_t", L, 1068, 224, "customers: [customer_b]", "outline", w=206, mono=True)
box("s2_api", L, 1410, 170, 240, 100, title_html("API server", "Verifies the token"), icon="ec2", isz=40)
box("s2_map", L, 710, 400, 640, 150, title_html("Group → customer map",
    f'<span style="{MONO}">ClaimClarity-LABCORP → labcorp</span><br><span style="{MONO}">ClaimClarity-CUSTB&nbsp;&nbsp;&nbsp;→ customer_b</span><br>'
    f'<span style="color:{GRAY}">Admins add users to groups in Entra</span>'), "white", icon="doc", isz=40, pad=22)
edge("s2_e1", "s2_user", "s2_entra", L, 1, 0.5, 0, 0.5)
edge("s2_e2", "s2_entra", "s2_cog", L, 1, 0.5, 0, 0.5, label="Groups")
edge("s2_e3", "s2_cog", "s2_tok", L, 1, 0.5, 0, 0.5)
edge("s2_e4", "s2_tok", "s2_api", L, 1, 0.5, 0, 0.5)
edge("s2_e5", "s2_map", "s2_cog", L, 0.219, 0, 0.5, 1, label="Adds customer")
gatelabel("s2_gl", L, 1380, 136, "cognito", "Token valid?")
gate("s2_g4", L, 1363, 203, 4)

# ============================ 03 QUERY (abs 4460,1170) ============================
Q = "s3"; FW[Q] = 1900
frame(Q, 4460, 1170, 1900, 800)
header("s3_h", Q, "03", "Query path", "Shown for Customer B")
zone("s3_zec2", Q, 340, 100, 1000, 480)
zone("s3_zs3", Q, 1400, 100, 460, 480)
iconlabel("s3_l1", Q, 360, 106, "ec2", "API backend · EC2")
iconlabel("s3_l2", Q, 1420, 106, "s3", "Prod S3 bucket | claimclarity/")
label("s3_l3", Q, 60, 604, "What gets blocked")
add("s3_chat", "", f"{BASE}rounded=0;fillColor=#FFFFFF;strokeColor=#A3A5A7;container=1;pointerEvents=0;collapsible=0;", 40, 120, 240, 180, Q)
add("s3_chat_bar", f'<span style="{COND}font-weight:500;letter-spacing:0.06em;color:{DEEP}">● ● ●&nbsp;&nbsp;Chat</span>',
    f"{BASE}rounded=0;fillColor=#FFFFFF;strokeColor=#A3A5A7;align=left;verticalAlign=middle;spacingLeft=10;fontSize=13;", 0, 0, 240, 32, "s3_chat")
text("s3_chat_q", "s3_chat", 0, 68, 240, 22, '<span style="font-weight:500">User question</span>', align="center", size=16)
tag("s3_chat_t", "s3_chat", 76, 104, "Customer B", "tint", w=88)
box("s3_api", Q, 380, 165, 280, 90, title_html("API server", "Verifies token · customer = B"))
box("s3_sts", Q, 720, 165, 260, 90, title_html("STS", "Short-lived Role B creds"), icon="sts", isz=40)
box("s3_duck", Q, 1040, 165, 260, 90, title_html("DuckDB", "Opened per request · closed after"), icon="db", isz=34)
add("s3_agent", (f'<span style="{COND}font-size:22px;color:#FFFFFF">Claude agent</span><br>'
                 f'<span style="font-size:13px;color:#FFFFFF">Bedrock · BAA</span>'),
    f"{BASE}shape=hexagon;perimeter=hexagonPerimeter2;size=0.22;fillColor={NAVY};strokeColor={NAVY};fontColor=#FFFFFF;", 400, 340, 240, 120, Q)
box("s3_comp", Q, 1040, 355, 260, 90, title_html("Metric compiler", '<span style="font-size:13px">Writes SQL over semantic_layer/ tables</span>'), "white")
box("s3_cb", Q, 1430, 165, 400, 90, f'<span style="{MONO}font-size:13px;font-weight:500;color:{NAVY}">customer_b/ parquet + semantic_layer</span>')
tag("s3_cb_t", Q, 1766, 200, "Read", "mid", w=46)
box("s3_ca", Q, 1430, 300, 400, 60, f'<span style="{MONO}color:{GRAY}">customer_a/</span>', "ext")
tag("s3_ca_t", Q, 1736, 320, "No access", "ink", w=76)
add("s3_bar", "", f"{BASE}rounded=0;fillColor=#FFFFFF;strokeColor={MID};container=1;pointerEvents=0;collapsible=0;", 380, 500, 920, 56, Q)
for i, (t, bg, fc) in enumerate([("<b>Open</b> · DuckDB with Role B creds", TINT, INK),
                                  ("<b>Query</b> · semantic_layer/ only", "#FFFFFF", INK),
                                  ("<b>Close</b> · creds expire in 15 min", NAVY, "#FFFFFF")]):
    add(f"s3_seg{i}", t, f"{BASE}rounded=0;fillColor={bg};strokeColor={MID};fontColor={fc};align=left;verticalAlign=middle;spacingLeft=16;fontSize=14;", round(i * 920 / 3, 1), 0, round(920 / 3, 1), 56, "s3_bar")
box("s3_rej", Q, 40, 630, 880, 90, f'<span style="{MONO}font-size:13px">GET /claims?customer=customer_a</span><br>The customer comes only from the verified token, never the URL or body', "black", pad=118)
tag("s3_rej_t", Q, 58, 665, "✕ Rejected", "ink", w=88)
box("s3_den", Q, 960, 630, 900, 90, f'Agent SQL points at <span style="{MONO}font-size:13px">customer_a/</span><br>Role B creds can\'t read it. Enforcement is in AWS, not app code.', "black", pad=148)
tag("s3_den_t", Q, 978, 665, "✕ Denied by IAM", "ink", w=118)
edge("s3_e1", "s3_chat", "s3_api", Q, 1, 0.5, 0, 0.5, label="Ask", lx=-0.55)
edge("s3_e2", "s3_api", "s3_sts", Q, 1, 0.5, 0, 0.5)
edge("s3_e3", "s3_sts", "s3_duck", Q, 1, 0.5, 0, 0.5)
edge("s3_e4", "s3_duck", "s3_cb", Q, 1, 0.5, 0, 0.5)
edge("s3_e5", "s3_api", "s3_agent", Q, 0.357, 1, 0.333, 0, label="Data")
edge("s3_e6", "s3_agent", "s3_api", Q, 0.667, 0, 0.643, 1, label="Tools")
edge("s3_e7", "s3_agent", "s3_comp", Q, 1, 0.5, 0, 0.5, label="Metrics by name")
edge("s3_e8", "s3_comp", "s3_duck", Q, 0.5, 0, 0.5, 1, label="SQL")
gatelabel("s3_gl4", Q, 335, 132, "cognito", "Token valid?")
gatelabel("s3_gl5", Q, 690, 132, "sts", "Trust policy allows B?")
gatelabel("s3_gl6", Q, 1365, 132, "key_management_service", "IAM + KMS allow?")
gate("s3_g4", Q, 330, 193, 4)
gate("s3_g5", Q, 673, 193, 5)
gate("s3_g6", Q, 1348, 193, 6)

# ============================ PAGE TITLE + ZOOM-IN LINKS ============================
text("page_title", "1", 1860 + DX, -110 + DY, 900, 50, f'<span style="{COND}font-size:44px">ClaimClarity Data Backend</span>', size=44)
ZST = (f"edgeStyle=orthogonalEdgeStyle;rounded=1;arcSize=24;html=1;strokeColor={MID};strokeWidth=1;opacity=45;"
       "dashed=1;dashPattern=4 4;endArrow=open;endSize=7;startArrow=oval;startSize=4;startFill=1;")
zoom_links = [("zl_00", "ov_z00", "s0", 1, 0.5, 0.5, 0, [(3100, 320), (3100, -40), (920, -40)]),
              ("zl_01", "ov_z01", "s1", 1, 0.5, 0.5, 0, [(3800, 320), (3800, -40), (5410, -40)]),
              ("zl_02", "ov_z02", "s2", 0.5, 0, 1, 0.5, [(2900, 1150), (1790, 1150), (1790, 1400)]),
              ("zl_03", "ov_z03", "s3", 0.5, 0, 0, 0.5, [(3800, 1150), (4390, 1150), (4390, 1570)])]
zl = []
for cid, s, t, ex, ey, nx, ny, pts in zoom_links:
    st = ZST + f"exitX={ex};exitY={ey};exitDx=0;exitDy=0;entryX={nx};entryY={ny};entryDx=0;entryDy=0;"
    geo = '<mxGeometry relative="1" as="geometry"><Array as="points">' + "".join(f'<mxPoint x="{a + DX}" y="{b + DY}"/>' for a, b in pts) + "</Array></mxGeometry>"
    zl.append(f'<mxCell id="{cid}" value="" style="{st}" edge="1" parent="1" source="{s}" target="{t}">{geo}</mxCell>')

# zoom links last: they only run through empty gaps
xml = ('<mxfile host="drawio" version="29.0.3"><diagram id="claimclarity-backend" name="Data backend">'
       '<mxGraphModel dx="1600" dy="900" grid="1" gridSize="12" guides="1" tooltips="1" connect="1" arrows="1" fold="1" '
       'page="1" pageScale="1" pageWidth="6500" pageHeight="2200" math="0" shadow="0" background="#FFFFFF"><root>'
       '<mxCell id="0"/><mxCell id="1" parent="0"/>' + "".join(cells) + "".join(zl) + "</root></mxGraphModel></diagram></mxfile>")
open(sys.argv[1], "w").write(xml)
print(len(cells), "cells")
