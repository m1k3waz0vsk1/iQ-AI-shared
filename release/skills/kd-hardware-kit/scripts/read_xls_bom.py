#!/usr/bin/env python3
"""Читає комплектацію з .xls (BIFF8, вивантаження 1С) без сторонніх бібліотек.

Вхід:
  * шлях до .xls файлу;
  * або текстовий файл з base64 (повний вміст або один-кілька фрагментів,
    кожен з нового рядка). Вирівнювання base64 підбирається автоматично,
    тому фрагмент можна вирізати з будь-якого місця.

Вихід: JSON зі списком рядків {row, no, name, characteristic, qty, unit}
і, з --markdown, таблиця для перевірки.

Скрипт нічого не змінює в джерелі.
"""
import argparse
import base64
import json
import re
import struct
import sys

CELL_LEN = {0x00FD: 10, 0x027E: 10, 0x0203: 14, 0x0201: 6}


def decode_fragment(text):
    """Повертає список декодованих байтових блоків для всіх 4 зсувів."""
    text = re.sub(r"[^A-Za-z0-9+/=]", "", text).rstrip("=")
    blobs = []
    for shift in range(4):
        t = text[shift:]
        if len(t) % 4 == 1:
            t = t[:-1]
        t += "=" * (-len(t) % 4)
        if not t:
            continue
        try:
            blobs.append(base64.b64decode(t))
        except Exception:
            pass
    return blobs


def rk_value(v):
    if v & 2:
        x = v >> 2
        if x & (1 << 29):
            x -= 1 << 30
    else:
        x = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return x / 100 if v & 1 else x


def parse_sst(b, p):
    """Пробує розібрати SST на позиції p. Повертає список рядків або None."""
    if p + 12 > len(b):
        return None
    rid, ln = struct.unpack_from("<HH", b, p)
    if rid != 0x00FC or ln < 8 or ln > 8224:
        return None
    total, uniq = struct.unpack_from("<II", b, p + 4)
    if not (0 < uniq <= total < 1_000_000):
        return None
    end = min(p + 4 + ln, len(b))
    q = p + 12
    out = []
    try:
        for _ in range(uniq):
            if q + 3 > end:
                break
            n = struct.unpack_from("<H", b, q)[0]
            flags = b[q + 2]
            q += 3
            runs = ext = 0
            if flags & 0x08:
                runs = struct.unpack_from("<H", b, q)[0]
                q += 2
            if flags & 0x04:
                ext = struct.unpack_from("<I", b, q)[0]
                q += 4
            if flags & 0x01:
                s = b[q : q + 2 * n].decode("utf-16-le")
                q += 2 * n
            else:
                s = b[q : q + n].decode("latin-1")
                q += n
            q += 4 * runs + ext
            out.append(s)
    except Exception:
        return None
    return out if out else None


def scan(blobs):
    sst, cells = None, {}
    for b in blobs:
        for p in range(len(b) - 4):
            rid = b[p] | (b[p + 1] << 8)
            if rid == 0x00FC and sst is None:
                s = parse_sst(b, p)
                if s and len(s) >= 2:
                    sst = s
                continue
            ln = b[p + 2] | (b[p + 3] << 8)
            if rid == 0x0006:  # FORMULA: кешований результат
                if not (20 <= ln <= 2000) or p + 4 + ln > len(b):
                    continue
                d = b[p + 4 : p + 4 + ln]
                row, col = struct.unpack_from("<HH", d)
                if row > 5000 or col > 50:
                    continue
                if d[12:14] != b"\xff\xff":
                    cells[(row, col)] = ("num", struct.unpack_from("<d", d, 6)[0])
                continue
            need = CELL_LEN.get(rid)
            if need is None:
                continue
            if ln != need or p + 4 + ln > len(b):
                continue
            d = b[p + 4 : p + 4 + ln]
            row, col = struct.unpack_from("<HH", d)
            if row > 5000 or col > 50:
                continue
            if rid == 0x00FD:
                cells[(row, col)] = ("sst", struct.unpack_from("<I", d, 6)[0])
            elif rid == 0x027E:
                cells[(row, col)] = ("num", rk_value(struct.unpack_from("<I", d, 6)[0]))
            elif rid == 0x0203:
                cells[(row, col)] = ("num", struct.unpack_from("<d", d, 6)[0])
            elif rid == 0x0201:
                cells.setdefault((row, col), ("blank", None))
    return sst, cells


def resolve(sst, cells):
    grid = {}
    for (r, c), (kind, v) in cells.items():
        if kind == "sst":
            if sst is None or v >= len(sst):
                continue  # хибний збіг при скануванні
            v = sst[v]
        elif kind == "blank":
            v = None
        grid.setdefault(r, {})[c] = v
    return grid


HARDWARE = re.compile(r"^(мб|лб|бпк|болт|гайк|шайб|гвинт|винт|шпильк|заклеп|шуруп|саморіз|саморез|штифт|шплінт|шплинт|дюбел)", re.I)
LINING = re.compile(r"підкладк|подкладк|прокладк", re.I)
MATERIAL = re.compile(r"^(лист|пластин[аи] пласт|фарба|краска|труба|круг|кут |уголок|смуга|полоса)", re.I)


def classify(name):
    n = name.strip().lower()
    if HARDWARE.search(n):
        return "метиз"
    if LINING.search(n):
        return "підкладка"
    if MATERIAL.search(n):
        return "матеріал"
    return "інше"


def find_col(header, *keys):
    for c, v in header.items():
        if isinstance(v, str) and any(k in v.lower() for k in keys):
            return c
    return None


def build_rows(grid):
    header_row = None
    for r in sorted(grid):
        if find_col(grid[r], "номенклатура") is not None:
            header_row = r
            break
    if header_row is None:
        raise SystemExit("Не знайдено рядок заголовків з колонкою 'Номенклатура'.")
    h = grid[header_row]
    c_name = find_col(h, "номенклатура")
    c_char = find_col(h, "характеристика")
    c_qty = find_col(h, "кількість", "количество")
    c_unit = find_col(h, "од.", "ед.")
    c_no = find_col(h, "№")
    if c_char == c_name:
        c_char = None
    rows, title = [], None
    for r in sorted(grid):
        if r <= header_row:
            continue
        row = grid[r]
        name = row.get(c_name)
        if not isinstance(name, str) or not name.strip():
            continue
        if c_no is not None and row.get(c_no) is None and len(row) <= 2:
            title = name.strip()  # підпис/назва виробу під таблицею
            continue
        qty = row.get(c_qty) if c_qty is not None else None
        rows.append({
            "row": r + 1,
            "no": row.get(c_no),
            "name": name.strip(),
            "characteristic": row.get(c_char) if c_char is not None else None,
            "qty": qty if isinstance(qty, (int, float)) else None,
            "unit": (row.get(c_unit) or "").strip() if c_unit is not None else None,
            "category": classify(name),
        })
    return rows, title


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help=".xls файл або .b64/.txt з base64 (фрагментами)")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--kit-only", action="store_true",
                    help="лише метизи, підкладки та 'інше' (без листового матеріалу)")
    a = ap.parse_args()
    raw = open(a.source, "rb").read()
    if raw[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        blobs = [raw]
    else:
        blobs = []
        for line in raw.decode("ascii", "ignore").split("\n"):
            if line.strip():
                blobs += decode_fragment(line)
    sst, cells = scan(blobs)
    if sst is None:
        sys.exit("SST (таблицю рядків) не знайдено: додайте фрагмент base64 з назвами позицій.")
    if not cells:
        sys.exit("Записи клітинок не знайдено: додайте фрагмент base64 з даними аркуша.")
    rows, title = build_rows(resolve(sst, cells))
    if a.kit_only:
        rows = [x for x in rows if x["category"] != "матеріал"]
    if a.markdown:
        print(f"Виріб: {title or '-'}\n")
        print("| № | Номенклатура | Кількість | Од. | Категорія | Характеристика |\n|---|---|---|---|---|---|")
        for x in rows:
            q = "—" if x["qty"] is None else (int(x["qty"]) if float(x["qty"]).is_integer() else round(x["qty"], 4))
            no = x["no"]
            no = int(no) if isinstance(no, float) and no.is_integer() else (no if no is not None else "")
            print(f"| {no} | {x['name']} | {q} | {x['unit'] or ''} | {x['category']} | {x['characteristic'] or ''} |")
    else:
        json.dump({"title": title, "rows": rows}, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
