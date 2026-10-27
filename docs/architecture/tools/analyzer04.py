"""python3 docs/architecture/tools/analyzer04.py <user's agent_analyzer.excalidraw>: write drilldowns/04-analyzer.excalidraw.

04 is the user's own excalidraw, edited, not redrawn. Every element keeps its position and label, except:
1. the KPI gate (a diamond on the "User Query" arrow) and the "Preset KPI queries" box are added inside the Claim Query
   Agent box, at its entry; the box grows left and everything right of it shifts right to make room; yes runs to the
   presets and on to the SQL guard, no continues into the agent path;
2. wording: the agent node, the DuckDB box (now the DuckDB query service) and cli.py;
3. the two IAM + KMS diamonds are bigger, numbered 6, and the callout reads "GATE 6 · IAM + KMS: this customer only".
Render it with the excalidraw-diagram skill's renderer (references/render_excalidraw.py).
"""
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "drilldowns" / "04-analyzer.excalidraw"
SHIFT = 700                      # room for the KPI gate between the frontend and the agent
SHIFT_FROM = -4330               # the Claim Query Agent zone starts at x = -4313


def main(src):
    doc = json.loads(Path(src).read_text())
    els = [e for e in doc["elements"] if not e.get("isDeleted")]
    by = {e["id"][:10]: e for e in els}               # elements by the first 10 characters of their id
    seed = [900000]

    def new(typ, x, y, w, h, **kw):
        seed[0] += 11
        e = dict(id=f"kpi_{typ}_{seed[0]}", type=typ, x=x, y=y, width=w, height=h, angle=0, strokeColor="#1e1e1e",
                 backgroundColor="transparent", fillStyle="solid", strokeWidth=2, strokeStyle="solid", roughness=1,
                 opacity=100, groupIds=[], frameId=None, roundness=None, seed=seed[0], version=1, versionNonce=seed[0] * 7,
                 isDeleted=False, boundElements=None, updated=1, link=None, locked=False)
        e.update(kw)
        els.append(e)
        return e

    def text(x, y, s, size=20, align="center", w=None, container=None):
        lines = s.split("\n")
        w = w or max(len(l) for l in lines) * size * 0.56
        return new("text", x - (w / 2 if align == "center" else 0), y, w, len(lines) * size * 1.25, text=s, originalText=s,
                   fontSize=size, fontFamily=5, textAlign=align, verticalAlign="middle" if container else "top",
                   containerId=container, lineHeight=1.25, autoResize=True)

    def arrow(pts, **kw):
        x0, y0 = pts[0]
        rel = [[px - x0, py - y0] for px, py in pts]
        xs, ys = [p[0] for p in rel], [p[1] for p in rel]
        return new("arrow", x0, y0, max(xs) - min(xs), max(ys) - min(ys), points=rel, lastCommittedPoint=None,
                   startBinding=None, endBinding=None, startArrowhead=None, endArrowhead="arrow", elbowed=False,
                   **{"roughness": 0, **kw})

    def retext(tid, s, size=None):
        t = by[tid]
        box = by[t["containerId"][:10]] if t.get("containerId") else None
        t["text"] = t["originalText"] = s
        if size:
            t["fontSize"] = size
        lines = s.split("\n")
        t["width"] = max(len(l) for l in lines) * t["fontSize"] * 0.56
        t["height"] = len(lines) * t["fontSize"] * 1.25
        if box:
            t["x"] = box["x"] + (box["width"] - t["width"]) / 2
            t["y"] = box["y"] + (box["height"] - t["height"]) / 2

    # 1. shift the agent and everything right of it
    for e in els:
        if e["x"] >= SHIFT_FROM:
            e["x"] += SHIFT
    for aid in ("qI9MfLE4fF",):                       # follow-up arrow: frontend -> agent zone, now longer
        a = by[aid]; a["points"][-1][0] += SHIFT; a["width"] += SHIFT
    a = by["TvL2CjKkfN"]                               # answer arrow: agent zone -> frontend, starts further right
    a["points"][-1][0] -= SHIFT; a["width"] += SHIFT    # (the loop above already moved its start)
    # the agent box grows left by the same amount, so the KPI gate and the presets sit inside it, at its entry
    zone = by["rL8BxLtQ6X"]; zone["x"] -= SHIFT; zone["width"] += SHIFT
    by["qHtTjH_-AM"]["x"] -= SHIFT                    # the box's title stays at its top-left corner
    uq = by["liKA4lL-bL"]                             # User Query: frontend -> the gate
    gx, gy, gw, gh = -4250, 1120, 240, 200            # KPI gate diamond, centred on the User Query arrow (y = 1220)
    uq["points"][-1][0] = gx - uq["x"]; uq["width"] = gx - uq["x"]; uq["endBinding"] = None
    bank = by["BInIQNotFm"]
    new("diamond", gx, gy, gw, gh, backgroundColor="#fff3bf")
    text(gx + gw / 2, gy + gh / 2 - 38, "KPI gate\ndoes a preset\nanswer it?", 20)
    arrow([(gx + gw, 1220), (bank["x"], 1220)])        # no: the question goes on to the agent path
    text((gx + gw + bank["x"]) / 2, 1188, "no", 20)
    px, py, pw, ph = -4290, 930, 420, 160             # presets, inside the box above the gate
    new("rectangle", px, py, pw, ph, roundness={"type": 3}, strokeWidth=4)
    text(px + pw / 2, py + 14, "Preset KPI queries\n16 presets, fixed SQL; the gate fills\npayer, payer class, month, CPT, CARC", 20)
    arrow([(gx + gw / 2, gy), (gx + gw / 2, py + ph)])
    text(gx + gw / 2 + 14, 1098, "yes", 20, "left", w=40)
    guard = by["cStFoef2wJ"]
    gcx = guard["x"] + guard["width"] / 2
    arrow([(px + pw - 30, py), (px + pw - 30, 790), (gcx, 790), (gcx, guard["y"])])
    text((px + gcx) / 2, 756, "preset SQL, parameters checked against the data", 20)

    # 2. wording
    retext("rpSyu-mhGo", "Claude Haiku · one agent\n(Strands on Amazon Bedrock)")
    retext("u5ouzPkJwj", "DuckDB query service\nQuack server, own Fargate service\none session per run\nread-only SELECT, row/time/cost caps")
    cli = by["klRhw-nptO"]
    cli["x"] -= 60; cli["width"] += 60                 # widen leftwards; the arrow into it gets shorter
    a = by["3FgTcUyAcv"]; a["points"][-1][0] -= 60; a["width"] -= 60
    retext("CyNxJ7u2C7", "cli.py ·\nClaimClarity CLI", 20)

    # 3. bigger, numbered gate-6 diamonds, still on their arrows
    for did in ("499pBNb1Mh", "AeKE4Dl3tF"):
        dm = by[did]
        cx, cy = dm["x"] + dm["width"] / 2, dm["y"] + dm["height"] / 2
        dm["width"], dm["height"] = 104, 96
        dm["x"], dm["y"] = cx - 52, cy - 48
        text(cx, cy - 18, "6", 28)
    by["80_0YFkhw-"]["text"] = by["80_0YFkhw-"]["originalText"] = "GATE 6 · IAM + KMS: this customer only"
    by["80_0YFkhw-"]["width"] = 38 * 28 * 0.56
    by["80_0YFkhw-"]["x"] -= 170                      # the longer callout would touch the customer prefix border
    low = by["AeKE4Dl3tF"]
    call = by["DQGZJ5UYd0"]                            # the callout arrow ends on the lower diamond's bottom tip
    call["points"][-1] = [low["x"] + 52 - call["x"], low["y"] + 96 - call["y"]]

    doc["elements"] = els
    OUT.write_text(json.dumps(doc, indent=1))
    print(OUT)


if __name__ == "__main__":
    main(sys.argv[1])
