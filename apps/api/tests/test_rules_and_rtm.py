"""WO#9-B: atomic rules, extended techniques, pairwise, and the rules-layer RTM."""

from __future__ import annotations

from itertools import combinations

from galeqea.intelligence import rules as rule_engine
from galeqea.intelligence import testdesign as td
from galeqea.intelligence.coverage import traceability_matrix
from galeqea.reports import traceability as trace_report
from galeqea.services import requirements as req_service

_PRD = """# Checkout

## Account Registration

- The password must be between 8 and 64 characters.
- The email field is required and must match a standard email format.

## Roles

- Only an Admin may issue a refund.

## Checkout

- The shipping method is one of: standard, express, or overnight.
- The payment method is one of: card, paypal, or credit.
- If the shipping country is not the billing country, then a customs declaration is required.
"""


# --- deterministic extractor (No-AI) --------------------------------------- #

def test_extractor_classifies_rule_types():
    assert rule_engine.extract_rules("The password must be between 8 and 64 characters.")[0].rule_type == "validation"
    assert rule_engine.extract_rules("Only an Admin may issue a refund.")[0].rule_type == "permission"
    assert rule_engine.extract_rules("If X then the system shall block Y.")[0].rule_type == "functional"
    nav = rule_engine.extract_rules("The system shall redirect the user to sign-in.")[0]
    assert nav.rule_type == "nav"


def test_extractor_captures_permission_role():
    rule = rule_engine.extract_rules("Only an Admin may issue a refund.")[0]
    assert rule.constraints["role"] == "Admin" and rule.constraints["negated"] is False


# --- pairwise & string edges ----------------------------------------------- #

def test_pairwise_covers_all_pairs_and_is_deterministic():
    params = {"a": ["1", "2", "3"], "b": ["x", "y", "z"], "c": ["p", "q"]}
    rows = td.pairwise(params)
    for k1, k2 in combinations(params, 2):
        for v1 in params[k1]:
            for v2 in params[k2]:
                assert any(r[k1] == v1 and r[k2] == v2 for r in rows), (k1, v1, k2, v2)
    assert len(rows) < 3 * 3 * 2          # fewer than the full cross-product
    assert td.pairwise(params) == rows    # deterministic


def test_string_edges_added_to_length_field():
    a = td.analyse("The username must be between 3 and 20 characters.")
    labels = {v.label for v in a.values}
    assert {"empty", "whitespace only", "unicode & emoji", "far over the maximum length"} <= labels


def test_pairwise_appears_for_two_enums():
    a = td.analyse("Ship one of: standard, express, overnight. Pay one of: card, paypal, credit.")
    assert a.pairwise
    assert "pairwise" in a.techniques_applied


# --- persistence + coverage join ------------------------------------------- #

def _ingest(db, project):
    return req_service.ingest_document(
        db, project_id=project.id, filename="prd.md", data=_PRD.encode(), title="Checkout")


def test_rules_are_persisted_on_ingest(db, project):
    from galeqea.models import RequirementRule
    _ingest(db, project)
    rules = db.query(RequirementRule).filter_by(project_id=project.id).all()
    assert rules
    assert any(r.rule_type == "permission" for r in rules)
    assert all(r.rule_id.endswith(("R1", "R2", "R3", "R4")) for r in rules)


async def test_generate_no_ai_produces_rules_and_cases_with_covers(db, project):
    _ingest(db, project)
    result = await req_service.generate(db, project_id=project.id, provider=None)
    proposals = result["proposals"]
    assert proposals
    # at least one proposal links to a rule via covers
    assert any(p.get("covers") for p in proposals)
    # a pairwise proposal exists for the two-enum checkout rules
    assert any("pairwise" in (p.get("tags") or []) for p in proposals)


def test_rtm_has_rules_layer_and_priority(db, project):
    _ingest(db, project)
    matrix = traceability_matrix(db, project.id)
    assert matrix
    row = next(r for r in matrix if "password" in r["title"].lower())
    assert row["rules_total"] >= 1
    assert row["priority"] in {"P1", "P2", "P3", "P4"}
    assert "rules" in row and row["rules"][0]["rule_id"]


def test_rtm_html_renders(db, project):
    _ingest(db, project)
    report = trace_report.build_traceability_report(db, project)
    html = trace_report.traceability_report_html(report)
    assert "<title>Traceability matrix</title>" in html
    assert "rules covered" in html
    # a rule id appears in the HTML
    assert "-R1" in html
    # the coverage dashboard: an inline SVG doughnut plus its KPI cards
    assert "<svg" in html and "stroke-dasharray" in html
    s = report["summary"]
    assert f'<b>{s["total"]}</b>' in html and f'<b>{s["covered"]}</b>' in html


# --- ambiguity gate -------------------------------------------------------- #

def test_ambiguity_produces_assumption_tags(db, project):
    req_service.ingest_document(
        db, project_id=project.id, filename="vague.md",
        data=b"# Perf\n\n- The page must load reasonably fast for various users.\n", title="V")
    from galeqea.models import RequirementItem
    item = db.query(RequirementItem).filter_by(project_id=project.id).first()
    assert item.open_questions            # 'reasonably'/'various'/'fast' flagged
    assumptions = req_service._assumptions(item.open_questions)
    assert assumptions and all(a.startswith("ASSUMPTION:") for a in assumptions)
