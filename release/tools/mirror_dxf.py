#!/usr/bin/env python3
"""Віддзеркалення DXF виробів 15 мм (правило користувача 03.11.2026: відвал 15 мм фрезерується з тильного боку,
DXF у КД намальовані з лицьового, тож перед нестингом їх дзеркалять, інакше L вийде як R).
python3 mirror_dxf.py <папка завдання> [--thickness 15]
Для кожної деталі груп указаної товщини в jobs.json: dxf/<ім'я>.DXF -> dxf/<ім'я>_MIRROR.DXF (X -> -X),
у jobs.json шлях замінюється, ставиться "mirrored": true (у деталі й групі). Повторний запуск не дзеркалить двічі.
Відсутній або порожній DXF: помилка з переліком (нічого не змінюється).
Дзеркалення текстове: LINE, ARC (кути 180-a1 / 180-a0), CIRCLE, POLYLINE/VERTEX (bulge 42 зі зміною знака),
LWPOLYLINE, POINT, SPLINE (05.11.2026). Інший тип у ENTITIES: зупинка."""
import sys, os, json, argparse, shutil, datetime

OK_T = {"LINE", "ARC", "CIRCLE", "POLYLINE", "VERTEX", "SEQEND", "LWPOLYLINE", "POINT", "SPLINE"}  # SPLINE з 05.11.2026: X контрольних/fit-точок і дотичних (10-13) зі зміною знака, вузли без змін
def fnum(v):
    s = "%.10f" % v; s = s.rstrip("0").rstrip("."); return "0" if s in ("-0", "") else s
def mirror_text(raw):
    nl = "\r\n" if "\r\n" in raw else "\n"
    L = raw.split(nl); out = list(L)
    i = 0; ent = False; cur = None; arc = {}
    while i + 1 < len(L):
        g, v = L[i].strip(), L[i + 1]
        if g == "2" and v.strip() == "ENTITIES": ent = True
        elif g == "0" and ent:
            if cur == "ARC" and arc: fix_arc(out, arc)
            vv = v.strip(); arc = {}
            if vv == "ENDSEC": ent = False; cur = None
            else:
                if vv not in OK_T: raise SystemExit("у DXF об'єкт %s: дзеркалення для нього не підтримано" % vv)
                cur = vv
        elif ent and cur:
            if g in ("10", "11", "12", "13"): out[i + 1] = fnum(-float(v))
            elif g == "42" and cur in ("VERTEX", "LWPOLYLINE"): out[i + 1] = fnum(-float(v))
            elif g in ("50", "51") and cur == "ARC": arc[g] = i + 1
        i += 2
    return nl.join(out)
def fix_arc(out, arc):
    a0, a1 = float(out[arc["50"]]), float(out[arc["51"]])
    out[arc["50"]] = fnum((180 - a1) % 360); out[arc["51"]] = fnum((180 - a0) % 360)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("task"); ap.add_argument("--thickness", type=int, default=15); ap.add_argument("--force", action="store_true"); a = ap.parse_args()
    if not a.force:
        print("ВИМКНЕНО 06.11.2026: DXF 15 мм у КД уже з тильного боку, дзеркалення дає R замість L. Нічого не змінено. (--force лише за прямим рішенням користувача)"); return 2
    jp = os.path.join(a.task, "jobs.json"); J = json.load(open(jp, encoding="utf-8"))
    todo, missing = [], []
    for g in J["groups"]:
        if int(g.get("thickness_mm", 0)) != a.thickness: continue
        for p in g["parts"]:
            if p.get("mirrored"): continue
            src = os.path.join(a.task, p["dxf"])
            if not os.path.exists(src) or os.path.getsize(src) == 0: missing.append("%s: %s" % (p["name"], p["dxf"]))
            else: todo.append((g, p, src))
    if missing:
        print("ПОМИЛКА: немає DXF для дзеркалення (%d):" % len(missing)); [print("  ", m) for m in missing]; return 1
    if not todo: print("нічого дзеркалити (немає груп %d мм або все вже віддзеркалено)" % a.thickness); return 0
    shutil.copyfile(jp, jp + ".bak_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
    for g, p, src in todo:
        raw = open(src, "rb").read().decode("latin-1")
        stem, ext = os.path.splitext(src); dst = stem + "_MIRROR" + ext
        open(dst, "wb").write(mirror_text(raw).encode("latin-1"))
        p["dxf"] = os.path.relpath(dst, a.task); p["mirrored"] = True; g["mirrored"] = True
        print("віддзеркалено:", p["name"], "->", p["dxf"])
    json.dump(J, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0
if __name__ == "__main__": sys.exit(main())
