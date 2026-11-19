"""Показує вкладеність контурів DXF (глибина 0 = зовнішній, 1 = отвір, 2 = острівець у отворі).
python3 -I nesting_levels.py file.dxf [...]  — самодостатній, без shapely."""
import sys, os, math
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spline import spline_pts, lwpoly_pts  # SPLINE, LWPOLYLINE з 05.11.2026

TOL = 1e-3


def load(path):
    raw = open(path, "rb").read().decode("latin-1").split("\r\n")
    if raw and raw[-1] == "":
        raw.pop()
    pairs = [(raw[i].strip(), raw[i + 1]) for i in range(0, len(raw) - 1, 2)]
    s = [i for i, (g, v) in enumerate(pairs) if g == "2" and v == "ENTITIES"][0]
    ents, cur = [], None
    for g, v in pairs[s:]:
        if g == "0":
            if v == "ENDSEC":
                break
            cur = {"t": v, "d": defaultdict(list), "seq": []}
            ents.append(cur)
        elif cur is not None:
            cur["d"][g].append(v); cur["seq"].append((g, v))
    return ents


def F(e, g, i=0):
    return float(e["d"][g][i])


def arc(cx, cy, r, a0, a1):
    a0, a1 = math.radians(a0), math.radians(a1)
    while a1 <= a0:
        a1 += 2 * math.pi
    n = max(8, int(math.degrees(a1 - a0)))
    t = np.linspace(a0, a1, n + 1)
    return np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)])


def segs_of(ents):
    segs, circ, poly = [], [], None
    for e in ents:
        t = e["t"]
        if t == "LINE":
            segs.append(("LINE", np.array([[F(e, "10"), F(e, "20")], [F(e, "11"), F(e, "21")]])))
        elif t == "ARC":
            segs.append(("ARC", arc(F(e, "10"), F(e, "20"), F(e, "40"), F(e, "50"), F(e, "51"))))
        elif t == "LWPOLYLINE":
            segs.append(("LWPOLY", np.array(lwpoly_pts(e["seq"], bool(int(float((e["d"].get("70") or ["0"])[0])) & 1)))))
        elif t == "SPLINE":
            segs.append(("SPLINE", np.array(spline_pts(e["d"]))))
        elif t == "CIRCLE":
            circ.append((F(e, "10"), F(e, "20"), F(e, "40")))
        elif t == "POLYLINE":
            poly = []
        elif t == "VERTEX":
            poly.append([F(e, "10"), F(e, "20")])
        elif t == "SEQEND":
            if poly:
                segs.append(("POLY", np.array(poly)))
            poly = None
    return segs, circ


def same(a, b):
    return abs(a[0] - b[0]) < TOL and abs(a[1] - b[1]) < TOL


def chain(segs):
    left = list(range(len(segs)))
    out = []
    while left:
        i = left.pop(0)
        kinds = [segs[i][0]]
        pts = segs[i][1].copy()
        go = True
        while go:
            go = False
            if len(pts) > 2 and same(pts[0], pts[-1]):
                break
            for j in list(left):
                k, p = segs[j]
                if same(pts[-1], p[0]):
                    pts = np.vstack([pts, p[1:]])
                elif same(pts[-1], p[-1]):
                    pts = np.vstack([pts, p[::-1][1:]])
                else:
                    continue
                kinds.append(k)
                left.remove(j)
                go = True
                break
        out.append({"kinds": kinds, "pts": pts, "closed": bool(same(pts[0], pts[-1]))})
    return out


def area(p):
    x, y = p[:, 0], p[:, 1]
    return abs(0.5 * float(np.sum(x[:-1] * y[1:] - x[1:] * y[:-1])))


def inside(pt, poly):
    x, y = pt
    ins = False
    for i in range(len(poly) - 1):
        x1, y1 = poly[i]
        x2, y2 = poly[i + 1]
        if (y1 > y) != (y2 > y) and x1 + (y - y1) * (x2 - x1) / (y2 - y1) > x:
            ins = not ins
    return ins


def describe(it):
    p = it["pts"]
    w = p[:, 0].max() - p[:, 0].min()
    h = p[:, 1].max() - p[:, 1].min()
    cx = (p[:, 0].max() + p[:, 0].min()) / 2
    cy = (p[:, 1].max() + p[:, 1].min()) / 2
    k = it["kinds"]
    if k == ["CIRCLE"]:
        return "коло Ø%.1f" % w, cx, cy
    if k.count("LINE") == 4 and len(k) == 4:  # лише 4 LINE (05.11.2026: раніше 4 LINE + SPLINE теж ставали «прямокутником»)
        return "прямокутник %.1f x %.1f" % (min(w, h), max(w, h)), cx, cy
    if k.count("LINE") == 2 and k.count("ARC") == 2 and len(k) == 4:
        return "овальний паз %.1f x %.1f" % (min(w, h), max(w, h)), cx, cy
    return "контур (%d сегм.) %.1f x %.1f" % (len(k), w, h), cx, cy


for path in sys.argv[1:]:
    segs, circ = segs_of(load(path))
    items = chain(segs)
    for cx, cy, r in circ:
        t = np.linspace(0, 2 * math.pi, 181)
        items.append({"kinds": ["CIRCLE"], "pts": np.column_stack([cx + r * np.cos(t), cy + r * np.sin(t)]), "closed": True})
    for it in items:
        it["area"] = area(it["pts"])
    for it in items:
        it["depth"] = sum(1 for o in items if o is not it and o["area"] > it["area"] and inside(it["pts"][0], o["pts"]))
    print("==", os.path.basename(path), "| контурів:", len(items), "| незамкнених:", sum(1 for i in items if not i["closed"]))
    for it in sorted(items, key=lambda i: (i["depth"], -i["area"])):
        d, cx, cy = describe(it)
        print("  " + "    " * it["depth"] + "[рівень %d] %s  центр (%.1f; %.1f)  площа %.1f мм2" % (it["depth"], d, cx, cy, it["area"]))
