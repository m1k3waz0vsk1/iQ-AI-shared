#!/usr/bin/env python3
"""Комплектація виробів (метизи, підкладки, інше) для jobs.json і розкладки PDF.

Джерело: Google Sheets комплектації з папки виробу (kd-dxf-pack читає її get_values A1:H40).
Claude зберігає відповіді get_values у JSON і записує їх у jobs.json:

  python3 kit.py set <папка завдання> <rows.json>
  rows.json: {"<name або kd_product з jobs.json>": {"title": "<назва таблиці>", "id": "<spreadsheetId>",
                                                    "values": [[...рядки з get_values...]]}, ...}

У jobs.json у деталь/складський виріб додається "kit": {"source", "sheet_id", "sheet_product", "items": [{"name","qty","unit"}],
"excluded": [назви]}. Матеріал (лист пластику, листова сталь з од. «т.», ФОРМА, фарба) не включається.
Назви позицій зберігаються точно як у КД. Кількість порожня → qty null (на розкладці «не вказано»).
"""
import sys, os, json, re, shutil, datetime

MATERIAL = re.compile(r"^\s*(лист\b|форма\b)|фарб", re.I)


def num(s):
    s = str(s or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def filter_rows(values):
    if not values:
        raise ValueError("таблиця порожня")
    hdr = [str(c).strip().lower() for c in values[0]]
    def col(word, default):
        return next((i for i, c in enumerate(hdr) if word in c), default)
    cn, cq, cu = col("номенклат", 1), col("кільк", 2), col("од", 3)
    items, excluded, product = [], [], None
    for r in values[1:]:
        r = list(r) + [""] * 8
        name = str(r[cn]).rstrip()
        if not name.strip():
            continue
        q, u = str(r[cq]).strip(), str(r[cu]).strip()
        if not q and not u and not str(r[0]).strip():
            product = name.strip()  # підпис виробу під таблицею
            continue
        if MATERIAL.search(name) or u.lower().startswith("т"):
            excluded.append(name.strip())
            continue
        v = num(q)
        if v is not None and abs(v - round(v)) < 1e-6:
            v = int(round(v))
        items.append({"name": name, "qty": v, "unit": u.rstrip(".") or "шт"})
    return items, excluded, product


def cmd_set(task, rows_path):
    jp = os.path.join(task, "jobs.json")
    jobs = json.load(open(jp, encoding="utf-8"))
    rows = json.load(open(rows_path, encoding="utf-8"))
    done, left = [], set(rows)
    for g in jobs["groups"]:
        for p in (g.get("parts") or []) + (g.get("fillers") or []):
            key = next((k for k in (p["name"], p.get("kd_product")) if k in rows), None)
            if not key:
                continue
            r = rows[key]
            items, excl, prod = filter_rows(r["values"])
            p["kit"] = {"source": r.get("title", ""), "sheet_id": r.get("id", ""), "sheet_product": prod,
                        "items": items, "excluded": excl}
            done.append((p["name"], len(items), [i["name"] for i in items if i["qty"] in (None, 0)]))
            left.discard(key)
    bak = jp + ".bak_kit_" + datetime.datetime.now().strftime("%H%M%S")
    shutil.copy2(jp, bak)
    json.dump(jobs, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    for n, k, z in done:
        print("OK %s: позицій %d%s" % (n, k, ("; УВАГА кількість не вказана: " + ", ".join(z)) if z else ""))
    for k in sorted(left):
        print("УВАГА у jobs.json немає виробу «%s»" % k)
    missing = [p["name"] for g in jobs["groups"] for p in g.get("parts") or [] if not p.get("kit")]
    for n in missing:
        print("УВАГА без комплектації: %s" % n)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "set":
        cmd_set(sys.argv[2].rstrip("/"), sys.argv[3])
    else:
        raise SystemExit(__doc__)
