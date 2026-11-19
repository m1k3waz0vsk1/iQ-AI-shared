#!/usr/bin/env python3
"""Зіставлення заявки з папками виробів у КД. Лише стандартна бібліотека.

python3 find_product.py --kd <корінь КД> --request заявка.txt [--aliases aliases.json] [--root-sub <підпапка>]
Шукає по всьому КД (серійна і несерійна продукція), пропускає папки Архив/Раскрой/КАЛЬКУЛЯТОРЫ.
Рядок заявки: "<назва>: <к-сть> шт (№1798, 1808)". Вихід: JSON зі статусом кожної позиції:
  OK          однозначний збіг (alias або точний збіг слів без урахування порядку)
  ПЕРЕВІРИТИ  знайдено, але є зауваження (напр. кілька DXF, обрано за назвою)
  ЗАБЛОКОВАНО не знайдено / кілька кандидатів / немає DXF; у нестинг не йде
Нічого не підставляє «схоже»: кандидати лише показуються.
"""
import sys, os, re, json, argparse, unicodedata

LOOK = str.maketrans({"а": "a", "б": "b", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p",
                      "с": "c", "т": "t", "х": "x", "і": "i", "у": "y"})
STOP = {"відвал"}
MATERIAL = {"ua", "tivar"}
SINGLE = {"р": "r", "л": "l", "рл": "rl"}  # R/L, набрані кирилицею


def tokens(s):
    s = unicodedata.normalize("NFC", s).lower().replace("№", " ")
    out = []
    for t in re.findall(r"[0-9a-zа-яіїєґ']+", s):
        if t in STOP:
            continue
        t = SINGLE.get(t, t)
        out.append(t.translate(LOOK))
    return out


def key(s):
    return " ".join(sorted(t for t in tokens(s) if t not in MATERIAL))


def tokmap(s):
    """нормалізований токен -> як написано (для зрозумілого показу різниці)"""
    s = unicodedata.normalize("NFC", s)
    orig = [t for t in re.findall(r"[0-9A-Za-zА-Яа-яІіЇїЄєҐґ']+", s.replace("№", " ")) if t.lower() not in STOP]
    return {tokens(t)[0]: t for t in orig if tokens(t)}


def nfc(p):
    return unicodedata.normalize("NFC", p)


def parse_line(line):
    line = line.strip().lstrip("*-• ").strip()
    if not line:
        return None
    m = re.match(r"^(?P<name>.+?)\s*:\s*(?P<qty>\d+)\s*шт\.?\s*(?:\((?P<orders>[^)]*)\))?", line)
    if not m:
        return {"raw": line, "error": "не вдалося розібрати рядок (очікується «назва: N шт (№...)»)"}
    name = m.group("name").strip()
    orders = re.findall(r"\d+", m.group("orders") or "")
    toks = tokens(name)
    return {"raw": line, "name": name, "qty": int(m.group("qty")), "orders": orders,
            "material_flag": "UA" if "ua" in toks else "TIVAR"}


SKIP_DIRS = ("архив", "архів", "раскрой", "розкрій", "калькулятор")


def product_dirs(base, maxdepth=6):
    res = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [x for x in dirs if not nfc(x).lower().startswith(SKIP_DIRS)]
        depth = root[len(base):].count(os.sep)
        if depth >= maxdepth:
            dirs[:] = []
        if any(f.lower().endswith(".dxf") for f in files) or any(f.lower().endswith((".gsheet", ".xls", ".xlsx")) for f in files):
            res.append((root, files))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kd", required=True)
    ap.add_argument("--request", required=True, help="файл із заявкою або '-' для stdin")
    ap.add_argument("--aliases")
    ap.add_argument("--root-sub", default="", help="підпапка КД; за замовчуванням увесь КД (серійна і несерійна)")
    a = ap.parse_args()
    text = sys.stdin.read() if a.request == "-" else open(a.request, encoding="utf-8").read()
    aliases = json.load(open(a.aliases, encoding="utf-8")).get("aliases", {}) if a.aliases and os.path.exists(a.aliases) else {}
    base = os.path.join(a.kd, a.root_sub)
    dirs = product_dirs(base)
    index = {}
    for d, files in dirs:
        index.setdefault(key(nfc(os.path.basename(d))), []).append((d, files))
    out = []
    for line in text.splitlines():
        it = parse_line(line)
        if it is None:
            continue
        if "error" in it:
            it["status"] = "ЗАБЛОКОВАНО"
            out.append(it)
            continue
        k = key(it["name"])
        it["issues"], it["candidates"] = [], []
        hits, how = [], None
        if it["name"] in aliases or k in aliases:
            rel = aliases.get(it["name"]) or aliases.get(k)
            p = os.path.join(a.kd, rel)
            if os.path.isdir(p):
                hits, how = [(p, os.listdir(p))], "alias"
            else:
                it["issues"].append("alias вказує на неіснуючу папку: " + rel)
        if not hits and k in index:
            hits, how = index[k], "точний збіг слів"
        derive_bo = False
        if not hits and "bo" in k.split():
            # Правило користувача (27.10.2026): БО немає в КД -> беремо однозначний виріб МБ.. з тими ж словами
            # і прибираємо отвори (strip_holes.py). Лише якщо кандидат рівно один.
            base = set(k.split()) - {"bo"}
            mb = [x for kk, lst in index.items() for x in lst
                  if (lambda f: base <= f and len(f - base) == 1 and next(iter(f - base)).startswith("mb"))(set(kk.split()))]
            if len(mb) == 1:
                hits, how, derive_bo = mb, "БО з МБ: DXF з отворами, отвори прибрати", True
        if len(hits) == 1:
            d, files = hits[0]
            dxf = [f for f in files if f.lower().endswith(".dxf")]
            it["folder"] = nfc(os.path.relpath(d, a.kd))
            it["match"] = how
            if not dxf:
                it["status"] = "ЗАБЛОКОВАНО"
                it["issues"].append("у папці немає DXF")
            elif len(dxf) == 1:
                it["dxf"] = nfc(os.path.join(it["folder"], dxf[0]))
                it["status"] = "OK"
            else:
                same = [f for f in dxf if key(os.path.splitext(f)[0]) == key(os.path.basename(d))]
                if not same:  # Drive обрізає довгі імена файлів: приймаємо DXF, чия назва є початком назви папки
                    fb = nfc(os.path.basename(d))
                    same = [f for f in dxf if len(os.path.splitext(f)[0]) >= 30 and fb.startswith(nfc(os.path.splitext(f)[0]).rstrip())]
                if len(same) == 1:
                    it["dxf"] = nfc(os.path.join(it["folder"], same[0]))
                    it["status"] = "ПЕРЕВІРИТИ"
                    it["issues"].append("у папці %d DXF, обрано за назвою папки: %s" % (len(dxf), same[0]))
                else:
                    it["status"] = "ЗАБЛОКОВАНО"
                    it["issues"].append("у папці кілька DXF, однозначно вибрати не можна: " + ", ".join(dxf))
            if derive_bo and it.get("dxf"):
                it["derive_bo"] = True
                it["dxf_local"] = re.sub(r"\s+(UA|TIVAR)\s*$", "", nfc(it["name"]), flags=re.I).strip() + ".DXF"
                it["status"] = "ПЕРЕВІРИТИ"
                it["issues"].append("БО немає в КД: взято %s, отвори й виточки прибрати (strip_holes.py -> dxf/%s)" % (os.path.basename(it["folder"]), it["dxf_local"]))
            if not any(f.lower().endswith(".gsheet") for f in files):
                it["issues"].append("немає Google Sheets комплектації: матеріал визначити не можна")
                it["status"] = "ЗАБЛОКОВАНО"
        else:
            it["status"] = "ЗАБЛОКОВАНО"
            q = set(k.split())
            scored = []
            for kk, lst in index.items():
                f = set(kk.split())
                j = len(q & f) / len(q | f) if q | f else 0
                for d, _ in lst:
                    qm, fm = tokmap(it["name"]), tokmap(os.path.basename(d))
                    scored.append((j, nfc(os.path.relpath(d, a.kd)), sorted(qm.get(t, t) for t in q - f), sorted(fm.get(t, t) for t in f - q)))
            scored.sort(key=lambda x: -x[0])
            if len(hits) > 1:
                it["issues"].append("кілька папок з однаковою назвою")
                it["candidates"] = [{"folder": nfc(os.path.relpath(d, a.kd))} for d, _ in hits]
            else:
                sup = [x for x in scored if not x[2]]  # усі слова заявки є в назві папки, але там є ще щось
                if sup:
                    it["issues"].append("назва неповна: підходить %d виробів, потрібне уточнення" % len(sup))
                    pick = sup[:5]
                else:
                    it["issues"].append("виріб не знайдено в КД (" + (a.root_sub or "увесь КД") + ")")
                    pick = [x for x in scored[:3] if x[0] > 0]
                it["candidates"] = [{"folder": f, "similarity": round(j, 2), "missing_in_kd": m, "extra_in_kd": x}
                                    for j, f, m, x in pick]
        out.append(it)
    print(json.dumps({"kd_root_sub": a.root_sub or "увесь КД", "folders_scanned": len(dirs), "items": out}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
