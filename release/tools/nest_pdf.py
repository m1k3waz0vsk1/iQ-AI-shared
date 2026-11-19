#!/usr/bin/env python3
"""PDF для друку після нестингу: розкладка кожного листа на A4 (назва і номер замовлення на кожній деталі)
+ наклейки, одна сторінка = одна наклейка = один виріб з листа.

python3 nest_pdf.py <папка завдання> [--labels-only-copy] [--no-labels]
Вихід: <папка>/nesting/розкладка_<завдання>.pdf (A4, сторінка на лист, чорно-біла, без заливки; далі сторінки комплектації
на кожне замовлення з "kit" у jobs.json, див. kit.py) і наклейки_<завдання>.docx (Word за шаблоном 70x35, сторінка на виріб; tools/labels_docx.py).
Налаштування наклейки: ../labels.json (розмір, QR, логотип). Потрібен reportlab.

Як визначаються деталі: якщо в report.json у листа є "placements" (пише nest_run.py з 27.10.2026),
беруться звідти. Інакше: номери позицій з SVG листа, назва за площею зовнішнього контуру DXF серед виробів,
що є на цьому листі за report.json; кількості звіряються зі звітом, при розбіжності PDF не пишеться.
"""
import sys, os, re, json, math, argparse, datetime
from collections import Counter, defaultdict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nest_tools as nt
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_DIRS = [os.path.join(BASE, "assets"), "/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "/System/Library/Fonts/Supplemental",
             os.path.join(BASE, "assets")]


def reg_fonts():
    want = {"FN": ["Impact.ttf", "DejaVuSansCondensed-Bold.ttf"], "F": ["DejaVuSans.ttf", "Arial.ttf"], "FB": ["DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
            "FC": ["DejaVuSansCondensed-Bold.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"]}
    for k, names in want.items():
        for n in names:
            p = next((os.path.join(d, n) for d in FONT_DIRS if os.path.exists(os.path.join(d, n))), None)
            if p:
                pdfmetrics.registerFont(TTFont(k, p)); break
        else:
            raise SystemExit("Немає шрифту з кирилицею для %s (%s)" % (k, names))


def sw(t, f, s):  # ширина тексту в мм
    return pdfmetrics.stringWidth(t, f, s) / mm


# ---------- геометрія листа ----------
def load_sheet(path):
    import tempfile
    fh = tempfile.NamedTemporaryFile("w", suffix=".dxf", delete=False, encoding="latin-1", errors="replace", newline="")
    fh.write(open(path, "rb").read().decode("cp1251")); fh.close(); tmp = fh.name
    try:
        prims, _ = nt.load_prims(tmp)
    finally:
        os.unlink(tmp)
    loops = [l for l in nt.chain(prims, 0.1) if l["closed"]]
    for l in loops:
        l["area"] = abs(nt.area_signed(l["pts"]))
    return prims, loops


def area_centroid(p):
    a = cx = cy = 0.0
    for i in range(len(p) - 1):
        x0, y0 = p[i]; x1, y1 = p[i + 1]
        c = x0 * y1 - x1 * y0
        a += c; cx += (x0 + x1) * c; cy += (y0 + y1) * c
    return (cx / (3 * a), cy / (3 * a)) if abs(a) > 1e-9 else (p[0][0], p[0][1])


def dseg(p, a, b):
    ax, ay = a; bx, by = b; px, py = p
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def principal_angle(p):
    xs = [x for x, _ in p]; ys = [y for _, y in p]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    a = 0.5 * math.atan2(2 * sxy, sxx - syy)
    if a > math.pi / 2: a -= math.pi
    if a < -math.pi / 2: a += math.pi
    return a


def place_text(poly, lines_fn, scale, sizes):
    """Шукає точку й кут, де блок тексту вміщується всередині контуру. lines_fn(size)->(w_mm,h_mm на папері)."""
    th = principal_angle(poly)
    c, s = math.cos(-th), math.sin(-th)
    loc = [(c * x - s * y, s * x + c * y) for x, y in poly]
    xs = [x for x, _ in loc]; ys = [y for _, y in loc]
    step = max(4.0, min(max(xs) - min(xs), max(ys) - min(ys)) / 25)
    cand = []
    y = min(ys) + step / 2
    while y < max(ys):
        x = min(xs) + step / 2
        while x < max(xs):
            if nt.pip((x, y), loc + [loc[0]] if loc[0] != loc[-1] else loc):
                d = min(dseg((x, y), loc[k], loc[k + 1]) for k in range(len(loc) - 1))
                cand.append((d, x, y))
            x += step
        y += step
    cand.sort(reverse=True)
    cand = cand[:40]
    closed = loc if loc[0] == loc[-1] else loc + [loc[0]]

    def fits(x, y, w, h):
        pts = []
        n = max(2, int(w / step) + 1)
        for i in range(n + 1):
            pts += [(x - w / 2 + w * i / n, y - h / 2), (x - w / 2 + w * i / n, y + h / 2)]
        pts += [(x - w / 2, y), (x + w / 2, y)]
        if not all(nt.pip(q, closed) for q in pts):
            return False
        return not any(x - w / 2 < vx < x + w / 2 and y - h / 2 < vy < y + h / 2 for vx, vy in loc)

    for size in sizes:
        w, h = lines_fn(size)
        w, h = (w + 1.0) / scale, (h + 0.6) / scale
        for _, x, yy in cand:
            if fits(x, yy, w, h):
                ci, si = math.cos(th), math.sin(th)
                return size, (ci * x - si * yy, si * x + ci * yy), math.degrees(th)
    d, x, yy = cand[0] if cand else (0, *area_centroid(poly))
    ci, si = math.cos(th), math.sin(th)
    return None, (ci * x - si * yy, si * x + ci * yy), math.degrees(th)


def parse_svg_numbers(path, sheet_w, sheet_h):
    """[(n, stock, (x,y) у мм листа)] з SVG-прев'ю nest_tools.svg_preview."""
    if not os.path.exists(path):
        return []
    t = open(path, encoding="utf-8").read()
    sc = 1000.0 / sheet_w
    out = []
    for pts, lab in re.findall(r'<polygon points="([^"]+)"[^>]*/>\s*<text[^>]*>([^<]+)</text>', t):
        m = re.match(r"^(\d+)( С)?$", lab.strip())
        if not m:
            continue
        P = [tuple(map(float, q.split(","))) for q in pts.split()]
        P = [(x / sc, sheet_h - (y - 30) / sc) for x, y in P]
        out.append((int(m.group(1)), bool(m.group(2)), area_centroid(P + [P[0]])))
    return out


# ---------- зіставлення ----------
def identify(task, jobs_g, rep_sheet, sheet_mm, used=None):
    nest = os.path.join(task, "nesting")
    prims, loops = load_sheet(os.path.join(nest, rep_sheet["file"]))
    shL, shW = sheet_mm[1], sheet_mm[0]
    catalog = {}
    def kd_label(p):
        # БО, виведений з DXF МБ (derive_bo): kd_product = папка МБ, а на наклейці/розкладці має бути виріб із заявки (13.11.2026)
        kd = p.get("kd_product", p["name"])
        if "БО" in p["name"].split() and "БО" not in kd.split():
            return p["name"] if p["name"].startswith("Відвал") else "Відвал " + p["name"]
        return kd
    for p in jobs_g["parts"]:
        catalog[p["name"]] = {"name": p["name"], "kd": kd_label(p), "area": p["outline_area_mm2"],
                              "orders": p.get("orders") or [], "split": p.get("orders_split"), "stock": bool(p.get("stock"))}
    for f in jobs_g.get("fillers") or []:
        catalog.setdefault(f["name"], {"name": f["name"], "kd": f.get("kd_product", f["name"]),
                                       "area": f["outline_area_mm2"], "orders": [], "split": None, "stock": True})
    want = Counter(rep_sheet.get("parts", {})) + Counter(rep_sheet.get("stock", {}))
    on_sheet = [catalog[n] for n in want]
    min_area = 0.5 * min(c["area"] for c in on_sheet)
    outer = [l for l in loops if min_area < l["area"] < shL * shW * 0.9]
    outer = [l for l in outer if not any(o is not l and o["area"] > l["area"] and nt.pip(l["pts"][0], o["pts"]) for o in outer)]
    issues = []
    items = []
    if rep_sheet.get("placements"):
        for pl in rep_sheet["placements"]:
            lp = next((l for l in outer if nt.pip((pl["cx"], pl["cy"]), l["pts"])), None)
            if lp is None:  # центр вершин увігнутої деталі може бути поза контуром: найближчий центр
                lp = min(outer, key=lambda l: math.dist(area_centroid(l["pts"]), (pl["cx"], pl["cy"])), default=None)
            if lp is None or abs(lp["area"] - catalog[pl["name"]]["area"]) / catalog[pl["name"]]["area"] > 0.005:
                raise SystemExit("%s: поз. %s не знайдена в DXF" % (rep_sheet["file"], pl["n"]))
            items.append({"n": pl["n"], "c": catalog[pl["name"]], "loop": lp})
    else:
        nums = parse_svg_numbers(os.path.join(nest, rep_sheet["file"][:-4] + ".svg"), shL, shW)
        for l in outer:
            best = sorted(on_sheet, key=lambda c: abs(c["area"] - l["area"]) / c["area"])
            err = abs(best[0]["area"] - l["area"]) / best[0]["area"]
            if err > 0.005:
                raise SystemExit("%s: контур площею %.0f мм2 не відповідає жодному виробу листа" % (rep_sheet["file"], l["area"]))
            if len(best) > 1 and abs(best[1]["area"] - l["area"]) / best[1]["area"] < 2 * err + 0.001:
                issues.append("площі «%s» і «%s» майже однакові, розпізнавання ненадійне" % (best[0]["name"], best[1]["name"]))
            n = next((k for k, st, pt in nums if nt.pip(pt, l["pts"])), None)
            items.append({"n": n, "c": best[0], "loop": l})
        got = Counter(it["c"]["name"] for it in items)
        if got != want:
            raise SystemExit("%s: розпізнано %s, а за звітом %s. PDF не створено." % (rep_sheet["file"], dict(got), dict(want)))
        if any(it["n"] is None for it in items) or len({it["n"] for it in items}) != len(items):
            ordered = sorted(items, key=lambda it: area_centroid(it["loop"]["pts"])[0])
            for k, it in enumerate(ordered, 1):
                it["n"] = k
            issues.append("номери з SVG не зчитано, позиції пронумеровано зліва направо")
    items.sort(key=lambda it: it["n"])
    # номери замовлень на кожен екземпляр; лічильник спільний для всіх листів завдання (виправлено 30.10.2026:
    # раніше на кожному листі розподіл починався з першого замовлення і номери дублювались)
    used = Counter() if used is None else used
    for it in items:
        c = it["c"]
        if c["stock"]:
            it["order"] = "СКЛАД"
        elif c["split"]:
            seq = [o for o, q in c["split"].items() for _ in range(int(q))]
            uk = (jobs_g.get("id"), c["name"])  # лічильник на групу (11.11.2026): виріб у кількох групах має orders_split на кожну
            k = used[uk]; used[uk] += 1
            if k >= len(seq):
                raise SystemExit("«%s»: на листах більше штук, ніж в orders_split. Файли не створено." % c["name"])
            it["order"] = "№" + seq[k]
        elif len(c["orders"]) == 1:
            it["order"] = "№" + c["orders"][0]
        elif not c["orders"]:
            it["order"] = "без №"
            issues.append("«%s»: немає номера замовлення" % c["name"])
        else:  # один виріб = один номер (29.10.2026): без розбивки номер визначити не можна
            raise SystemExit("«%s»: кілька замовлень (%s) без orders_split, номер на наклейці визначити не можна. "
                             "Перезберіть jobs.json (make_jobs.py). Файли не створено." % (c["name"], ", ".join(c["orders"])))
    return prims, items, sorted(set(issues))


# ---------- сторінка розкладки ----------
def layout_page(cv, task, gr, si, rep_sheet, prims, items, date_s):
    W, H = 297 * mm, 210 * mm
    cv.setPageSize((W, H))
    shL, shW = gr["sheet_mm"][1], gr["sheet_mm"][0]
    mar = 10
    scale = min((297 - 2 * mar) / shL, (210 - 2 * mar - 14 - 45) / shW)
    ox, oy = mar, 210 - mar - 14 - shW * scale
    X = lambda x: (ox + x * scale) * mm
    Y = lambda y: (oy + y * scale) * mm
    cv.setFont("FB", 12)
    cv.drawString(mar * mm, (210 - mar - 5) * mm, "Нестинг %s · лист %s%d" % (task, gr["id"], si))
    cv.setFont("F", 8)
    cv.drawString(mar * mm, (210 - mar - 10) * mm, "%s, %d×%d мм · деталей %d · використання %s%% · %s" % (
        gr["material"], shL, shW, len(items), rep_sheet.get("utilization_pct", "?"), date_s))
    cv.drawRightString((297 - mar) * mm, (210 - mar - 10) * mm, "масштаб 1:%d" % round(1 / scale))
    # чорно-біла розкладка без заливки (рішення користувача 27.10.2026)
    cv.setFillColorRGB(0, 0, 0); cv.setStrokeColorRGB(0, 0, 0)
    cv.setLineWidth(0.6)
    cv.rect(X(0), Y(0), shL * scale * mm, shW * scale * mm, fill=0, stroke=1)
    if rep_sheet.get("remnant"):
        r = rep_sheet["remnant"]
        cv.setDash(3, 2)
        cv.rect(X(r["x_from_mm"]), Y(0), (shL - r["x_from_mm"]) * scale * mm, shW * scale * mm, fill=0, stroke=1)
        cv.setDash()
    cv.setLineWidth(0.25)
    for pr in prims:
        if pr["k"] == "CIRCLE":
            cv.circle(X(pr["c"][0]), Y(pr["c"][1]), pr["r"] * scale * mm, stroke=1, fill=0)
            continue
        q = nt.tess(pr, 0.5)
        p = cv.beginPath(); p.moveTo(X(q[0][0]), Y(q[0][1]))
        for x, y in q[1:]:
            p.lineTo(X(x), Y(y))
        cv.drawPath(p, fill=0, stroke=1)
    # написи
    for it in items:
        short, order = it["c"]["name"], it["order"]
        l1 = "%d · %s" % (it["n"], short)

        def dims(s):
            return max(sw(l1, "FC", s), sw(order, "FB", s * 1.15)), (s + s * 1.15) * 0.3528 * 1.15
        size, (cx, cy), ang = place_text(it["loop"]["pts"], dims, scale, [8, 7.5, 7, 6.5, 6, 5.5, 5, 4.5])
        it["label_pt"] = size
        if ang > 90: ang -= 180
        if ang < -90: ang += 180
        cv.saveState(); cv.translate(X(cx), Y(cy)); cv.rotate(ang)
        cv.setFillColorRGB(0, 0, 0)
        if size:
            lh = size * 0.3528 * mm * 1.1
            cv.setFont("FC", size); cv.drawCentredString(0, lh * 0.15, l1)
            cv.setFont("FB", size * 1.15); cv.drawCentredString(0, -lh * 1.0, order)
        else:
            cv.setFont("FB", 7); cv.drawCentredString(0, -1 * mm, str(it["n"]))
        cv.restoreState()
    # таблиця
    cv.setFillColorRGB(0, 0, 0); cv.setStrokeColorRGB(0, 0, 0)
    rows = defaultdict(list)
    for it in items:
        rows[(it["c"]["stock"], it["c"]["kd"], it["order"])].append(it["n"])
    ty = oy - 7
    cv.setFont("FB", 8)
    cols = [mar, mar + 120, mar + 158, mar + 172]
    for x, t in zip(cols, ["Виріб", "Замовлення", "К-сть", "Позиції на листі"]):
        cv.drawString(x * mm, ty * mm, t)
    cv.setLineWidth(0.3); cv.line(mar * mm, (ty - 1.2) * mm, (297 - mar) * mm, (ty - 1.2) * mm)
    cv.setFont("F", 8)
    for (st, kd, order), ns in sorted(rows.items(), key=lambda kv: (kv[0][0], min(kv[1]))):
        ty -= 4.2
        if ty < 8:
            cv.drawString(mar * mm, ty * mm, "… таблицю скорочено"); break
        for x, t in zip(cols, [kd, order, str(len(ns)), ", ".join(map(str, sorted(ns)))]):
            cv.drawString(x * mm, ty * mm, t)
    cv.showPage()


# ---------- комплектація ----------
def fmt_q(v):
    if v is None:
        return "не вказано"
    return str(int(v)) if abs(v - round(v)) < 1e-6 else ("%.3f" % v).rstrip("0").rstrip(".").replace(".", ",")


def kit_orders(jobs, rep):
    """[(номер(и) замовлення, виріб jobs, к-сть)] для виробів замовлення, що є на листах; складські не комплектуються."""
    placed = Counter()
    for gr in rep["groups"]:
        for s in gr["sheets"]:
            for k, v in (s.get("parts") or {}).items():
                placed[(gr.get("id"), k)] += v
    out, issues = [], []
    for g in jobs["groups"]:
        for p in g.get("parts") or []:
            n = placed.get((g.get("id"), p["name"]), 0)
            if not n or p.get("stock"):  # складський виріб з фіксованою к-стю (11.11.2026): без комплектації
                continue
            if n != p.get("qty"):
                issues.append("«%s»: на листах %d шт, у заявці %s" % (p["name"], n, p.get("qty")))
            if p.get("orders_split"):
                for o, q in p["orders_split"].items():
                    out.append(("№" + o, p, int(q)))
            else:
                out.append(("№" + ", №".join(p["orders"]) if p.get("orders") else "без №", p, n))
            if not p.get("kit"):
                issues.append("«%s»: комплектації немає в jobs.json (kit.py set)" % p["name"])
            else:
                z = [i["name"] for i in p["kit"]["items"] if i["qty"] in (None, 0)]
                if z:
                    issues.append("«%s»: у КД не вказано кількість: %s" % (p["name"], ", ".join(z)))
    # той самий виріб у кількох групах (напр. смуга і повний лист): один рядок, кількості разом (09.11.2026)
    merged = {}
    for o, p, q in out:
        k = (o, p["name"])
        if k in merged:
            merged[k] = (o, merged[k][1], merged[k][2] + q)
        else:
            merged[k] = (o, p, q)
    out = sorted(merged.values(), key=lambda t: t[0])
    return out, issues


def kit_pages(cv, task, rows, date_s):
    """A4 книжкова: блок на замовлення (позиція, на 1 шт, разом) + підсумок по завданню."""
    W, H = 210, 297
    mar, lh = 12, 4.6
    cols = [mar, mar + 120, mar + 145, mar + 170]
    st = {"y": 0, "page": 0}

    def new_page():
        if st["page"]:
            cv.showPage()
        st["page"] += 1
        cv.setPageSize((W * mm, H * mm)); cv.setFillColorRGB(0, 0, 0); cv.setStrokeColorRGB(0, 0, 0)
        cv.setFont("FB", 12)
        cv.drawString(mar * mm, (H - mar - 5) * mm, "Комплектація · нестинг %s" % task)
        cv.setFont("F", 8)
        cv.drawString(mar * mm, (H - mar - 10) * mm, "метизи, підкладки та інше з КД на кожне замовлення · %s" % date_s)
        cv.drawRightString((W - mar) * mm, (H - mar - 10) * mm, "стор. %d" % st["page"])
        st["y"] = H - mar - 20

    def need(h):
        if st["y"] - h < 12:
            new_page()

    def head_row():
        cv.setFont("FB", 8)
        for x, t in zip(cols, ["Позиція", "На 1 шт", "Разом", "Од."]):
            cv.drawString(x * mm, st["y"] * mm, t)
        cv.setLineWidth(0.3); cv.line(mar * mm, (st["y"] - 1.3) * mm, (W - mar) * mm, (st["y"] - 1.3) * mm)
        st["y"] -= lh
        cv.setFont("F", 9)

    new_page()
    total = {}
    for order, p, n in rows:
        kit = p.get("kit") or {}
        items = kit.get("items") or []
        need(lh * (len(items) + 4) + 4)
        cv.setFont("FB", 10)
        cv.drawString(mar * mm, st["y"] * mm, "%s · %s · %d шт" % (order, p.get("kd_product", p["name"]), n))
        st["y"] -= lh
        src = kit.get("source")
        if src and src != p.get("kd_product"):
            cv.setFont("F", 7.5); cv.drawString(mar * mm, st["y"] * mm, "за таблицею КД: %s" % src); st["y"] -= lh * 0.9
        if not items:
            cv.setFont("FB", 9); cv.drawString(mar * mm, st["y"] * mm, "КОМПЛЕКТАЦІЇ НЕМАЄ В ЗАВДАННІ: перевірити КД")
            st["y"] -= lh * 2; continue
        head_row()
        for i in items:
            q = i["qty"]
            tot = None if q is None else q * n
            for x, t in zip(cols, [i["name"], fmt_q(q), fmt_q(tot), i["unit"]]):
                cv.drawString(x * mm, st["y"] * mm, t)
            k = (" ".join(i["name"].split()), i["unit"])
            total[k] = None if (tot is None or total.get(k, 0) is None) else total.get(k, 0) + tot
            st["y"] -= lh
        cv.setLineWidth(0.15); cv.line(mar * mm, (st["y"] + lh - 1.3) * mm, (W - mar) * mm, (st["y"] + lh - 1.3) * mm)
        st["y"] -= lh * 0.8
    if total:
        need(lh * (len(total) + 3))
        st["y"] -= lh * 0.5
        cv.setFont("FB", 10); cv.drawString(mar * mm, st["y"] * mm, "Разом по завданню"); st["y"] -= lh
        cv.setFont("FB", 8)
        for x, t in zip([cols[0], cols[2], cols[3]], ["Позиція", "Разом", "Од."]):
            cv.drawString(x * mm, st["y"] * mm, t)
        cv.setLineWidth(0.3); cv.line(mar * mm, (st["y"] - 1.3) * mm, (W - mar) * mm, (st["y"] - 1.3) * mm)
        st["y"] -= lh; cv.setFont("F", 9)
        for (nm, u), v in sorted(total.items()):
            need(lh)
            for x, t in zip([cols[0], cols[2], cols[3]], [nm, fmt_q(v), u]):
                cv.drawString(x * mm, st["y"] * mm, t)
            st["y"] -= lh
    cv.showPage()
    return st["page"]


# ---------- наклейка ----------
def fit_lines(text, font, max_w, sizes, max_lines=2):
    words = text.split()
    for s in sizes:
        lines, cur = [], ""
        for w_ in words:
            t = (cur + " " + w_).strip()
            if sw(t, font, s) <= max_w or not cur:
                cur = t
            else:
                lines.append(cur); cur = w_
        lines.append(cur)
        if len(lines) <= max_lines and all(sw(l, font, s) <= max_w for l in lines):
            return s, lines
    return sizes[-1], lines


def draw_logo(cv, x, y, sz, cfg):
    lp = cfg.get("logo")
    lp = lp and (lp if os.path.isabs(lp) else os.path.join(BASE, lp))
    if lp and os.path.exists(lp):
        cv.drawImage(lp, x, y, sz, sz, preserveAspectRatio=True, mask="auto")
        return False
    cv.setFillColorRGB(0, 0, 0)
    cv.roundRect(x, y, sz, sz, sz * 0.12, fill=1, stroke=0)
    cv.setFillColorRGB(1, 1, 1)
    fs = sz / mm * 2.0
    cv.setFont("FB", fs)
    cv.drawCentredString(x + sz / 2, y + sz * 0.3, "iQ")
    return True


def label_page(cv, it, task, sheet_tag, date_s, cfg):
    """Наклейка за шаблоном користувача (Word 70x35 мм, поля 2 мм): назва Impact 12 по центру рамки зверху,
    логотип ліворуч, QR праворуч, номер замовлення між ними. Координати в мм від лівого верхнього кута."""
    Wm, Hm = cfg["label_mm"]
    cv.setPageSize((Wm * mm, Hm * mm))
    Y = lambda top: (Hm - top) * mm  # з відліку зверху в координати PDF
    bx, by, bw, bh = cfg["name_box_mm"]
    ins = cfg.get("name_inset_mm", 2.54)
    maxw = bw - 2 * ins
    font, base = "FN", cfg.get("name_pt", 12)
    s, lines = base, [it["c"]["kd"]]
    if sw(lines[0], font, s) > maxw:
        s, lines = fit_lines(it["c"]["kd"], font, maxw, [base - k * 0.5 for k in range(int((base - 7) * 2) + 1)], 2)
    lh = s * 0.3528 * 1.08
    y0 = by + bh / 2 - lh * len(lines) / 2 + s * 0.3528 * 0.82  # базова лінія першого рядка
    cv.setFillColorRGB(0, 0, 0); cv.setFont(font, s)
    for k, l in enumerate(lines):
        cv.drawCentredString((bx + bw / 2) * mm, Y(y0 + k * lh), l)
    lx, ly, lw, lh_ = cfg["logo_mm"]
    lp = cfg.get("logo")
    lp = lp and (lp if os.path.isabs(lp) else os.path.join(BASE, lp))
    if lp and os.path.exists(lp):
        cv.drawImage(lp, lx * mm, Y(ly + lh_), lw * mm, lh_ * mm, mask="auto")
    qx, qy, qs = cfg["qr_mm"]
    if cfg.get("qr_value"):
        w = QrCodeWidget(cfg["qr_value"], barLevel="M", barBorder=0)
        b = w.getBounds(); bw_, bh_ = b[2] - b[0], b[3] - b[1]
        d = Drawing(qs * mm, qs * mm, transform=[qs * mm / bw_, 0, 0, qs * mm / bh_, 0, 0]); d.add(w)
        renderPDF.draw(d, cv, qx * mm, Y(qy + qs))
    if cfg.get("show_order", False):
        gx0, gx1 = lx + lw, qx
        t = it["order"]
        fs = cfg.get("order_pt", 10)
        while fs > 5 and sw(t, "FB", fs) > gx1 - gx0 - 1:
            fs -= 0.5
        cv.setFont("FB", fs)
        cv.drawCentredString((gx0 + gx1) / 2 * mm, Y(ly + lh_ / 2 + fs * 0.3528 * 0.35), t)
    cv.showPage()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("task")
    ap.add_argument("--no-labels", action="store_true")
    ap.add_argument("--no-kit", action="store_true", help="без сторінок комплектації")
    a = ap.parse_args()
    task = a.task.rstrip("/")
    name = os.path.basename(task)
    cfg = {"label_mm": [70, 35], "name_box_mm": [2.15, 2.3, 65.85, 15.06], "name_pt": 12, "logo_mm": [12.9, 18.72, 13.92, 13.48],
           "qr_mm": [44.37, 18.12, 14.53], "qr_value": "https://qrco.de/bgmjIr", "logo": "assets/logo.png", "show_order": False}
    cp = os.path.join(BASE, "labels.json")
    if os.path.exists(cp):
        cfg.update(json.load(open(cp, encoding="utf-8")))
    reg_fonts()
    rep = json.load(open(os.path.join(task, "nesting", "report.json"), encoding="utf-8"))
    jobs = json.load(open(os.path.join(task, "jobs.json"), encoding="utf-8"))
    fin = rep.get("finished", "")[:10]
    try:
        date_s = datetime.date.fromisoformat(fin).strftime("%d.%m.%Y")
    except ValueError:
        date_s = ""
    jg = {g["id"]: g for g in jobs["groups"]}
    sheets, issues = [], []
    used_orders = Counter()
    for gr in rep["groups"]:
        for si, s in enumerate(gr["sheets"], 1):
            prims, items, iss = identify(task, jg[gr["id"]], s, gr["sheet_mm"], used_orders)
            sheets.append((gr, si, s, prims, items)); issues += ["%s: %s" % (s["file"], i) for i in iss]
    out = os.path.join(task, "nesting", "розкладка_%s.pdf" % name)
    cv = canvas.Canvas(out); cv.setTitle("Нестинг %s" % name)
    for gr, si, s, prims, items in sheets:
        layout_page(cv, name, gr, si, s, prims, items, date_s)
    n_kit = 0
    if not a.no_kit:
        krows, kiss = kit_orders(jobs, rep)
        issues += ["комплектація: " + i for i in kiss]
        if krows:
            n_kit = kit_pages(cv, name, krows, date_s)
    n_lab = 0
    if not a.no_labels:
        import labels_docx
        # номер виробу «i/N» (правило користувача 13.11.2026, замість 05.11.2026): N = кількість цього виробу
        # в ОДНОМУ замовленні (не в завданні), i = порядковий номер у цьому замовленні за листами і позиціями;
        # складські нумеруються окремо (замовлення «СКЛАД»)
        allit = [it for gr, si, s, prims, items in sheets for it in sorted(items, key=lambda t: t["n"])]
        key = lambda it: (it["c"]["kd"], it["c"]["stock"], it.get("order", ""))
        tot = Counter(key(it) for it in allit); seen = Counter(); names = []
        for it in allit:
            k_ = key(it); seen[k_] += 1
            names.append((it["c"]["kd"], it.get("order", ""), "%d/%d" % (seen[k_], tot[k_])))
        labels_docx.write_labels(os.path.join(task, "nesting", "наклейки_%s.docx" % name), names, cfg)
        n_lab = len(names)
    cv.save()
    small = ["%s поз.%d: %s" % (s["file"], it["n"], ("%.1f pt" % it["label_pt"]) if it.get("label_pt") else "лише номер")
             for gr, si, s, prims, items in sheets for it in items if not it.get("label_pt") or it["label_pt"] < 6]
    for t in small:
        issues.append("дрібний напис на розкладці " + t)
    print("OK %s: сторінок розкладки %d, комплектації %d; наклейки_%s.docx: %d шт (%sx%s мм)" % (out, len(sheets), n_kit, name, n_lab, *cfg["label_mm"]))
    for i in issues:
        print("УВАГА " + i)


if __name__ == "__main__":
    main()
