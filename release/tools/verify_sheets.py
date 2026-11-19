#!/usr/bin/env python3
"""Незалежна перевірка листів після нестингу (читає готові DXF, а не дані Sparrow).
python3 verify_sheets.py <папка завдання> [--profile default]
Перевіряє: поле до країв листа >= margin, відстань між контурами деталей >= spacing,
кількість деталей на листах = кількість у jobs.json. Код виходу 0 = усе гаразд."""
import sys, os, json, math, glob, argparse, tempfile
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nest_tools as nt


def dseg(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def outlines(path, min_area):
    tmp = tempfile.NamedTemporaryFile("w", suffix=".dxf", delete=False, encoding="latin-1", errors="replace", newline="")
    tmp.write(open(path, "rb").read().decode("cp1251")); tmp.close()
    prims, _ = nt.load_prims(tmp.name); os.unlink(tmp.name)
    loops = [l for l in nt.chain(prims, 0.1) if l["closed"]]
    for l in loops:
        l["area"] = abs(nt.area_signed(l["pts"]))
    sheet = max(loops, key=lambda l: l["area"])
    L, W = max(p[0] for p in sheet["pts"]), max(p[1] for p in sheet["pts"])
    parts = [l for l in loops if min_area < l["area"] < L * W * 0.9 and max(p[0] for p in l["pts"]) < L - 0.5]
    # вкладені контури (заглиблення 15 мм під сталь, 03.11.2026) не є окремими деталями
    parts = [l for l in parts if not any(o is not l and o["area"] > l["area"] and nt.pip(l["pts"][0], o["pts"]) for o in parts)]
    return L, W, parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--profile", default="default")
    a = ap.parse_args()
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prof = json.load(open(os.path.join(base, "profiles.json"), encoding="utf-8"))["profiles"][a.profile]
    rep = json.load(open(os.path.join(a.task, "nesting", "report.json"), encoding="utf-8"))
    jobs = json.load(open(os.path.join(a.task, "jobs.json"), encoding="utf-8"))
    m, sp = rep["profile"]["margin_mm"], rep["profile"]["spacing_mm"]
    ok = True
    for g, gr in zip(jobs["groups"], rep["groups"]):
        want = Counter({p["name"]: int(p["qty"]) for p in g["parts"] if p.get("status", "OK") != "ЗАБЛОКОВАНО"})
        got = Counter()
        areas = [p["outline_area_mm2"] for p in g["parts"]] + [f["outline_area_mm2"] for f in (g.get("fillers") or [])]
        min_area = 0.5 * min(areas) if areas else 0
        for s in gr["sheets"]:
            got.update(s["parts"])
            L, W, parts = outlines(os.path.join(a.task, "nesting", s["file"]), min_area)
            xs = [p[0] for l in parts for p in l["pts"]]; ys = [p[1] for l in parts for p in l["pts"]]
            edge = min(min(xs), min(ys), W - max(ys), L - max(xs))
            md = float("inf")
            for i in range(len(parts)):
                for j in range(i + 1, len(parts)):
                    P, Q = parts[i]["pts"], parts[j]["pts"]
                    if min(x for x, _ in P) > max(x for x, _ in Q) + sp + 1 or min(x for x, _ in Q) > max(x for x, _ in P) + sp + 1:
                        continue
                    if min(y for _, y in P) > max(y for _, y in Q) + sp + 1 or min(y for _, y in Q) > max(y for _, y in P) + sp + 1:
                        continue
                    for p in P:
                        md = min(md, min(dseg(p, Q[k], Q[k + 1]) for k in range(len(Q) - 1)))
                    for p in Q:
                        md = min(md, min(dseg(p, P[k], P[k + 1]) for k in range(len(P) - 1)))
            good = len(parts) == s["count"] and edge >= m - 0.5 and md >= sp - 0.05
            ok &= good
            print("%s %s: деталей %d (звіт %d), до краю %.1f мм (норма %s), між деталями %s мм (норма %s)" % (
                "OK " if good else "ПОМИЛКА", s["file"], len(parts), s["count"], edge, m,
                "%.2f" % md if md < float("inf") else "-", sp))
        if got != want:
            ok = False
            print("ПОМИЛКА група %s: на листах %s, а замовлено %s" % (g["id"], dict(got), dict(want)))
        else:
            print("OK  група %s: кількість деталей збігається із заявкою" % g["id"])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
