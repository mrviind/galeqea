"""WO#5 5-B: the deterministic release chat verbs and their cards."""

from __future__ import annotations

from galeqea.models import Role, TestCase, TestCategory, TestStatus, User
from galeqea.models.base import new_id
from galeqea.services import release_chat


def _approver(db):
    u = User(email=f"ap-{new_id()[:6]}@corp.example", role=Role.APPROVER, password_hash="x")
    db.add(u); db.commit()
    return u


def _cases(db, project, n=3):
    for i in range(n):
        db.add(TestCase(project_id=project.id, key=f"C-{i}", title=f"c{i}",
                        status=TestStatus.APPROVED, category=TestCategory.AUTOMATED,
                        tags=["golden-path"]))
    db.commit()


def _h(db, project, text, user):
    return release_chat.try_handle(db, project, text, user)


def test_the_full_release_conversation(db, project):
    _cases(db, project, 3)
    ap = _approver(db)

    # create release
    txt, blocks = _h(db, project, "create release 1.4 due Sept 15", ap)
    assert blocks[0]["type"] == "milestone_card" and blocks[0]["version"] == "1.4"
    assert blocks[0]["target_date"].endswith("09-15T00:00:00+00:00")

    # exit criteria
    txt, blocks = _h(db, project,
                     "exit criteria: pass rate ≥ 95%, 0 open blockers, "
                     "P1 requirements 100% covered, flaky ≤ 2%", ap)
    crit = {c["metric"]: (c["op"], c["value"]) for c in blocks[0]["exit_criteria"]}
    assert crit["pass_rate"] == (">=", 0.95)
    assert crit["open_blockers"] == ("==", 0)
    assert crit["p1_requirement_coverage"] == (">=", 1.0)
    assert crit["flaky_rate"] == ("<=", 0.02)

    # environment
    txt, blocks = _h(db, project, "add environment staging https://staging.example.com", ap)
    assert blocks[0]["type"] == "environment_card" and blocks[0]["base_url"] == "https://staging.example.com"

    # plan across a configuration matrix
    txt, blocks = _h(db, project, "plan regression for 1.4 on chromium+firefox, mobile+desktop", ap)
    assert blocks[0]["type"] == "plan_card"
    assert blocks[0]["case_count"] == 3
    assert len(blocks[0]["configurations"]) == 4   # 2 browsers × 2 viewports

    # start cycles → one per configuration
    txt, blocks = _h(db, project, "start cycles", ap)
    assert len(blocks) == 4 and all(b["type"] == "cycle_card" for b in blocks)
    assert blocks[0]["case_count"] == 3

    # readiness
    txt, blocks = _h(db, project, "are we ready to release 1.4?", ap)
    assert blocks[0]["type"] == "release_readiness_card"
    assert blocks[0]["verdict"] in ("go", "no_go")

    # sign off (approver, human)
    txt, blocks = _h(db, project, "sign off 1.4 as go, note: verified manually", ap)
    assert blocks[0]["type"] == "milestone_card" and blocks[0]["signoff"]["decision"] == "go"
    assert "immutable" in txt.lower()

    # release report
    txt, blocks = _h(db, project, "release report 1.4", ap)
    assert blocks[0]["type"] == "release_report" and blocks[0]["milestone"]["version"] == "1.4"


def test_non_release_text_is_ignored(db, project):
    assert _h(db, project, "what did the last run fail on?", _approver(db)) is None
    assert _h(db, project, "run the smoke tests", _approver(db)) is None


def test_environment_verbs_beat_the_url_onramp(db, project):
    ap = _approver(db)
    # "add environment <name> <url>" must create an environment, not be read as "test this URL".
    txt, blocks = _h(db, project, "add environment staging http://localhost:8765", ap)
    assert blocks[0]["type"] == "environment_card"
    assert blocks[0]["base_url"] == "http://localhost:8765"
    # "set base url for staging to <url>" updates it in place.
    txt, blocks = _h(db, project, "set base url for staging to https://staging.example.com", ap)
    assert blocks[0]["type"] == "environment_card"
    assert blocks[0]["base_url"] == "https://staging.example.com"
    # A bare "test <url>" is NOT a release verb → release_chat declines, on-ramp handles it.
    assert _h(db, project, "test http://localhost:8765", ap) is None


def test_create_release_is_idempotent(db, project):
    ap = _approver(db)
    t1, b1 = _h(db, project, "create release 1.4", ap)
    assert b1[0]["type"] == "milestone_card"
    t2, b2 = _h(db, project, "create release 1.4", ap)
    assert "already exists" in t2.lower()
    assert b2[0]["id"] == b1[0]["id"]     # same milestone, no duplicate


def test_archive_and_delete_verbs(db, project):
    from galeqea.models import Milestone
    from galeqea.services import release
    ap = _approver(db)
    admin = User(email=f"ad-{new_id()[:6]}@corp.example", role=Role.ADMIN, password_hash="x")
    db.add(admin); db.commit()

    release.create_milestone(db, project, name="R", version="1.4")
    db.commit()

    # Archive is available to an approver and frees the version.
    text, _ = _h(db, project, "archive release 1.4", ap)
    assert "Archived release 1.4" in text
    assert release.milestone_for(db, project.id, "1.4") is None

    # Delete needs admin; an approver is refused with guidance.
    release.create_milestone(db, project, name="R", version="1.5")
    db.commit()
    text, _ = _h(db, project, "delete release 1.5", ap)
    assert "admin role" in text
    assert release.milestone_for(db, project.id, "1.5") is not None

    # An admin can delete.
    text, _ = _h(db, project, "delete release 1.5", admin)
    assert "Deleted release 1.5" in text
    from sqlalchemy import select
    assert db.execute(select(Milestone).where(
        Milestone.project_id == project.id, Milestone.version == "1.5")).scalars().first() is None
