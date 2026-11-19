#!/usr/bin/env python3
"""Збирає jobs.json із match.json + norms.json (крок 4-5 скіла kd-dxf-pack).
python3 make_jobs.py <папка завдання> [--out jobs.json]
norms.json у папці завдання: {"<name з match.json>": {"norm": 22, "thickness": 12, "issues": ["..."], "status": "OK|ПЕРЕВІРИТИ",
  "pockets": [], "note_open": "питання"}}  (норма 'шт з листа' з таблиці комплектації; thickness за замовчуванням 12)
DXF мають лежати в <завдання>/dxf/ під своїми іменами (для derive_bo: під dxf_local, після strip_holes.py). Позиції ЗАБЛОКОВАНО йдуть в unresolved.
Матеріал: material_flag UA -> 1000x3000 UA, інакше TIVAR 1220x3050. Після цього запустити add_fillers.py."""
import sys, os, json, math, argparse, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nest_tools as nt

def nfc(s): return unicodedata.normalize("NFC", s)
def min_rect(pts):
    best = None
    for k in range(1800):
        th = math.radians(k / 10); c, s = math.cos(th), math.sin(th)
        xs = [x * c + y * s for x, y in pts]; ys = [-x * s + y * c for x, y in pts]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if best is None or w * h < best[0]: best = (w * h, w, h)
    return sorted([round(best[1], 1), round(best[2], 1)], reverse=True)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("task"); ap.add_argument("--out", default="jobs.json"); a = ap.parse_args()
    T = a.task
    m = json.load(open(os.path.join(T, "match.json"), encoding="utf-8"))
    norms = {nfc(k): v for k, v in json.load(open(os.path.join(T, "norms.json"), encoding="utf-8")).items()}
    date = os.path.basename(os.path.abspath(T))[:10]
    groups = {}; unresolved = []; openq = []; gi = 0
    # Один виріб = одна деталь; кожна штука має рівно одне замовлення (рішення користувача 29.10.2026).
    # Рядки заявки з тим самим виробом зливаються, штуки по замовленнях ідуть в orders_split.
    merged, byname = [], {}
    for it in m["items"]:
        if it.get("status") == "ЗАБЛОКОВАНО" or not it.get("dxf"):
            merged.append(it); continue
        nm = nfc(it["name"]); orders = it.get("orders") or []
        split = it.get("orders_split")
        if split:
            split = {str(k): int(v) for k, v in split.items()}
            if sum(split.values()) != it["qty"]:
                sys.exit("orders_split не дорівнює кількості: " + it["raw"])
        elif len(orders) > 1:
            sys.exit("у рядку кілька замовлень без кількості по кожному, розбийте рядок заявки: " + it["raw"])
        elif orders:
            split = {orders[0]: it["qty"]}
        k = (nm, it.get("material_flag"))
        if k not in byname:
            c = dict(it); c["orders_split"] = dict(split) if split else None; c["orders"] = list(split) if split else []
            c["raw"] = it["raw"]; byname[k] = c; merged.append(c); continue
        c = byname[k]
        if (c.get("dxf_local") or c.get("dxf")) != (it.get("dxf_local") or it.get("dxf")):
            sys.exit("той самий виріб з різними DXF: " + it["raw"])
        if not split or c["orders_split"] is None:
            sys.exit("виріб у кількох рядках, але не всі мають номер замовлення: " + it["raw"])
        c["qty"] += it["qty"]
        for o, q in split.items():
            c["orders_split"][o] = c["orders_split"].get(o, 0) + q
        c["orders"] = list(c["orders_split"]); c["raw"] += " | " + it["raw"]
        c["issues"] = list(dict.fromkeys((c.get("issues") or []) + (it.get("issues") or [])))
    for it in merged:
        nm = nfc(it["name"])
        if it.get("status") == "ЗАБЛОКОВАНО" or not it.get("dxf"):
            unresolved.append({"raw": it["raw"], "status": "ЗАБЛОКОВАНО", "issues": it.get("issues", []), "candidates": it.get("candidates", [])}); continue
        n = norms.get(nm)
        if not n: sys.exit("немає норми в norms.json для: " + nm)
        ua = it.get("material_flag") == "UA"; th = n.get("thickness", 12)
        sheet = [1000, 3000] if ua else [1220, 3050]
        mat = "Лист пластиковий %d %d %d %s" % (th, sheet[0], sheet[1], "UA" if ua else "TIVAR")
        f = it.get("dxf_local") or os.path.basename(it["dxf"]); path = os.path.join(T, "dxf", f)
        if not os.path.exists(path) or os.path.getsize(path) == 0: sys.exit("немає DXF (або 0 байт): " + path)
        prims, _w = nt.load_prims(path); main_, others = nt.outer_polygon(prims, 0.05)
        iss = list(it.get("issues", [])) + n.get("issues", [])
        part = {"name": nm.replace("Відвал передплужника ", "").replace("Відвал ", "").strip(), "kd_product": os.path.basename(it["folder"]),
                "qty": it["qty"], "orders": it["orders"], "orders_split": it.get("orders_split"), "dxf": "dxf/" + f,
                "outline_area_mm2": round(main_["area"]), "min_rect_mm": min_rect(main_["pts"]), "features": {"inner_contours": len(others)},
                "pockets": n.get("pockets", []), "status": n.get("status", "OK"), "issues": iss}
        if n.get("note_open"): openq.append(n["note_open"])
        g = groups.get(mat)
        if not g:
            gi += 1; g = groups[mat] = {"id": chr(64 + gi), "material": mat, "thickness_mm": th, "sheet_mm": sheet, "kd_norm_parts_per_sheet": {}, "parts": [], "fillers": []}
        g["parts"].append(part); g["kd_norm_parts_per_sheet"][part["name"]] = n["norm"]
    jobs = {"date": date, "units": "mm", "groups": list(groups.values()), "unresolved": unresolved, "fillers_problems": [], "open_questions": openq}
    json.dump(jobs, open(os.path.join(T, a.out), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for g in jobs["groups"]:
        print("група", g["id"], g["material"], "деталей:", sum(p["qty"] for p in g["parts"]))
        for p in g["parts"]: print("  ", p["name"], p["qty"], p["outline_area_mm2"], p["min_rect_mm"], p["status"])
    for u in unresolved: print("ЗАБЛОКОВАНО:", u["raw"], u["issues"])
main()
