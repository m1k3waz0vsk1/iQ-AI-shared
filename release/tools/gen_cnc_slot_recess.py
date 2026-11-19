#!/usr/bin/env python3
"""Окрема програма ЧПУ: потай (виборка) навколо овальних пазів 12x38 на готовому листі нестингу.
python3 gen_cnc_slot_recess.py <папка завдання> лист_A_1 [--length 55 --width 30 --depth 2]
Координати ті самі, що в основній програмі листа (нуль XY, Z0 = низ листа, верх Z12). Залежить від gen_cnc.py."""
import sys, os, math, argparse
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_cnc as g
R, TR, TOP, ZS, FP, f3 = g.R, g.TR, g.TOP, g.ZS, g.FP, g.f3

def stadium(c, u, s, r, n_arc=None):
    """контур 'стадіон' проти годинникової: центр c, вісь u, пів-прямої s, радіус r; старт у середині нижньої сторони"""
    v = np.array([-u[1], u[0]]); n = n_arc or max(16, math.ceil(math.pi / math.acos(1 - g.SAG / r)))
    pts = [c - r*v]
    for k in range(n + 1):
        th = -math.pi/2 + math.pi*k/n; pts.append(c + s*u + r*(math.cos(th)*u + math.sin(th)*v))
    for k in range(n + 1):
        th = math.pi/2 + math.pi*k/n; pts.append(c - s*u + r*(math.cos(th)*u + math.sin(th)*v))
    pts.append(c - r*v)
    return np.array(pts)

def in_stadium(P, c, u, s, r):
    v = np.array([-u[1], u[0]]); d = P - c; x = d @ u; y = d @ v
    xe = np.clip(x, -s, s); return np.hypot(x - xe, y) <= r

def in_roundrect(P, c, u, hl, hw, rc):
    v = np.array([-u[1], u[0]]); d = P - c; x = np.abs(d @ u); y = np.abs(d @ v)
    qx = np.maximum(x - (hl - rc), 0); qy = np.maximum(y - (hw - rc), 0)
    return (x <= hl) & (y <= hw) & (np.hypot(qx, qy) <= rc)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("task"); ap.add_argument("sheet")
    ap.add_argument("--length", type=float, default=55.0); ap.add_argument("--width", type=float, default=30.0); ap.add_argument("--depth", type=float, default=2.0)
    a = ap.parse_args(); nest = os.path.join(a.task, "nesting"); sh = a.sheet if a.sheet.endswith(".dxf") else a.sheet + ".dxf"
    F = g.classify(g.read_sheet(os.path.join(nest, sh)))
    x0, y0, x1, y1 = F["sheet"]; sx, sy = x1 - x0, y1 - y0
    T = (lambda p: (y1 - p[1], p[0] - x0)) if sx > sy else (lambda p: (p[0] - x0, p[1] - y0)); W, L = (sy, sx) if sx > sy else (sx, sy)
    Tarr = lambda P: np.array([T(p) for p in P])
    slots = []
    for kind, it in F["through"]:
        if kind != "паз": continue
        c, u, ha, hb = g.min_rect(Tarr(it["pts"][:-1])); slots.append((c, u, ha, hb))
    if not slots: print("ПОМИЛКА: пазів немає"); return 1
    # інші елементи (для перевірки зіткнень) і контури деталей
    others = []
    for ci in F["circles"]:
        cx, cy, r = ci["c"]; others.append(("виточка", np.array(T((cx, cy))), (R["recess"]["nominal_to_final_diameter_mm"][str(int(round(2*r)))]) / 2))
    outl = [g.simplify_closed(Tarr(o["pts"]), 0.02) for o in F["outlines"]]
    HL, HW = a.length / 2, a.width / 2; zr = TOP - a.depth
    rf, sf = HW - TR, HL - HW          # фінальний прохід: центр фрези
    out = ["%", "G90", "G49", "M3 S%d" % R["spindle_rpm"]]; first = True; report = []
    # порядок: найближчий сусід від (0;0)
    left = list(slots); cur = np.array([0.0, 0.0]); order = []
    while left:
        j = min(range(len(left)), key=lambda i: float(np.hypot(*(left[i][0] - cur)))); s_ = left.pop(j); order.append(s_); cur = s_[0]
    allpaths = []
    for c, u, ha, hb in order:
        if abs(ha*2 - 38) > 0.3 or abs(hb*2 - 12) > 0.3: print("УВАГА: паз %.1f x %.1f, очікувалось 38 x 12" % (2*ha, 2*hb))
        ri, si = hb + TR, ha - hb          # внутрішній прохід: фреза торкається стінки паза
        P1 = stadium(c, u, si, ri); P2 = stadium(c, u, sf, rf)
        # перевірки
        edge = stadium(c, u, HL - HW, HW)
        for p in edge:
            if not any(g.pip(p, o) for o in outl): raise g.Err("потай виходить за контур деталі")
        md = min(g.dist_to_poly(p, o) for p in edge for o in outl)
        if md < 3.0: raise g.Err("потай ближче 3 мм до краю деталі (%.2f)" % md)
        for kind, oc, orr in others:
            d = float(np.hypot(*(in_ := (oc - c)))); 
            dm = min(float(np.hypot(*(q - oc))) for q in edge) - orr
            if dm < 0: print("УВАГА: потай перетинає %s (на %.1f мм)" % (kind, -dm))
        report.append((c, md))
        if first: out.append("G0 X%s Y%s Z%s" % (f3(P1[0][0]), f3(P1[0][1]), f3(ZS))); first = False
        else: out.extend(["G0   Z%s" % f3(ZS), "G0 X%s Y%s " % (f3(P1[0][0]), f3(P1[0][1]))])
        out.append("G1   Z%s F%d" % (f3(zr), FP))
        out.append("G1 X%s Y%s  F%d" % (f3(P1[1][0]), f3(P1[1][1]), R["pocket"]["feed"]))
        out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in P1[2:]]
        out += ["X%s Y%s " % (f3(p[0]), f3(p[1])) for p in P2]
        allpaths.append((c, u, ha, hb, np.vstack([P1, P2])))
    out += ["G0   Z%s" % f3(ZS), "G0 X0.000 Y0.000 ", "G0Z20.000", "X0.000Y0.000", "M05", "M02", "%"]
    # перевірка покриття: розтин фрезою vs цільова зона (потай мінус реальний отвір паза)
    for c, u, ha, hb, path in allpaths:
        xs = np.arange(c[0] - 40, c[0] + 40, 0.25); ys = np.arange(c[1] - 40, c[1] + 40, 0.25)
        G = np.array([(x, y) for x in xs for y in ys])
        # щільні точки шляху
        dense = []
        for i in range(len(path) - 1):
            d = path[i+1] - path[i]; n = max(1, int(math.hypot(*d) / 0.2)); dense += [path[i] + d*k/n for k in range(n)]
        dense = np.array(dense); swept = np.zeros(len(G), bool)
        for k in range(0, len(G), 4000):
            B = G[k:k+4000]; swept[k:k+4000] = (np.hypot(B[:, 0:1] - dense[:, 0][None], B[:, 1:2] - dense[:, 1][None]).min(axis=1) <= TR)
        target = in_stadium(G, c, u, HL - HW, HW) & ~in_roundrect(G, c, u, 19.0, 6.0, 3.0)
        miss = (target & ~swept).sum() * 0.0625; over = (swept & ~in_stadium(G, c, u, HL - HW, HW) & ~in_roundrect(G, c, u, 19.0, 6.0, 3.0)).sum() * 0.0625
        print("  потай (%.1f; %.1f): недофрезеровано %.2f мм2, зайве за межами %.2f мм2" % (c[0], c[1], miss, over))
        if miss > 5.0 or over > 5.0: raise g.Err("покриття не пройшло перевірку")
    base = os.path.splitext(sh)[0].replace("лист_", "")
    dst = os.path.join(nest, "%s_SLOT-RECESS_%dx%d_Z-%g.cnc" % (base.replace("_", ""), round(a.length), round(a.width), a.depth))
    open(dst, "wb").write(("\r\n".join(out) + "\r\n").encode("ascii"))
    xs_ = [float(m.group(1)) for l in out for m in [__import__("re").search(r"X(-?[\d.]+)", l)] if m]; ys_ = [float(m.group(1)) for l in out for m in [__import__("re").search(r"Y(-?[\d.]+)", l)] if m]
    ok = min(xs_) >= 0 and max(xs_) <= W and min(ys_) >= 0 and max(ys_) <= L
    print("%s %s: рядків %d; потаїв %d; мін. відстань потаю до краю деталі %.1f мм; X %.0f..%.0f Y %.0f..%.0f" % ("OK" if ok else "ПОМИЛКА", os.path.basename(dst), len(out), len(allpaths), min(m for _, m in report), min(xs_), max(xs_), min(ys_), max(ys_)))
    return 0 if ok else 1
if __name__ == "__main__":
    try: sys.exit(main())
    except g.Err as e: print("ПОМИЛКА:", e); sys.exit(1)
