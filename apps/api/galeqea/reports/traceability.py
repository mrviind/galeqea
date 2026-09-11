"""The traceability report: requirement → test → last result.

The artefact auditors ask for and teams rarely have: every requirement with the
tests that reference it and how those tests last ran, in one matrix. It answers
"is this requirement actually verified, and by what?" without a spreadsheet
archaeology dig.
"""

from __future__ import annotations

from .common import api_href, cap_markdown, envelope, md_table, ui_href


def build_traceability_report(db, project) -> dict:
    """The canonical JSON traceability matrix. Deterministic; ordered by ref."""
    from ..intelligence.coverage import traceability_matrix

    rows = traceability_matrix(db, project.id)

    items = []
    for row in rows:
        ref = row["ref"]
        items.append({
            "id": ref,
            "ref": ref,
            "title": row["title"],
            "risk": row["risk"],
            "priority": row.get("priority", ""),
            "acceptance_criteria": row["acceptance_criteria"],
            "open_questions": row["open_questions"],
            "source_anchor": row.get("source_anchor", {}),
            "covered": row["covered"],
            "rules": row.get("rules", []),
            "rules_total": row.get("rules_total", 0),
            "rules_covered": row.get("rules_covered", 0),
            "tests": sorted(row["tests"], key=lambda t: t["key"]),
            "href": api_href(project.id, "requirements", ref),
            "ui_href": ui_href("requirements"),
        })

    covered = sum(1 for it in items if it["covered"])
    total = len(items)
    rules_total = sum(it["rules_total"] for it in items)
    rules_covered = sum(it["rules_covered"] for it in items)

    return envelope(
        project.id, "traceability",
        ("traceability", "report.json"),
        {
            "summary": {
                "total": total,
                "covered": covered,
                "uncovered": total - covered,
                "rules_total": rules_total,
                "rules_covered": rules_covered,
                "rules_uncovered": rules_total - rules_covered,
            },
            "requirements": items,
        },
    )


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def traceability_report_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# Traceability matrix",
        "",
        f"**{s['covered']}/{s['total']} requirement(s) covered**, "
        f"{s['uncovered']} without an approved test. "
        f"**{s.get('rules_covered', 0)}/{s.get('rules_total', 0)} rule(s) covered.**",
        "",
        "## Matrix",
        "",
        md_table(
            ["Requirement", "Risk", "P", "Rules covered", "Covered", "Tests"],
            [
                [f"{it['ref']} {it['title']}", it["risk"], it.get("priority", ""),
                 f"{it['rules_covered']}/{it['rules_total']}",
                 "yes" if it["covered"] else "no", len(it["tests"])]
                for it in report["requirements"]
            ],
        ),
    ]
    return cap_markdown("\n".join(lines) + "\n", report["href"])


# --------------------------------------------------------------------------- #
# HTML: a standalone, self-contained RTM a test manager can open or attach.
# --------------------------------------------------------------------------- #
def traceability_report_html(report: dict) -> str:
    import html as _html

    from .common import donut_svg

    s = report["summary"]

    def esc(x: object) -> str:
        return _html.escape(str(x))

    cov_pct = round(100 * s["covered"] / s["total"]) if s["total"] else 0
    dashboard = (
        '<div class="dash">'
        + donut_svg([("covered", s["covered"], "1f9d55"), ("gap", s["uncovered"], "c0392b")],
                    center_value=f"{cov_pct}%", center_sub="covered",
                    ink="#e6e9ef", muted="#98a2b3")
        + '<div class="kpis">'
        + f'<div class="kpi"><b>{s["total"]}</b><span>Requirements</span></div>'
        + f'<div class="kpi ok"><b>{s["covered"]}</b><span>Covered</span></div>'
        + f'<div class="kpi gap"><b>{s["uncovered"]}</b><span>Gap</span></div>'
        + f'<div class="kpi"><b>{s.get("rules_covered", 0)}/{s.get("rules_total", 0)}</b>'
        + '<span>Rules covered</span></div>'
        + '</div></div>'
    )

    def status_pill(covered: bool) -> str:
        cls = "ok" if covered else "gap"
        return f'<span class="pill {cls}">{"covered" if covered else "gap"}</span>'

    body: list[str] = []
    for it in report["requirements"]:
        anchor = it.get("source_anchor") or {}
        where = " › ".join(anchor.get("heading_path", []))
        if anchor.get("page"):
            where += f" · p{anchor['page']}"
        rule_rows = []
        for rule in it.get("rules", []):
            tests = ", ".join(esc(t) for t in rule.get("tests", [])) or "-"
            rule_rows.append(
                f'<tr class="rule"><td>{esc(rule["rule_id"])}</td>'
                f'<td>{esc(rule["rule_type"])}</td>'
                f'<td>{esc(rule.get("technique") or "-")}</td>'
                f'<td>{status_pill(rule["covered"])}</td>'
                f'<td>{tests}</td></tr>'
            )
        rules_html = "".join(rule_rows) or '<tr class="rule"><td colspan="5" class="muted">no rules extracted</td></tr>'
        body.append(
            f'<section class="req">'
            f'<h3>{esc(it["ref"])}: {esc(it["title"])} '
            f'<span class="tag risk-{esc(it["risk"])}">{esc(it["risk"])}</span> '
            f'<span class="tag p">{esc(it.get("priority",""))}</span> '
            f'{status_pill(it["covered"])}</h3>'
            f'<div class="where">{esc(where)}</div>'
            f'<div class="counts">{it["rules_covered"]}/{it["rules_total"]} rules covered · '
            f'{len(it["tests"])} test(s)</div>'
            f'<table class="rules"><thead><tr><th>Rule</th><th>Type</th><th>Technique</th>'
            f'<th>Coverage</th><th>Tests</th></tr></thead><tbody>{rules_html}</tbody></table>'
            f'</section>'
        )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Traceability matrix</title>
<style>
  :root {{ --bg:#0b0e14; --fg:#e6e9ef; --muted:#98a2b3; --line:#232a36;
           --card:#131822; --ok:#1f9d55; --gap:#c0392b; --accent:#4f8cff; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:24px; background:var(--bg); color:var(--fg);
          font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  h1 {{ font-size:20px; margin:0 0 4px; }}
  .summary {{ color:var(--muted); margin-bottom:20px; }}
  .summary strong {{ color:var(--fg); }}
  section.req {{ background:var(--card); border:1px solid var(--line);
                 border-radius:12px; padding:14px 16px; margin-bottom:12px; }}
  section.req h3 {{ font-size:15px; margin:0 0 4px; font-weight:600; }}
  .where {{ color:var(--muted); font-size:12px; margin-bottom:6px; }}
  .counts {{ color:var(--muted); font-size:12px; margin-bottom:10px; }}
  table.rules {{ width:100%; border-collapse:collapse; font-size:12.5px; }}
  table.rules th {{ text-align:left; color:var(--muted); font-weight:500;
                    border-bottom:1px solid var(--line); padding:4px 8px; }}
  table.rules td {{ padding:4px 8px; border-bottom:1px solid var(--line); vertical-align:top; }}
  .muted {{ color:var(--muted); }}
  .pill {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:11px;
           font-weight:600; }}
  .pill.ok {{ background:rgba(31,157,85,.18); color:#4ade80; }}
  .pill.gap {{ background:rgba(192,57,43,.18); color:#f87171; }}
  .tag {{ display:inline-block; padding:1px 7px; border-radius:6px; font-size:11px;
          background:#1c2431; color:var(--muted); margin-left:4px; }}
  .tag.risk-critical {{ color:#f87171; }} .tag.risk-high {{ color:#fbbf24; }}
  .tag.p {{ color:var(--accent); }}
  .dash {{ display:flex; align-items:center; gap:24px; flex-wrap:wrap;
           background:var(--card); border:1px solid var(--line); border-radius:12px;
           padding:16px 20px; margin-bottom:20px; }}
  .kpis {{ display:flex; gap:28px; flex-wrap:wrap; }}
  .kpi {{ display:flex; flex-direction:column; }}
  .kpi b {{ font-size:22px; font-weight:700; }}
  .kpi span {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
  .kpi.ok b {{ color:#4ade80; }} .kpi.gap b {{ color:#f87171; }}
</style></head>
<body>
  <h1>Traceability matrix</h1>
  {dashboard}
  {''.join(body)}
</body></html>
"""
