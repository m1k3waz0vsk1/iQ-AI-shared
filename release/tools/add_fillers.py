#!/usr/bin/env python3
"""Додає складські вироби-заповнювачі (fillers.json) до груп jobs.json завдання.
python3 add_fillers.py <папка завдання> --kd ~/mnt/КД [--base <папка скіла>]
Копіює DXF заповнювачів у <завдання>/dxf_fillers/ і пише group["fillers"] для груп
з тією ж товщиною і доступним матеріалом. Нестинг (nest_run.py) дозаповнює ними листи."""
import sys, os, json, shutil, argparse, unicodedata
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nest_tools as nt

N = lambda s: unicodedata.normalize("NFC", s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--kd", required=True)
    ap.add_argument("--base", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()
    cfg = json.load(open(os.path.join(a.base, "fillers.json"), encoding="utf-8"))["fillers"]
    jp = os.path.join(a.task, "jobs.json")
    jobs = json.load(open(jp, encoding="utf-8"))
    out = os.path.join(a.task, "dxf_fillers")
    os.makedirs(out, exist_ok=True)
    info, problems = {}, []
    for f in cfg:
        d = os.path.join(a.kd, f["kd_folder"])
        if f.get("dxf_local"):  # 09.11.2026: похідний DXF (напр. БО з МБ) лежить у папці скіла
            lp = os.path.join(a.base, f["dxf_local"]); d, dx = os.path.dirname(lp), [os.path.basename(lp)]
            if not os.path.isfile(lp): dx = []
        else:
            dx = [x for x in os.listdir(d) if x.lower().endswith(".dxf")] if os.path.isdir(d) else []
        if len(dx) != 1:
            problems.append("%s: у папці КД %d DXF (потрібен 1)" % (f["name"], len(dx)))
            continue
        src, dst = os.path.join(d, dx[0]), os.path.join(out, N(dx[0]))
        try:
            shutil.copyfile(src, dst)
        except OSError as e:
            problems.append("%s: не вдалося скопіювати (%s). Файл лише в хмарі Drive: спершу device_stage_files" % (f["name"], e))
            continue
        if os.path.getsize(src) != os.path.getsize(dst):
            problems.append("%s: розмір копії не збігається" % f["name"])
            continue
        prims, warn = nt.load_prims(dst)
        main, others = nt.outer_polygon(prims, 0.1)
        info[f["name"]] = {"name": f["name"], "kd_product": N(os.path.basename(os.path.join(a.kd, f["kd_folder"]))) if not f.get("dxf_local") else "Відвал " + f["name"], "dxf": "dxf_fillers/" + N(dx[0]),
                           "outline_area_mm2": round(main["area"]), "priority": f["priority"], "side": f["side"],
                           "pockets": f.get("pockets"), "kd_norm": f.get("kd_norm"), "warn": warn,
                           "thickness_mm": f["thickness_mm"], "materials": f["materials"]}
    for g in jobs["groups"]:
        mat = "UA" if g["material"].rstrip().upper().endswith("UA") else "TIVAR"
        g["fillers"] = [{k: v for k, v in i.items() if k not in ("thickness_mm", "materials")}
                        for i in sorted(info.values(), key=lambda x: x["priority"])
                        if i["thickness_mm"] == g.get("thickness_mm") and mat in i["materials"]]
        print("група %s (%s): заповнювачів %d" % (g["id"], g["material"], len(g["fillers"])))
    jobs["fillers_problems"] = problems
    json.dump(jobs, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for p in problems:
        print("ПРОБЛЕМА:", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
