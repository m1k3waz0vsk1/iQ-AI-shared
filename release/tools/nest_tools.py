#!/usr/bin/env python3
"""Місток між jobs.json і нестером Sparrow. Лише стандартна бібліотека Python 3.

  prep   : jobs.json + DXF  ->  sparrow_<група>.json  (+ prep_<група>.meta.json)
  layout : вихід Sparrow (SVG) -> перевірка + DXF/SVG розкладки листів + звіт

Припущення (змінюються аргументами): мм, зовнішній контур деталі = найбільший замкнений контур DXF,
решта (отвори, виборки, накладені контури) у нестингу не бере участі, але потрапляє в DXF розкладки.
"""
import sys, os, re, json, math, argparse
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from spline import spline_pts, lwpoly_pts  # SPLINE, LWPOLYLINE з 05.11.2026

TOL = 1e-3


# ---------------- DXF ----------------
def load_prims(path):
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
    prims, poly, warn = [], None, []
    f = lambda e, g, i=0: float(e["d"][g][i])
    for e in ents:
        t = e["t"]
        if t == "LINE":
            prims.append({"k": "LINE", "p": [[f(e, "10"), f(e, "20")], [f(e, "11"), f(e, "21")]]})
        elif t == "ARC":
            prims.append({"k": "ARC", "c": [f(e, "10"), f(e, "20")], "r": f(e, "40"), "a0": f(e, "50"), "a1": f(e, "51")})
        elif t == "CIRCLE":
            prims.append({"k": "CIRCLE", "c": [f(e, "10"), f(e, "20")], "r": f(e, "40")})
        elif t == "POLYLINE":
            poly = {"k": "POLY", "pts": [], "closed": bool(int(e["d"].get("70", ["0"])[0]) & 1)}
        elif t == "VERTEX" and poly is not None:
            if abs(float(e["d"].get("42", ["0"])[0])) > 1e-9:
                warn.append("POLYLINE з bulge: дуги в полілінії спрощено до відрізків")
            poly["pts"].append([f(e, "10"), f(e, "20")])
        elif t == "SEQEND" and poly is not None:
            if poly["closed"] and poly["pts"]:
                poly["pts"].append(list(poly["pts"][0]))
            prims.append(poly)
            poly = None
        elif t == "LWPOLYLINE":
            _c = bool(int(float(e["d"].get("70", ["0"])[0])) & 1); _p = lwpoly_pts(e["seq"], _c)
            prims.append({"k": "POLY", "pts": _p, "closed": _c})
        elif t == "SPLINE":
            prims.append({"k": "POLY", "pts": spline_pts(e["d"]), "closed": bool(int(float(e["d"].get("70", ["0"])[0])) & 1)})
        elif t not in ("VERTEX", "SEQEND"):
            warn.append("Пропущено непідтримуваний об'єкт " + t)
    return prims, sorted(set(warn))


def tess(p, tol=0.1):
    k = p["k"]
    if k == "LINE":
        return [list(q) for q in p["p"]]
    if k == "POLY":
        return [list(q) for q in p["pts"]]
    a0, a1 = (math.radians(p["a0"]), math.radians(p["a1"])) if k == "ARC" else (0.0, 2 * math.pi)
    while a1 <= a0:
        a1 += 2 * math.pi
    r = p["r"]
    dth = 2 * math.acos(max(0.0, 1 - tol / r)) if r > tol else math.pi / 2
    n = max(4, int(math.ceil((a1 - a0) / max(dth, 1e-3))))
    cx, cy = p["c"]
    return [[cx + r * math.cos(a0 + (a1 - a0) * i / n), cy + r * math.sin(a0 + (a1 - a0) * i / n)] for i in range(n + 1)]


def same(a, b):
    return abs(a[0] - b[0]) < TOL and abs(a[1] - b[1]) < TOL


def area_signed(p):
    return 0.5 * sum(p[i][0] * p[i + 1][1] - p[i + 1][0] * p[i][1] for i in range(len(p) - 1))


def chain(prims, tol):
    segs = [(i, tess(p, tol)) for i, p in enumerate(prims) if p["k"] != "CIRCLE"]
    left, loops = list(range(len(segs))), []
    while left:
        i = left.pop(0)
        pts = [list(q) for q in segs[i][1]]
        used = [segs[i][0]]
        go = True
        while go:
            go = False
            if len(pts) > 2 and same(pts[0], pts[-1]):
                break
            for j in list(left):
                q = segs[j][1]
                if same(pts[-1], q[0]):
                    pts += q[1:]
                elif same(pts[-1], q[-1]):
                    pts += q[::-1][1:]
                else:
                    continue
                used.append(segs[j][0])
                left.remove(j)
                go = True
                break
        loops.append({"pts": pts, "prims": used, "closed": len(pts) > 2 and same(pts[0], pts[-1])})
    return loops


def outer_polygon(prims, tol):
    loops = [l for l in chain(prims, tol) if l["closed"]]
    if not loops:
        raise SystemExit("У DXF немає замкнених контурів")
    for l in loops:
        l["area"] = abs(area_signed(l["pts"]))
    main = max(loops, key=lambda l: l["area"])
    others = [l for l in loops if l is not main]
    return main, others


# ---------------- геометрія ----------------
def xform_pts(pts, th, tx, ty):
    c, s = math.cos(th), math.sin(th)
    return [[c * x - s * y + tx, s * x + c * y + ty] for x, y in pts]


def pip(pt, poly):
    x, y = pt
    ins = False
    for i in range(len(poly) - 1):
        x1, y1 = poly[i]
        x2, y2 = poly[i + 1]
        if (y1 > y) != (y2 > y) and x1 + (y - y1) * (x2 - x1) / (y2 - y1) > x:
            ins = not ins
    return ins


def seg_x(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


def overlap(p, q):
    bp = (min(x for x, _ in p), min(y for _, y in p), max(x for x, _ in p), max(y for _, y in p))
    bq = (min(x for x, _ in q), min(y for _, y in q), max(x for x, _ in q), max(y for _, y in q))
    if bp[2] <= bq[0] or bq[2] <= bp[0] or bp[3] <= bq[1] or bq[3] <= bp[1]:
        return False
    if pip(p[0], q) or pip(q[0], p):
        return True
    for i in range(len(p) - 1):
        for j in range(len(q) - 1):
            if seg_x(p[i], p[i + 1], q[j], q[j + 1]):
                return True
    return False


# ---------------- prep ----------------
def cmd_prep(a):
    jobs = json.load(open(a.jobs))
    os.makedirs(a.out, exist_ok=True)
    angles = [float(x) for x in a.rot.split(",")]
    sep = a.spacing + a.tol  # апроксимація дуг вписаним полігоном + відступ між деталями
    for u in jobs.get("unresolved", []):
        print("НЕ В НЕСТИНГУ (unresolved): %s: %s" % (u.get("name") or u.get("raw"), "; ".join(u.get("issues", []))))
    for g in jobs["groups"]:
        sheet = sorted(g["sheet_mm"])  # [коротка, довга]
        H = sheet[0] - 2 * a.margin
        Lmax = sheet[1] - 2 * a.margin
        items, meta_items = [], []
        parts = [p for p in g["parts"] if p.get("status", "OK") != "ЗАБЛОКОВАНО"]
        skipped = [p["name"] for p in g["parts"] if p not in parts]
        if skipped:
            print("група %s: пропущено заблоковані позиції: %s" % (g["id"], ", ".join(skipped)))
        if not parts:
            print("група %s: немає позицій для нестингу" % g["id"])
            continue
        for idx, p in enumerate(parts):
            path = os.path.join(a.root, p["dxf"])
            prims, warn = load_prims(path)
            main, others = outer_polygon(prims, a.tol)
            pts = main["pts"]
            if area_signed(pts) < 0:
                pts = pts[::-1]
            ox = min(x for x, _ in pts)
            oy = min(y for _, y in pts)
            poly = [[round(x - ox, 4), round(y - oy, 4)] for x, y in pts]
            items.append({
                "id": idx, "demand": int(p["qty"]),
                "orientation": {"rotation": {"mode": "discrete", "angles": angles}},
                "shape": {"type": "simple_polygon", "data": poly},
            })
            meta_items.append({"id": idx, "name": p["name"], "dxf": p["dxf"], "orders": p.get("orders"),
                               "qty": int(p["qty"]), "offset": [ox, oy], "main_prims": main["prims"],
                               "outline_area": main["area"], "warn": warn,
                               "extra_closed_loops": len(others)})
        name = "job_" + g["id"]
        json.dump({"name": name, "items": items, "strip_height": H, "min_item_separation": sep},
                  open(os.path.join(a.out, "sparrow_%s.json" % g["id"]), "w"), ensure_ascii=False)
        json.dump({"group": g["id"], "name": name, "material": g["material"], "sheet_mm": sheet,
                   "margin": a.margin, "strip_height": H, "length_limit": Lmax, "items": meta_items,
                   "kd_norm": g.get("kd_norm_parts_per_sheet")},
                  open(os.path.join(a.out, "prep_%s.meta.json" % g["id"]), "w"), ensure_ascii=False, indent=1)
        print("група %s: %d типів, смуга H=%.1f, відступ між деталями %.2f, кути %s -> sparrow_%s.json"
              % (g["id"], len(items), H, sep, angles, g["id"]))


# ---------------- layout ----------------
def parse_svg(path):
    txt = open(path, encoding="utf-8").read()
    out = []
    for tag in re.findall(r"<use\b[^>]*>", txt):
        h = re.search(r'href="#item_(\d+)"', tag)
        t = re.search(r'transform="([^"]*)"', tag)
        if not h or not t:
            continue
        tr = re.search(r"translate\(\s*([-\d.eE+]+)[\s,]+([-\d.eE+]+)\s*\)", t.group(1))
        ro = re.search(r"rotate\(\s*([-\d.eE+]+)", t.group(1))
        out.append((int(h.group(1)), float(tr.group(1)) if tr else 0.0, float(tr.group(2)) if tr else 0.0,
                    float(ro.group(1)) if ro else 0.0))
    return out


def prim_xf(p, ox, oy, th_deg, tx, ty):
    th = math.radians(th_deg)
    c, s = math.cos(th), math.sin(th)
    f = lambda x, y: (c * (x - ox) - s * (y - oy) + tx, s * (x - ox) + c * (y - oy) + ty)
    k = p["k"]
    if k == "LINE":
        return {"k": "LINE", "p": [list(f(*q)) for q in p["p"]]}
    if k == "POLY":
        return {"k": "POLY", "pts": [list(f(*q)) for q in p["pts"]]}
    cx, cy = f(*p["c"])
    if k == "CIRCLE":
        return {"k": "CIRCLE", "c": [cx, cy], "r": p["r"]}
    return {"k": "ARC", "c": [cx, cy], "r": p["r"], "a0": (p["a0"] + th_deg) % 360, "a1": (p["a1"] + th_deg) % 360}


def dxf_text(prims_by_layer, texts):
    L = ["0", "SECTION", "2", "HEADER", "9", "$ACADVER", "1", "AC1009", "9", "$DWGCODEPAGE", "3", "ANSI_1251",
         "9", "$INSUNITS", "70", "4", "0", "ENDSEC", "0", "SECTION", "2", "ENTITIES"]
    def add(*kv):
        L.extend(str(x) for x in kv)
    for layer, prims in prims_by_layer.items():
        for p in prims:
            k = p["k"]
            if k == "LINE":
                add(0, "LINE", 8, layer, 10, p["p"][0][0], 20, p["p"][0][1], 30, 0, 11, p["p"][1][0], 21, p["p"][1][1], 31, 0)
            elif k == "POLY":
                for q0, q1 in zip(p["pts"], p["pts"][1:]):
                    add(0, "LINE", 8, layer, 10, q0[0], 20, q0[1], 30, 0, 11, q1[0], 21, q1[1], 31, 0)
            elif k == "CIRCLE":
                add(0, "CIRCLE", 8, layer, 10, p["c"][0], 20, p["c"][1], 30, 0, 40, p["r"])
            else:
                add(0, "ARC", 8, layer, 10, p["c"][0], 20, p["c"][1], 30, 0, 40, p["r"], 50, p["a0"], 51, p["a1"])
    for x, y, h, s in texts:
        add(0, "TEXT", 8, "LABELS", 10, x, 20, y, 30, 0, 40, h, 1, s)
    L += ["0", "ENDSEC", "0", "EOF"]
    return "\r\n".join(L) + "\r\n"


def svg_preview(path, sheet_w, sheet_h, parts, title):
    sc = 1000.0 / sheet_w
    W, Hh = sheet_w * sc, sheet_h * sc + 30
    o = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %.1f %.1f">' % (W, Hh, W, Hh),
         '<rect x="0" y="30" width="%.1f" height="%.1f" fill="#f5f5f5" stroke="#333"/>' % (W, sheet_h * sc),
         '<text x="6" y="20" font-size="14" font-family="sans-serif">%s</text>' % title]
    for poly, label in parts:
        d = " ".join("%.1f,%.1f" % (x * sc, (sheet_h - y) * sc + 30) for x, y in poly)
        o.append('<polygon points="%s" fill="#9ec5e8" stroke="#1f4e79" stroke-width="1"/>' % d)
        cx = sum(x for x, _ in poly) / len(poly) * sc
        cy = (sheet_h - sum(y for _, y in poly) / len(poly)) * sc + 30
        o.append('<text x="%.1f" y="%.1f" font-size="12" text-anchor="middle" font-family="sans-serif">%s</text>' % (cx, cy, label))
    o.append("</svg>")
    open(path, "w", encoding="utf-8").write("\n".join(o))


def cmd_layout(a):
    meta = json.load(open(a.meta))
    items = {m["id"]: m for m in meta["items"]}
    if a.json:
        sol = json.load(open(a.json))["solution"]["layout"]["placed_items"]
        raws = []
        for unit in ("rad", "deg"):  # одиниці кута у вихідному JSON перевіряємо за коректністю розкладки
            conv = (lambda r: math.degrees(r)) if unit == "rad" else (lambda r: r)
            raws.append([(it["item_id"], it["transformation"]["translation"][0], it["transformation"]["translation"][1],
                          conv(it["transformation"]["rotation"])) for it in sol])
    else:
        raws = [parse_svg(a.svg)]
        if not raws[0]:
            raise SystemExit("У SVG не знайдено <use ... item_N>. Використайте --json.")
    sparrow_in = json.load(open(a.sparrow_input))
    base = {it["id"]: it["shape"]["data"] for it in sparrow_in["items"]}
    H, margin, Lmax = meta["strip_height"], meta["margin"], meta["length_limit"]

    chosen = None
    for placed_raw, sign in [(r, sg) for r in raws for sg in (1, -1)]:  # одиниці/знак кута: обираємо той, що дає коректну розкладку
        polys, ok = [], True
        for iid, tx, ty, rot in placed_raw:
            pl = xform_pts(base[iid], math.radians(sign * rot), tx, ty)
            if min(y for _, y in pl) < -0.05 or max(y for _, y in pl) > H + 0.05 or min(x for x, _ in pl) < -0.05:
                ok = False
                break
            polys.append(pl)
        if ok:
            for i in range(len(polys)):
                for j in range(i + 1, len(polys)):
                    if overlap(polys[i], polys[j]):
                        ok = False
                        break
                if not ok:
                    break
        if ok:
            chosen = (sign, polys, placed_raw)
            break
    if not chosen:
        raise SystemExit("Не вдалося узгодити перетворення SVG з геометрією (вихід за смугу або перетини). "
                         "Не використовувати результат; надішліть output-файли для розбору.")
    sign, polys, placed_raw = chosen

    # розбиття смуги на листи (наївне, за x)
    order = sorted(range(len(polys)), key=lambda i: min(x for x, _ in polys[i]))
    sheets, cur, x0 = [], [], None
    for i in order:
        xmin = min(x for x, _ in polys[i])
        xmax = max(x for x, _ in polys[i])
        if x0 is None:
            x0 = xmin
        if xmax - x0 > Lmax and cur:
            sheets.append((x0, cur))
            cur, x0 = [], xmin
        cur.append(i)
    sheets.append((x0, cur))

    os.makedirs(a.out, exist_ok=True)
    prims_cache = {}
    report = {"group": meta["group"], "material": meta["material"], "sheet_mm": meta["sheet_mm"],
              "angle_sign": sign, "strip_length_used": max(x for p in polys for x, _ in p) - min(x for p in polys for x, _ in p),
              "sheets": [], "kd_norm": meta.get("kd_norm")}
    for si, (sx0, idxs) in enumerate(sheets, 1):
        by_layer = defaultdict(list)
        texts, prev, net = [], [], 0.0
        sw, sh = meta["sheet_mm"][1], meta["sheet_mm"][0]
        by_layer["SHEET"].append({"k": "LINE", "p": [[0, 0], [sw, 0]]})
        by_layer["SHEET"].append({"k": "LINE", "p": [[sw, 0], [sw, sh]]})
        by_layer["SHEET"].append({"k": "LINE", "p": [[sw, sh], [0, sh]]})
        by_layer["SHEET"].append({"k": "LINE", "p": [[0, sh], [0, 0]]})
        counts = defaultdict(int)
        for n, i in enumerate(idxs, 1):
            iid, tx, ty, rot = placed_raw[i]
            m = items[iid]
            if m["dxf"] not in prims_cache:
                prims_cache[m["dxf"]] = load_prims(os.path.join(a.root, m["dxf"]))[0]
            th = sign * rot
            # tx,ty задані для полігону, зсунутого в (0,0): переносимо ще й на поле листа
            for p in prims_cache[m["dxf"]]:
                by_layer["PARTS"].append(prim_xf(p, m["offset"][0], m["offset"][1], th, tx - sx0 + margin, ty + margin))
            poly = [[x - sx0 + margin, y + margin] for x, y in polys[i]]
            prev.append((poly, str(n)))
            cx = sum(x for x, _ in poly) / len(poly)
            cy = sum(y for _, y in poly) / len(poly)
            texts.append((cx, cy, 20, "%d %s" % (n, m["name"])))
            net += m["outline_area"]
            counts[m["name"]] += 1
        base = "лист_%s_%d" % (meta["group"], si)
        open(os.path.join(a.out, base + ".dxf"), "w", encoding="cp1251", errors="replace", newline="").write(dxf_text(by_layer, texts))
        svg_preview(os.path.join(a.out, base + ".svg"), sw, sh, prev, "%s, лист %d: %s" % (meta["group"], si, dict(counts)))
        report["sheets"].append({"file": base + ".dxf", "parts": dict(counts), "count": len(idxs),
                                 "utilization_outline_pct": round(100 * net / (sw * sh), 1)})
    json.dump(report, open(os.path.join(a.out, "report_%s.json" % meta["group"]), "w"), ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if len(sheets) > 1:
        print("УВАГА: смуга довша за лист, розбиття наївне. Краще змінити параметри/склад і запустити знову.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prep")
    p.add_argument("--jobs", required=True)
    p.add_argument("--root", required=True, help="папка завдання (відносно неї dxf у jobs.json)")
    p.add_argument("--out", default=".")
    p.add_argument("--spacing", type=float, default=8.0, help="мм між деталями (ПРИПУЩЕННЯ, підтвердити)")
    p.add_argument("--margin", type=float, default=10.0, help="мм від краю листа (ПРИПУЩЕННЯ, підтвердити)")
    p.add_argument("--rot", default="0,180", help="дозволені кути, градуси (ПРИПУЩЕННЯ)")
    p.add_argument("--tol", type=float, default=0.1, help="похибка апроксимації дуг, мм")
    q = sub.add_parser("layout")
    q.add_argument("--meta", required=True)
    q.add_argument("--sparrow-input", dest="sparrow_input", required=True)
    q.add_argument("--svg")
    q.add_argument("--json")
    q.add_argument("--root", required=True)
    q.add_argument("--out", required=True)
    a = ap.parse_args()
    {"prep": cmd_prep, "layout": cmd_layout}[a.cmd](a)
