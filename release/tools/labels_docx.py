#!/usr/bin/env python3
"""Наклейки у Word (.docx) за шаблоном користувача від 27.10.2026: сторінка 70x35 мм, поля 2 мм,
одна наклейка = одна сторінка. Розмітка (рамка з назвою Impact 12, логотип, QR) з шаблону; позиції логотипа і QR
з labels.json. З "show_order": true між логотипом і QR друкується номер замовлення (один номер на виріб, Impact; СКЛАД для складських; 29.10.2026). Лише стандартна бібліотека + cv2 для QR (необов'язково, якщо є assets/qr.png)."""
import os, io, zipfile
from xml.sax.saxutils import escape

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NS = ('xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
      'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
      'xmlns:o="urn:schemas-microsoft-com:office:office" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:v="urn:schemas-microsoft-com:vml" '
      'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:w10="urn:schemas-microsoft-com:office:word" '
      'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
      'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
      'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
      'xmlns:a14="http://schemas.microsoft.com/office/drawing/2010/main" '
      'mc:Ignorable="w14 wp14"')


def qr_png(value, path):
    """QR як чорно-білий PNG без полів (поле дає розмітка), 20 px на модуль."""
    import cv2, numpy as np
    m = cv2.QRCodeEncoder.create().encode(value)  # з полем 4 модулі
    g = (m > 127).astype(np.uint8) * 255
    ys, xs = np.where(g == 0)
    g = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    g = cv2.resize(g, (g.shape[1] * 20, g.shape[0] * 20), interpolation=cv2.INTER_NEAREST)
    assert cv2.QRCodeDetector().detectAndDecode(cv2.copyMakeBorder(g, 80, 80, 80, 80, cv2.BORDER_CONSTANT, value=255))[0] == value, "QR не читається"
    cv2.imwrite(path, g)


def _pic(did, rid, name, x, y, cx, cy, wrap, rel_h, behind, z):
    return ('<w:r><w:rPr><w:noProof/></w:rPr><w:drawing><wp:anchor distT="0" distB="0" distL="114300" distR="114300" simplePos="0" '
            'relativeHeight="%d" behindDoc="%d" locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>'
            '<wp:positionH relativeFrom="%s"><wp:posOffset>%d</wp:posOffset></wp:positionH>'
            '<wp:positionV relativeFrom="page"><wp:posOffset>%d</wp:posOffset></wp:positionV>'
            '<wp:extent cx="%d" cy="%d"/><wp:effectExtent l="0" t="0" r="0" b="0"/>%s<wp:docPr id="%d" name="%s %d"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
            '<pic:nvPicPr><pic:cNvPr id="%d" name="%s %d"/><pic:cNvPicPr/></pic:nvPicPr>'
            '<pic:blipFill><a:blip r:embed="%s" cstate="print"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            '<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            '</pic:pic></a:graphicData></a:graphic></wp:anchor></w:drawing></w:r>') % (
        z, behind, rel_h, x, y, cx, cy, wrap, did, name, did, did, name, did, rid, cx, cy)


def _textbox(did, text, sz_half, x=77545, y=82996, cx=2370455, cy=542020, ins=91440, name="Назва"):
    sz = '' if sz_half == 24 else '<w:sz w:val="%d"/><w:szCs w:val="%d"/>' % (sz_half, sz_half)
    rpr = '<w:rPr><w:rFonts w:ascii="Impact" w:hAnsi="Impact" w:cs="Times New Roman"/>%s</w:rPr>' % sz
    if "\n" in text:  # кілька рядків (замовлення + i/N, 05.11.2026): кожен окремим абзацом
        return _textbox(did, "\x00", sz_half, x, y, cx, cy, ins, name).replace(
            '<w:t xml:space="preserve">\x00</w:t></w:r></w:p>',
            '</w:r></w:p>'.join('<w:t xml:space="preserve">%s</w:t>' % escape(t) if i == 0 else
                                '<w:p><w:pPr><w:pStyle w:val="ac"/><w:jc w:val="center"/>%s</w:pPr><w:r>%s<w:t xml:space="preserve">%s</w:t>' % (rpr, rpr, escape(t))
                                for i, t in enumerate(text.split("\n"))) + '</w:r></w:p>')
    return ('<w:r><w:rPr><w:noProof/></w:rPr><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing>'
            '<wp:anchor distT="0" distB="0" distL="114300" distR="114300" simplePos="0" relativeHeight="251659264" behindDoc="0" '
            'locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>'
            '<wp:positionH relativeFrom="page"><wp:posOffset>%d</wp:posOffset></wp:positionH>'
            '<wp:positionV relativeFrom="page"><wp:posOffset>%d</wp:posOffset></wp:positionV>'
            '<wp:extent cx="%d" cy="%d"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
            '<wp:wrapNone/><wp:docPr id="%d" name="%s %d"/><wp:cNvGraphicFramePr/>'
            '<a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"><wps:wsp><wps:cNvSpPr/>'
            '<wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
            '<a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:ln><a:noFill/></a:ln></wps:spPr>'
            '<wps:txbx><w:txbxContent><w:p><w:pPr><w:pStyle w:val="ac"/><w:jc w:val="center"/>%s</w:pPr>'
            '<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p></w:txbxContent></wps:txbx>'
            '<wps:bodyPr rot="0" vert="horz" wrap="square" lIns="%d" tIns="0" rIns="%d" bIns="0" anchor="ctr" anchorCtr="0">'
            '<a:noAutofit/></wps:bodyPr></wps:wsp></a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice></mc:AlternateContent></w:r>') % (
        x, y, cx, cy, did, name, did, cx, cy, rpr, rpr, escape(text), ins, ins)


def fit_size(text, maxw, max_lines=2, hi=24, lo=12):
    """Найбільший розмір Impact (півпункти) від hi до lo, за якого текст уміщується в max_lines рядків шириною maxw мм."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        try:
            pdfmetrics.getFont("FN_impact")
        except KeyError:
            pdfmetrics.registerFont(TTFont("FN_impact", os.path.join(BASE, "assets", "Impact.ttf")))
    except Exception:
        return hi
    for half in range(hi, lo - 1, -1):
        s = half / 2.0
        lines, cur = 1, ""
        for w in text.split():
            t = (cur + " " + w).strip()
            if pdfmetrics.stringWidth(t, "FN_impact", s) / 2.835 <= maxw or not cur:
                cur = t
            else:
                lines += 1; cur = w
        if lines <= max_lines and pdfmetrics.stringWidth(cur, "FN_impact", s) / 2.835 <= maxw:
            return half
    return lo


def name_size(text, max_lines=2):
    """Розмір Impact (півпункти): 12 pt, якщо назва вміщується в 2 рядки рамки, інакше менше."""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        try:
            pdfmetrics.getFont("FN_impact")
        except KeyError:
            pdfmetrics.registerFont(TTFont("FN_impact", os.path.join(BASE, "assets", "Impact.ttf")))
    except Exception:
        return 24
    maxw = (2370455 - 2 * 91440) / 36000.0 - 1.0  # мм, із запасом на різницю рендерів
    for half in range(24, 13, -1):
        s = half / 2.0
        lines, cur = 1, ""
        for w in text.split():
            t = (cur + " " + w).strip()
            if pdfmetrics.stringWidth(t, "FN_impact", s) / 2.835 <= maxw or not cur:
                cur = t
            else:
                lines += 1; cur = w
        if lines <= max_lines and pdfmetrics.stringWidth(cur, "FN_impact", s) / 2.835 <= maxw:
            return half
    return 14


def write_labels(path, names, cfg):
    logo = os.path.join(BASE, cfg.get("logo", "assets/logo.png"))
    qr = os.path.join(BASE, "assets", "qr.png")
    if not os.path.exists(qr) or cfg.get("_regen_qr"):
        qr_png(cfg["qr_value"], qr)
    E = 36000  # EMU на мм
    show = bool(cfg.get("show_order"))
    mid = show or any(not isinstance(it, str) and len(it) > 2 and it[2] for it in names)  # середня зона: замовлення і/або i/N
    lg = cfg.get("logo_mm_order" if mid else "logo_mm", [12.9, 18.72, 13.92, 13.48])
    qm = cfg.get("qr_mm_order" if mid else "qr_mm", [44.37, 18.12, 14.53])
    ob = cfg.get("order_box_mm", [17.6, 18.12, 34.6, 14.53])
    opt = int(round(cfg.get("order_pt", 14) * 2))
    body, did = [], 1
    for i, it in enumerate(names):
        nm, order = (it, "") if isinstance(it, str) else (it[0], it[1] or "")
        seq = it[2] if not isinstance(it, str) and len(it) > 2 else ""  # «i/N» (05.11.2026)
        # координати шаблону (відносно абзацу/полів 2 мм) переведені у відлік від краю сторінки; абзац порожній і крихітний
        ppr = ('<w:pPr>%s<w:spacing w:before="0" w:after="0" w:line="20" w:lineRule="exact"/>'
               '<w:rPr><w:sz w:val="2"/><w:szCs w:val="2"/></w:rPr></w:pPr>') % ('<w:pageBreakBefore/>' if i else '')
        runs = (_pic(did, "rId5", "Логотип", int(lg[0] * E), int(lg[1] * E), int(lg[2] * E), int(lg[3] * E), '<wp:wrapNone/>', "page", 1, 251665408) +
                _pic(did + 1, "rId6", "QR", int(qm[0] * E), int(qm[1] * E), int(qm[2] * E), int(qm[2] * E), '<wp:wrapNone/>', "page", 0, 251664384) +
                _textbox(did + 2, nm, name_size(nm)))
        did += 3
        txt = " · ".join(t for t in ((order if show else ""), seq) if t)  # замовлення і i/N в одному рядку (06.11.2026)
        if txt:
            runs += _textbox(did, txt, min(fit_size(t, ob[2] - 0.6, 1, opt, 12) for t in txt.split("\n")), int(ob[0] * E), int(ob[1] * E),
                             int(ob[2] * E), int(ob[3] * E), 0, "Замовлення")
            did += 1
        body.append('<w:p>%s%s</w:p>' % (ppr, runs))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document %s><w:body>%s'
           '<w:sectPr><w:pgSz w:w="3969" w:h="1985"/><w:pgMar w:top="113" w:right="113" w:bottom="113" w:left="113" '
           'w:header="709" w:footer="709" w:gutter="0"/><w:cols w:space="708"/><w:docGrid w:linePitch="360"/></w:sectPr>'
           '</w:body></w:document>') % (NS, "".join(body))
    W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles %s><w:docDefaults><w:rPrDefault><w:rPr>'
              '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Calibri" w:cs="Times New Roman"/><w:kern w:val="2"/>'
              '<w:sz w:val="24"/><w:szCs w:val="24"/><w:lang w:val="uk-UA" w:eastAsia="en-US" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
              '<w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="278" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>'
              '<w:style w:type="paragraph" w:default="1" w:styleId="a"><w:name w:val="Normal"/><w:qFormat/></w:style>'
              '<w:style w:type="character" w:default="1" w:styleId="a0"><w:name w:val="Default Paragraph Font"/><w:uiPriority w:val="1"/><w:semiHidden/></w:style>'
              '<w:style w:type="paragraph" w:styleId="ac"><w:name w:val="No Spacing"/><w:uiPriority w:val="1"/><w:qFormat/>'
              '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:style></w:styles>') % W
    settings = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings %s><w:defaultTabStop w:val="720"/>'
                '<w:characterSpacingControl w:val="doNotCompress"/><w:compat>'
                '<w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/>'
                '<w:compatSetting w:name="overrideTableStyleFontSizeAndJustification" w:uri="http://schemas.microsoft.com/office/word" w:val="1"/>'
                '<w:compatSetting w:name="enableOpenTypeFeatures" w:uri="http://schemas.microsoft.com/office/word" w:val="1"/>'
                '<w:compatSetting w:name="doNotFlipMirrorIndents" w:uri="http://schemas.microsoft.com/office/word" w:val="1"/>'
                '<w:compatSetting w:name="differentiateMultirowTableHeaders" w:uri="http://schemas.microsoft.com/office/word" w:val="1"/>'
                '</w:compat></w:settings>') % W
    ct = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="png" ContentType="image/png"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
          '<Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
          '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
          '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
    drels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
             '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
             '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
             '<Relationship Id="rId6" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image2.png"/></Relationships>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", ct); z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", doc); z.writestr("word/_rels/document.xml.rels", drels)
        z.writestr("word/styles.xml", styles); z.writestr("word/settings.xml", settings)
        z.write(logo, "word/media/image1.png"); z.write(qr, "word/media/image2.png")
    open(path, "wb").write(buf.getvalue())
