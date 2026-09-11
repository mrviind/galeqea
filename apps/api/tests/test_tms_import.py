"""WO#6 6-F. Import test assets from other tools: Gherkin/CSV → proposals (through
the review gate), JUnit → a historical run, with a proposed CSV column mapping.
"""

from __future__ import annotations

from sqlalchemy import select

from galeqea.models import Run, TestCase, TestStatus
from galeqea.services import tms_import

_FEATURE = """Feature: Checkout
  Scenario: Pay with a card
    Given I have items in my cart
    When I pay with a valid card
    Then the order is confirmed
  Scenario: Declined card
    Given I have items in my cart
    When I pay with a declined card
    Then I see a decline message
"""

_CSV = "Title,Steps,Expected Result,Priority\n" \
       "Login works,\"1. open login\n2. enter creds\",dashboard shown,High\n" \
       "Logout works,click logout,login shown,Low\n"

_JUNIT = """<testsuites><testsuite name="s" tests="2">
  <testcase name="a" classname="c" time="0.5"/>
  <testcase name="b" classname="c" time="1.2"><failure message="boom">trace</failure></testcase>
</testsuite></testsuites>"""


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #
def test_parse_gherkin():
    props = tms_import.parse_gherkin(_FEATURE)
    assert [p["title"] for p in props] == ["Pay with a card", "Declined card"]
    assert props[0]["steps"][-1]["expected"] == "Then the order is confirmed"
    assert props[0]["source"] == "gherkin"


def test_guess_mapping_and_parse_csv():
    headers = tms_import.csv_headers(_CSV)
    mapping = tms_import.guess_mapping(headers)
    assert mapping["title"] == "Title" and mapping["steps"] == "Steps"
    props = tms_import.parse_csv(_CSV, mapping)
    assert props[0]["title"] == "Login works"
    assert len(props[0]["steps"]) == 2 and props[0]["priority"] == "high"


def test_parse_junit():
    cases = tms_import.parse_junit(_JUNIT)
    assert {c["status"] for c in cases} == {"passed", "failed"}
    assert next(c for c in cases if c["status"] == "failed")["message"] == "boom"


# --------------------------------------------------------------------------- #
# import_file routing
# --------------------------------------------------------------------------- #
def test_import_gherkin_creates_proposals(db, project, humans):
    out = tms_import.import_file(db, project, filename="x.feature", content=_FEATURE,
                                 actor=humans["author"])
    db.commit()
    assert out["count"] == 2
    props = db.execute(select(TestCase).where(TestCase.project_id == project.id,
                                              TestCase.status == TestStatus.PROPOSED)).scalars().all()
    assert len(props) == 2 and props[0].provenance["origin"] == "gherkin"


def test_import_csv_needs_mapping_then_imports(db, project, humans):
    first = tms_import.import_file(db, project, filename="x.csv", content=_CSV,
                                   actor=humans["author"])
    assert first["needs_mapping"] is True and first["suggested"]["title"] == "Title"

    out = tms_import.import_file(db, project, filename="x.csv", content=_CSV,
                                 actor=humans["author"], mapping=first["suggested"])
    db.commit()
    assert out["count"] == 2


def test_import_junit_creates_run(db, project, humans):
    out = tms_import.import_file(db, project, filename="r.xml", content=_JUNIT,
                                 actor=humans["author"])
    db.commit()
    assert out["run_id"] and out["passed"] == 1 and out["failed"] == 1
    run = db.get(Run, out["run_id"])
    assert run.status == "failed" and run.trigger == "import"


def test_http_import_gherkin(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    client = TestClient(app)
    r = client.post(f"/api/projects/{project.id}/tests/import",
                    files={"file": ("x.feature", _FEATURE, "text/plain")})
    assert r.status_code == 200 and r.json()["count"] == 2


def test_http_import_csv_returns_mapping(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    client = TestClient(app)
    r = client.post(f"/api/projects/{project.id}/tests/import",
                    files={"file": ("x.csv", _CSV, "text/csv")})
    assert r.status_code == 200 and r.json()["needs_mapping"] is True
