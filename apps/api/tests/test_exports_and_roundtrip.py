"""WO#9-C: exporters, round-trip stale detection, tidy verbs, and covers in the API."""

from __future__ import annotations

import csv
import io
import json

from galeqea.exporters import (
    export_cases,
    gherkin_feature,
    parse_imported_refs,
    qase_json,
    stale_cases,
    testrail_csv,
    xray_csv,
)
from galeqea.services import requirements as req
from galeqea.services import requirements_chat

_PRD = """# Checkout

## Account Registration

- The password must be between 8 and 64 characters.

## Checkout

- The shipping method is one of: standard, express, or overnight.
- The payment method is one of: card, paypal, or credit.
"""


async def _seed(db, project):
    req.ingest_document(db, project_id=project.id, filename="prd.md", data=_PRD.encode(), title="C")
    result = await req.generate(db, project_id=project.id, provider=None)
    cases = req.persist_proposals(db, project_id=project.id, proposals=result["proposals"])
    db.commit()
    return cases


# --- exporters ------------------------------------------------------------- #

async def test_testrail_csv_text_and_steps(db, project):
    cases = await _seed(db, project)
    text = testrail_csv(cases)
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][:3] == ["Title", "Section", "Template"]
    assert len(rows) > 1
    stepped = testrail_csv(cases, separated_steps=True)
    assert "Steps (Step)" in stepped.splitlines()[0]


async def test_xray_csv_has_tests_link_and_cucumber(db, project):
    cases = await _seed(db, project)
    text = xray_csv(cases)
    header = next(csv.reader(io.StringIO(text)))
    assert "Tests" in header and "Cucumber" in header and "Test Type" in header
    # the pairwise case is exported as Cucumber with a Gherkin body
    assert "Cucumber" in text and "Scenario" in text


async def test_qase_json_is_valid(db, project):
    cases = await _seed(db, project)
    data = json.loads(qase_json(cases))
    assert "cases" in data and data["cases"]
    assert all("steps" in c and "title" in c for c in data["cases"])


async def test_gherkin_examples_carry_rows(db, project):
    cases = await _seed(db, project)
    pairwise = next(c for c in cases if "pairwise" in (c.tags or []))
    feature = gherkin_feature(pairwise)
    assert "Scenario Outline:" in feature and "Examples:" in feature
    assert "| shipping method |" in feature or "shipping method" in feature


async def test_playwright_has_requirement_annotations(db, project):
    cases = await _seed(db, project)
    covered = next((c for c in cases if c.covers), cases[0])
    code, _ = export_cases([covered], "playwright")
    assert "annotations.push" in code
    assert "type: 'requirement'" in code or "type: 'rule'" in code


# --- round-trip stale detection -------------------------------------------- #

def test_stale_cases_logic():
    imported = [
        {"key": "T1", "refs": ["REQ-001"]},       # still current
        {"key": "T2", "refs": ["REQ-999"]},       # gone → stale
        {"key": "T3", "refs": []},                # unlinked
    ]
    report = stale_cases(imported, {"REQ-001", "REQ-002"})
    assert report["summary"]["stale"] == 1 and report["stale"][0]["key"] == "T2"
    assert "T3" in report["unlinked"]
    assert "REQ-002" in report["newly_uncovered"]


def test_parse_imported_refs_csv_and_feature():
    csv_text = "Title,References\nLogin works,REQ-001; REQ-002\nCheckout,FR-3\n"
    cases = parse_imported_refs(csv_text, "export.csv")
    assert {r for c in cases for r in c["refs"]} >= {"REQ-001", "REQ-002", "FR-3"}
    feature = "Feature: X\n  Scenario: pay\n    # covers: REQ-014\n    Given a cart\n"
    fcases = parse_imported_refs(feature, "t.feature")
    assert fcases and "REQ-014" in fcases[0]["refs"]


# --- covers round-trips through the HTTP API (manager fix #2) --------------- #

async def test_covers_survives_http_list_and_detail(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    await _seed(db, project)
    client = TestClient(app)
    listing = client.get(f"/api/projects/{project.id}/tests").json()["tests"]
    assert any(t.get("covers") for t in listing), "covers missing from list payload"
    assert all("technique" in t for t in listing)
    with_cover = next(t for t in listing if t.get("covers"))
    detail = client.get(f"/api/projects/{project.id}/tests/{with_cover['id']}").json()
    assert detail["test"]["covers"] == with_cover["covers"]
    assert "covered_rules" in detail and detail["covered_rules"]
    assert detail["covered_rules"][0]["source_anchor"]  # rule anchor present


# --- upload dedup + RTM dedup + tidy verbs (manager fix #1) ----------------- #

def test_reupload_same_content_updates_in_place(db, project):
    from galeqea.models import RequirementDoc
    r1 = req.ingest_document(db, project_id=project.id, filename="p.md", data=_PRD.encode(), title="C")
    r2 = req.ingest_document(db, project_id=project.id, filename="p.md", data=_PRD.encode(), title="C2")
    assert r1.doc.id == r2.doc.id                       # same doc, updated in place
    docs = db.query(RequirementDoc).filter_by(project_id=project.id).count()
    assert docs == 1


def test_dedupe_removes_older_duplicates(db, project):
    # Force two docs with the same content hash by inserting directly.
    import hashlib

    from galeqea.models import RequirementDoc
    h = hashlib.sha256(_PRD.encode()).hexdigest()
    for i in range(2):
        db.add(RequirementDoc(project_id=project.id, title=f"d{i}", content=_PRD,
                              content_sha256=h, source_filename="p.md"))
    db.commit()
    preview = req.dedupe_requirement_docs(db, project_id=project.id, apply=False)
    assert preview["summary"]["groups"] == 1
    applied = req.dedupe_requirement_docs(db, project_id=project.id, apply=True)
    assert applied["summary"]["removed"] == 1
    assert db.query(RequirementDoc).filter_by(project_id=project.id).count() == 1


async def test_rtm_does_not_double_count_duplicate_docs(db, project):
    from galeqea.intelligence.coverage import traceability_matrix
    # ingest the same PRD content twice under different filenames → two docs, same refs
    req.ingest_document(db, project_id=project.id, filename="v1.md", data=_PRD.encode(), title="v1")
    req.ingest_document(db, project_id=project.id, filename="v2.md",
                        data=(_PRD + "\n<!-- v2 -->\n").encode(), title="v2")
    matrix = traceability_matrix(db, project.id)
    refs = [r["ref"] for r in matrix]
    assert len(refs) == len(set(refs)), "RTM lists a requirement ref more than once"


def test_archive_hides_doc_from_rtm(db, project):
    from galeqea.intelligence.coverage import traceability_matrix
    r = req.ingest_document(db, project_id=project.id, filename="p.md", data=_PRD.encode(), title="C")
    before = len(traceability_matrix(db, project.id))
    req.archive_requirement_doc(db, project_id=project.id, doc_id=r.doc.id)
    db.commit()
    assert len(traceability_matrix(db, project.id)) < before


# --- chat verbs ------------------------------------------------------------ #

async def test_chat_traceability_and_export(db, project, humans):
    await _seed(db, project)
    user = humans["author"]
    text, blocks = await requirements_chat.try_handle(db, project, "show traceability", user)
    assert "covered" in text.lower() and blocks[0]["type"] == "traceability"
    text, blocks = await requirements_chat.try_handle(
        db, project, "export tests for DEMO-001 as testrail csv", user)
    assert blocks and blocks[0]["type"] == "export" and blocks[0]["format"] == "testrail_csv"


async def test_chat_dedupe_previews_then_none_for_unrelated(db, project, humans):
    user = humans["author"]
    out = await requirements_chat.try_handle(db, project, "run the smoke tests", user)
    assert out is None
    text, _ = await requirements_chat.try_handle(db, project, "dedupe requirement docs", user)
    assert "duplicate" in text.lower() or "nothing to tidy" in text.lower()
