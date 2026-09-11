"""Report v2: the release report a stakeholder reads.

Built on the canonical run report (``reports.runs``), it adds the sections a QA
lead and a release manager actually want: an executive summary with the three
headline risks and the trend since last run, readiness, coverage vs the plan and
the discovered app map (which pages went untested), root-cause groups and their
dispositions, accessibility broken down by axe impact, performance vs budgets, the
cost, and the concrete next actions. Deterministic; renders to Markdown and to a
self-contained HTML share page. A *stakeholder* mode redacts names, links and
screenshots for sharing outside the team.
"""

from __future__ import annotations

import html
import re

from ..models import Run

_IMPACTS = ["critical", "serious", "moderate", "minor"]


def build(db, project, run: Run, journey=None, *, stakeholder: bool = False) -> dict:
    from ..services import journeys, readiness
    from ..services.regression import compute_delta
    from ..services.triage import triage_board
    from .common import api_href
    from .runs import build_run_report

    # The canonical endpoints have a run but not always an active journey; resolve
    # it from the run's target so coverage/readiness are complete when possible,
    # and degrade gracefully (never error) when there is none.
    if journey is None:
        journey = (journeys.for_target(db, project.id, run.base_url)
                   or journeys.active_journey(db, project.id))
    base = build_run_report(db, project, run)
    results = base["results"]
    by_type = base["by_test_type"]
    gate = readiness.evaluate(db, journey, run)
    board = triage_board(db, run, journey)
    delta = compute_delta(db, run)

    coverage = _coverage(journey, base)
    a11y = _a11y_by_impact(results)
    risks = _headline_risks(board, gate, coverage)
    trend = _trend(db, project, run, base)
    dispositions = _dispositions(board)

    report = {
        # Envelope: v2 is now THE report, served by the canonical endpoints, the
        # CLI, the MCP resource and share alike. One generator, one document.
        "schema_version": "2.0",
        "report": "run",
        "project_id": project.id,
        "href": api_href(project.id, "runs", run.id, "report.json"),
        "kind": "release_report_v2",
        "generated_for": run.id,
        "run": base["run"],
        "exec_summary": {
            "verdict": gate["verdict"],
            "headline_risks": risks,
            "trend": trend,
            "one_liner": _one_liner(gate, base, trend),
        },
        "since_last_run": delta,
        "exploratory": _exploratory(db, project, journey),
        "readiness": gate,
        "summary": base["summary"],
        "cost": base["cost"],
        "by_test_type": by_type,
        "coverage": coverage,
        "accessibility_by_impact": a11y,
        "root_causes": dispositions,
        "next_actions": _next_actions(board, gate, coverage),
        "results": [_redact(it, stakeholder) for it in results],
        "stakeholder_mode": stakeholder,
    }
    if stakeholder:
        _redact_report(report)
    return report


_URL_RE = re.compile(r'https?://[^\s/"\')]+(/[^\s"\')]*)?')


def redact_urls(text: str) -> str:
    """Absolute URLs → their path (or '/'), so a shared artifact never leaks the
    host/target on every row."""
    return _URL_RE.sub(lambda m: m.group(1) or "/", text or "")


def _redact_report(report: dict) -> None:
    """Stakeholder mode: strip URLs from every text field, in place. This is the
    same redaction the HTML gets, applied so MD/JSON/JUnit can't leak what HTML hides."""
    ex = report["exec_summary"]
    ex["one_liner"] = redact_urls(ex["one_liner"])
    for r in ex["headline_risks"]:
        r["risk"] = redact_urls(str(r["risk"]))
        r.pop("signature", None)  # a raw hash is noise for a stakeholder
    for g in report["root_causes"]:
        g["label"] = redact_urls(g["label"])
    for it in report["results"]:
        it["title"] = redact_urls(it["title"])
        if it.get("error"):
            it["error"]["message"] = redact_urls(it["error"]["message"])
    report["run"] = {**report["run"], "base_url": redact_urls(report["run"].get("base_url", ""))}
    gate = report["readiness"]
    gate["target"] = redact_urls(gate.get("target", ""))
    for c in gate.get("criteria", []):
        c["evidence"] = ""  # evidence links are internal, not for outside sharing
    delta = report.get("since_last_run") or {}
    delta["what_changed"] = redact_urls(delta.get("what_changed", ""))
    for r in delta.get("rows", []):
        r["unit"] = redact_urls(r.get("unit", ""))


# --------------------------------------------------------------------------- #
def _exploratory(db, project, journey) -> list[dict]:
    """Recent exploratory sessions against this target and their anomalies."""
    if journey is None:
        return []
    from ..services.exploration import sessions_for_journey
    return sessions_for_journey(db, project.id, journey.target)


def _coverage(journey, base) -> dict:
    plan = (journey.plan if journey else None) or {}
    planned = (plan.get("typed") or {}).get("totals", {}).get("test_count", 0)
    built = len((journey.test_ids if journey else None) or [])
    disc = (journey.discovery if journey else None) or {}
    pages = [p for p in (disc.get("pages") or []) if p]
    tested_pages = sorted({it.get("unit") for it in base["results"]
                           if it.get("unit", "").startswith("/")})
    from urllib.parse import urlparse
    discovered_paths = sorted({urlparse(p).path or "/" for p in pages})
    untested = [p for p in discovered_paths if p not in tested_pages]
    return {
        "built": built, "planned": planned,
        "coverage_pct": round(built / planned * 100) if planned else 100,
        "pages_discovered": len(discovered_paths),
        "pages_tested": len(tested_pages),
        "untested_pages": untested,
    }


def _a11y_by_impact(results: list[dict]) -> dict:
    counts = dict.fromkeys(_IMPACTS, 0)
    for it in results:
        if it["test_type"] != "a11y" or not it.get("error"):
            continue
        msg = it["error"].get("message", "")
        for impact in _IMPACTS:
            m = re.search(rf"(\d+)\s+{impact}", msg)
            if m:
                counts[impact] += int(m.group(1))
    return {"by_impact": counts, "total": sum(counts.values())}


def _headline_risks(board, gate, coverage) -> list[dict]:
    risks = []
    for g in board["groups"]:
        if g["verdict"] in ("failed", "blocked") and not g["disposition"]:
            risks.append({"severity": "high" if g["rca"] in ("app-bug",) else "medium",
                          "risk": g["label"], "count": g["count"], "rca": g["rca"],
                          "signature": g["signature"]})
    if coverage["coverage_pct"] < 80:
        risks.append({"severity": "medium",
                      "risk": f"coverage is {coverage['coverage_pct']}% of the plan",
                      "count": len(coverage["untested_pages"]), "rca": "coverage"})
    sev_order = {"high": 0, "medium": 1, "low": 2}
    risks.sort(key=lambda r: (sev_order.get(r["severity"], 9), -r["count"]))
    return risks[:3]


def _trend(db, project, run, base) -> dict:
    from sqlalchemy import and_, desc, select
    prev = db.execute(
        select(Run).where(and_(Run.project_id == project.id, Run.trigger == run.trigger,
                               Run.base_url == run.base_url, Run.number < run.number))
        .order_by(desc(Run.number)).limit(1)
    ).scalars().first()
    if prev is None:
        return {"has_previous": False}
    prev_totals = prev.totals or {}
    return {
        "has_previous": True, "previous_run": prev.number,
        "failed_delta": base["summary"]["failed"] - (prev_totals.get("failed", 0)),
        "passed_delta": base["summary"]["passed"] - (prev_totals.get("passed", 0)),
    }


def _dispositions(board) -> list[dict]:
    return [{"label": g["label"], "rca": g["rca"], "count": g["count"],
             "verdict": g["verdict"], "confidence": g.get("confidence"),
             "disposition": (g["disposition"] or {}).get("disposition") if g["disposition"] else None,
             "keys": g.get("keys", [])}
            for g in board["groups"]]


def _next_actions(board, gate, coverage) -> list[str]:
    actions = []
    if board["open_count"]:
        actions.append(f"Disposition the {board['open_count']} open failure group(s) in triage.")
    for key in gate["failed_criteria"]:
        actions.append({"a11y": "Fix the critical/serious accessibility violations.",
                        "security": "Resolve the critical security findings.",
                        "perf": "Bring the pages over the Core Web Vitals budget back in.",
                        "coverage": "Raise plan coverage before release.",
                        "flaky": "Stabilise or quarantine the flaky tests."}.get(key, f"Address {key}."))
    if coverage["untested_pages"]:
        actions.append(f"{len(coverage['untested_pages'])} discovered page(s) are untested. "
                       "Run 'test all pages' to cover them.")
    if not actions:
        actions.append("All criteria pass, ready for a human sign-off.")
    return actions


def _one_liner(gate, base, trend) -> str:
    s = base["summary"]
    verdict = gate["verdict"].replace("_", "-").upper()
    trend_bit = ""
    if trend.get("has_previous"):
        d = trend["failed_delta"]
        trend_bit = (f" {abs(d)} more failing" if d > 0 else f" {abs(d)} fewer failing"
                     if d < 0 else " no change") + f" vs run #{trend['previous_run']}"
    return (f"{verdict}: {s['passed']}/{s['total']} passed, {s['failed']} failed"
            f"{',' + trend_bit if trend_bit else ''}.")


def _redact(item: dict, stakeholder: bool) -> dict:
    if not stakeholder:
        return item
    # Stakeholder mode: drop the internal links and any step detail that could
    # carry a name or a screenshot path.
    return {**item, "href": "", "ui_href": "", "steps": []}


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def to_markdown(report: dict) -> str:
    from .common import md_table
    from .runs import _detail_name
    run = report["run"]
    ex = report["exec_summary"]
    cov = report["coverage"]
    a11y = report["accessibility_by_impact"]
    lines = [
        f"# Release report: run #{run['number']} · {run['title'] or 'untitled'}",
        "",
        f"**{ex['one_liner']}**",
        "",
        "## Headline risks",
    ]
    if ex["headline_risks"]:
        for r in ex["headline_risks"]:
            lines.append(f"- **[{r['severity']}]** {r['risk']} ({r['count']}×, {r['rca']})")
    else:
        lines.append("- None. Nothing is blocking.")
    delta = report["since_last_run"]
    lines += ["", "## Since last run", "", delta["what_changed"]]
    if delta["has_baseline"] and delta["rows"]:
        lines += ["",
                  md_table(["Change", "Test", "Was → now"],
                           [[r["delta"].replace("_", " "), r["unit"] or r["key"],
                             f"{r['baseline']} → {r['current']}"] for r in delta["rows"][:20]])]
    lines += ["", "## Readiness",
              f"Verdict: **{report['readiness']['verdict'].replace('_', '-').upper()}**"]
    for c in report["readiness"]["criteria"]:
        lines.append(f"- {'✓' if c['pass'] else '✗'} {c['label']}: {c['actual']} (need {c['threshold']})")
    lines += ["", "## Coverage",
              f"- Built {cov['built']} of {cov['planned']} planned test(s) ({cov['coverage_pct']}%)",
              f"- {cov['pages_tested']} of {cov['pages_discovered']} discovered page(s) tested"]
    if cov["untested_pages"]:
        lines.append(f"- Untested: {', '.join(cov['untested_pages'][:10])}")
    if a11y["total"]:
        lines += ["", "## Accessibility by impact",
                  ", ".join(f"{n} {imp}" for imp, n in a11y["by_impact"].items() if n)]
    expl = report.get("exploratory") or []
    if expl:
        lines += ["", "## Exploratory"]
        for s in expl:
            lines.append(f"- {s['summary'] or s['charter']}")
            for f in s["findings"][:8]:
                lines.append(f"  - [{f['severity']}] {f['kind']}: {f['title'] or f['detail'][:80]}")
    lines += ["", "## Next actions"]
    lines += [f"- {a}" for a in report["next_actions"]]
    # One document, one H1: the run detail is a section, not a second report.
    from ..services.test_plan import type_label
    lines += ["", "## By test type", "",
              md_table(["Type", "Total", "Passed", "Failed", "Skipped"],
                       [[type_label(t), g["total"], g["passed"], g["failed"], g["skipped"]]
                        for t, g in sorted(report["by_test_type"].items())]),
              "## Detailed results", "",
              md_table(["Test", "Status", "ms", "Notes"],
                       [[_detail_name(it), it["status"], it["duration_ms"],
                         (it["error"]["message"] if it["error"]
                          else it.get("skip_reason") or ("healed" if it["healed"] else ""))]
                        for it in report["results"]])]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# HTML share page: self-contained, no external assets.
# --------------------------------------------------------------------------- #
def to_html(report: dict) -> str:
    from .common import donut_svg
    run = report["run"]
    ex = report["exec_summary"]
    gate = report["readiness"]
    summary = report["summary"]
    verdict = gate["verdict"].replace("_", "-").upper()
    go = gate["verdict"] == "go"
    e = html.escape

    def results_donut():
        total = summary.get("total", 0)
        passed, failed = summary.get("passed", 0), summary.get("failed", 0)
        skipped = summary.get("skipped", 0)
        pass_rate = round(100 * passed / total) if total else 0
        svg = donut_svg(
            [("passed", passed, "3fb950"), ("failed", failed, "f85149"),
             ("skipped", skipped, "9aa4b2")],
            center_value=f"{pass_rate}%" if total else "-", center_sub="pass rate")
        legend = "".join(
            f'<li><span class="dot" style="background:#{color}"></span>{label}: {n} '
            f'<span class="muted">({round(100 * n / total) if total else 0}%)</span></li>'
            for label, n, color in
            [("Passed", passed, "3fb950"), ("Failed", failed, "f85149"), ("Skipped", skipped, "9aa4b2")])
        return f'<div class="donut-row">{svg}<ul class="legend">{legend}</ul></div>'

    def risk_rows():
        if not ex["headline_risks"]:
            return '<li class="ok">None. Nothing is blocking.</li>'
        return "".join(
            f'<li><span class="sev {e(r["severity"])}">{e(r["severity"])}</span> '
            f'{e(str(r["risk"]))} <span class="muted">({r["count"]}×, {e(r["rca"])})</span></li>'
            for r in ex["headline_risks"])

    def crit_rows():
        return "".join(
            f'<tr class="{"pass" if c["pass"] else "fail"}"><td>{"✓" if c["pass"] else "✗"}</td>'
            f'<td>{e(c["label"])}</td><td class="num">{e(str(c["actual"]))}</td>'
            f'<td class="muted">{e(str(c["threshold"]))}</td></tr>'
            for c in gate["criteria"])

    def type_rows():
        from ..services.test_plan import type_label

        def tbar(g):
            total = max(1, g["total"])
            segs = [("3fb950", g["passed"]), ("f85149", g["failed"]), ("9aa4b2", g["skipped"])]
            spans = "".join(f'<span style="width:{100 * n / total:.1f}%;background:#{c}"></span>'
                            for c, n in segs if n > 0)
            return f'<div class="tbar">{spans}</div>'

        return "".join(
            f'<tr><td>{e(type_label(t))}</td><td class="tbar-cell">{tbar(g)}</td>'
            f'<td class="num">{g["total"]}</td>'
            f'<td class="num pass">{g["passed"]}</td><td class="num fail">{g["failed"]}</td>'
            f'<td class="num muted">{g["skipped"]}</td></tr>'
            for t, g in sorted(report["by_test_type"].items()))

    def action_items():
        return "".join(f"<li>{e(a)}</li>" for a in report["next_actions"])

    delta = report["since_last_run"]
    since = (f'<h2>Since last run</h2><div class="card"><p>{e(delta["what_changed"])}</p>'
             + ("<ul>" + "".join(
                 f'<li><span class="sev {"high" if r["delta"] in ("new_fail", "still_failing") else "medium"}">'
                 f'{e(r["delta"].replace("_", " "))}</span> {e(r["unit"] or r["key"])} '
                 f'<span class="muted">{e(r["baseline"])} → {e(r["current"])}</span></li>'
                 for r in delta["rows"][:20]) + "</ul>" if delta["rows"] else "")
             + "</div>")
    expl_sessions = report.get("exploratory") or []
    exploratory = ""
    if expl_sessions:
        items = []
        for s in expl_sessions:
            fs = "".join(
                f'<li><span class="sev {"high" if f["severity"] == "high" else "medium"}">'
                f'{e(f["severity"])}</span> {e(f["kind"])}: {e(f["title"] or f["detail"][:80])}</li>'
                for f in s["findings"][:8])
            items.append(f'<p>{e(s["summary"] or s["charter"])}</p>'
                         + (f"<ul>{fs}</ul>" if fs else ""))
        exploratory = '<h2>Exploratory</h2><div class="card">' + "".join(items) + "</div>"
    cov = report["coverage"]
    cost = report["cost"]
    return _HTML_TEMPLATE.format(
        title=e(f"Release report: run #{run['number']}"),
        subtitle=e(run["title"] or run.get("base_url") or ""),
        verdict=verdict, verdict_class="go" if go else "nogo",
        one_liner=e(ex["one_liner"]),
        env=e(run["environment"]), target=e(run.get("base_url") or "-"),
        results_donut=results_donut(),
        risks=risk_rows(), criteria=crit_rows(), types=type_rows(),
        coverage_pct=cov["coverage_pct"], built=cov["built"], planned=cov["planned"],
        pages_tested=cov["pages_tested"], pages_discovered=cov["pages_discovered"],
        cost_calls=cost["model_calls"] if "model_calls" in cost else cost.get("calls", 0),
        actions=action_items(), since=since, exploratory=exploratory,
        stakeholder=" · stakeholder view" if report["stakeholder_mode"] else "",
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
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
.oneliner {{ font-size:17px; margin:14px 0 0; }}
ul {{ margin:0; padding-left:0; list-style:none; }}
ul li {{ padding:5px 0; border-bottom:1px solid var(--line); }}
ul li:last-child {{ border-bottom:0; }}
.sev {{ font-size:11px; text-transform:uppercase; padding:2px 7px; border-radius:5px; margin-right:8px; }}
.sev.high {{ background:rgba(248,81,73,.15); color:var(--fail); }}
.sev.medium {{ background:rgba(212,160,23,.15); color:var(--accent); }}
.muted {{ color:var(--ink2); }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
td, th {{ text-align:left; padding:7px 8px; border-bottom:1px solid var(--line); }}
.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.pass {{ color:var(--pass); }} .fail {{ color:var(--fail); }}
.meta {{ display:flex; gap:24px; flex-wrap:wrap; color:var(--ink2); font-size:13px; margin-top:8px; }}
.bar {{ height:8px; background:var(--line); border-radius:6px; overflow:hidden; margin-top:6px; }}
.bar > span {{ display:block; height:100%; background:var(--accent); }}
.donut-row {{ display:flex; align-items:center; gap:28px; flex-wrap:wrap; }}
.legend {{ flex:1; min-width:160px; }}
.legend li {{ display:flex; align-items:center; gap:8px; border-bottom:0; padding:4px 0; }}
.dot {{ width:10px; height:10px; border-radius:50%; flex-shrink:0; }}
.tbar-cell {{ width:32%; }}
.tbar {{ display:flex; height:10px; border-radius:5px; overflow:hidden; background:var(--line); }}
.tbar > span {{ display:block; height:100%; }}
footer {{ color:var(--ink2); font-size:12px; margin-top:40px; text-align:center; }}
</style></head>
<body><div class="wrap">
<h1>{title}</h1>
<p class="sub">{subtitle}{stakeholder}</p>
<span class="verdict {verdict_class}">{verdict}</span>
<p class="oneliner">{one_liner}</p>
<div class="meta"><span>environment: {env}</span><span>target: {target}</span>
  <span>model cost: {cost_calls} call(s) · $0 re-run</span></div>
<div class="card">{results_donut}</div>
{since}
<h2>Headline risks</h2><div class="card"><ul>{risks}</ul></div>
<h2>Readiness</h2><div class="card"><table>{criteria}</table></div>
<h2>Coverage</h2><div class="card">
  <div>Built {built} of {planned} planned · {coverage_pct}% · {pages_tested}/{pages_discovered} pages tested</div>
  <div class="bar"><span style="width:{coverage_pct}%"></span></div></div>
<h2>By test type</h2><div class="card"><table>
  <tr><th>Type</th><th>Share</th><th class="num">Total</th><th class="num">Pass</th><th class="num">Fail</th><th class="num">Skip</th></tr>
  {types}</table></div>
{exploratory}
<h2>Next actions</h2><div class="card"><ul>{actions}</ul></div>
<footer>Generated by GaleQEA · re-running a built test calls no model and costs nothing.</footer>
</div></body></html>
"""
