#!/usr/bin/env python3
"""Повний нестинг завдання: jobs.json -> листи DXF/SVG через Sparrow. Лише стандартна бібліотека.

  python3 nest_run.py --task <папка завдання> [--profile default] [--trial-seconds 45]
  python3 nest_run.py --queue-request <файл запиту> --base <папка скіла>     (режим служби)

Алгоритм для кожної групи матеріалу: усі деталі в одну смугу шириною листа (мінус поля);
якщо не вміщується в довжину листа, бінарним пошуком шукається найбільша кількість деталей
(від більших до менших), що вміщується на лист; решта переходить на наступний лист.
Кожна розкладка перевіряється (межі, перетини). На кожному листі обчислюється залишок.
"""
import sys, os, json, math, argparse, subprocess, shutil, time, re
from collections import defaultdict, Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nest_tools as nt

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)


def rot_spec(r):
    if str(r).lower() in ("free", "continuous", "any"):
        return {"mode": "continuous"}
    return {"mode": "discrete", "angles": [float(x) for x in str(r).split(",")]}


def build_items(group, root, tol, with_fillers=True):
    g = group
    items, blocked = [], []
    for p in group["parts"]:
        if p.get("status", "OK") == "ЗАБЛОКОВАНО":
            blocked.append(p["name"])
            continue
        path = os.path.join(root, p["dxf"])
        prims, warn = nt.load_prims(path)
        main, others = nt.outer_polygon(prims, tol)
        pts = main["pts"] if nt.area_signed(main["pts"]) > 0 else main["pts"][::-1]
        ox, oy = min(x for x, _ in pts), min(y for _, y in pts)
        items.append({"id": len(items), "name": p["name"], "kd_product": p.get("kd_product", p["name"]), "dxf": p["dxf"], "qty": int(p["qty"]),
                      "orders": p.get("orders"), "offset": [ox, oy], "prims": prims, "area": main["area"],
                      "poly": [[round(x - ox, 4), round(y - oy, 4)] for x, y in pts], "warn": warn})
    for f in (g.get("fillers") or []) if with_fillers else []:
        prims, warn = nt.load_prims(os.path.join(root, f["dxf"]))
        main, others = nt.outer_polygon(prims, tol)
        pts = main["pts"] if nt.area_signed(main["pts"]) > 0 else main["pts"][::-1]
        ox, oy = min(x for x, _ in pts), min(y for _, y in pts)
        items.append({"id": len(items), "name": f["name"], "kd_product": f.get("kd_product", f["name"]), "dxf": f["dxf"],
                      "qty": 0, "stock": True, "priority": f.get("priority", 99), "offset": [ox, oy], "prims": prims,
                      "area": main["area"], "poly": [[round(x - ox, 4), round(y - oy, 4)] for x, y in pts], "warn": warn})
    return items, blocked


def run_sparrow(binary, items, ids, H, sep, rot, seconds, work, tag):
    dem = Counter(ids)
    inst = {"name": tag, "strip_height": H, "min_item_separation": sep,
            "items": [{"id": it["id"], "demand": dem[it["id"]], "orientation": {"rotation": rot},
                       "shape": {"type": "simple_polygon", "data": it["poly"]}} for it in items if dem[it["id"]]]}
    wd = os.path.join(work, tag)
    shutil.rmtree(wd, ignore_errors=True)
    os.makedirs(wd)
    inp = os.path.join(wd, "in.json")
    json.dump(inst, open(inp, "w"))
    with open(os.path.join(wd, "sparrow.log"), "w") as log:
        r = subprocess.run([binary, "-i", inp, "-t", str(int(seconds))], cwd=wd, stdout=log, stderr=subprocess.STDOUT)
    out = os.path.join(wd, "output", "final_%s.json" % tag)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError("Sparrow завершився з помилкою, див. " + os.path.join(wd, "sparrow.log"))
    return json.load(open(out))["solution"]["layout"]["placed_items"]


def decode(placed, items, H):
    base = {it["id"]: it["poly"] for it in items}
    for unit in ("rad", "deg"):
        for sign in (1, -1):
            res, polys, ok = [], [], True
            for pi in placed:
                r = pi["transformation"]["rotation"]
                deg = sign * (math.degrees(r) if unit == "rad" else r)
                tx, ty = pi["transformation"]["translation"]
                pl = nt.xform_pts(base[pi["item_id"]], math.radians(deg), tx, ty)
                if min(y for _, y in pl) < -0.05 or max(y for _, y in pl) > H + 0.05 or min(x for x, _ in pl) < -0.05:
                    ok = False
                    break
                res.append((pi["item_id"], deg, tx, ty))
                polys.append(pl)
            if ok:
                for i in range(len(polys)):
                    for j in range(i + 1, len(polys)):
                        if nt.overlap(polys[i], polys[j]):
                            ok = False
                            break
                    if not ok:
                        break
            if ok:
                return res, polys
    raise RuntimeError("розкладку Sparrow не вдалося перевірити (вихід за межі або перетини)")


def nest_group(g, root, prof, binary, work, log):
    tol = prof.get("arc_tol_mm", 0.1)
    items, blocked = build_items(g, root, tol, with_fillers=prof.get("fill_stock", True))
    sheet = sorted(g["sheet_mm"])
    m = prof["margin_mm"]
    sep = prof["spacing_mm"] + tol
    # Sparrow тримає min_item_separation від низу, верху і лівого краю смуги, але не від правого кінця
    # (довжина смуги = край крайньої деталі). Тому по висоті компенсуємо 2*sep, по довжині лише sep.
    H, Lmax = sheet[0] - 2 * m + 2 * sep, sheet[1] - 2 * m + sep
    rot = rot_spec(prof["rotation"])
    t = prof["trial_seconds"]
    remaining = sorted([it["id"] for it in items for _ in range(it["qty"])], key=lambda i: -items[i]["area"])
    sheets, n = [], 0

    def attempt(ids):
        nonlocal n
        n += 1
        tag = "g%s_%02d" % (g["id"], n)
        dec = decode(run_sparrow(binary, items, ids, H, sep, rot, t, work, tag), items, H)
        used = max(x for p in dec[1] for x, _ in p)
        log("  спроба %d: %d дет., довжина %.0f з %.0f -> %s" % (n, len(ids), used, Lmax, "вміщується" if used <= Lmax else "ні"))
        return used <= Lmax, used, dec

    while remaining:
        ok, used, dec = attempt(remaining)
        if ok:
            sheets.append((list(remaining), used, dec))
            break
        lo, hi, best = 1, len(remaining) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            ok, used, dec2 = attempt(remaining[:mid])
            if ok:
                best, lo = (mid, used, dec2), mid + 1
            else:
                hi = mid - 1
        if not best:
            raise RuntimeError("деталь %s не вміщується на лист" % items[remaining[0]]["name"])
        k, used, dec2 = best
        sheets.append((remaining[:k], used, dec2))
        remaining = remaining[k:]
    # дозаповнення складськими виробами: спершу більші за площею (за рівності priority), на всіх листах
    ftypes = sorted([it["id"] for it in items if it.get("stock")], key=lambda i: (-items[i]["area"], items[i]["priority"]))
    if ftypes:
        usable = H * Lmax
        for si in range(len(sheets)):
            cur = sheets[si]
            log("  лист %d: дозаповнення складськими виробами" % (si + 1))
            for ft in ftypes:
                while usable - sum(items[i]["area"] for i in cur[0]) >= items[ft]["area"] * 1.05:
                    ok, u, d = attempt(cur[0] + [ft])
                    if not ok:
                        break
                    cur = (cur[0] + [ft], u, d)
                    log("    + %s" % items[ft]["name"])
            sheets[si] = cur
    return items, blocked, sheets, (sheet, m, H, Lmax, sep)


def write_sheet(path_base, g, si, items, dec, used, geom, prof):
    sheet, m, H, Lmax, sep = geom
    sw, sh = sheet[1], sheet[0]
    off = m - sep  # зсув смуги Sparrow на листі
    res, polys = dec
    by_layer = defaultdict(list)
    for a, b in (((0, 0), (sw, 0)), ((sw, 0), (sw, sh)), ((sw, sh), (0, sh)), ((0, sh), (0, 0))):
        by_layer["SHEET"].append({"k": "LINE", "p": [list(a), list(b)]})
    texts, prev, counts, stock, net = [], [], Counter(), Counter(), 0.0
    placements = []
    for n, ((iid, deg, tx, ty), pl) in enumerate(zip(res, polys), 1):
        it = items[iid]
        for p in it["prims"]:
            by_layer["PARTS"].append(nt.prim_xf(p, it["offset"][0], it["offset"][1], deg, tx + off, ty + off))
        poly = [[x + off, y + off] for x, y in pl]
        if min(x for x, _ in poly) < m - 0.5 or min(y for _, y in poly) < m - 0.5 or \
           max(x for x, _ in poly) > sw - m + 0.5 or max(y for _, y in poly) > sh - m + 0.5:
            raise RuntimeError("деталь %d на листі %d порушує поле %.0f мм; результат не записано" % (n, si, m))
        cx, cy = sum(x for x, _ in poly) / len(poly), sum(y for _, y in poly) / len(poly)
        texts.append((cx, cy, 20, "%d %s%s" % (n, "СКЛАД " if it.get("stock") else "", it["name"])))
        prev.append((poly, ("%d С" % n) if it.get("stock") else str(n)))
        (stock if it.get("stock") else counts)[it["name"]] += 1
        placements.append({"n": n, "name": it["name"], "stock": bool(it.get("stock")), "cx": round(cx, 1), "cy": round(cy, 1)})
        net += it["area"]
    rx0 = off + used + prof["spacing_mm"]
    rem_len = sw - rx0
    remnant = None
    if rem_len >= prof["min_remnant_mm"]:
        remnant = {"length_mm": round(rem_len), "width_mm": sh, "x_from_mm": round(rx0)}
        for a, b in (((rx0, 0), (sw, 0)), ((sw, 0), (sw, sh)), ((sw, sh), (rx0, sh)), ((rx0, sh), (rx0, 0))):
            by_layer["REMNANT"].append({"k": "LINE", "p": [list(a), list(b)]})
        prev.append(([[rx0, 0], [sw, 0], [sw, sh], [rx0, sh], [rx0, 0]], "залишок %d x %d" % (rem_len, sh)))
    open(path_base + ".dxf", "w", encoding="cp1251", errors="replace", newline="").write(nt.dxf_text(by_layer, []))  # без написів у DXF (рішення замовника 23.10.2026); номери лишаються лише в SVG
    nt.svg_preview(path_base + ".svg", sw, sh, prev, "%s, лист %d: %s%s" % (g["id"], si, dict(counts), ("; склад " + str(dict(stock))) if stock else ""))
    return {"file": os.path.basename(path_base) + ".dxf", "parts": dict(counts), "stock": dict(stock), "count": len(res),
            "layout_end_x_mm": round(off + used), "utilization_pct": round(100 * net / (sw * sh), 1),
            "utilization_without_remnant_pct": round(100 * net / (sh * (rx0 if remnant else sw)), 1),
            "remnant": remnant, "placements": placements}


def run_task(task, prof, binary, log, keep_work=False):
    jobs = json.load(open(os.path.join(task, "jobs.json"), encoding="utf-8"))
    out = os.path.join(task, "nesting")
    os.makedirs(out, exist_ok=True)
    work = os.path.join(out, "_work")
    report = {"task": os.path.basename(task), "profile": prof, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
              "groups": [], "unresolved": [u.get("name") or u.get("raw") for u in jobs.get("unresolved", [])]}
    for g in jobs["groups"]:
        log("група %s: %s" % (g["id"], g["material"]))
        items, blocked, sheets, geom = nest_group(g, task, prof, binary, work, log)
        gr = {"id": g["id"], "material": g["material"], "sheet_mm": g["sheet_mm"], "blocked": blocked, "sheets": [],
              "kd_norm": g.get("kd_norm_parts_per_sheet")}
        for si, (ids, used, dec) in enumerate(sheets, 1):
            gr["sheets"].append(write_sheet(os.path.join(out, "лист_%s_%d" % (g["id"], si)), g, si, items, dec, used, geom, prof))
        st = Counter()
        for s_ in gr["sheets"]:
            st.update(s_["stock"])
        gr["stock_total"] = dict(st)
        if gr["kd_norm"]:
            nk = lambda it: gr["kd_norm"].get(it["kd_product"].replace("Відвал ", ""))
            ord_items = [it for it in items if not it.get("stock")]
            if all(nk(it) for it in ord_items):
                gr["kd_norm_sheets_estimate"] = round(sum(it["qty"] / nk(it) for it in ord_items), 2)
        report["groups"].append(gr)
        log("група %s: листів %d" % (g["id"], len(sheets)))
    report["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _t = Counter()
    for _g in report["groups"]:
        _t.update(_g.get("stock_total", {}))
    report["stock_total"] = dict(_t)
    json.dump(report, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    L = ["# Нестинг: %s" % report["task"], "",
         "Параметри: відступ %s мм, поле %s мм, поворот %s, фреза %s мм, мін. залишок %s мм." % (
             prof["spacing_mm"], prof["margin_mm"], prof["rotation"], prof.get("tool_d_mm"), prof["min_remnant_mm"]), ""]
    for gr in report["groups"]:
        L.append("## Група %s: %s, листів %d%s" % (gr["id"], gr["material"], len(gr["sheets"]),
                 (" (за нормами КД %.2f)" % gr["kd_norm_sheets_estimate"]) if gr.get("kd_norm_sheets_estimate") else ""))
        for s in gr["sheets"]:
            rem = ("залишок %d x %d мм" % (s["remnant"]["length_mm"], s["remnant"]["width_mm"])) if s["remnant"] else "придатного залишку немає"
            L.append("- %s: %s%s; використання %.1f%%; %s" % (s["file"], ", ".join("%s x %d" % kv for kv in s["parts"].items()),
                     ("; на склад: " + ", ".join("%s x %d" % kv for kv in s["stock"].items())) if s["stock"] else "",
                     s["utilization_pct"], rem))
        if gr["stock_total"]:
            L.append("- усього на склад у групі: " + ", ".join("%s x %d" % kv for kv in gr["stock_total"].items()))
        if gr["blocked"]:
            L.append("- не в нестингу (заблоковано): " + ", ".join(gr["blocked"]))
        L.append("")
    tot = Counter()
    for gr in report["groups"]:
        tot.update(gr["stock_total"])
    report["stock_total"] = dict(tot)
    if tot:
        L.append("## На склад (дозаповнення листів)")
        L += ["- %s: %d шт" % kv for kv in tot.items()]
        L.append("")
    if report["unresolved"]:
        L.append("Не розпізнано в заявці: " + ", ".join(report["unresolved"]))
    open(os.path.join(out, "report.md"), "w", encoding="utf-8").write("\n".join(L) + "\n")
    if not keep_work:
        shutil.rmtree(work, ignore_errors=True)
    return report


def load_profile(base, name, overrides):
    d = json.load(open(os.path.join(base, "profiles.json"), encoding="utf-8"))
    if name not in d["profiles"]:
        raise SystemExit("немає профілю " + name)
    p = dict(d["profiles"][name])
    for k, v in overrides.items():
        if v is not None:
            p[k] = v
    return p


def queue_request(req_path, base):
    req = json.load(open(req_path, encoding="utf-8"))
    zroot = os.path.realpath(os.path.join(base, "zavdannia"))
    task = os.path.realpath(os.path.join(base, str(req.get("task", ""))))
    if not task.startswith(zroot + os.sep) or not os.path.isfile(os.path.join(task, "jobs.json")):
        raise SystemExit("відхилено: task має бути папкою в zavdannia/ з jobs.json")
    ov = {}
    for k, lo, hi in (("trial_seconds", 5, 900), ("spacing_mm", 0, 50), ("margin_mm", 0, 100), ("min_remnant_mm", 0, 3000)):
        if k in req:
            v = float(req[k])
            if not lo <= v <= hi:
                raise SystemExit("відхилено: %s поза межами" % k)
            ov[k] = v
    if "fill_stock" in req:
        ov["fill_stock"] = bool(req["fill_stock"])
    if "rotation" in req:
        r = str(req["rotation"])
        if not re.fullmatch(r"free|[0-9.,]+", r):
            raise SystemExit("відхилено: rotation")
        ov["rotation"] = r
    prof = load_profile(base, str(req.get("profile", "default")), ov)
    return task, prof


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task")
    ap.add_argument("--profile", default="default")
    ap.add_argument("--trial-seconds", type=float)
    ap.add_argument("--binary", default=os.path.expanduser("~/sparrow/target/release/sparrow"))
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--queue-request")
    ap.add_argument("--keep-work", action="store_true")
    a = ap.parse_args()
    log = lambda s: print(time.strftime("%H:%M:%S"), s, flush=True)
    if a.queue_request:
        task, prof = queue_request(a.queue_request, a.base)
    else:
        if not a.task:
            raise SystemExit("--task обов'язковий")
        task = os.path.realpath(a.task)
        prof = load_profile(a.base, a.profile, {"trial_seconds": a.trial_seconds})
    log("завдання %s, профіль %s" % (task, prof))
    rep = run_task(task, prof, a.binary, log, a.keep_work)
    log("готово: " + os.path.join(task, "nesting", "report.md"))
    for gr in rep["groups"]:
        log("група %s: листів %d" % (gr["id"], len(gr["sheets"])))


if __name__ == "__main__":
    main()
