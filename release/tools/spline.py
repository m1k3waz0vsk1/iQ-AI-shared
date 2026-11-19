#!/usr/bin/env python3
"""SPLINE з DXF -> ламана (додано 05.11.2026, рішення користувача: КД буває зі сплайнами, напр. ПНЯ 02.401 R).
spline_pts(d): d = {група: [значення]} однієї сутності SPLINE. B-сплайн будь-якого степеня (71),
вузли (40), контрольні точки (10/20), ваги (41, раціональний NURBS). Обчислення де Бура по кожному
ненульовому прольоту вузлів, потім спрощення Дугласа-Пекера з допуском TOL_MM (відхилення від кривої
не більше ~0,01 мм). Кінцеві точки затиснутого сплайна = перша й остання контрольні точки (точно,
щоб з'єднатися з сусідніми LINE/ARC). Без контрольних точок береться ламана через fit-точки (11/21).
Замкнений (70 & 1): ламана замикається. Лише стандартна бібліотека."""
import math

TOL_MM = 0.01
PER_SPAN = 48


def _fl(d, g):
    return [float(v) for v in d.get(g, [])]


def _deboor(k, x, t, c, p):
    d = [list(c[j + k - p]) for j in range(p + 1)]
    for r in range(1, p + 1):
        for j in range(p, r - 1, -1):
            den = t[j + 1 + k - r] - t[j + k - p]
            a = 0.0 if den == 0 else (x - t[j + k - p]) / den
            d[j] = [(1 - a) * d[j - 1][q] + a * d[j][q] for q in range(3)]
    return d[p]


def _dp(pts, tol):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts); keep[0] = keep[-1] = True
    st = [(0, len(pts) - 1)]
    while st:
        a, b = st.pop()
        ax, ay = pts[a]; bx, by = pts[b]; L = math.hypot(bx - ax, by - ay)
        best, bi = -1.0, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            dist = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / L if L > 1e-12 else math.hypot(px - ax, py - ay)
            if dist > best:
                best, bi = dist, i
        if bi > 0 and best > tol:
            keep[bi] = True; st += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def spline_pts(d, tol=TOL_MM):
    p = int(float(d.get("71", ["3"])[0]))
    t = _fl(d, "40"); xs, ys = _fl(d, "10"), _fl(d, "20")
    closed = bool(int(float(d.get("70", ["0"])[0])) & 1)
    if not xs:
        pts = [list(q) for q in zip(_fl(d, "11"), _fl(d, "21"))]
        if not pts:
            raise ValueError("SPLINE без контрольних і fit-точок")
        if closed and pts[0] != pts[-1]:
            pts.append(list(pts[0]))
        return pts
    n = len(xs); w = _fl(d, "41")
    if len(w) != n:
        w = [1.0] * n
    if len(t) != n + p + 1:
        raise ValueError("SPLINE: вузлів %d, очікувалось %d" % (len(t), n + p + 1))
    c = [(xs[i] * w[i], ys[i] * w[i], w[i]) for i in range(n)]
    out = []
    for k in range(p, n):
        if t[k + 1] <= t[k]:
            continue
        for s in range(PER_SPAN):
            x = t[k] + (t[k + 1] - t[k]) * s / PER_SPAN
            h = _deboor(k, x, t, c, p); out.append([h[0] / h[2], h[1] / h[2]])
    k = max(i for i in range(p, n) if t[i + 1] > t[i])
    h = _deboor(k, t[k + 1], t, c, p); out.append([h[0] / h[2], h[1] / h[2]])
    # затиснутий сплайн: кінці точно в контрольних точках
    if all(abs(v - t[0]) < 1e-12 for v in t[:p + 1]):
        out[0] = [xs[0], ys[0]]
    if all(abs(v - t[-1]) < 1e-12 for v in t[-p - 1:]):
        out[-1] = [xs[-1], ys[-1]]
    out = _dp(out, tol)
    if closed and (abs(out[0][0] - out[-1][0]) > 1e-9 or abs(out[0][1] - out[-1][1]) > 1e-9):
        out.append(list(out[0]))
    return out


def lwpoly_pts(seq, closed, tol=TOL_MM):
    """LWPOLYLINE -> ламана (додано 05.11.2026). seq = впорядкований список (група, значення) сутності:
    bulge (42) належить вершині, після якої стоїть. Дуги з bulge розбиваються з відхиленням не більше tol."""
    V = []
    for g, v in seq:
        g = g.strip()
        if g == "10":
            V.append([float(v), 0.0, 0.0])
        elif g == "20" and V:
            V[-1][1] = float(v)
        elif g == "42" and V:
            V[-1][2] = float(v)
    if not V:
        return []
    n = len(V); out = [[V[0][0], V[0][1]]]
    for i in range(n if closed else n - 1):
        x0, y0, b = V[i]; x1, y1, _ = V[(i + 1) % n]
        if abs(b) > 1e-12:
            th = 4 * math.atan(b); c = math.hypot(x1 - x0, y1 - y0); r = c / (2 * abs(math.sin(th / 2)))
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2; h = (c / 2) * (1 - b * b) / (2 * b)
            cx, cy = mx - (y1 - y0) / c * h, my + (x1 - x0) / c * h
            a0 = math.atan2(y0 - cy, x0 - cx)
            step = 2 * math.acos(max(0.0, 1 - tol / r)) if r > tol else math.pi / 4
            k = max(2, int(math.ceil(abs(th) / max(step, 1e-3))))
            for j in range(1, k):
                a = a0 + th * j / k; out.append([cx + r * math.cos(a), cy + r * math.sin(a)])
        out.append([x1, y1])
    return out
