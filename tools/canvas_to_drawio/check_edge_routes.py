"""Check that every edge is drawn along the path it was given.

Exports the .drawio to SVG with the draw.io CLI, reads the path draw.io actually draws for each edge, and compares
its corners with the edge's sourcePoint, waypoints and targetPoint. Orthogonal routing can add jogs or detours
around shapes; any edge whose drawn corners differ from the intended ones by more than 2px is reported.

Usage: python3 check_edge_routes.py file.drawio /path/to/drawio   (exit 1 on any finding)
"""
import os, re, subprocess, sys, tempfile
import xml.etree.ElementTree as ET


def intended_routes(drawio_path):
    """Edge id -> (route, has start arrow, has end arrow)."""
    routes = {}
    for cell in ET.parse(drawio_path).iter("mxCell"):
        if cell.get("edge") != "1":
            continue
        g = cell.find("mxGeometry")
        ends = {p.get("as"): (float(p.get("x")), float(p.get("y"))) for p in g.findall("mxPoint")}
        mids = [(float(p.get("x")), float(p.get("y"))) for p in g.findall("Array/mxPoint")]
        style = cell.get("style") or ""
        routes[cell.get("id")] = ([ends["sourcePoint"], *mids, ends["targetPoint"]],
                                  "startArrow=none" not in style and "startArrow=" in style,
                                  "endArrow=none" not in style)
    return routes


def corners(points):
    """Drop repeated and collinear points, leaving the polyline's corners."""
    out = []
    for p in points:
        if out and abs(out[-1][0] - p[0]) < 0.5 and abs(out[-1][1] - p[1]) < 0.5:
            continue
        out.append(p)
    kept = out[:1]
    for i in range(1, len(out) - 1):
        a, b, c = kept[-1], out[i], out[i + 1]
        if abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) > 1.0:
            kept.append(b)
    return kept + out[-1:] if len(out) > 1 else out


def drawn_routes(svg_path):
    svg = open(svg_path, encoding="utf-8").read()
    routes = {}
    for match in re.finditer(r'data-cell-id="(e\d+)"><g(?: transform="translate\(([-\d.]+),([-\d.]+)\)")?><path d="([^"]+)"', svg):
        cid, d = match.group(1), match.group(4)
        tx, ty = (float(match.group(2)), float(match.group(3))) if match.group(2) else (0.5, 0.5)
        nums = [float(v) for v in re.findall(r"-?\d*\.?\d+", d)]
        points, i = [], 0
        for cmd in re.findall(r"[MLQC]", d):
            n = {"M": 2, "L": 2, "Q": 4, "C": 6}[cmd]
            vals = nums[i:i + n]
            i += n
            if cmd == "Q":  # a rounded corner: its control point is the corner itself
                points += [(vals[0], vals[1]), (vals[2], vals[3])]
            else:
                points.append((vals[-2], vals[-1]))
        routes[cid] = [(x + tx - 0.5, y + ty - 0.5) for x, y in points]
    return routes


ARROW_GAP = 14  # the drawn line stops where its arrowhead begins


def close(want, got, index, count, start_arrow, end_arrow, route):
    """Corners must match within 2px; an end with an arrowhead may stop short along its own segment."""
    if abs(want[0] - got[0]) <= 2 and abs(want[1] - got[1]) <= 2:
        return True
    arrow_end = (index == 0 and start_arrow) or (index == count - 1 and end_arrow)
    if not arrow_end:
        return False
    neighbour = route[1] if index == 0 else route[-2]
    along_x = abs(want[1] - neighbour[1]) < 0.5  # horizontal final segment
    gap = abs(want[0] - got[0]) if along_x else abs(want[1] - got[1])
    off = abs(want[1] - got[1]) if along_x else abs(want[0] - got[0])
    return off <= 2 and gap <= ARROW_GAP


def findings(drawio_path, drawio_binary):
    with tempfile.TemporaryDirectory() as tmp:
        svg_path = os.path.join(tmp, "out.svg")
        subprocess.run(["xvfb-run", "-a", drawio_binary, "-x", "-f", "svg", "--border", "0", "-o", svg_path, drawio_path,
                        "--no-sandbox", "--disable-gpu"], check=True, capture_output=True, env={**os.environ, "HOME": "/tmp"})
        drawn = drawn_routes(svg_path)
    out = []
    for cid, (route, start_arrow, end_arrow) in intended_routes(drawio_path).items():
        want, got = corners(route), corners(drawn.get(cid, []))
        # the SVG export is offset to the diagram's bounding box; the background rect starts at 0,0, so offsets match
        if len(want) != len(got) or any(not close(a, b, i, len(want), start_arrow, end_arrow, want)
                                        for i, (a, b) in enumerate(zip(want, got))):
            out.append(f"{cid}: drawn {[(round(x), round(y)) for x, y in got]} instead of {[(round(x), round(y)) for x, y in want]}")
    return out


if __name__ == "__main__":
    problems = findings(sys.argv[1], sys.argv[2])
    print("\n".join(problems) + f"\n\n{len(problems)} finding(s)")
    sys.exit(1 if problems else 0)
