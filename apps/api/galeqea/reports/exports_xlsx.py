"""Excel (.xlsx) exports: corporate-standard workbooks for the test artefacts.

Three deliverables a QA lead hands to stakeholders as a spreadsheet: the **test-case
register**, the **Test Completion Report**, and the **Requirements Traceability
Matrix**. Deterministic, branded header styling, one sheet per concern. Built with
openpyxl (already a dependency).
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.chart import BarChart, DoughnutChart, Reference
from openpyxl.chart.label import DataLabelList
from openpyxl.chart.legend import Legend
from openpyxl.chart.series import DataPoint
from openpyxl.chart.shapes import GraphicalProperties
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

_INK = "0B1220"
_ACCENT = "2F6FE0"
_HEADER_FILL = PatternFill("solid", fgColor="0B1220")
_HEADER_FONT = Font(bold=True, color="FFFFFF", size=11, name="Calibri")
_TITLE_FONT = Font(bold=True, size=16, color="0B1220", name="Calibri")
_SUB_FONT = Font(size=10, color="6B7486", name="Calibri")
_PASS_FONT = Font(bold=True, color="1F9D55")
_FAIL_FONT = Font(bold=True, color="C0392B")
_THIN = Side(style="thin", color="D6DBE5")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_WRAP = Alignment(vertical="top", wrap_text=True)

# Same status palette as the Word report (docx_style.PASS_HEX/FAIL_HEX and the
# app's own status tokens) so a reader sees the same colors mean the same thing
# in every artefact GaleQEA hands them.
_PASS_HEX, _FAIL_HEX, _SKIP_HEX = "1F9D55", "C0392B", "9AA3B2"
_KPI_FILL = PatternFill("solid", fgColor="F2F4F8")
_KPI_NUM_FONT = Font(bold=True, size=22, color="0B1220", name="Calibri")
_KPI_LABEL_FONT = Font(bold=True, size=9, color="6B7486", name="Calibri")


def _kpi_card(ws, row: int, col: int, label: str, value: str, color: str = "0B1220") -> None:
    """A single KPI cell pair (big number over a small caption), styled like the
    stat cards on the Word report's cover, so the two documents read as a pair."""
    top = ws.cell(row, col, value)
    top.font = Font(bold=True, size=22, color=color, name="Calibri")
    top.fill = _KPI_FILL
    top.alignment = Alignment(horizontal="center", vertical="center")
    bottom = ws.cell(row + 1, col, label.upper())
    bottom.font = _KPI_LABEL_FONT
    bottom.fill = _KPI_FILL
    bottom.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[row].height = 30


def _donut(ws, *, anchor: str, title: str, labels_values: list[tuple[str, int]],
          colors: list[str], data_col: int, data_row: int) -> None:
    """A native, editable Excel doughnut chart: real chart data written to cells
    (so it's also readable as a table), colored per-slice to match the app's own
    status palette, percent-only labels, legend on the right."""
    ws.cell(data_row, data_col, "Label")
    ws.cell(data_row, data_col + 1, "Count")
    for i, (label, value) in enumerate(labels_values, start=1):
        ws.cell(data_row + i, data_col, label)
        ws.cell(data_row + i, data_col + 1, value)

    chart = DoughnutChart()
    chart.title = title
    chart.height, chart.width = 8, 12
    n = len(labels_values)
    vals = Reference(ws, min_col=data_col + 1, min_row=data_row, max_row=data_row + n)
    cats = Reference(ws, min_col=data_col, min_row=data_row + 1, max_row=data_row + n)
    chart.add_data(vals, titles_from_data=True)
    chart.set_categories(cats)
    series = chart.series[0]
    series.data_points = [
        DataPoint(idx=i, spPr=GraphicalProperties(solidFill=c)) for i, c in enumerate(colors)
    ]
    dl = DataLabelList()
    dl.showPercent, dl.showCatName, dl.showSerName = True, False, False
    dl.showVal, dl.showLegendKey = False, False
    series.dLbls = dl
    chart.legend = Legend(legendPos="r")
    chart.legend.overlay = False
    ws.add_chart(chart, anchor)


def _stacked_bar(ws, *, anchor: str, title: str, categories: list[str],
                 series_names: list[str], series_values: list[list[int]],
                 colors: list[str], data_col: int, data_row: int) -> None:
    """A native, editable horizontal stacked bar chart (e.g. pass/fail/skip per
    test type), same color convention as the doughnut and the Word report."""
    ws.cell(data_row, data_col, "Category")
    for j, name in enumerate(series_names, start=1):
        ws.cell(data_row, data_col + j, name)
    for i, cat in enumerate(categories, start=1):
        ws.cell(data_row + i, data_col, cat)
        for j, values in enumerate(series_values, start=1):
            ws.cell(data_row + i, data_col + j, values[i - 1])

    chart = BarChart()
    chart.type, chart.grouping, chart.overlap = "bar", "stacked", 100
    chart.title = title
    chart.height, chart.width = 9, 14
    n = len(categories)
    cats = Reference(ws, min_col=data_col, min_row=data_row + 1, max_row=data_row + n)
    data = Reference(ws, min_col=data_col + 1, max_col=data_col + len(series_names),
                     min_row=data_row, max_row=data_row + n)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    for s, c in zip(chart.series, colors, strict=False):
        s.graphicalProperties.solidFill = c
    chart.legend = Legend(legendPos="b")
    chart.y_axis.delete = False
    chart.x_axis.delete = False
    ws.add_chart(chart, anchor)


def _title_block(ws, title: str, subtitle: str, *, ncols: int) -> int:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=ncols)
    c = ws.cell(1, 1, title)
    c.font = _TITLE_FONT
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=ncols)
    s = ws.cell(2, 1, subtitle)
    s.font = _SUB_FONT
    ws.row_dimensions[1].height = 24
    return 4  # first data row (header goes here)


def _header(ws, row: int, headers: list[str]) -> None:
    for i, h in enumerate(headers, 1):
        cell = ws.cell(row, i, h)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        cell.border = _BORDER
    ws.freeze_panes = ws.cell(row + 1, 1)


def _autosize(ws, widths: dict[int, int]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def _save(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _safe(value):
    """Strip the ASCII control characters Excel's XML rejects outright (e.g. a
    raw ESC/BEL byte inside a scraped page's error text or console output).
    openpyxl otherwise raises IllegalCharacterError and the whole export 500s
    on a single bad string, so every value bound for a cell goes through this."""
    if isinstance(value, str):
        return ILLEGAL_CHARACTERS_RE.sub("", value)
    return value


# --------------------------------------------------------------------------- #
def test_cases_workbook(cases, *, project_name: str = "") -> bytes:
    """A test-case register: one row per case with its traceability and steps."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Test Cases"
    hdr = ["ID", "Title", "Type", "Priority", "Risk", "Status", "Requirements",
           "Rules covered", "Technique", "Preconditions", "Steps", "Rationale"]
    start = _title_block(ws, "Test Case Register",
                         f"{project_name} · {len(cases)} case(s)".strip(" ·"), ncols=len(hdr))
    _header(ws, start, hdr)
    r = start + 1
    for case in cases:
        steps = sorted(getattr(case, "steps", []) or [], key=lambda s: s.index)
        steps_text = "\n".join(f"{i + 1}. {s.intent or s.action}"
                               + (f" → {s.expected}" if s.expected else "")
                               for i, s in enumerate(steps))
        row = [
            case.key, case.title, _type_of(case), case.priority, case.risk, case.status,
            ", ".join(case.requirement_refs or []), ", ".join(case.covers or []),
            (case.provenance or {}).get("technique", ""),
            "\n".join(case.preconditions or []), steps_text, case.rationale or "",
        ]
        for i, v in enumerate(row, 1):
            cell = ws.cell(r, i, _safe(v))
            cell.border = _BORDER
            cell.alignment = _WRAP
        r += 1
    _autosize(ws, {1: 16, 2: 40, 3: 14, 4: 10, 5: 8, 6: 11, 7: 16, 8: 16, 9: 16,
                   10: 26, 11: 50, 12: 40})
    return _save(wb)


def _type_of(case) -> str:
    p = (case.provenance or {}).get("type")
    if p:
        return str(p)
    for t in (case.tags or []):
        if t not in ("golden-path", "pairwise", "data-driven"):
            return t
    return case.category


def tcr_workbook(db, project, run) -> bytes:
    """The Test Completion Report as a workbook: a Summary dashboard (KPI cards,
    a results doughnut, a by-type bar chart), a Results sheet (one row per test),
    and a By-Type sheet."""
    from .runs import build_run_report
    rep = build_run_report(db, project, run)
    summary = rep["summary"]
    results = rep["results"]
    by_type = rep.get("by_test_type", {})
    total = summary.get("total", len(results))
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    pass_rate = round(100 * passed / total) if total else 0

    wb = Workbook()
    # --- Summary sheet: a dashboard, not just a key/value list --- #
    ws = wb.active
    ws.title = "Summary"
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    _title_block(ws, "Test Completion Report",
                 f"{project.name} · Run #{run.number} · {run.base_url or ''}".strip(" ·"), ncols=6)

    _kpi_card(ws, 4, 1, "Total", str(total))
    _kpi_card(ws, 4, 2, "Passed", str(passed), _PASS_HEX)
    _kpi_card(ws, 4, 3, "Failed", str(failed), _FAIL_HEX if failed else "6B7486")
    _kpi_card(ws, 4, 4, "Skipped", str(skipped), "6B7486")
    _kpi_card(ws, 4, 5, "Pass rate", f"{pass_rate}%",
             _PASS_HEX if pass_rate >= 80 else _FAIL_HEX if pass_rate < 50 else "B47A0E")

    meta_rows = [
        ("Project", project.name), ("Run", f"#{run.number}"),
        ("Overall result", str(run.status).replace("_", " ").title()),
        ("Environment", run.environment or "-"),
    ]
    r = 8
    for k, v in meta_rows:
        kc = ws.cell(r, 1, k)
        kc.font = Font(bold=True, color="6B7486")
        ws.cell(r, 2, v)
        r += 1
    _autosize(ws, {1: 16, 2: 26, 3: 16, 4: 16, 5: 16})

    # Charts stack below the KPI/meta block (not off to the right) so the whole
    # dashboard, numbers and both charts, is visible on the first screen without
    # scrolling; their backing-data tables live out at column H so they stay
    # readable as plain numbers without cluttering the dashboard's left edge.
    if total:
        _donut(ws, anchor="A14", title="Results", data_col=8, data_row=4,
              labels_values=[("Passed", passed), ("Failed", failed), ("Skipped", skipped)],
              colors=[_PASS_HEX, _FAIL_HEX, _SKIP_HEX])
    if by_type:
        cats = sorted(by_type)
        _stacked_bar(
            ws, anchor="A32", title="Results by type", data_col=8, data_row=10,
            categories=[str(c).replace("_", " ").title() for c in cats],
            series_names=["Passed", "Failed", "Skipped"],
            series_values=[
                [by_type[c].get("passed", 0) for c in cats],
                [by_type[c].get("failed", 0) for c in cats],
                [by_type[c].get("skipped", 0) for c in cats],
            ],
            colors=[_PASS_HEX, _FAIL_HEX, _SKIP_HEX],
        )

    # --- Results sheet --- #
    rs = wb.create_sheet("Results")
    hdr = ["ID", "Test case", "Type", "Result", "Duration (s)", "Error"]
    _header(rs, 1, hdr)
    rr = 2
    for it in results:
        cells = [it["key"], it["title"], str(it["test_type"]), str(it["status"]).upper(),
                 round((it["duration_ms"] or 0) / 1000, 1),
                 (it.get("error") or {}).get("message", "") if it.get("error") else ""]
        for i, v in enumerate(cells, 1):
            c = rs.cell(rr, i, _safe(v))
            c.border = _BORDER
            c.alignment = _WRAP
        res = rs.cell(rr, 4)
        res.font = _PASS_FONT if it["status"] in ("passed", "flaky") else (
            _FAIL_FONT if it["status"] not in ("skipped", "blocked") else Font(color="6B7486"))
        rr += 1
    _autosize(rs, {1: 18, 2: 44, 3: 14, 4: 12, 5: 12, 6: 50})

    # --- By-Type sheet --- #
    bt = wb.create_sheet("By Type")
    _header(bt, 1, ["Type", "Total", "Passed", "Failed", "Skipped"])
    br = 2
    for ty, g in sorted(by_type.items()):
        for i, v in enumerate([str(ty).replace("_", " ").title(), g.get("total", 0),
                               g.get("passed", 0), g.get("failed", 0), g.get("skipped", 0)], 1):
            bt.cell(br, i, v).border = _BORDER
        br += 1
    _autosize(bt, {1: 20, 2: 10, 3: 10, 4: 10, 5: 10})
    return _save(wb)


def rtm_workbook(matrix: list[dict], *, project_name: str = "") -> bytes:
    """The Requirements Traceability Matrix as a workbook: a Dashboard sheet
    (coverage KPIs and a covered/gap doughnut) plus the Traceability detail,
    requirement → rules → tests → coverage status."""
    wb = Workbook()
    covered = sum(1 for r in matrix if r.get("covered"))
    gap = len(matrix) - covered
    cov_pct = round(100 * covered / len(matrix)) if matrix else 0

    # --- Dashboard sheet --- #
    dash = wb.active
    dash.title = "Dashboard"
    dash.page_setup.orientation = "landscape"
    dash.page_setup.fitToWidth = 1
    dash.page_setup.fitToHeight = 0
    dash.sheet_properties.pageSetUpPr.fitToPage = True
    _title_block(dash, "Requirements Traceability Matrix",
                 f"{project_name} · {covered}/{len(matrix)} requirement(s) covered".strip(" ·"), ncols=5)
    _kpi_card(dash, 4, 1, "Requirements", str(len(matrix)))
    _kpi_card(dash, 4, 2, "Covered", str(covered), _PASS_HEX)
    _kpi_card(dash, 4, 3, "Gap", str(gap), _FAIL_HEX if gap else "6B7486")
    _kpi_card(dash, 4, 4, "Coverage", f"{cov_pct}%",
             _PASS_HEX if cov_pct >= 80 else _FAIL_HEX if cov_pct < 50 else "B47A0E")
    _autosize(dash, {1: 16, 2: 16, 3: 16, 4: 16})
    if matrix:
        _donut(dash, anchor="A8", title="Requirement coverage", data_col=8, data_row=4,
              labels_values=[("Covered", covered), ("Gap", gap)],
              colors=[_PASS_HEX, _FAIL_HEX])

    # --- Traceability sheet (detail) --- #
    ws = wb.create_sheet("Traceability")
    hdr = ["Requirement", "Title", "Risk", "Priority", "Rule", "Rule type", "Technique",
           "Tests", "Covered"]
    start = _title_block(ws, "Requirements Traceability Matrix",
                         f"{project_name} · {covered}/{len(matrix)} requirement(s) covered".strip(" ·"),
                         ncols=len(hdr))
    _header(ws, start, hdr)
    r = start + 1
    for req in matrix:
        rules = req.get("rules") or [{}]
        for rule in rules:
            cells = [req["ref"], req["title"], req.get("risk", ""), req.get("priority", ""),
                     rule.get("rule_id", ""), rule.get("rule_type", ""), rule.get("technique", ""),
                     ", ".join(rule.get("tests", [])),
                     "Yes" if (rule.get("covered") if rule else req.get("covered")) else "No"]
            for i, v in enumerate(cells, 1):
                c = ws.cell(r, i, _safe(v))
                c.border = _BORDER
                c.alignment = _WRAP
            cov = ws.cell(r, 9)
            cov.font = _PASS_FONT if cells[8] == "Yes" else _FAIL_FONT
            r += 1
    _autosize(ws, {1: 14, 2: 40, 3: 8, 4: 8, 5: 16, 6: 14, 7: 16, 8: 24, 9: 10})
    return _save(wb)
