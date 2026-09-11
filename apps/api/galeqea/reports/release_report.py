"""Milestone / release report: the release view of a milestone, in the WO#2
formats (JSON / Markdown / HTML). Built from the metrics service and the readiness
evaluation; deterministic, no model."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Milestone
from ..services import release, release_metrics

SCHEMA_VERSION = "1.0"


def build(db: Session, milestone: Milestone) -> dict:
    metrics = release_metrics.compute(db, milestone)
    readiness = release.evaluate_readiness(db, milestone)
    return {
        "schema_version": SCHEMA_VERSION,
        "milestone": {"id": milestone.id, "name": milestone.name, "version": milestone.version,
                      "status": milestone.status,
                      "target_date": milestone.target_date.isoformat() if milestone.target_date else None,
                      "signoff": milestone.signoff or None},
        "readiness": {"verdict": readiness["verdict"], "criteria": readiness["criteria"]},
        "metrics": metrics,
    }


def _pct(x) -> str:
    return f"{round((x or 0) * 100, 1)}%"


def to_markdown(db: Session, milestone: Milestone) -> str:
    r = build(db, milestone)
    m, mx = r["milestone"], r["metrics"]
    c = mx["counts"]
    verdict = r["readiness"]["verdict"].upper().replace("_", "-")
    lines = [
        f"# Release {m['version']}: {m['name']}",
        "",
        f"**Readiness: {verdict}** · status: {m['status']}"
        + (f" · signed off by {m['signoff']['by']} ({m['signoff']['decision']})"
           if m["signoff"] else ""),
        "",
        "## Exit criteria",
        "",
        "| Criterion | Target | Actual | Met |",
        "|---|---|---|---|",
    ]
    for crit in r["readiness"]["criteria"]:
        lines.append(f"| {crit['metric']} | {crit['op']} {crit['target']} | "
                     f"{crit['actual']} | {'✅' if crit['met'] else '❌'} |")
    lines += [
        "",
        "## Metrics",
        "",
        f"- **Execution progress**: {_pct(mx['execution_progress'])} "
        f"({c['executed']}/{c['planned']})",
        f"- **Pass rate**: {_pct(mx['pass_rate'])} ({c['passed']} passed, {c['failed']} failed)",
        f"- **Requirement coverage**: {_pct(mx['requirement_coverage'])} "
        f"(tested {_pct(mx['tested_coverage'])}; P1 {_pct(mx['p1_requirement_coverage'])})",
        f"- **Automation ratio**: {_pct(mx['automation_ratio'])}",
        f"- **Flaky rate**: {_pct(mx['flaky_rate'])}",
        f"- **Open blockers**: {mx['open_blockers']}",
        f"- **Defect density**: {mx['defect_density']} per 100 cases ({c['defects']} defects)",
        f"- **MTTR**: {round(mx['mttr_ms'] / 1000, 1)}s",
        f"- **Effort variance**: {_pct(mx['effort_variance'])}",
        f"- **Cycles**: {c['cycles']} · **Cases**: {c['cases']} · **Requirements**: {c['requirements']}",
    ]
    return "\n".join(lines)


def _status_macro(colour: str, title: str) -> str:
    return (f'<ac:structured-macro ac:name="status">'
            f'<ac:parameter ac:name="colour">{colour}</ac:parameter>'
            f'<ac:parameter ac:name="title">{title}</ac:parameter>'
            f'</ac:structured-macro>')


def to_confluence_storage(db: Session, milestone: Milestone) -> str:
    """Confluence storage-format (XHTML) body: tables + a Go/No-Go status macro. This
    is what a published release-report page contains."""
    import html as _html

    r = build(db, milestone)
    m, mx = r["milestone"], r["metrics"]
    c = mx["counts"]
    go = r["readiness"]["verdict"] == "go"
    parts = [
        f"<h1>Release {_html.escape(m['version'])}: {_html.escape(m['name'])}</h1>",
        f"<p><strong>Readiness:</strong> {_status_macro('Green' if go else 'Red', 'GO' if go else 'NO-GO')}"
        f" &nbsp; status: {_html.escape(m['status'])}"
        + (f" &nbsp; signed off by {_html.escape(m['signoff']['by'])} "
           f"({_html.escape(m['signoff']['decision'])})" if m.get("signoff") else "") + "</p>",
        "<h2>Exit criteria</h2>",
        "<table><tbody><tr><th>Criterion</th><th>Target</th><th>Actual</th><th>Met</th></tr>",
    ]
    for crit in r["readiness"]["criteria"]:
        met = _status_macro("Green", "MET") if crit["met"] else _status_macro("Red", "NOT MET")
        parts.append(f"<tr><td>{_html.escape(str(crit['metric']))}</td>"
                     f"<td>{_html.escape(str(crit['op']))} {_html.escape(str(crit['target']))}</td>"
                     f"<td>{_html.escape(str(crit['actual']))}</td><td>{met}</td></tr>")
    parts.append("</tbody></table>")
    parts.append("<h2>Metrics</h2><table><tbody>")
    rows = [
        ("Execution progress", f"{_pct(mx['execution_progress'])} ({c['executed']}/{c['planned']})"),
        ("Pass rate", f"{_pct(mx['pass_rate'])} ({c['passed']} passed, {c['failed']} failed)"),
        ("Requirement coverage", _pct(mx["requirement_coverage"])),
        ("P1 requirement coverage", _pct(mx["p1_requirement_coverage"])),
        ("Automation ratio", _pct(mx["automation_ratio"])),
        ("Flaky rate", _pct(mx["flaky_rate"])),
        ("Open blockers", str(mx["open_blockers"])),
        ("Defect density", f"{mx['defect_density']} per 100 cases"),
    ]
    for label, value in rows:
        parts.append(f"<tr><th>{label}</th><td>{_html.escape(value)}</td></tr>")
    parts.append("</tbody></table>")
    parts.append("<p><em>Published by GaleQEA.</em></p>")
    return "".join(parts)


def to_html(db: Session, milestone: Milestone) -> str:
    """A self-contained, styled release dashboard: a Go/No-Go badge, a pass-rate
    doughnut, exit criteria as a checklist, and the rest of the metrics as KPI
    cards. Same visual language as the run release report (``report_v2.to_html``)
    so a stakeholder sees one consistent dashboard style across GaleQEA's HTML
    reports, not a plain preformatted text dump."""
    import html as _html

    from .common import donut_svg

    r = build(db, milestone)
    m, mx = r["milestone"], r["metrics"]
    c = mx["counts"]
    e = _html.escape
    go = r["readiness"]["verdict"] == "go"
    pass_rate = round((mx["pass_rate"] or 0) * 100)

    donut = donut_svg(
        [("passed", c["passed"], "3fb950"), ("failed", c["failed"], "f85149")],
        center_value=f"{pass_rate}%", center_sub="pass rate")

    def crit_rows():
        return "".join(
            f'<tr class="{"pass" if crit["met"] else "fail"}">'
            f'<td>{"✓" if crit["met"] else "✗"}</td><td>{e(str(crit["metric"]))}</td>'
            f'<td class="num muted">{e(str(crit["op"]))} {e(str(crit["target"]))}</td>'
            f'<td class="num">{e(str(crit["actual"]))}</td></tr>'
            for crit in r["readiness"]["criteria"])

    def kpi(label: str, value: str, cls: str = "") -> str:
        return f'<div class="kpi {cls}"><b>{e(value)}</b><span>{e(label)}</span></div>'

    kpis = "".join([
        kpi("Execution progress", f"{_pct(mx['execution_progress'])}"),
        kpi("Requirement coverage", _pct(mx["requirement_coverage"])),
        kpi("Automation ratio", _pct(mx["automation_ratio"])),
        kpi("Flaky rate", _pct(mx["flaky_rate"]),
           "warn" if (mx["flaky_rate"] or 0) > 0.05 else ""),
        kpi("Open blockers", str(mx["open_blockers"]),
           "fail" if mx["open_blockers"] else ""),
        kpi("Defect density", f"{mx['defect_density']}/100 cases"),
        kpi("MTTR", f"{round(mx['mttr_ms'] / 1000, 1)}s"),
        kpi("Effort variance", _pct(mx["effort_variance"])),
    ])
    signoff = (f'<div class="signoff">Signed off by {e(m["signoff"]["by"])} '
              f'({e(m["signoff"]["decision"])})</div>' if m.get("signoff") else "")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Release {e(m['version'])}: {e(m['name'])}</title>
<style>
:root {{ --bg:#0e1116; --card:#161b22; --line:#2a2f3a; --ink:#e6edf3; --ink2:#9aa4b2;
  --pass:#3fb950; --fail:#f85149; --accent:#d4a017; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
.wrap {{ max-width:820px; margin:0 auto; padding:40px 20px 80px; }}
h1 {{ font-size:24px; margin:0 0 4px; }}
h2 {{ font-size:15px; text-transform:uppercase; letter-spacing:.06em; color:var(--ink2); margin:32px 0 10px; }}
.sub {{ color:var(--ink2); margin:0 0 20px; }}
.verdict {{ display:inline-block; padding:6px 14px; border-radius:8px; font-weight:700; letter-spacing:.04em; }}
.verdict.go {{ background:rgba(63,185,80,.15); color:var(--pass); }}
.verdict.nogo {{ background:rgba(248,81,73,.15); color:var(--fail); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:18px 20px; margin:10px 0; }}
.signoff {{ color:var(--ink2); margin-top:8px; font-size:13px; }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
td, th {{ text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.muted {{ color:var(--ink2); }}
tr.pass td:first-child {{ color:var(--pass); }} tr.fail td:first-child {{ color:var(--fail); }}
.donut-row {{ display:flex; align-items:center; gap:28px; flex-wrap:wrap; }}
.kpis {{ display:flex; flex-wrap:wrap; gap:20px 32px; }}
.kpi {{ display:flex; flex-direction:column; min-width:120px; }}
.kpi b {{ font-size:20px; font-weight:700; }}
.kpi span {{ font-size:10.5px; text-transform:uppercase; letter-spacing:.04em; color:var(--ink2); }}
.kpi.fail b {{ color:var(--fail); }} .kpi.warn b {{ color:var(--accent); }}
footer {{ color:var(--ink2); font-size:12px; margin-top:40px; text-align:center; }}
</style></head>
<body><div class="wrap">
<h1>Release {e(m['version'])}: {e(m['name'])}</h1>
<p class="sub">status: {e(m['status'])}</p>
<span class="verdict {'go' if go else 'nogo'}">{'GO' if go else 'NO-GO'}</span>
{signoff}
<div class="card"><div class="donut-row">{donut}
  <div>{c['passed']} passed, {c['failed']} failed of {c['executed']} executed
  ({c['planned']} planned)</div></div></div>
<h2>Exit criteria</h2>
<div class="card"><table><tr><th></th><th>Criterion</th><th class="num">Target</th>
  <th class="num">Actual</th></tr>{crit_rows()}</table></div>
<h2>Metrics</h2>
<div class="card"><div class="kpis">{kpis}</div></div>
<footer>Generated by GaleQEA.</footer>
</div></body></html>
"""
