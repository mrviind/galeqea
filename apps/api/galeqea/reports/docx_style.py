"""Shared visual system for GaleQEA's Word documents (Test Plan, Test Completion
Report, and any future one). One place owns the palette, type, and the primitives
(cover page, running header/footer with page numbers, an auto-built table of
contents, rule-styled tables, status pills, and a proportional stat bar) so every
generated document reads as one document family rather than two one-off scripts.

Typography is Calibri throughout, and deliberately not the newer "Aptos" (the
2023+ Microsoft 365 default). Verified by rendering both in LibreOffice: Aptos
has no bundled cross-platform substitute and resolves *inconsistently* between
weights (bold text fell back to a sans, regular text to a serif; a broken-
looking mix on the same page), while Calibri's metric-compatible substitute
(Carlito) renders identically everywhere. A report that must look right on a
recipient's unknown machine needs to render *correctly*, not just carry the
newest font name. One disciplined typeface, used with a real size/weight/
color scale, reads as more considered than a pairing that half-breaks.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

# --- palette ----------------------------------------------------------------- #
INK = RGBColor(0x0B, 0x12, 0x20)
ACCENT = RGBColor(0x2F, 0x6F, 0xE0)
ACCENT_HEX = "2F6FE0"
MUTED = RGBColor(0x6B, 0x74, 0x86)
LINE_HEX = "E2E6ED"
TINT_HEX = "EEF2FB"          # a near-white tint of the accent, for header shading
PASS = RGBColor(0x1F, 0x9D, 0x55)
PASS_HEX = "1F9D55"
FAIL = RGBColor(0xC0, 0x39, 0x2B)
FAIL_HEX = "C0392B"
WARN = RGBColor(0xB4, 0x7A, 0x0E)
WARN_HEX = "B47A0E"
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

#: Same family on purpose (see the module docstring). Bold/size/color carry the
#: hierarchy instead of a second font that might not survive rendering intact.
FONT_BODY = "Calibri"
FONT_DISPLAY = "Calibri"

RISK_COLOR = {"critical": FAIL_HEX, "high": WARN_HEX, "medium": ACCENT_HEX, "low": "9AA3B2"}

DEFAULT_LOGO = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "report-logo.png")


# --- low-level OOXML helpers -------------------------------------------------- #

def _shade(cell, hex_fill: str) -> None:
    tc = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), hex_fill)
    tc.append(shd)


def _cell_border(cell, **edges: tuple[int, str]) -> None:
    """edges: top/bottom/left/right -> (eighths-of-a-point size, hex color)."""
    tcPr = cell._tc.get_or_add_tcPr()
    borders = OxmlElement("w:tcBorders")
    for edge, (size, color) in edges.items():
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tcPr.append(borders)


def row_rule(row, *, color: str = LINE_HEX, size: int = 4) -> None:
    """A hairline under a data row - and vertically centers its cells, so a
    status pill sitting next to a two-line-wrapped title reads as a centered
    badge, not a solid block stretched to the row's full height."""
    for cell in row.cells:
        _cell_border(cell, bottom=(size, color))
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


def _no_autofit(table) -> None:
    table.autofit = False
    table.allow_autofit = False


def _set_widths(table, widths_in: list[float]) -> None:
    _no_autofit(table)
    for row in table.rows:
        for cell, w in zip(row.cells, widths_in, strict=False):
            cell.width = Inches(w)
    for col, w in zip(table.columns, widths_in, strict=False):
        col.width = Inches(w)


def _field(paragraph, code: str) -> None:
    """Insert a Word field (e.g. PAGE, NUMPAGES) that Word computes on open."""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = code
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for el in (begin, instr, sep, end):
        run._r.append(el)


def _style_border(style, *, size: int = 10, color: str = ACCENT_HEX, space: int = 6) -> None:
    """A bottom rule under a *style* (not one paragraph). Every heading that
    uses this style gets the divider for free, and it survives edits in Word."""
    pPr = style.element.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), str(space))
    bottom.set(qn("w:color"), color)
    pBdr.append(bottom)
    pPr.append(pBdr)


# --- document scaffold --------------------------------------------------------- #

def base_document(*, title: str, subject: str, author: str):
    from docx import Document

    doc = Document()
    doc.core_properties.title = title
    doc.core_properties.subject = subject
    doc.core_properties.author = author or "GaleQEA"

    normal = doc.styles["Normal"]
    normal.font.name = FONT_BODY
    normal.font.size = Pt(10.5)
    normal.font.color.rgb = INK
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.18

    title_style = doc.styles["Title"]
    title_style.font.name = FONT_DISPLAY
    title_style.font.size = Pt(34)
    title_style.font.bold = True
    title_style.font.color.rgb = INK
    title_style.paragraph_format.space_after = Pt(2)

    h1 = doc.styles["Heading 1"]
    h1.font.name = FONT_DISPLAY
    h1.font.size = Pt(15)
    h1.font.bold = True
    h1.font.color.rgb = INK
    h1.font.italic = False
    h1.paragraph_format.space_before = Pt(20)
    h1.paragraph_format.space_after = Pt(8)
    h1.paragraph_format.page_break_before = False
    _style_border(h1)

    h2 = doc.styles["Heading 2"]
    h2.font.name = FONT_DISPLAY
    h2.font.size = Pt(11.5)
    h2.font.bold = True
    h2.font.color.rgb = ACCENT
    h2.font.italic = False
    h2.paragraph_format.space_before = Pt(12)
    h2.paragraph_format.space_after = Pt(4)

    section = doc.sections[0]
    section.left_margin = section.right_margin = Inches(0.9)
    section.top_margin = Inches(0.8)
    section.bottom_margin = Inches(0.8)
    return doc


def running_header_footer(doc, *, doc_kind: str, company: str) -> None:
    """A header/footer for every page after the cover: a light identity strip up
    top, page X of Y at the bottom. The cover stays clean (different-first-page)."""
    section = doc.sections[0]
    section.different_first_page_header_footer = True

    header = section.header
    hp = header.paragraphs[0]
    hp.text = ""
    hp.paragraph_format.space_after = Pt(0)
    tab_stops = hp.paragraph_format.tab_stops
    tab_stops.add_tab_stop(Inches(6.3), alignment=2)  # right-aligned tab
    left = hp.add_run(doc_kind.upper())
    left.font.name = FONT_BODY
    left.font.size = Pt(8)
    left.font.color.rgb = MUTED
    left.font.bold = True
    hp.add_run("\t")
    right = hp.add_run(company)
    right.font.name = FONT_BODY
    right.font.size = Pt(8)
    right.font.color.rgb = MUTED
    _paragraph_border(hp, edge="bottom", color=LINE_HEX, size=6)

    footer = section.footer
    fp = footer.paragraphs[0]
    fp.text = ""
    fp.paragraph_format.space_before = Pt(4)
    tab_stops = fp.paragraph_format.tab_stops
    tab_stops.add_tab_stop(Inches(6.3), alignment=2)
    left_f = fp.add_run(f"{company} · Generated by GaleQEA")
    left_f.font.name = FONT_BODY
    left_f.font.size = Pt(8)
    left_f.font.color.rgb = MUTED
    fp.add_run("\t")
    pg = fp.add_run("Page ")
    pg.font.name = FONT_BODY
    pg.font.size = Pt(8)
    pg.font.color.rgb = MUTED
    _field(fp, "PAGE")
    of = fp.add_run(" of ")
    of.font.name = FONT_BODY
    of.font.size = Pt(8)
    of.font.color.rgb = MUTED
    _field(fp, "NUMPAGES")
    _paragraph_border(fp, edge="top", color=LINE_HEX, size=6)

    # the first page's own header/footer stays blank because the cover carries its
    # own identity (logo + title); a repeated running strip would compete with it.
    section.first_page_header.paragraphs[0].text = ""
    section.first_page_footer.paragraphs[0].text = ""


def _paragraph_border(paragraph, *, edge: str, color: str, size: int = 6, space: int = 4) -> None:
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = pPr.find(qn("w:pBdr"))
    if pBdr is None:
        pBdr = OxmlElement("w:pBdr")
        pPr.append(pBdr)
    el = OxmlElement(f"w:{edge}")
    el.set(qn("w:val"), "single")
    el.set(qn("w:sz"), str(size))
    el.set(qn("w:space"), str(space))
    el.set(qn("w:color"), color)
    pBdr.append(el)


# --- cover --------------------------------------------------------------------- #

def cover(doc, *, kicker: str, title: str, subtitle: str, logo_path: str | None,
          meta_rows: list[tuple[str, str]], stat_cards: list[tuple[str, str, RGBColor]] | None = None,
          hero: tuple[str, str, RGBColor] | None = None) -> None:
    logo_path = logo_path or DEFAULT_LOGO
    if logo_path and os.path.exists(logo_path):
        doc.add_picture(logo_path, width=Inches(2.2))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.LEFT
    for _ in range(2):
        doc.add_paragraph()

    kp = doc.add_paragraph()
    kr = kp.add_run(kicker.upper())
    kr.font.name = FONT_BODY
    kr.font.size = Pt(10)
    kr.font.bold = True
    kr.font.color.rgb = ACCENT
    _letter_spacing(kr, 30)

    doc.add_paragraph(title, style="Title")
    _paragraph_border(doc.paragraphs[-1], edge="bottom", color=ACCENT_HEX, size=14, space=10)

    sub = doc.add_paragraph()
    sr = sub.add_run(subtitle)
    sr.font.name = FONT_BODY
    sr.font.size = Pt(14)
    sr.font.color.rgb = MUTED
    sub.paragraph_format.space_after = Pt(18)

    if hero:
        label, value, color = hero
        hp = doc.add_paragraph()
        hp.alignment = WD_ALIGN_PARAGRAPH.LEFT
        hv = hp.add_run(value)
        hv.font.name = FONT_DISPLAY
        hv.font.size = Pt(46)
        hv.font.bold = True
        hv.font.color.rgb = color
        hp.paragraph_format.space_after = Pt(0)
        hl = doc.add_paragraph()
        hlr = hl.add_run(label.upper())
        hlr.font.name = FONT_BODY
        hlr.font.size = Pt(9.5)
        hlr.font.bold = True
        hlr.font.color.rgb = MUTED
        _letter_spacing(hlr, 20)
        hl.paragraph_format.space_after = Pt(16)

    if stat_cards:
        _stat_cards(doc, stat_cards)
        doc.add_paragraph().paragraph_format.space_after = Pt(4)

    kv_table(doc, meta_rows)
    doc.add_page_break()


def _letter_spacing(run, twentieths_of_a_point: int) -> None:
    rPr = run._r.get_or_add_rPr()
    spacing = OxmlElement("w:spacing")
    spacing.set(qn("w:val"), str(twentieths_of_a_point))
    rPr.append(spacing)


def _stat_cards(doc, cards: list[tuple[str, str, RGBColor]]) -> None:
    table = doc.add_table(rows=2, cols=len(cards))
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    width = 6.3 / len(cards)
    for i, (label, value, color) in enumerate(cards):
        num_cell = table.rows[0].cells[i]
        _cell_border(num_cell, top=(16, ACCENT_HEX if i == 0 else LINE_HEX))
        np = num_cell.paragraphs[0]
        np.paragraph_format.space_before = Pt(8)
        nr = np.add_run(value)
        nr.font.name = FONT_DISPLAY
        nr.font.size = Pt(22)
        nr.font.bold = True
        nr.font.color.rgb = color

        lbl_cell = table.rows[1].cells[i]
        lp = lbl_cell.paragraphs[0]
        lp.paragraph_format.space_after = Pt(4)
        lr = lp.add_run(label.upper())
        lr.font.name = FONT_BODY
        lr.font.size = Pt(8.5)
        lr.font.bold = True
        lr.font.color.rgb = MUTED
        _letter_spacing(lr, 15)
    _set_widths(table, [width] * len(cards))


# --- table of contents ---------------------------------------------------------- #

def table_of_contents(doc, sections: list[str] | None = None) -> None:
    """A real TOC field: Word recalculates it (with live page numbers) on open
    because ``w:updateFields`` is set. Its cached "last known result" is a plain
    list of the section names rather than an apology: LibreOffice's headless PDF
    export (used to preview these reports) does not run that update pass, and a
    viewer that never triggers Word's own update deserves a useful page, not a
    blank one or an instruction to fix it themselves."""
    doc.add_paragraph("Contents", style="Heading 1")
    p = doc.add_paragraph()

    # Each fldChar/instrText/result piece is its own run and *sibling* of the
    # others - w:fldChar is an empty marker, not a container, so the cached
    # result text (the w:t) must be a separate run between "separate" and
    # "end", never nested inside a fldChar element.
    begin_run = p.add_run()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    fld_begin.set(qn("w:dirty"), "true")
    begin_run._r.append(fld_begin)

    instr_run = p.add_run()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-2" \\h \\z \\u'
    instr_run._r.append(instr)

    sep_run = p.add_run()
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    sep_run._r.append(fld_sep)

    result_run = p.add_run(
        "  ·  ".join(sections) if sections else "See the numbered sections below.")
    result_run.font.name = FONT_BODY
    result_run.font.size = Pt(10.5)
    result_run.font.color.rgb = INK

    end_run = p.add_run()
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    end_run._r.append(fld_end)

    settings = doc.settings.element
    update_fields = OxmlElement("w:updateFields")
    update_fields.set(qn("w:val"), "true")
    settings.append(update_fields)
    doc.add_page_break()


# --- body primitives -------------------------------------------------------------- #

def heading(doc, text: str, *, level: int = 1):
    return doc.add_paragraph(text, style=f"Heading {level}")


def kv_table(doc, rows: list[tuple[str, str]], *, blank_values: set[str] | None = None) -> None:
    """A borderless key/value list: a hairline under each row, not a boxed grid."""
    blank_values = blank_values or set()
    t = doc.add_table(rows=0, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for k, v in rows:
        cells = t.add_row().cells
        kr = cells[0].paragraphs[0].add_run(k.upper())
        kr.bold = True
        kr.font.size = Pt(8.5)
        kr.font.name = FONT_BODY
        kr.font.color.rgb = MUTED
        _letter_spacing(kr, 10)
        if k in blank_values:
            _cell_border(cells[1], bottom=(6, LINE_HEX))
        else:
            vr = cells[1].paragraphs[0].add_run(str(v))
            vr.font.size = Pt(10.5)
            vr.font.name = FONT_BODY
            vr.font.color.rgb = INK
        row_rule(t.rows[-1])
    _set_widths(t, [2.0, 4.3])


def styled_table(doc, headers: list[str], *, widths: list[float] | None = None):
    """A clean rule-styled table: a tinted header row with a bottom accent rule,
    hairline row rules, no vertical gridlines. The header-row-per-cell coloured
    grid of "Light Grid Accent 1" is deliberately not used here."""
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, h in enumerate(headers):
        cell = t.rows[0].cells[i]
        _shade(cell, TINT_HEX)
        _cell_border(cell, bottom=(14, ACCENT_HEX))
        p = cell.paragraphs[0]
        p.paragraph_format.space_before = Pt(3)
        p.paragraph_format.space_after = Pt(3)
        r = p.add_run(h.upper())
        r.bold = True
        r.font.size = Pt(8.5)
        r.font.name = FONT_BODY
        r.font.color.rgb = INK
        _letter_spacing(r, 10)
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    if widths:
        _set_widths(t, widths)
    return t


def data_row(table, values: list[str], *, size: float = 9.5):
    row = table.add_row()
    for i, v in enumerate(values):
        p = row.cells[i].paragraphs[0]
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(str(v))
        r.font.size = Pt(size)
        r.font.name = FONT_BODY
        r.font.color.rgb = INK
    row_rule(row)
    return row


def pill(cell, text: str, bg_hex: str, fg: RGBColor = WHITE) -> None:
    cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    _shade(cell, bg_hex)
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after = Pt(1)
    r = p.add_run(text.upper())
    r.bold = True
    r.font.size = Pt(8)
    r.font.name = FONT_BODY
    r.font.color.rgb = fg
    _letter_spacing(r, 8)


def stat_bar(doc, segments: list[tuple[str, int, str]]) -> None:
    """A single-row proportional bar: one cell per segment, width ~ its share of
    the total, shaded its status color. Reads the run's composition at a glance
    without repeating the numbers the cover already showed."""
    total = sum(max(0, n) for _, n, _ in segments)
    if total <= 0:
        return
    kept = [(label, n, color) for label, n, color in segments if n > 0]
    t = doc.add_table(rows=1, cols=len(kept))
    row = t.rows[0]
    widths = []
    for i, (label, n, color) in enumerate(kept):
        cell = row.cells[i]
        _shade(cell, color)
        share = n / total
        widths.append(max(0.35, round(6.3 * share, 2)))
        p = cell.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(5)
        p.paragraph_format.space_after = Pt(5)
        if share >= 0.09:                              # a sliver is too narrow for a label
            r = p.add_run(f"{n} {label}")
            r.bold = True
            r.font.size = Pt(9)
            r.font.name = FONT_BODY
            r.font.color.rgb = WHITE
    _set_widths(t, widths)
    cap = doc.add_paragraph()
    cap.paragraph_format.space_before = Pt(2)
    cr = cap.add_run("Share of the run by result")
    cr.italic = True
    cr.font.size = Pt(8.5)
    cr.font.name = FONT_BODY
    cr.font.color.rgb = MUTED


def _donut_png(segments: list[tuple[str, int, str]], *, size: int = 640,
               hole_ratio: float = 0.58) -> bytes:
    """A ring-chart PNG, one wedge per segment sized to its share, rendered at 4×
    and downsampled for anti-aliased edges (python-docx has no native chart, and
    a jagged pieslice reads as unfinished). No text is drawn on it: the headline
    number and the legend are real docx text next to it, crisp at any zoom and
    still selectable/readable by a screen reader, unlike text baked into a raster.
    """
    import io as _io

    from PIL import Image, ImageDraw

    scale = 4
    canvas = size * scale
    img = Image.new("RGB", (canvas, canvas), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    pad = int(canvas * 0.03)
    bbox = (pad, pad, canvas - pad, canvas - pad)
    total = sum(max(0, n) for _, n, _ in segments)
    if total > 0:
        start = -90.0
        for _, n, color_hex in segments:
            if n <= 0:
                continue
            sweep = 360.0 * n / total
            draw.pieslice(bbox, start, start + sweep, fill=_hex_to_rgb(color_hex))
            start += sweep
    else:
        draw.ellipse(bbox, fill=(226, 230, 237))
    hole = int(canvas * hole_ratio)
    off = (canvas - hole) // 2
    draw.ellipse((off, off, off + hole, off + hole), fill=(255, 255, 255))
    img = img.resize((size, size), Image.LANCZOS)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def donut_with_legend(doc, segments: list[tuple[str, int, str]], *,
                      center_value: str = "", center_label: str = "",
                      caption: str = "", image_inches: float = 1.9) -> None:
    """A donut chart beside a legend: colored-dot rows with the count and share,
    the headline number in real text under the ring. Segments with 0 are kept out
    of the ring but still listed in the legend at 0%, so "no failures" reads as a
    stated fact rather than a silently missing row."""
    import io as _io

    total = sum(max(0, n) for _, n, _ in segments)
    png_bytes = _donut_png(segments)

    t = doc.add_table(rows=1, cols=2)
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    img_cell, legend_cell = t.rows[0].cells
    img_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
    legend_cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    ip = img_cell.paragraphs[0]
    ip.alignment = WD_ALIGN_PARAGRAPH.CENTER
    ip.add_run().add_picture(_io.BytesIO(png_bytes), width=Inches(image_inches))
    if center_value:
        cv = img_cell.add_paragraph()
        cv.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cv.paragraph_format.space_before = Pt(2)
        cvr = cv.add_run(center_value)
        cvr.font.name = FONT_DISPLAY
        cvr.bold = True
        cvr.font.size = Pt(20)
        cvr.font.color.rgb = INK
        if center_label:
            cl = img_cell.add_paragraph()
            cl.alignment = WD_ALIGN_PARAGRAPH.CENTER
            clr = cl.add_run(center_label.upper())
            clr.font.name = FONT_BODY
            clr.font.size = Pt(8)
            clr.font.color.rgb = MUTED
            _letter_spacing(clr, 15)

    first = True
    for label, n, color_hex in segments:
        p = legend_cell.paragraphs[0] if first else legend_cell.add_paragraph()
        first = False
        p.paragraph_format.space_after = Pt(7)
        dot = p.add_run("●  ")
        dot.font.color.rgb = RGBColor.from_string(color_hex)
        dot.font.size = Pt(13)
        pct = round(100 * n / total) if total else 0
        txt = p.add_run(f"{label}: {n} ({pct}%)")
        txt.font.name = FONT_BODY
        txt.font.size = Pt(11)
        txt.font.color.rgb = INK
    _set_widths(t, [image_inches + 0.3, 6.3 - image_inches - 0.3])

    if caption:
        cap = doc.add_paragraph()
        cap.paragraph_format.space_before = Pt(2)
        cr = cap.add_run(caption)
        cr.italic = True
        cr.font.size = Pt(8.5)
        cr.font.name = FONT_BODY
        cr.font.color.rgb = MUTED


def bar_chart_with_legend(doc, rows: list[tuple[str, list[tuple[int, str]]]], *,
                         caption: str = "") -> None:
    """A horizontal bar per category (e.g. test type), each bar always spanning
    the full width and split by its own pass/fail/skip share, with the row's raw
    total as a text column since share alone hides the count. Same technique as
    ``stat_bar`` (full-width proportional cells, no nested-table-narrower-than-
    its-cell trick, which Word's table layout does not reliably honour) and the
    same convention the in-app Run board uses, so a category's bar always fills
    its row and the *count* carries how big that category was, not the bar's
    length relative to the others. ``rows`` is
    ``[(category_label, [(count, color_hex), ...]), ...]``."""
    if not rows:
        return
    label_in, bar_in, total_in = 1.5, 3.7, 0.9

    t = doc.add_table(rows=len(rows), cols=3)
    t.alignment = WD_TABLE_ALIGNMENT.LEFT
    for i, (label, segs) in enumerate(rows):
        label_cell, bar_cell, total_cell = t.rows[i].cells
        for cell in (label_cell, bar_cell, total_cell):
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        lr = label_cell.paragraphs[0].add_run(label)
        lr.font.name = FONT_BODY
        lr.font.size = Pt(9.5)
        lr.font.color.rgb = INK

        row_total = sum(max(0, n) for n, _ in segs)
        kept = [(n, c) for n, c in segs if n > 0]
        bar_cell.paragraphs[0].text = ""
        inner = bar_cell.add_table(rows=1, cols=len(kept) or 1)
        _no_autofit(inner)
        if not kept:
            _shade(inner.rows[0].cells[0], "E2E6ED")
            _set_widths(inner, [bar_in])
        else:
            widths = []
            for j, (n, color_hex) in enumerate(kept):
                cell = inner.rows[0].cells[j]
                _shade(cell, color_hex)
                cell.paragraphs[0].text = ""
                widths.append(max(0.04, round(bar_in * (n / row_total), 3)))
            _set_widths(inner, widths)

        tr = total_cell.paragraphs[0].add_run(str(row_total))
        tr.font.name = FONT_BODY
        tr.font.size = Pt(9.5)
        tr.bold = True
        tr.font.color.rgb = INK
    _set_widths(t, [label_in, bar_in, total_in])
    if caption:
        cap = doc.add_paragraph()
        cap.paragraph_format.space_before = Pt(4)
        cr = cap.add_run(caption)
        cr.italic = True
        cr.font.size = Pt(8.5)
        cr.font.name = FONT_BODY
        cr.font.color.rgb = MUTED


def callout(doc, text: str, *, color_hex: str = FAIL_HEX, label: str = "") -> None:
    """A left-rule, tinted-background note for a failure's error message, not
    just a red run of text lost in a paragraph."""
    t = doc.add_table(rows=1, cols=1)
    cell = t.rows[0].cells[0]
    _shade(cell, "FBEEEC" if color_hex == FAIL_HEX else TINT_HEX)
    _cell_border(cell, left=(24, color_hex))
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(6)
    p.paragraph_format.space_after = Pt(6)
    if label:
        lr = p.add_run(f"{label}\n")
        lr.bold = True
        lr.font.size = Pt(8.5)
        lr.font.name = FONT_BODY
        lr.font.color.rgb = RGBColor.from_string(color_hex)
    r = p.add_run(text)
    r.font.size = Pt(9.5)
    r.font.name = FONT_BODY
    r.font.color.rgb = INK
    _set_widths(t, [6.3])


def framed_picture(doc, image_path: str, *, width: float = 5.6, caption: str = "") -> None:
    """A screenshot inside a hairline frame, not a bare image floating on the page."""
    t = doc.add_table(rows=1, cols=1)
    cell = t.rows[0].cells[0]
    _cell_border(cell, top=(4, LINE_HEX), bottom=(4, LINE_HEX), left=(4, LINE_HEX), right=(4, LINE_HEX))
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(image_path, width=Inches(width))
    _set_widths(t, [width + 0.1])
    if caption:
        cap = doc.add_paragraph()
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        cr = cap.add_run(caption)
        cr.italic = True
        cr.font.size = Pt(8.5)
        cr.font.name = FONT_BODY
        cr.font.color.rgb = MUTED


def divider(doc) -> None:
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(10)
    _paragraph_border(p, edge="bottom", color=LINE_HEX, size=4)


def bullets(doc, items: list[str]) -> None:
    for it in items:
        p = doc.add_paragraph(style="List Bullet")
        r = p.add_run(str(it))
        r.font.size = Pt(10.5)
        r.font.name = FONT_BODY
        r.font.color.rgb = INK


def today() -> str:
    return datetime.now(UTC).strftime("%d %B %Y")
