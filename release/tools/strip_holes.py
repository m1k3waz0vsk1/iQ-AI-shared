#!/usr/bin/env python3
"""Робить DXF «БО» з DXF «МБ»: лишає тільки зовнішній замкнений контур, прибирає отвори,
виточки, виборки й усе інше всередині. Решта файлу (HEADER, TABLES, шари) без змін.
python3 strip_holes.py <джерело.DXF> <результат.DXF> [--keep-psg]
--keep-psg (13.11.2026, ПСГ БО): лишає також замкнені контури, що перетинають край деталі (смуга ПСГ, виборка під грудину).
Підтверджене користувачем правило (27.10.2026): якщо в КД немає БО, а є однозначний DXF того ж
виробу з отворами (МБ10/МБ12), беремо його і прибираємо отвори. Лише стандартна бібліотека."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spline import spline_pts  # SPLINE з 05.11.2026

TOL = 1e-3

def main(src, dst, keep_psg=False):
    raw = open(src, "rb").read().decode("latin-1")
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(nl)
    if lines and lines[-1] == "":
        lines.pop()
    pairs = [(lines[i], lines[i + 1]) for i in range(0, len(lines) - 1, 2)]
    s = next(i for i, (g, v) in enumerate(pairs) if g.strip() == "2" and v.strip() == "ENTITIES") + 1
    e = next(i for i in range(s, len(pairs)) if pairs[i][0].strip() == "0" and pairs[i][1].strip() == "ENDSEC")
    # сутності (POLYLINE разом з VERTEX/SEQEND)
    ents, cur = [], None
    for i in range(s, e):
        g, v = pairs[i][0].strip(), pairs[i][1].strip()
        if g == "0":
            if cur and cur["t"] == "POLYLINE" and v in ("VERTEX", "SEQEND"):
                cur["sub"].append({"t": v, "d": {}}); cur["end"] = i
                if v == "SEQEND": cur["closed_seq"] = True
                continue
            cur = {"t": v, "d": {}, "start": i, "end": i, "sub": []}; ents.append(cur)
        else:
            tgt = cur["sub"][-1] if cur and cur["sub"] else cur
            tgt["d"].setdefault(g, []).append(v)
        cur["end"] = i
    def F(d, g): return float(d[g][0])
    segs = []  # (ent_idx, pts)
    for k, en in enumerate(ents):
        d, t = en["d"], en["t"]
        if t == "LINE":
            segs.append((k, [(F(d, "10"), F(d, "20")), (F(d, "11"), F(d, "21"))]))
        elif t == "ARC":
            cx, cy, r = F(d, "10"), F(d, "20"), F(d, "40"); a0, a1 = math.radians(F(d, "50")), math.radians(F(d, "51"))
            while a1 <= a0: a1 += 2 * math.pi
            n = max(8, int(math.degrees(a1 - a0)))
            segs.append((k, [(cx + r * math.cos(a0 + (a1 - a0) * j / n), cy + r * math.sin(a0 + (a1 - a0) * j / n)) for j in range(n + 1)]))
        elif t == "CIRCLE":
            cx, cy, r = F(d, "10"), F(d, "20"), F(d, "40")
            segs.append((k, [(cx + r * math.cos(2 * math.pi * j / 90), cy + r * math.sin(2 * math.pi * j / 90)) for j in range(91)]))
        elif t == "POLYLINE":
            pts = [(F(v["d"], "10"), F(v["d"], "20")) for v in en["sub"] if v["t"] == "VERTEX"]
            if pts and int(d.get("70", ["0"])[0]) & 1: pts.append(pts[0])
            if pts: segs.append((k, pts))
        elif t == "SPLINE":
            segs.append((k, [tuple(q) for q in spline_pts(d)]))
        elif t == "LWPOLYLINE":
            pts = list(zip(map(float, d.get("10", [])), map(float, d.get("20", []))))
            if pts and int(d.get("70", ["0"])[0]) & 1: pts.append(pts[0])
            if pts: segs.append((k, pts))
    same = lambda a, b: abs(a[0] - b[0]) < TOL and abs(a[1] - b[1]) < TOL
    left = list(range(len(segs))); chains = []
    while left:
        i = left.pop(0); idx = [segs[i][0]]; pts = list(segs[i][1])
        while not (len(pts) > 2 and same(pts[0], pts[-1])):
            for j in left:
                p = segs[j][1]
                if same(pts[-1], p[0]): pts += p[1:]
                elif same(pts[-1], p[-1]): pts += p[::-1][1:]
                else: continue
                idx.append(segs[j][0]); left.remove(j); break
            else:
                break
        ar = abs(0.5 * sum(pts[q][0] * pts[q + 1][1] - pts[q + 1][0] * pts[q][1] for q in range(len(pts) - 1)))
        chains.append({"idx": set(idx), "closed": len(pts) > 2 and same(pts[0], pts[-1]), "area": ar, "pts": pts})
    closed = [c for c in chains if c["closed"]]
    if not closed: sys.exit("немає замкненого контуру: " + src)
    outer = max(closed, key=lambda c: c["area"])
    keep = set(outer["idx"])
    psg = 0
    if keep_psg:
        poly = outer["pts"]
        def inside(x, y, poly):
            c = False
            for q in range(len(poly) - 1):
                (x1, y1), (x2, y2) = poly[q], poly[q + 1]
                if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1): c = not c
            return c
        for c in closed:
            if c is outer: continue
            ins = [inside(x, y, poly) for x, y in c["pts"]]
            if not all(ins) and (any(ins) or any(inside(x, y, c["pts"]) for x, y in poly)):
                keep |= c["idx"]; psg += 1
    drop = set()
    for k, en in enumerate(ents):
        if k not in keep and en["t"] in ("LINE", "ARC", "CIRCLE", "POLYLINE", "LWPOLYLINE", "SPLINE", "ELLIPSE", "INSERT", "TEXT", "MTEXT", "POINT", "HATCH", "DIMENSION"):
            drop.update(range(en["start"], en["end"] + 1))
    out = [p for i, p in enumerate(pairs) if i not in drop]
    open(dst, "wb").write((nl.join(l for p in out for l in p) + nl).encode("latin-1"))
    print("залишено сутностей зовнішнього контуру: %d, прибрано: %d, площа %.1f мм2" % (len(keep), len(ents) - len(keep), outer["area"]))
    if keep_psg: print("смуг ПСГ залишено: %d" % psg)

if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x != "--keep-psg"]
    main(a[0], a[1], "--keep-psg" in sys.argv)
