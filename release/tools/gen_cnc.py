#!/usr/bin/env python3
"""Програма ЧПУ (G-код у форматі еталона ArtCAM) для листів нестингу kd-nesting.
python3 gen_cnc.py <папка завдання> лист_A_1 [лист_A_2 ...] | --all  [--out ПАПКА]
Правила: tools/cnc_rules.json. Потрібен лише numpy. Лист береться з nesting/<лист>.dxf.
Ім'я .cnc: <ДДММ>_<лист>_<товщина><мат>_<замовлення>.cnc, без назв виробів: див. cnc_name()."""
import sys, os, re, json, math, importlib.util, argparse
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
import copy
BASE = json.load(open(os.path.join(HERE, "cnc_rules.json"), encoding="utf-8"))
R = None; TR = TOP = ZS = FP = None
def _merge(a, b):
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(a.get(k), dict): _merge(a[k], v)
        else: a[k] = copy.deepcopy(v)
def set_profile(th):
    """правила для товщини: базові + by_thickness[th] (15 мм: правило користувача 03.11.2026)"""
    global R, TR, TOP, ZS, FP
    R = copy.deepcopy(BASE); _merge(R, BASE.get("by_thickness", {}).get(str(th), {}))
    TR = R["tool_diameter_mm"] / 2; TOP = R["top_z_mm"]; ZS = R["safe_z_mm"]; FP = R["feed_plunge"]
set_profile(12)
SAG = 0.01
_saved = sys.argv; sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("dc", os.path.join(HERE, "dxf_contours.py")); dc = importlib.util.module_from_spec(spec); spec.loader.exec_module(dc)
sys.argv = _saved

NAMES = json.load(open(os.path.join(HERE, "cnc_names.json"), encoding="utf-8"))["products"]
_TL = dict(zip("абвгдеєжзиіїйклмнопрстуфхцчшщьюя", ["a","b","v","h","d","e","ye","zh","z","y","i","yi","y","k","l","m","n","o","p","r","s","t","u","f","kh","ts","ch","sh","shch","","yu","ya"]))
def _key(n):
    import unicodedata
    n = unicodedata.normalize("NFC", n).replace("х", "x").replace("Х", "X").strip()
    return re.sub(r"\s+UA$", "", n)
def _code(n):
    k = _key(n)
    if k in NAMES: return NAMES[k]
    c = "".join(_TL.get(ch, ch) for ch in k.lower())
    c = re.sub(r"[^a-z0-9]+", "", c).upper()[:24]
    print("УВАГА: для «%s» немає коду в cnc_names.json, у імені файлу %s" % (n, c)); return c
def cnc_name(sheet_file, material, parts, stock, orders_map=None, date=""):
    """Ім'я CNC-файлу (правило від 27.10.2026, без назв виробів): <ДДММ>_<група><N>_<товщина><матеріал>_<№замовлень через дефіс>.cnc.
    Приклад: 1027_A1_12TV_1815-1835-1854.cnc. Замовлення беруться з jobs.json за деталями листа; складські вироби в ім'я не входять."""
    m = re.match(r"\D*_?([A-Za-z]+)_(\d+)", os.path.splitext(sheet_file)[0]); sid = (m.group(1) + m.group(2)) if m else "S"
    t = re.search(r"Лист пластиковий (\d+)", material); mat = (t.group(1) if t else "") + ("UA" if material.rstrip().upper().endswith("UA") else "TV")
    om = orders_map or {}
    ords = sorted({o for n in parts for o in om.get(_key(n), [])}, key=lambda x: (len(x), x))
    pre = (date + "_") if date else ""
    return pre + "_".join([sid, mat] + (["-".join(ords)] if ords else [])) + ".cnc"

def area(p):
    p = np.asarray(p); return 0.5 * float(np.sum(p[:, 0] * np.roll(p[:, 1], -1) - np.roll(p[:, 0], -1) * p[:, 1]))
def f3(x): return "%.3f" % x
def sq_final(side):
    """розмір квадратного отвору, що фрезерується, за номіналом сторони з DXF (cnc_rules.json: through.square_nominal_to_final_mm)"""
    m = R["through"]["square_nominal_to_final_mm"]; tol = R["through"].get("square_tol_mm", 0.1)
    # ключі таблиці можуть бути дробові ("11.5"): найближчий у межах допуску
    best = min(m, key=lambda k: abs(float(k) - side))
    if abs(float(best) - side) > tol:
        raise Err("квадратний виріз зі стороною %.2f: розміру немає в cnc_rules.json (through.square_nominal_to_final_mm)" % side)
    return m[best]
class Err(Exception): pass
def recess_final_d(it, items, what):
    """кінцевий діаметр (ширина) виточки за болтом (правило 02.11.2026): болт за отвором усередині виточки"""
    rr = R["recess"]; kids = [o for o in items if o["depth"] == it["depth"] + 1 and dc.inside(o["pts"][0], it["pts"])]
    if not kids: raise Err("%s без отвору всередині: болт не визначено" % what)
    sides = set()
    for o in kids:
        c, u, a, b = min_rect(o["pts"]); sides.add(sq_final(2*b))
    if len(sides) != 1: raise Err("%s: усередині отвори різних розмірів %s" % (what, sorted(sides)))
    key = "%g" % sides.pop(); bolt = rr["bolt_by_hole_final_mm"].get(key)
    if bolt is None: raise Err("%s: отвір %s мм не зіставлено з болтом (recess.bolt_by_hole_final_mm)" % (what, key))
    return rr["final_diameter_by_bolt_mm"][bolt], bolt
TRIMS = []  # обрізки листа (Y центру різу, залишок мм)
RD = []  # (діаметр у DXF, кінцевий за болтом), де вони різні
SR = None  # потай навколо овальних пазів: (довжина, ширина) мм; вмикається --slot-recess 55x30

# ---------- геометрія ----------
def dp(P, tol):
    P = np.asarray(P)
    def rec(a, b, out):
        if b <= a + 1: out.append(b); return
        A, B = P[a], P[b]; d = B - A; L = math.hypot(*d)
        dist = np.hypot(*(P[a+1:b] - A).T) if L < 1e-12 else np.abs(d[0]*(P[a+1:b, 1]-A[1]) - d[1]*(P[a+1:b, 0]-A[0])) / L
        if dist.max() > tol:
            k = a + 1 + int(dist.argmax()); rec(a, k, out); rec(k, b, out)
        else: out.append(b)
    out = [0]; rec(0, len(P) - 1, out); return P[out]
def simplify_closed(P, tol=0.02):
    P = np.asarray(P); P = P[:-1] if np.allclose(P[0], P[-1]) else P
    k = int(np.argmax(np.hypot(*(P - P[0]).T)))
    a = dp(P[:k+1], tol); b = dp(np.vstack([P[k:], P[:1]]), tol); return np.vstack([a[:-1], b[:-1]])
def seg_int(a, b, c, d):
    r = b - a; s = d - c; den = r[0]*s[1] - r[1]*s[0]
    if abs(den) < 1e-12: return None
    t = ((c[0]-a[0])*s[1] - (c[1]-a[1])*s[0]) / den; u = ((c[0]-a[0])*r[1] - (c[1]-a[1])*r[0]) / den
    return a + t*r if (1e-9 < t < 1-1e-9 and 1e-9 < u < 1-1e-9) else None
def offset_ccw(P, d):
    n = len(P); E = np.roll(P, -1, axis=0) - P; Ls = np.hypot(E[:, 0], E[:, 1]); N = np.column_stack([E[:, 1], -E[:, 0]]) / Ls[:, None]
    out = []; step = 2 * math.acos(1 - SAG / d)
    for i in range(n):
        n0, n1 = N[i-1], N[i]; v = P[i]; cr = E[i-1][0]*E[i][1] - E[i-1][1]*E[i][0]; dot = float(n0 @ n1)
        if cr > 1e-9:
            a0 = math.atan2(n0[1], n0[0]); da = math.atan2(n1[1], n1[0]) - a0
            while da < 0: da += 2*math.pi
            k = max(1, math.ceil(da / step))
            for j in range(k + 1):
                a = a0 + da*j/k; out.append(v + d*np.array([math.cos(a), math.sin(a)]))
        elif dot < -0.95: out += [v + d*n0, v + d*n1]
        else: out.append(v + d*(n0 + n1)/(1 + dot))
    return np.array(out)
def remove_loops(poly, sign):
    poly = [np.array(p) for p in poly]
    for _ in range(500):
        n = len(poly); hit = None
        for i in range(n):
            for j in range(i+2, n):
                if i == 0 and j == n-1: continue
                q = seg_int(poly[i], poly[(i+1) % n], poly[j], poly[(j+1) % n])
                if q is not None: hit = (i, j, q); break
            if hit: break
        if not hit: return np.array(poly)
        i, j, q = hit
        if area([q] + poly[i+1:j+1]) * sign < 0: poly = poly[:i+1] + [q] + poly[j+1:]
        elif area([q] + poly[j+1:] + poly[:i+1]) * sign < 0: poly = [q] + poly[i+1:j+1]
        else: raise Err("не вдалося прибрати петлю в офсеті контуру")
    raise Err("забагато петель в офсеті контуру")
def dist_to_poly(p, P):
    A = P; B = np.roll(P, -1, axis=0); AB = B - A
    t = np.clip(np.sum((p-A)*AB, axis=1) / np.maximum(np.sum(AB*AB, axis=1), 1e-12), 0, 1)
    return float(np.min(np.hypot(*(A + AB*t[:, None] - p).T)))
def pip(p, P):
    x, y = p; ins = False; n = len(P)
    for i in range(n):
        x1, y1 = P[i]; x2, y2 = P[(i+1) % n]
        if (y1 > y) != (y2 > y) and x1 + (y-y1)*(x2-x1)/(y2-y1) > x: ins = not ins
    return ins
def min_rect(P):
    """найменший прямокутник: центр, одиничний вектор довгої осі, піврозміри a (вздовж u) >= b"""
    P = np.asarray(P); best = None
    for i in range(len(P)):
        d = P[(i+1) % len(P)] - P[i]; L = math.hypot(*d)
        if L < 1e-9: continue
        u = d / L; v = np.array([-u[1], u[0]]); x = P @ u; y = P @ v
        w, h = x.max() - x.min(), y.max() - y.min()
        if best is None or w*h < best[0]: best = (w*h, u, v, (x.max()+x.min())/2, (y.max()+y.min())/2, w, h)
    _, u, v, cx, cy, w, h = best
    c = u*cx + v*cy
    if h > w: u, v, w, h = v, -u, h, w
    return c, u, w/2, h/2


def stadium(c, u, s, r):
    """контур-стадіон проти годинникової: центр c, вісь u, пів-прямої s, радіус r; старт у середині нижньої сторони"""
    v = np.array([-u[1], u[0]]); n = max(16, math.ceil(math.pi / math.acos(1 - SAG / r))); pts = [c - r*v]
    for k in range(n + 1): th = -math.pi/2 + math.pi*k/n; pts.append(c + s*u + r*(math.cos(th)*u + math.sin(th)*v))
    for k in range(n + 1): th = math.pi/2 + math.pi*k/n; pts.append(c - s*u + r*(math.cos(th)*u + math.sin(th)*v))
    pts.append(c - r*v); return np.array(pts)

# ---------- читання листа ----------
ARCS = []
def read_sheet(dxf):
    global ARCS
    # шар REMNANT (прямокутник придатного залишку від nest_run.py) не фрезерується (виправлено 04.11.2026)
    ents = [e for e in dc.load(dxf) if (e["d"].get("8") or [""])[0].strip() != "REMNANT"]; segs, circ = dc.segs_of(ents); items = dc.chain(segs)
    ARCS = [tuple(float(e["d"][g][0]) for g in ("10", "20", "40", "50", "51")) for e in ents if e["t"] == "ARC"]
    for cx, cy, r in circ:
        t = np.linspace(0, 2*math.pi, 181)
        items.append({"kinds": ["CIRCLE"], "pts": np.column_stack([cx + r*np.cos(t), cy + r*np.sin(t)]), "closed": True, "c": (cx, cy, r)})
    if not all(i["closed"] for i in items): raise Err("у DXF є незамкнені контури")
    for it in items: it["area"] = dc.area(it["pts"])
    for it in items: it["bb"] = (it["pts"][:, 0].min(), it["pts"][:, 1].min(), it["pts"][:, 0].max(), it["pts"][:, 1].max())
    # габарити спершу (03.11.2026): промінь через вершину однакової сусідньої деталі на тій самій висоті давав хибне «всередині»
    def _bbin(i, o): return o["bb"][0] - 1e-6 <= i["bb"][0] and o["bb"][1] - 1e-6 <= i["bb"][1] and i["bb"][2] <= o["bb"][2] + 1e-6 and i["bb"][3] <= o["bb"][3] + 1e-6
    for it in items: it["depth"] = sum(1 for o in items if o is not it and o["area"] > it["area"] and _bbin(it, o) and dc.inside(it["pts"][0], o["pts"]))
    for it in items: it["_bbin"] = _bbin
    return items
def classify(items):
    sheet = [i for i in items if i["depth"] == 0]
    if len(sheet) != 1: raise Err("не знайдено єдиний контур листа")
    sp = sheet[0]["pts"]; F = {"sheet": (sp[:, 0].min(), sp[:, 1].min(), sp[:, 0].max(), sp[:, 1].max()), "outlines": [], "pockets": [], "circles": [], "through": [], "oval_recess": [], "arc_pockets": [], "poly_pockets": [], "items": items}
    outl0 = [o for o in items if o["depth"] == 1 and o["area"] > R["outline_min_area_mm2"] and o["kinds"] != ["CIRCLE"]]
    def crosses(it):
        """контур частково в деталі, частково поза нею (виборка ПСГ уздовж краю)"""
        P = it["pts"][::max(1, len(it["pts"]) // 200)]
        for o in outl0:
            if o is it: continue
            f = [dc.inside(p, o["pts"]) for p in P]
            if any(f) and not all(f): return o
        return None
    for it in items:
        if it["depth"] == 0: continue
        k = it["kinds"]
        if k == ["CIRCLE"]: F["circles"].append(it); continue
        if it["depth"] == 1 and it["area"] > R["outline_min_area_mm2"]: F["outlines"].append(it); continue
        c, u, a, b = min_rect(it["pts"])
        nl, na = k.count("LINE"), k.count("ARC")
        # контур лише з ліній, де сторона розбита на колінеарні відрізки (Bomet 35 020253 R ПСГ: трапеція з 59 ліній),
        # вважається чотирикутником; виборка фрезерується мінімальним прямокутником (рішення користувача 30.10.2026)
        if na == 0 and nl > 4:
            Q = simplify_closed(it["pts"], tol=0.05); Q = Q[:-1] if np.allclose(Q[0], Q[-1]) else Q
            if len(Q) == 4: nl = 4; print("УВАГА: контур з %d ліній біля (%.0f; %.0f) = чотирикутник %.1f x %.1f (колінеарні відрізки обʼєднано)" % (len(k), c[0], c[1], 2*b, 2*a))
        if nl == 4 and na == 0:
            if a / b >= R["pocket"]["min_aspect_ratio"]: F["pockets"].append(it)
            else: F["through"].append(("квадрат", it))
        elif nl == 2 and na == 2:
            # овал (стадіон): з вкладеним отвором = виточка такої форми, без вкладених = наскрізний паз (правило 27.10.2026)
            exp = 4*(a - b)*b + math.pi*b*b
            if abs(it["area"] - exp) > 0.01*exp:
                bd = arc_band(it, items)
                if bd is None: raise Err("контур 2 лінії + 2 дуги біля %s не є ні овалом, ні дуговою виборкою ПСГ уздовж краю: правило не задано" % ((round(c[0]), round(c[1])),))
                F["arc_pockets"].append(bd); continue
            kids = [o for o in items if o["depth"] == it["depth"] + 1 and dc.inside(o["pts"][0], it["pts"])]
            if kids: F["oval_recess"].append(it)
            else: F["through"].append(("паз", it))
        elif nl == 2 and na == 6:
            # дугова виборка ПСГ зі скругленими торцями (Lemken 3441027 L ПСГ МБ10: R3,2 у кутах; рішення користувача 03.11.2026: як дугова виборка, 6 мм)
            bd = arc_band(it, items, fillets=True)
            if bd is None: raise Err("контур 2 лінії + 6 дуг біля %s не є дуговою виборкою ПСГ зі скругленими торцями: правило не задано" % ((round(c[0]), round(c[1])),))
            print("УВАГА: дугова виборка зі скругленими торцями біля (%.0f; %.0f): R%.2f / R%.2f" % (c[0], c[1], bd["rw"], bd["rf"]))
            F["arc_pockets"].append(bd); continue
        else:
            # загальне правило (рішення користувача 05.11.2026): будь-який замкнений контур, що перетинає край деталі,
            # у 12 мм = виборка під грудину ПСГ на pocket.depth_mm (похилі смуги зі скругленнями: Наука 3-5-40, Lemken 3440716/3440717)
            own = crosses(it)
            if own is None: raise Err("невідомий контур (%s, площа %.0f) у %s: правило для нього не задано" % (k[:6], it["area"], (round(c[0]), round(c[1]))))
            print("УВАГА: виборка ПСГ довільної форми біля (%.0f; %.0f): %d ліній, %d дуг, %.1f x %.1f" % (c[0], c[1], nl, na, 2*b, 2*a))
            F["poly_pockets"].append({"it": it, "owner": own})
    return F

def classify15(items):
    """15 мм (правило 03.11.2026): у деталі лише заглиблення під сталеві компоненти (будь-яка замкнена форма прямо в контурі деталі,
    з острівцями всередині), наскрізні квадрати й овальні пази. Кола (виточки) і виборки ПСГ: зупинка."""
    sheet = [i for i in items if i["depth"] == 0]
    if len(sheet) != 1: raise Err("не знайдено єдиний контур листа")
    sp = sheet[0]["pts"]; F = {"sheet": (sp[:, 0].min(), sp[:, 1].min(), sp[:, 0].max(), sp[:, 1].max()), "outlines": [], "pockets": [], "circles": [], "through": [], "oval_recess": [], "arc_pockets": [], "support": [], "items": items}
    for it in items:
        if it["depth"] == 1 and it["kinds"] != ["CIRCLE"] and it["area"] > R["outline_min_area_mm2"]: F["outlines"].append(it)
    def parent(it):
        cs = [o for o in items if o is not it and o["area"] > it["area"] and it["_bbin"](it, o) and dc.inside(it["pts"][0], o["pts"])]
        return min(cs, key=lambda o: o["area"]) if cs else None
    role = {id(o): "outline" for o in F["outlines"]}; regions = {}
    for it in sorted(items, key=lambda o: o["depth"]):
        if it["depth"] == 0 or id(it) in role: continue
        k = it["kinds"]; c, u, a, b = min_rect(it["pts"]); where = "(%.0f; %.0f)" % (c[0], c[1])
        if k == ["CIRCLE"]: raise Err("коло Ø%.1f біля %s: виточок під потай для 15 мм не буває" % (2*it["c"][2], where))
        pr = parent(it); prole = role.get(id(pr)) if pr is not None else None
        if prole is None: raise Err("контур біля %s поза деталлю" % where)
        Q = simplify_closed(it["pts"], tol=0.05); nl, na = k.count("LINE"), k.count("ARC")
        is_quad = (na == 0 and len(Q) == 4)
        exp = 4*(a - b)*b + math.pi*b*b
        is_oval = (nl == 2 and na == 2 and abs(it["area"] - exp) <= 0.01*exp)
        if is_quad and a / b < R["pocket"]["min_aspect_ratio"] and 2*a < 30:
            sq_final(2*a); F["through"].append(("квадрат", it)); role[id(it)] = "hole"; continue
        if is_oval and 2*b < 30:
            if any(dc.inside(o["pts"][0], it["pts"]) for o in items if o["depth"] == it["depth"] + 1): raise Err("овал з отвором усередині біля %s: виточок для 15 мм не буває" % where)
            F["through"].append(("паз", it)); role[id(it)] = "hole"; continue
        if prole == "outline":
            if it["area"] < R["support_recess"]["min_area_mm2"]: raise Err("малий контур (%.0f мм2) біля %s: правило не задано" % (it["area"], where))
            if not all(pip(p, pr["pts"]) or dist_to_poly(p, pr["pts"]) < 0.05 for p in it["pts"][::5]): raise Err("заглиблення біля %s виходить за край деталі" % where)
            role[id(it)] = "region"; regions[id(it)] = {"it": it, "islands": []}; continue
        if prole == "region":
            role[id(it)] = "island"; regions[id(pr)]["islands"].append(it); continue
        raise Err("контур біля %s усередині острівця заглиблення: правило не задано" % where)
    F["support"] = list(regions.values())
    return F

def arc_band(it, items, fillets=False):
    """дугова виборка ПСГ (правило 27.10.2026): 2 концентричні дуги + 2 лінії; дуга-стінка всередині деталі, друга поза нею.
    fillets=True (03.11.2026): у кутах ще 4 малі дуги-скруглення (R <= 5); торці = лінії між вільними кінцями скруглень"""
    P = it["pts"]; on = lambda q: float(np.min(np.hypot(*(P - q).T))) < 0.05; cand = []; fil = []
    for cx, cy, r, a0, a1 in ARCS:
        e0 = np.array([cx + r*math.cos(math.radians(a0)), cy + r*math.sin(math.radians(a0))]); e1 = np.array([cx + r*math.cos(math.radians(a1)), cy + r*math.sin(math.radians(a1))])
        if on(e0) and on(e1):
            if fillets and r <= 5: fil.append((e0, e1))
            else: cand.append((np.array([cx, cy]), r, e0, e1))
    if len(cand) != 2 or np.hypot(*(cand[0][0] - cand[1][0])) > 0.05 or abs(cand[0][1] - cand[1][1]) < 1: return None
    outl = [o for o in items if o["depth"] == 1 and o["area"] > R["outline_min_area_mm2"]]
    def mid(cd):
        m = (cd[2] + cd[3]) / 2 - cd[0]; return cd[0] + cd[1] * m / np.hypot(*m)
    ins = [any(dc.inside(mid(cd), o["pts"]) for o in outl) for cd in cand]
    if ins.count(True) != 1: return None
    w, f = cand[ins.index(True)], cand[ins.index(False)]
    owner = next(o for o in outl if dc.inside(mid(w), o["pts"]))
    ends = [(e, f[2] if np.hypot(*(f[2] - e)) < np.hypot(*(f[3] - e)) else f[3]) for e in (w[2], w[3])]
    if fillets:
        if len(fil) != 4: return None
        big = [cd[2] for cd in cand] + [cd[3] for cd in cand]
        free = [e1 if min(np.hypot(*(e0 - b)) for b in big) < 0.05 else e0 for e0, e1 in fil]
        new_ends = []
        for e, _ in ends:
            two = sorted(free, key=lambda q: np.hypot(*(q - e)))[:2]
            A, B = sorted(two, key=lambda q: abs(np.hypot(*(q - w[0])) - w[1]))
            new_ends.append((A, B))
        ends = new_ends
    return {"c": w[0], "rw": w[1], "rf": f[1], "ends": ends, "m": (mid(w) + mid(f)) / 2, "owner": owner}
def arc_pts(c, rho, t0, t1):
    n = max(4, math.ceil(abs(t1 - t0) / (2*math.acos(max(-1.0, 1 - SAG / rho)))))
    return [c + rho*np.array([math.cos(t), math.sin(t)]) for t in np.linspace(t0, t1, n + 1)]

OP_DEBUG = {}
OPT = False  # --opt (06.11.2026): порядок операцій з поточної позиції + 2-opt, у заглибленнях 15 мм перехід між обходами на глибині без підйому

def two_opt(seq, key, start):
    """покращення порядку обходу (відкритий маршрут від start): 2-opt за точками key(it)"""
    P = [tuple(key(it)) for it in seq]; n = len(P); d = math.dist; imp = True
    while imp:
        imp = False
        for i in range(n - 1):
            a = start if i == 0 else P[i-1]
            for k in range(i + 1, n):
                c = P[k + 1] if k + 1 < n else None
                old = d(a, P[i]) + (d(P[k], c) if c else 0); new = d(a, P[k]) + (d(P[i], c) if c else 0)
                if new < old - 1e-6: P[i:k+1] = P[i:k+1][::-1]; seq[i:k+1] = seq[i:k+1][::-1]; imp = True
    return seq
def offset_pocket(B, Iw, s, h=0.5):
    """Заглиблення зміщенням (рішення користувача 04.11.2026). B: стінка заглиблення, Iw: острівці (координати листа, мм).
    Карта відстаней до стінок на сітці h; проходи = лінії рівної відстані TR + k*s (k >= 1), від середини назовні.
    Хребти (середина смуги, де наступного рівня вже немає) добираються проходами на півкроку.
    Стінки (рівень TR) фрезерує викликач точним офсетом. Повертає список полілиній; перевіряє, що не лишилось недорізу."""
    from contourpy import contour_generator
    from matplotlib.path import Path
    walls = [np.asarray(B)] + [np.asarray(I) for I in Iw]
    x0, y0 = B.min(axis=0) - 2; x1, y1 = B.max(axis=0) + 2
    xs = np.arange(x0, x1 + h, h); ys = np.arange(y0, y1 + h, h)
    X, Y = np.meshgrid(xs, ys); P = np.column_stack([X.ravel(), Y.ravel()])
    inside = Path(B).contains_points(P)
    for I in Iw: inside &= ~Path(I).contains_points(P)
    idx = np.where(inside)[0]; Q = P[idx]; dq = np.full(len(Q), np.inf)
    for W in walls:
        Wn = np.roll(W, -1, axis=0)
        for a, b in zip(W, Wn):
            ab = b - a; L2 = float(ab @ ab)
            if L2 < 1e-12: continue
            t = np.clip(((Q - a) @ ab) / L2, 0, 1)
            dq = np.minimum(dq, np.hypot(Q[:, 0] - a[0] - t*ab[0], Q[:, 1] - a[1] - t*ab[1]))
    Fv = np.full(len(P), -1.0); Fv[idx] = dq; Fd = Fv.reshape(X.shape); dmax = float(dq.max()) if len(dq) else 0.0
    cg = contour_generator(xs, ys, Fd)
    gy, gx = np.gradient(Fd, h)
    def at(G, pts):
        fx = np.clip((pts[:, 0] - x0) / h, 0, len(xs) - 1.001); fy = np.clip((pts[:, 1] - y0) / h, 0, len(ys) - 1.001)
        i0 = fx.astype(int); j0 = fy.astype(int); u = fx - i0; v = fy - j0
        return G[j0, i0]*(1-u)*(1-v) + G[j0, i0+1]*u*(1-v) + G[j0+1, i0]*(1-u)*v + G[j0+1, i0+1]*u*v
    paths = []
    k = 1
    while TR + k*s < dmax - 0.05:
        for ln in cg.lines(TR + k*s): paths.append((TR + k*s, ln))
        k += 1
    k = 1
    while TR + (k - 0.5)*s < dmax - 0.05:
        H = TR + (k - 0.5)*s; Ln = TR + k*s
        for ln in cg.lines(H):
            g = np.column_stack([at(gx, ln), at(gy, ln)]); g /= np.maximum(np.hypot(g[:, 0], g[:, 1]), 1e-9)[:, None]
            keep = at(Fd, ln + g*(s/2)) < Ln - 0.3
            if not keep.any(): continue
            closed = len(ln) > 2 and np.allclose(ln[0], ln[-1])
            if closed and keep.all(): paths.append((H, ln)); continue
            if closed and not keep.all():
                r = int(np.argmin(keep)); ln = np.vstack([ln[r:-1], ln[:r+1]]); keep = np.concatenate([keep[r:-1], keep[:r+1]])
            run = []
            for p, kk in zip(ln, keep):
                if kk: run.append(p)
                elif run:
                    if len(run) > 1: paths.append((H, np.array(run)))
                    run = []
            if len(run) > 1: paths.append((H, np.array(run)))
        k += 1
    out = []; lvls = []
    for lv, ln in sorted(paths, key=lambda t: -t[0]):
        if len(ln) > 2 and np.allclose(ln[0], ln[-1]):
            q = simplify_closed(ln, 0.03); q = np.vstack([q, q[:1]])
        else: q = dp(ln, 0.03)
        if len(q) >= 2: out.append(q); lvls.append(lv)
    OP_DEBUG["levels"] = lvls
    # перевірка покриття: кожна точка, куди дістає центр фрези, у межах радіуса фрези від якогось проходу або стінки
    cov = np.zeros(X.shape, bool); r = TR - 0.05; rr = int(math.ceil(r / h)) + 1
    for q in out + [ln for ln in cg.lines(TR)]:
        segs = [q[i] + (q[i+1] - q[i]) * t for i in range(len(q) - 1) for t in np.linspace(0, 1, max(2, int(math.hypot(*(q[i+1] - q[i])) / (h/2)) + 1))[:-1]]
        for p in segs + [q[-1]]:
            ci = int(round((p[0] - x0) / h)); cj = int(round((p[1] - y0) / h))
            i0, i1 = max(0, ci - rr), min(len(xs), ci + rr + 1); j0, j1 = max(0, cj - rr), min(len(ys), cj + rr + 1)
            cov[j0:j1, i0:i1] |= (X[j0:j1, i0:i1] - p[0])**2 + (Y[j0:j1, i0:i1] - p[1])**2 <= r*r
    # кути, недосяжні для фрези (радіус TR), не перевіряються: лише зона, де може стояти центр фрези (F >= TR)
    miss = (Fd >= TR) & ~cov
    OP_DEBUG.update(miss=miss, xs=xs, ys=ys, paths=out, F=Fd)
    if miss.sum() * h * h > 2.0:
        jj, ii = np.where(miss); raise Err("заглиблення зміщенням: недоріз %.1f мм2 біля (%.0f; %.0f)" % (miss.sum()*h*h, xs[ii[0]], ys[jj[0]]))
    return out

# ---------- G-код ----------
def build(F, thickness):
    x0, y0, x1, y1 = F["sheet"]; sx, sy = x1 - x0, y1 - y0
    if sx > sy: T = lambda p: (y1 - p[1], p[0] - x0); W, L = sy, sx
    else: T = lambda p: (p[0] - x0, p[1] - y0); W, L = sx, sy
    Tarr = lambda P: np.array([T(p) for p in P])
    out = ["%", "G90", "G49", "M3 S%d" % R["spindle_rpm"]]; st = {"first": True}
    def begin(x, y, z):
        if st["first"]: out.append("G0 X%s Y%s Z%s" % (f3(x), f3(y), f3(ZS))); st["first"] = False
        else: out.extend(["G0   Z%s" % f3(ZS), "G0 X%s Y%s " % (f3(x), f3(y))])
        out.append("G1   Z%s F%d" % (f3(z), FP))
    def lastpos():
        for l_ in reversed(out):
            m_ = re.search(r"X(-?[\d.]+) Y(-?[\d.]+)", l_)
            if m_: return (float(m_.group(1)), float(m_.group(2)))
        return (0.0, 0.0)
    def nn(items, key):
        left = list(items); cur = lastpos() if OPT else (0.0, 0.0); start = cur; res = []
        while left:
            j = min(range(len(left)), key=lambda i: (key(left[i])[0]-cur[0])**2 + (key(left[i])[1]-cur[1])**2)
            it = left.pop(j); res.append(it); cur = key(it)
        if OPT and len(res) > 2: res = two_opt(res, key, start)
        return res
    def emit_linked(chains, lvls, walls, z, feed, exact):
        """--opt: обходи заглиблення від середини до стінок (рівень за рівнем), у межах рівня найближчий,
        замкнені обходи починаються з найближчої точки; перехід до наступного обходу по прямій на глибині,
        якщо вся пряма лежить у зоні, де центр фрези не зачіпає стінок і острівців (поле відстаней >= радіус фрези);
        інакше підйом на безпечну Z. Стінки останніми. Повертає кількість сегментів (підйомів)."""
        Fd, xs_, ys_ = OP_DEBUG["F"], OP_DEBUG["xs"], OP_DEBUG["ys"]; h_ = xs_[1] - xs_[0]
        def fval(p):
            i = (p[0] - xs_[0]) / h_; j = (p[1] - ys_[0]) / h_
            i0 = int(math.floor(i)); j0 = int(math.floor(j))
            if i0 < 0 or j0 < 0 or i0 + 1 >= len(xs_) or j0 + 1 >= len(ys_): return -1.0
            return float(min(Fd[j0, i0], Fd[j0, i0+1], Fd[j0+1, i0], Fd[j0+1, i0+1]))
        def segdist(Q, W_):
            A = np.asarray(W_); Bn = np.roll(A, -1, axis=0); AB = Bn - A; L2 = np.maximum((AB**2).sum(1), 1e-12)
            t = np.clip(((Q[:, None, :] - A[None]) * AB[None]).sum(2) / L2[None], 0, 1)
            D = Q[:, None, :] - (A[None] + t[..., None] * AB[None]); return np.sqrt((D**2).sum(2)).min(1)
        from matplotlib.path import Path as _P
        Bx, Ix = exact
        def link_ok(p, q):
            L_ = math.dist(p, q); n_ = max(2, int(L_ / 0.25))
            ts = np.linspace(0, 1, n_ + 1)
            for t in ts[1:-1]:
                if t * L_ < 0.75 or (1 - t) * L_ < 0.75: continue
                if fval((p[0] + (q[0]-p[0])*t, p[1] + (q[1]-p[1])*t)) < TR - 0.02: return False
            Q = np.array([(p[0] + (q[0]-p[0])*t, p[1] + (q[1]-p[1])*t) for t in ts])  # точна перевірка: центр фрези не ближче за радіус до стінки й острівців
            if not _P(Bx).contains_points(Q).all(): return False
            if any(_P(I_).contains_points(Q).any() for I_ in Ix): return False
            return all(segdist(Q, W_).min() >= TR - 0.03 for W_ in [Bx] + list(Ix))
        groups = {}
        ONEG = OPT == "any"; MAXL = 1e9  # any: обходи заглиблення в будь-якому порядку (найближчий), стінки все одно останніми
        for ch, lv in zip(chains, lvls): groups.setdefault(0 if ONEG else round(lv, 3), []).append(np.asarray(ch))
        seqs = [groups[k] for k in sorted(groups, reverse=True)] + [[np.vstack([np.asarray(W_), np.asarray(W_)[:1]]) for W_ in walls]]
        cur = lastpos(); nseg = 0; first = True
        for grp in seqs:
            left = list(grp)
            while left:
                best = None
                for i_, q in enumerate(left):
                    closed = len(q) > 2 and np.allclose(q[0], q[-1])
                    cand = range(len(q) - 1) if closed else [0]
                    for k_ in cand:
                        dd = math.dist(cur, q[k_])
                        if best is None or dd < best[0]: best = (dd, i_, k_, closed)
                _, i_, k_, closed = best; q = left.pop(i_)
                if closed and k_: q = np.vstack([q[k_:-1], q[:k_+1]])
                if not first and math.dist(cur, q[0]) <= MAXL and link_ok(cur, q[0]): out.append("G1 X%s Y%s  F%d" % (f3(q[0][0]), f3(q[0][1]), feed)); out.extend("X%s Y%s " % (f3(p[0]), f3(p[1])) for p in q[1:])
                else:
                    begin(q[0][0], q[0][1], z); out.append("G1 X%s Y%s  F%d" % (f3(q[1][0]), f3(q[1][1]), feed))
                    out.extend("X%s Y%s " % (f3(p[0]), f3(p[1])) for p in q[2:]); nseg += 1
                first = False; cur = tuple(q[-1])
        return nseg
    # --- підготовка контурів деталей
    parts = []
    for o in F["outlines"]:
        P = simplify_closed(Tarr(o["pts"]), 0.02)
        if area(P) < 0: P = P[::-1]
        off = remove_loops(offset_ccw(P, R["contour"]["offset_outside_mm"]), 1)
        if max(abs(dist_to_poly(p, P) - R["contour"]["offset_outside_mm"]) for p in off) > 0.03: raise Err("офсет контуру не пройшов перевірку відстані")
        if not all(pip(p, off) for p in P): raise Err("контур деталі не всередині офсету")
        cw = off[::-1]; E = np.roll(cw, -1, axis=0) - cw; k = int(np.argmax(np.hypot(E[:, 0], E[:, 1])))
        parts.append(np.vstack([cw[k:], cw[:k]]))
    mind = 1e9
    for i in range(len(parts)):
        for j in range(i+1, len(parts)):
            a, b = parts[i], parts[j]
            if a[:, 0].min() > b[:, 0].max()+2 or a[:, 0].max() < b[:, 0].min()-2 or a[:, 1].min() > b[:, 1].max()+2 or a[:, 1].max() < b[:, 1].min()-2: continue
            mind = min(mind, min(min(dist_to_poly(p, b) for p in a), min(dist_to_poly(p, a) for p in b)))
    if mind < 0.5: raise Err("траєкторії різання сусідніх деталей зближуються до %.2f мм" % mind)
    # --- 1) виточки
    zr = TOP - R["recess"]["depth_mm"]; recs = []
    for c in F["circles"]:
        cx, cy, r = c["c"]; d, bolt = recess_final_d(c, F["items"], "виточка Ø%.1f біля (%.0f; %.0f)" % (2*r, cx, cy))
        if abs(2*r - d) > 0.05: RD.append((round(2*r, 1), d))
        X, Y = T((cx, cy)); recs.append((X, Y, d / 2 - TR))
    for X, Y, rf in nn(recs, lambda t: (t[0], t[1])):
        radii = []; rr = rf
        while rr > 0.05: radii.append(rr); rr -= R["recess"]["stepover_mm"]
        pts = []
        for rad in radii[::-1]:
            n = max(12, math.ceil(math.pi / math.acos(1 - SAG/rad)))
            for k in range(n+1): pts.append((X + rad*math.cos(-math.pi/2 + 2*math.pi*k/n), Y + rad*math.sin(-math.pi/2 + 2*math.pi*k/n)))
        begin(pts[0][0], pts[0][1], zr); out.append("G1 X%s Y%s  F%d" % (f3(pts[1][0]), f3(pts[1][1]), R["recess"]["feed"]))
        out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in pts[2:]]
    # --- 1б) потай навколо овальних пазів (--slot-recess): глибина як у виточок, від паза назовні, два проходи
    nsr = 0
    if SR:
        HL, HW = SR[0] / 2, SR[1] / 2; srl = []
        for kind, it in F["through"]:
            if kind != "паз": continue
            c, u, ha, hb = min_rect(Tarr(it["pts"][:-1]))
            if abs(2*ha - 38) > 0.3 or abs(2*hb - 12) > 0.3: raise Err("паз %.1f x %.1f: потай розрахований лише для 12 x 38" % (2*ha, 2*hb))
            edge = stadium(c, u, HL - HW, HW)
            for o in F["outlines"]:
                O = simplify_closed(Tarr(o["pts"]), 0.02)
                if pip(c, O) and min(dist_to_poly(p, O) for p in edge) < 3.0: raise Err("потай ближче 3 мм до краю деталі")
            for X, Y, rf0 in recs:
                if min(math.hypot(q[0]-X, q[1]-Y) for q in edge) < rf0 + TR - 0.05: print("УВАГА: потай перетинає виточку біля (%.0f; %.0f)" % (X, Y))
            for pk in F["pockets"]:
                Pk = Tarr(pk["pts"][:-1])
                if any(pip(q, Pk) for q in edge) or min(dist_to_poly(q, Pk) for q in edge) < 1.0: print("УВАГА: потай впритул або перетинає виборку біля (%.0f; %.0f)" % (c[0], c[1]))
            srl.append((c, u, ha, hb))
        for c, u, ha, hb in nn(srl, lambda t: (t[0][0], t[0][1])):
            P1 = stadium(c, u, ha - hb, hb + TR); P2 = stadium(c, u, (HL - HW), HW - TR)
            begin(P1[0][0], P1[0][1], zr); out.append("G1 X%s Y%s  F%d" % (f3(P1[1][0]), f3(P1[1][1]), R["pocket"]["feed"]))
            out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in P1[2:]] + ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in P2]
            nsr += 1
    # --- 1в) овальні виточки з DXF (овал з отвором усередині, правило 27.10.2026): глибина як у виточок,
    #         ширина за recess.nominal_to_final_diameter_mm, концентричні овали від центру назовні, проти годинникової
    ovr = []
    for it in F["oval_recess"]:
        c, u, a, b = min_rect(Tarr(it["pts"][:-1])); d, bolt = recess_final_d(it, F["items"], "овальна виточка %.1f x %.1f" % (2*a, 2*b))
        if abs(2*b - d) > 0.05: RD.append((round(2*b, 1), d))
        rf = d / 2 - TR; radii = []; rr = rf
        while rr > 0.05: radii.append(rr); rr -= R["recess"]["stepover_mm"]
        pts = np.vstack([stadium(c, u, a - b, rad) for rad in radii[::-1]]); ovr.append((c, pts))
    for c, pts in nn(ovr, lambda t: (t[0][0], t[0][1])):
        begin(pts[0][0], pts[0][1], zr); out.append("G1 X%s Y%s  F%d" % (f3(pts[1][0]), f3(pts[1][1]), R["recess"]["feed"]))
        out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in pts[2:]]
    recs = list(recs) + [None] * (nsr + len(ovr))  # лічильник для перевірки і M0; None далі не використовується
    # --- пауза M0 після виточок і потаю навколо овальних пазів, перед виборками (правило від 26.10.2026, друга редакція)
    if recs and (F["pockets"] or F["arc_pockets"] or F.get("poly_pockets") or parts): out += ["G0   Z%s" % f3(ZS), "M0"]
    # --- 2) прямокутні виборки (після M0)
    zp = TOP - R["pocket"]["depth_mm"]
    for pk in F["pockets"]:
        c, u, a, b = min_rect(Tarr(pk["pts"][:-1])); v = np.array([-u[1], u[0]]); hl, hw = a - TR, b - TR
        if hw < 0.05: raise Err("виборка вужча за фрезу")
        loops = []; o = 0.0
        while hw - o >= -1e-9 and hl - o >= -1e-9:
            A_, B_ = hl - o, hw - o; loops.append([c + u*A_ + v*B_, c - u*A_ + v*B_, c - u*A_ - v*B_, c + u*A_ - v*B_]); o += R["pocket"]["stepover_mm"]
        seq = []
        for lp in loops[::-1]: seq += lp + [lp[0]]  # старт з внутрішнього контуру, назовні; стінка виборки фрезерується останньою
        begin(seq[0][0], seq[0][1], zp); out.append("G1 X%s Y%s  F%d" % (f3(seq[1][0]), f3(seq[1][1]), R["pocket"]["feed"]))
        out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in seq[2:]]
    # --- 2б) дугові виборки ПСГ (правило 27.10.2026): глибина як у виборки, стінка = дуга всередині деталі,
    #         з боку відходу не далі траєкторії контуру; старт із середини, обходи назовні, стінка останньою
    for bd in F["arc_pockets"]:
        c = np.array(T(bd["c"])); rw, rf = bd["rw"], bd["rf"]; sg = 1.0 if rf > rw else -1.0
        ends = [(np.array(T(a)), np.array(T(b))) for a, b in bd["ends"]]; m = np.array(T(bd["m"]))
        O = simplify_closed(Tarr(bd["owner"]["pts"]), 0.02); lim = R["contour"]["offset_outside_mm"]
        ok = lambda q: pip(q, O) or dist_to_poly(q, O) <= lim + 1e-6
        ang = lambda q: math.atan2(q[1] - c[1], q[0] - c[0])
        ta, tb = ang(ends[0][0]), ang(ends[1][0]); dl = (tb - ta + math.pi) % (2*math.pi) - math.pi
        rmax = abs(rf - rw) - TR
        for t in np.linspace(ta, ta + dl, 41)[1:-1]:
            u_ = np.array([math.cos(t), math.sin(t)]); lo, hi = TR, abs(rf - rw) - TR
            if not ok(c + (rw + sg*lo)*u_): raise Err("дугова виборка: стінка не всередині деталі")
            if ok(c + (rw + sg*hi)*u_): continue
            for _ in range(40):
                md = (lo + hi) / 2
                if ok(c + (rw + sg*md)*u_): lo = md
                else: hi = md
            rmax = min(rmax, lo)
        BW = rmax - TR
        if BW < 0: raise Err("дугова виборка вужча за фрезу")
        def theta(rho, q, k):
            A, B = ends[k]; t_ = (B - A) / np.hypot(*(B - A)); n_ = np.array([-t_[1], t_[0]])
            if n_ @ (m - A) < 0: n_ = -n_
            kk = (n_ @ A + q - n_ @ c) / rho
            if abs(kk) > 1: raise Err("дугова виборка: торець не перетинає дугу")
            ph = math.atan2(n_[1], n_[0]); te = ang(A); best = None
            for sol in (ph + math.acos(kk), ph - math.acos(kk)):
                dd = abs((sol - te + math.pi) % (2*math.pi) - math.pi)
                if best is None or dd < best[0]: best = (dd, sol)
            return best[1]
        def span(rho, q):
            t0 = theta(rho, q, 0); t1 = theta(rho, q, 1); return t0, t0 + ((t1 - t0 + math.pi) % (2*math.pi) - math.pi)
        rho = lambda s_: rw + sg*(TR + s_)
        ds = []; d_ = 0.0
        while 2*d_ <= BW + 1e-9: ds.append(d_); d_ += R["pocket"]["stepover_mm"]
        rc = rho(BW / 2); t0, t1 = span(rc, TR + ds[-1]); tm = (t0 + t1) / 2
        seq = arc_pts(c, rc, tm, t1) + arc_pts(c, rc, t1, t0)[1:]
        for d_ in ds[::-1]:
            ri, ro = rho(d_), rho(BW - d_); i0, i1 = span(ri, TR + d_); o0, o1 = span(ro, TR + d_)
            lp = arc_pts(c, ri, i0, i1) + arc_pts(c, ro, o1, o0); seq += lp + [lp[0]]
        begin(seq[0][0], seq[0][1], zp); out.append("G1 X%s Y%s  F%d" % (f3(seq[1][0]), f3(seq[1][1]), R["pocket"]["feed"]))
        out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in seq[2:]]
    # --- 2г) виборки ПСГ довільної форми (рішення користувача 05.11.2026): глибина pocket.depth_mm, еквідистанти контуру
    #         виборки з кроком pocket.stepover_mm від середини назовні, стінка (центр фрези на TR від контуру) останньою;
    #         центр фрези не далі траєкторії контуру деталі (offset_outside_mm від краю)
    for pp in nn(F.get("poly_pockets", []), lambda r_: tuple(T(r_["it"]["pts"][0]))):
        B = simplify_closed(Tarr(pp["it"]["pts"]), 0.02)
        if area(B) < 0: B = B[::-1]
        O = simplify_closed(Tarr(pp["owner"]["pts"]), 0.02); lim = R["contour"]["offset_outside_mm"]
        loops = []; d = TR
        while True:
            try: Bi = remove_loops(offset_ccw(B[::-1], d), -1)[::-1]
            except Err: break
            if len(Bi) < 3 or area(Bi) <= 0.5 or not all(pip(p, B) for p in Bi) or max(abs(dist_to_poly(p, B) - d) for p in Bi) > 0.03:
                if d == TR: raise Err("виборка ПСГ біля (%.0f; %.0f): офсет стінки не пройшов перевірку або виборка вужча за фрезу" % tuple(B[0]))
                break
            loops.append(Bi); d += R["pocket"]["stepover_mm"]
        def near(p):
            A_ = O; B_ = np.roll(O, -1, axis=0); AB = B_ - A_
            t = np.clip(np.sum((p - A_)*AB, axis=1) / np.maximum(np.sum(AB*AB, axis=1), 1e-12), 0, 1); Q = A_ + AB*t[:, None]
            i = int(np.argmin(np.hypot(*(Q - p).T))); return Q[i], float(np.hypot(*(Q[i] - p)))
        def clip(Lp):
            """центр фрези не далі lim від краю деталі (як траєкторія контуру), щоб не зачепити сусідню деталь"""
            res = []
            for p in Lp:
                if pip(p, O): res.append(p); continue
                q, dd = near(p); res.append(p if dd <= lim else q + (p - q) * (lim / dd))
            return np.array(res)
        loops = [clip(Lp) for Lp in loops]
        # покриття: кожна точка виборки, куди дістає центр фрези, у межах радіуса фрези від якогось обходу;
        # якщо ні (гострі кути паралелограма), додаються проміжні обходи на півкроку
        def undercut(lps):
            from matplotlib.path import Path as _P
            xs_ = np.arange(B[:, 0].min(), B[:, 0].max(), 0.4); ys_ = np.arange(B[:, 1].min(), B[:, 1].max(), 0.4)
            X_, Y_ = np.meshgrid(xs_, ys_); Q = np.column_stack([X_.ravel(), Y_.ravel()])
            def dmin(Q, P):
                P = np.vstack([P, P[:1]]); d_ = np.full(len(Q), np.inf)
                for p0, p1 in zip(P[:-1], P[1:]):
                    ab = p1 - p0; L2 = float(ab @ ab)
                    t = np.clip(((Q - p0) @ ab) / L2, 0, 1) if L2 > 1e-12 else np.zeros(len(Q))
                    d_ = np.minimum(d_, np.hypot(Q[:, 0] - p0[0] - t*ab[0], Q[:, 1] - p0[1] - t*ab[1]))
                return d_
            m = _P(B).contains_points(Q); Q = Q[m]
            m = dmin(Q, B) >= TR + 0.05; Q = Q[m]
            m = _P(O).contains_points(Q) | (dmin(Q, O) <= lim - 0.05); Q = Q[m]
            if not len(Q): return 0.0
            dl = np.min([dmin(Q, Lp) for Lp in lps], axis=0)
            return float((dl > TR + 0.05).sum()) * 0.16  # крок = діаметр фрези: дотик обходів допустимий, як у прямих виборках
        if undercut(loops) > 1.0:
            for k_ in range(1, len(loops)):
                Bi = remove_loops(offset_ccw(B[::-1], TR + (k_ - 0.5) * R["pocket"]["stepover_mm"]), -1)[::-1]
                if len(Bi) >= 3 and area(Bi) > 0.5 and all(pip(p, B) for p in Bi): loops.append(clip(Bi))
            u_ = undercut(loops)
            if u_ > 1.0:  # вузька смуга (05.11.2026, Гетьман 020206, 13,5 мм): стінки лишають зазор посередині, додаємо ще один проміжний обхід
                n0_ = len(loops); Bi = remove_loops(offset_ccw(B[::-1], TR + (n0_ - 0.5) * R["pocket"]["stepover_mm"]), -1)[::-1]
                if len(Bi) >= 3 and area(Bi) > 0.5 and all(pip(p, B) for p in Bi): loops.append(clip(Bi))
                u_ = undercut(loops)
            if u_ > 1.0: raise Err("виборка ПСГ біля (%.0f; %.0f): недоріз %.1f мм2" % (B[0][0], B[0][1], u_))
        loops.sort(key=lambda Lp: -min(dist_to_poly(p, B) for p in Lp))  # від середини назовні
        seq = []; cur = (B.min(axis=0) + B.max(axis=0)) / 2  # старт з внутрішнього обходу біля середини виборки
        for Lp in loops:
            Lp = Lp[:-1] if np.allclose(Lp[0], Lp[-1]) else Lp
            j = int(np.argmin(np.hypot(*(Lp - cur).T)))
            Lp = np.vstack([Lp[j:], Lp[:j], Lp[j:j+1]]); seq += list(Lp); cur = Lp[-1]
        begin(seq[0][0], seq[0][1], zp); out.append("G1 X%s Y%s  F%d" % (f3(seq[1][0]), f3(seq[1][1]), R["pocket"]["feed"]))
        out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in seq[2:]]
    # --- 2в) заглиблення під сталеві компоненти (15 мм, правило 03.11.2026): глибина support_recess.depth_mm;
    #         спершу растр (лінії вздовж довшої сторони, крок stepover), потім стінки: зовнішня і острівців, останніми
    nsup = 0
    if F.get("support"):
        SRc = R["support_recess"]; zs_ = TOP - SRc["depth_mm"]
        for rg in nn(F["support"], lambda r_: tuple(T(r_["it"]["pts"][0])) if OPT else tuple(r_["it"]["pts"][0])):
            B = simplify_closed(Tarr(rg["it"]["pts"]), 0.02)
            if area(B) < 0: B = B[::-1]
            Bi = remove_loops(offset_ccw(B[::-1], TR), -1)[::-1]  # усередину на радіус фрези, проти годинникової
            if max(abs(dist_to_poly(p, B) - TR) for p in Bi) > 0.03 or not all(pip(p, B) for p in Bi): raise Err("заглиблення: офсет стінки не пройшов перевірку")
            Is = []; Iw = []
            for isl in rg["islands"]:
                I = simplify_closed(Tarr(isl["pts"]), 0.02)
                if area(I) < 0: I = I[::-1]
                Io = remove_loops(offset_ccw(I, TR), 1)
                if max(abs(dist_to_poly(p, I) - TR) for p in Io) > 0.03: raise Err("заглиблення: офсет острівця не пройшов перевірку")
                if not all(pip(p, Bi) for p in Io): raise Err("заглиблення: між острівцем і стінкою менше за діаметр фрези")
                Is.append(Io); Iw.append(I)
            polys = [Bi] + Is
            if SRc.get("strategy", "offset") == "raster":
                def inreg(q, tol=0.05):
                    if not (pip(q, Bi) or dist_to_poly(q, Bi) < tol): return False
                    return all((not pip(q, I)) or dist_to_poly(q, I) < tol for I in Is)
                bx, by = np.ptp(Bi[:, 0]), np.ptp(Bi[:, 1]); ax_ = 1 if bx >= by else 0  # 1: лінії Y=const
                lo_, hi_ = Bi[:, ax_].min(), Bi[:, ax_].max(); n_ = max(1, math.ceil((hi_ - lo_ - 1.0) / SRc["stepover_mm"]))
                lines = []
                for k_ in range(n_ + 1):
                    t_ = lo_ + 0.5 + (hi_ - lo_ - 1.0) * k_ / n_; xs = []
                    for P in polys:
                        Q = np.roll(P, -1, axis=0)
                        for p0, p1 in zip(P, Q):
                            if (p0[ax_] > t_) != (p1[ax_] > t_):
                                xs.append(p0[1-ax_] + (t_ - p0[ax_]) * (p1[1-ax_] - p0[1-ax_]) / (p1[ax_] - p0[ax_]))
                    xs.sort()
                    for j in range(0, len(xs) - 1, 2):
                        if xs[j+1] - xs[j] > 0.01:
                            A_ = [0, 0]; B_ = [0, 0]; A_[ax_] = B_[ax_] = t_; A_[1-ax_] = xs[j]; B_[1-ax_] = xs[j+1]
                            lines.append((k_, np.array(A_), np.array(B_)))
                def link_ok(p, q):
                    if math.hypot(*(q - p)) > 2.5 * SRc["stepover_mm"]: return False
                    return all(inreg(p + (q - p) * s_) for s_ in np.linspace(0, 1, 12)[1:-1])
                left = list(range(len(lines))); cur = None; chains = []
                while left:
                    ref = cur if cur is not None else np.array([0.0, 0.0])
                    j = min(left, key=lambda i: min(math.hypot(*(lines[i][1] - ref)), math.hypot(*(lines[i][2] - ref))))
                    left.remove(j); k_, A_, B_ = lines[j]
                    if math.hypot(*(B_ - ref)) < math.hypot(*(A_ - ref)): A_, B_ = B_, A_
                    ch = [A_, B_]; cur = B_; kk = k_
                    while True:
                        cand = [i for i in left if abs(lines[i][0] - kk) == 1]
                        best = None
                        for i in cand:
                            for e0, e1 in ((lines[i][1], lines[i][2]), (lines[i][2], lines[i][1])):
                                d_ = math.hypot(*(e0 - cur))
                                if (best is None or d_ < best[0]) and link_ok(cur, e0): best = (d_, i, e0, e1)
                        if best is None: break
                        _, i, e0, e1 = best; left.remove(i); ch += [e0, e1]; cur = e1; kk = lines[i][0]
                    chains.append(ch)
                for ch in chains:
                    begin(ch[0][0], ch[0][1], zs_); out.append("G1 X%s Y%s  F%d" % (f3(ch[1][0]), f3(ch[1][1]), SRc["feed"]))
                    out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in ch[2:]]; nsup += 1
            else:
                # зміщення (рішення користувача 04.11.2026): проходи паралельно стінкам, від середини назовні
                chains = offset_pocket(B, Iw, SRc["stepover_mm"])
                if OPT:
                    nsup += emit_linked(chains, OP_DEBUG["levels"], [Bi] + Is, zs_, SRc["feed"], (B, Iw)); continue
                for ch in chains:
                    begin(ch[0][0], ch[0][1], zs_); out.append("G1 X%s Y%s  F%d" % (f3(ch[1][0]), f3(ch[1][1]), SRc["feed"]))
                    out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in ch[2:]]; nsup += 1
            for P in [Bi] + Is:  # стінки останніми
                S = P[0]; begin(S[0], S[1], zs_); out.append("G1 X%s Y%s  F%d" % (f3(P[1][0]), f3(P[1][1]), SRc["feed"]))
                out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in P[2:]] + ["X%s Y%s " % (f3(S[0]), f3(S[1]))]; nsup += 1
    # --- 3) контури
    zc = R["contour"]["z_levels_mm"]
    for P in nn(parts, lambda t: tuple(t[0])):
        S, V1 = P[0], P[1]; begin(S[0], S[1], TOP)
        for zi, z in enumerate(zc):
            out.append(("G1 X%s Y%s Z%s F%d" % (f3(V1[0]), f3(V1[1]), f3(z), R["contour"]["feed"])) if zi == 0 else ("X%s Y%s Z%s" % (f3(V1[0]), f3(V1[1]), f3(z))))
            out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in P[2:]]; out.append("X%s Y%s " % (f3(S[0]), f3(S[1])))
        out.append("X%s Y%s " % (f3(V1[0]), f3(V1[1])))
    # --- 4) наскрізні: квадрати і пази
    zb = R["through"]["bottom_z_mm"]; thr = []
    for kind, it in F["through"]:
        c, u, a, b = min_rect(Tarr(it["pts"][:-1])); v = np.array([-u[1], u[0]]); hl, hw = a - TR, b - TR
        if kind == "паз":
            # овальний паз фрезерується овалом за DXF: центр фрези на радіус фрези всередині (правило 27.10.2026)
            if hw < 0.05: raise Err("паз вужчий за фрезу")
            thr.append((kind, stadium(c, u, a - b, b - TR))); continue
        hl, hw = sq_final(2*a) / 2 - TR, sq_final(2*b) / 2 - TR
        if hl < 0.05 or hw < 0.05: raise Err("%s менший за фрезу" % kind)
        C = np.array([c + u*hl + v*hw, c - u*hl + v*hw, c - u*hl - v*hw, c + u*hl - v*hw])
        ang = np.degrees(np.arctan2(C[:, 1]-c[1], C[:, 0]-c[0])); st_ = int(np.argmin(np.abs(((ang - 45 + 180) % 360) - 180)))
        C = np.vstack([C[st_:], C[:st_]]); thr.append((kind, np.vstack([C, C[:1]])))
    for kind, C in nn(thr, lambda t: tuple(t[1][0])):
        S = C[0]; begin(S[0], S[1], TOP); seq = list(C[1:])
        per = sum(math.hypot(*(seq[i] - (C[0] if i == 0 else seq[i-1]))) for i in range(len(seq))); done = 0.0; prev = C[0]
        for i, p in enumerate(seq):
            done += math.hypot(*(p - prev)); prev = p; z = TOP - (TOP - zb) * done / per
            out.append(("G1 X%s Y%s Z%s F%d" % (f3(p[0]), f3(p[1]), f3(z), R["through"]["feed"])) if i == 0 else ("X%s Y%s Z%s" % (f3(p[0]), f3(p[1]), f3(z))))
        out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in seq]
    # --- 5) обрізка неповного листа (15 мм, правило 03.11.2026): наскрізний різ поперек листа останньою операцією,
    #         центр фрези на gap + радіус фрези від найближчого краю деталей; лише якщо залишок не менший за min_remnant_mm
    trims = []
    if R.get("trim") and parts:
        TRm = R["trim"]; ys_ = np.concatenate([Tarr(o["pts"])[:, 1] for o in F["outlines"]])
        yc = ys_.max() + TRm["gap_mm"] + TR
        if L - (yc + TR) >= TRm["min_remnant_mm"]: trims.append((yc, L - (yc + TR)))
        yc = ys_.min() - TRm["gap_mm"] - TR
        if yc - TR >= TRm["min_remnant_mm"]: trims.append((yc, yc - TR))
        for yc, rem in trims:
            ov = TRm["overrun_mm"]; xa, xb = -ov, W + ov; begin(xa, yc, TRm["z_levels_mm"][0])
            for zi, z in enumerate(TRm["z_levels_mm"]):
                if zi: out.append("G1   Z%s F%d" % (f3(z), FP))
                out.append("G1 X%s Y%s  F%d" % (f3(xb), f3(yc), TRm["feed"])); xa, xb = xb, xa
            TRIMS.append((round(yc, 1), round(rem)))
    out += ["G0   Z%s" % f3(ZS), "G0 X0.000 Y0.000 ", "G0Z20.000", "X0.000Y0.000", "M05", "M02", "%"]
    info = {"W": W, "L": L, "recess": len(recs), "pockets": len(F["pockets"]) + len(F["arc_pockets"]) + len(F.get("poly_pockets", [])), "support": nsup, "trim": len(trims), "contours": len(parts), "through": len(thr), "min_gap": mind, "m0": 1 if (recs and (F["pockets"] or F["arc_pockets"] or F.get("poly_pockets") or parts)) else 0}
    return out, info

# ---------- незалежна перевірка готового файлу ----------
def verify(path, info):
    L = [l.strip() for l in open(path, "rb").read().decode("ascii").split("\r\n") if l.strip()]
    segs = []; cur = None; x = y = z = None
    for ln in L:
        if ln == "%": continue
        if ln == "M0":
            if cur: segs.append(cur)
            cur = None; segs.append("M0"); continue
        if re.match(r"^G0\s*Z20", ln):
            if cur: segs.append(cur)
            cur = None; continue
        mx = re.search(r"X(-?[\d.]+)", ln); my = re.search(r"Y(-?[\d.]+)", ln); mz = re.search(r"Z(-?[\d.]+)", ln)
        if mx: x = float(mx.group(1))
        if my: y = float(my.group(1))
        if mz: z = float(mz.group(1))
        if ln.startswith("G0 X"): cur = [(x, y, z)]; continue
        if cur is not None and (mx or my): cur.append((x, y, z))
    if cur: segs.append(cur)
    segs = [s for s in segs if s == "M0" or (len(s) >= 2 and any(p[2] is not None and abs(p[2] - ZS) > 1e-6 for p in s[1:]))]
    m0n = segs.count("M0"); kinds = []
    cnt = {"recess": 0, "pockets": 0, "support": 0, "contours": 0, "through": 0, "trim": 0}; bad = []
    zsr = round(TOP - R["support_recess"]["depth_mm"], 3) if R.get("support_recess") else None
    ztr = {round(v, 3) for v in R["trim"]["z_levels_mm"]} if R.get("trim") else None
    def sa(p): return 0.5 * sum(p[i][0]*p[i+1][1] - p[i+1][0]*p[i][1] for i in range(len(p)-1))
    zr = TOP - R["recess"]["depth_mm"]; zp = TOP - R["pocket"]["depth_mm"]; zc = R["contour"]["z_levels_mm"]; zb = R["through"]["bottom_z_mm"]
    for s in segs:
        if s == "M0": kinds.append("M0"); continue
        zs = {round(p[2], 3) for p in s[1:]}
        if zsr is not None and zs == {zsr}: kinds.append("s"); cnt["support"] += 1
        elif ztr is not None and zs == ztr:
            kinds.append("x"); cnt["trim"] += 1
            if min(p[0] for p in s) > 0 or max(p[0] for p in s) < info["W"]: bad.append("обрізка не перетинає лист повністю")
            if len({round(p[1], 3) for p in s}) != 1: bad.append("обрізка не пряма")
        elif zs == {round(zr, 3)}: kinds.append("r"); cnt["recess"] += 1
        elif zs == {round(zp, 3)}:
            kinds.append("p"); cnt["pockets"] += 1; bx = (min(p[0] for p in s) + max(p[0] for p in s)) / 2; by = (min(p[1] for p in s) + max(p[1] for p in s)) / 2
            if math.hypot(s[0][0]-bx, s[0][1]-by) >= math.hypot(s[-1][0]-bx, s[-1][1]-by) - 1e-6: bad.append("виборка починається не з внутрішньої частини")
        elif zs <= {round(v, 3) for v in zc}:
            kinds.append("c"); cnt["contours"] += 1; loop = [p for p in s if round(p[2], 3) == round(zc[0], 3)]
            if sa(loop + [loop[0]]) >= 0: bad.append("контур не за годинниковою")
            if {round(v, 3) for v in zc} != zs: bad.append("контур без обох рівнів Z")
        elif min(zs) == round(zb, 3):
            kinds.append("t"); cnt["through"] += 1; q = [p for p in s if abs(p[2] - zb) < 1e-3]
            if sa(q + [q[0]]) <= 0: bad.append("наскрізний не проти годинникової")
        else: bad.append("невідомий рівень Z %s" % sorted(zs)); kinds.append("?")
    if m0n != info["m0"]: bad.append("M0: у файлі %d, очікується %d" % (m0n, info["m0"]))
    rk = {"r": 0, "p": 1, "s": 1, "c": 2, "t": 3, "x": 4, "?": 9}; seq_ = [k for k in kinds if k != "M0"]
    if [rk[k] for k in seq_] != sorted(rk[k] for k in seq_): bad.append("порядок операцій не такий: виточки/потай, M0, виборки, контури, наскрізні")
    if m0n:
        i = kinds.index("M0")
        if not kinds[:i] or set(kinds[:i]) != {"r"} or not kinds[i+1:] or "r" in kinds[i+1:]: bad.append("M0 стоїть не між виточками/потаєм і виборками/контурами")
    segs = [s for s, k in zip([s for s in segs if s != "M0"], seq_) if k != "x"]
    xs = [p[0] for s in segs for p in s]; ys = [p[1] for s in segs for p in s]
    if min(xs) < 0 or max(xs) > info["W"] or min(ys) < 0 or max(ys) > info["L"]: bad.append("траєкторія виходить за лист")
    for k in cnt:
        if cnt[k] != info[k]: bad.append("%s: у файлі %d, у DXF %d" % (k, cnt[k], info[k]))
    return cnt, bad, (min(xs), max(xs), min(ys), max(ys))

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("task"); ap.add_argument("sheets", nargs="*"); ap.add_argument("--all", action="store_true"); ap.add_argument("--out"); ap.add_argument("--slot-recess", help="потай навколо овальних пазів 12x38, ДxШ мм, напр. 55x30 (глибина як у виточок)"); ap.add_argument("--holes-only", action="store_true", help="лише виточки і квадратні отвори, без виборок, контурів і пазів")
    ap.add_argument("--no-opt", action="store_true", help="без оптимізації холостих ходів: порядок як до 06.11.2026 (найближчий від (0;0), заглиблення з підйомом між обходами); для регресії й порівняння")
    ap.add_argument("--opt-level-order", action="store_true", help="оптимізація, але обходи заглиблень 15 мм рівень за рівнем від середини до стінок (варіант --opt від 06.11.2026)")
    ap.add_argument("--trim", action="store_true", help="обрізка неповного листа і для 12 мм (рішення користувача 05.11.2026): як у 15 мм, рівні = рівні контуру + дно наскрізних")
    a = ap.parse_args()
    global SR, OPT
    # типово (рішення користувача 06.11.2026): оптимізація холостих ходів, обходи заглиблень 15 мм у будь-якому порядку (найближчий), стінки останніми
    OPT = False if a.no_opt else (True if a.opt_level_order else "any")
    if a.slot_recess: l_, w_ = (float(v) for v in a.slot_recess.lower().replace("х", "x").split("x")); SR = (l_, w_)
    nest = os.path.join(a.task, "nesting"); rep = json.load(open(os.path.join(nest, "report.json"), encoding="utf-8"))
    mats = {}; outn = {}
    omap = {}; dt = ""; jmat = {}
    jp = os.path.join(a.task, "jobs.json")
    if os.path.exists(jp):
        jj = json.load(open(jp, encoding="utf-8")); d_ = jj.get("date", "")
        dt = (d_[8:10] + d_[5:7]) if len(d_) >= 10 else ""
        seqs = {}
        for g_ in jj["groups"]:
            jmat[g_["material"]] = g_
            for p_ in g_["parts"]:
                omap[(g_.get("id"), g_["material"], _key(p_["name"]))] = [str(o) for o in p_.get("orders", [])]
                sp_ = p_.get("orders_split")
                if sp_: seqs[(g_.get("id"), g_["material"], _key(p_["name"]))] = [str(o) for o, q in sp_.items() for _ in range(int(q))]
    # замовлення саме цього листа: штуки виробу розподіляються по orders_split послідовно в порядку листів,
    # так само як у nest_pdf.py (виправлено 30.10.2026: раніше в ім'я йшли всі замовлення виробу)
    used = {}  # ключ (матеріал, виріб): той самий виріб у UA і TIVAR має різні замовлення (виправлено 03.11.2026)
    for g in rep["groups"]:
        for s in g["sheets"]:
            som = {}
            for n, q in s.get("parts", {}).items():
                k = _key(n); gk = (g.get("id"), g["material"], k); sq = seqs.get(gk)  # + id групи (11.11.2026): дві групи з однаковим матеріалом (лист 2280 і 3000)
                if sq:
                    i0 = used.get(gk, 0); used[gk] = i0 + int(q); som[k] = sorted(set(sq[i0:i0 + int(q)]))
                else: som[k] = omap.get(gk, [])
            mats[s["file"]] = g["material"]; outn[s["file"]] = cnc_name(s["file"], g["material"], s.get("parts", {}), s.get("stock", {}), som, dt)
    names = [f for f in mats] if a.all else [s if s.endswith(".dxf") else s + ".dxf" for s in a.sheets]
    if not names: print("вкажіть лист або --all"); return 2
    rc = 0
    for f in names:
        try:
            th = int(re.search(r"Лист пластиковий (\d+)", mats.get(f, "")).group(1))
            if th not in BASE["supported_thickness_mm"]: raise Err("товщина %d мм: правила CNC для неї не задано (є лише %s)" % (th, BASE["supported_thickness_mm"]))
            set_profile(th)
            if a.trim and not R.get("trim"):
                R["trim"] = copy.deepcopy(BASE["by_thickness"]["15"]["trim"])
                R["trim"]["z_levels_mm"] = list(R["contour"]["z_levels_mm"]) + [R["through"]["bottom_z_mm"]]
            if R.get("require_mirrored"):
                g_ = jmat.get(mats[f])
                if not g_ or not all(p_.get("mirrored") for p_ in g_["parts"]): raise Err("товщина %d мм: DXF деталей не віддзеркалено (tools/mirror_dxf.py до нестингу)" % th)
            if R.get("forbid_mirrored"):
                g_ = jmat.get(mats[f])
                if g_ and (g_.get("mirrored") or any(p_.get("mirrored") for p_ in g_["parts"])): raise Err("товщина %d мм: DXF віддзеркалено, а з 06.11.2026 DXF КД 15 мм уже з тильного боку; дзеркало дає R замість L. Переробити jobs.json (make_jobs.py) і нестинг без mirror_dxf.py" % th)
            RD.clear(); TRIMS.clear(); items_ = read_sheet(os.path.join(nest, f))
            F = classify15(items_) if R.get("support_recess") else classify(items_)
            if a.holes_only: F["pockets"] = []; F["arc_pockets"] = []; F["poly_pockets"] = []; F["outlines"] = []; F["support"] = []; F["through"] = [t for t in F["through"] if t[0] == "квадрат"]
            lines, info = build(F, th)
            nm = outn.get(f, os.path.splitext(f)[0] + ".cnc")
            if a.holes_only: nm = nm[:-4] + "_HOLES.cnc"
            if SR: nm = nm[:-4] + "_SLOTREC.cnc"
            dst = os.path.join(a.out or nest, nm); os.makedirs(os.path.dirname(dst), exist_ok=True)
            open(dst, "wb").write(("\r\n".join(lines) + "\r\n").encode("ascii"))
            cnt, bad, bb = verify(dst, info)
            print("%s %s: рядків %d; виточок %d, виборок %d, контурів %d (по 2 рівні), наскрізних %d; мін. зазор між траєкторіями %.2f мм; X %.0f..%.0f Y %.0f..%.0f" % ("ПОМИЛКА" if bad else "OK", os.path.basename(dst), len(lines), cnt["recess"], cnt["pockets"], cnt["contours"], cnt["through"], info["min_gap"], bb[0], bb[1], bb[2], bb[3]))
            if RD:
                from collections import Counter
                print("   виточки за болтом (DXF -> кінцевий): " + ", ".join("Ø%g->Ø%g x%d" % (k[0], k[1], v) for k, v in sorted(Counter(RD).items())))
            if R.get("trim") and not R.get("support_recess"):
                print("   обрізка листа: %s" % (", ".join("різ Y%.1f, залишок %d мм" % t for t in TRIMS) if TRIMS else "немає (залишок < %d мм або лист повний)" % R["trim"]["min_remnant_mm"]))
            if R.get("support_recess"):
                print("   заглиблень під сталь (обходів %s і стінок): %d; обрізка листа: %s" % ("растру" if R.get("support_recess",{}).get("strategy","offset")=="raster" else "зміщення", cnt["support"], ", ".join("різ Y%.1f, залишок %d мм" % t for t in TRIMS) if TRIMS else "немає (лист повний)"))
            for b in bad: print("   ", b); rc = 1
        except Err as e: print("ПОМИЛКА %s: %s" % (f, e)); rc = 1
    return rc
if __name__ == "__main__": sys.exit(main())
