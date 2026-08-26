"""Session recording: capture fidelity, compression and safety.

The fixture is a real captured session, not a hand-written one. It was produced
by driving the demo application through the actual recorder, so if the in-page
capture script changes shape these tests notice.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from galeqea.engine import record as R
from galeqea.models import StepAction

FIXTURE = Path(__file__).parent / "fixtures_recorded_session.ndjson"


@pytest.fixture()
def captured() -> list[R.Recorded]:
    raw = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]
    return R.parse_events([e for e in raw if e.get("type") == "recorded_action"])


def _make(kind: str, index: int, *, name: str = "", tag: str = "input", ladder=None, **extra):
    return R.Recorded(
        kind=kind, index=index, url=extra.pop("url", "https://app.example/x"),
        element={"accessibleName": name, "tag": tag, "role": extra.pop("role", "textbox"),
                 "ladder": ladder or [{"kind": "testid", "value": name or "el"}],
                 "fingerprint": {"text": extra.pop("text", "")}},
        extra=extra,
    )


# --------------------------------------------------------------------------- #
# Capture fidelity
# --------------------------------------------------------------------------- #
def test_every_interaction_carries_a_locator_ladder(captured):
    """A step without a ladder cannot be replayed, let alone healed."""
    interactive = [e for e in captured if e.kind not in {"navigate", "new_page"}]
    assert interactive, "the fixture recorded no interactions"
    for event in interactive:
        assert event.ladder, f"{event.kind} on {event.name!r} captured no ladder"


def test_test_ids_outrank_everything_else_on_the_ladder(captured):
    """Where a team put a test id, the recorder must use it first."""
    card = next(e for e in captured if e.kind == "fill" and "Card" in e.name)
    assert card.ladder[0] == {"kind": "testid", "value": "cc-number-v2"}
    # And the alternatives are still there, so healing has somewhere to fall to.
    assert any(r["kind"] == "role" for r in card.ladder[1:])


def test_credential_values_are_never_captured(captured):
    """The value must be absent from the stream, not redacted downstream."""
    card = next(e for e in captured if e.kind == "fill" and "Card" in e.name)
    assert card.element["secret"] is True
    assert "generate" in card.extra["value"]
    assert "text" not in card.extra["value"]
    # Belt and braces: the typed digits appear nowhere in the whole recording.
    assert "9111222233334444" not in FIXTURE.read_text()


def test_ordinary_values_are_captured(captured):
    email = next(e for e in captured if e.kind == "fill" and "Email" in e.name)
    assert email.extra["value"]["text"] == "ravi@example.com"
    assert email.element["secret"] is False


# --------------------------------------------------------------------------- #
# Compression
# --------------------------------------------------------------------------- #
def test_focus_click_before_typing_is_dropped():
    events = [_make("click", 1, name="Email"), _make("fill", 2, name="Email", value={"text": "a@b.c"})]
    kept, notes = R.compress(events)
    assert [e.kind for e in kept] == ["fill"]
    assert any("focus click" in n for n in notes)


def test_a_click_on_a_different_field_is_kept():
    """The collapse must be scoped to one element, or real clicks vanish."""
    events = [_make("click", 1, name="Terms", ladder=[{"kind": "testid", "value": "terms"}]),
              _make("fill", 2, name="Email", ladder=[{"kind": "testid", "value": "email"}],
                    value={"text": "a@b.c"})]
    kept, _ = R.compress(events)
    assert [e.kind for e in kept] == ["click", "fill"]


def test_successive_edits_collapse_to_the_final_value():
    events = [_make("fill", i, name="Email", value={"text": t})
              for i, t in enumerate(["r", "ra", "ravi@example.com"], start=1)]
    kept, notes = R.compress(events)
    assert len(kept) == 1
    assert kept[0].extra["value"]["text"] == "ravi@example.com"
    assert any("keystroke" in n for n in notes)


def test_submit_following_its_own_click_is_absorbed(captured):
    kept, notes = R.compress(captured)
    assert not any(e.kind == "submit" for e in kept)
    assert any("redundant submit" in n for n in notes)


def test_repeated_navigation_to_the_same_place_collapses():
    events = [_make("navigate", 1, url="https://app.example/a/"),
              _make("navigate", 2, url="https://app.example/a")]
    kept, _ = R.compress(events)
    assert len(kept) == 1


def test_navigation_to_a_different_path_is_kept():
    events = [_make("navigate", 1, url="https://app.example/a"),
              _make("navigate", 2, url="https://app.example/b")]
    kept, _ = R.compress(events)
    assert len(kept) == 2


# --------------------------------------------------------------------------- #
# Step rendering
# --------------------------------------------------------------------------- #
def test_the_recorded_session_becomes_a_runnable_step_list(captured):
    proposal = R.build_proposal(captured, base_url="http://127.0.0.1:8765")
    actions = [s["action"] for s in proposal["steps"]]
    assert actions[0] == StepAction.GOTO
    assert StepAction.FILL in actions
    assert StepAction.CLICK in actions
    assert proposal["stats"]["steps"] < proposal["stats"]["captured"]


def test_a_navigation_the_user_caused_is_asserted_not_repeated(captured):
    """Re-navigating would skip whatever the click was meant to do."""
    proposal = R.build_proposal(captured, base_url="http://127.0.0.1:8765")
    gotos = [s for s in proposal["steps"] if s["action"] == StepAction.GOTO]
    assert len(gotos) == 1, "only the opening navigation should be a goto"
    assert any(s["action"] == StepAction.EXPECT_URL for s in proposal["steps"])


def test_urls_are_stored_as_paths_not_absolute(captured):
    proposal = R.build_proposal(captured, base_url="http://127.0.0.1:8765")
    for step in proposal["steps"]:
        url = (step["value"] or {}).get("url")
        if url:
            assert not url.startswith("http"), f"{url} welds the test to one host"


def test_alt_click_becomes_an_assertion(captured):
    proposal = R.build_proposal(captured, base_url="http://127.0.0.1:8765")
    assert any(s["action"] == StepAction.EXPECT_VISIBLE for s in proposal["steps"])


def test_volatile_text_is_not_asserted():
    """An order number changes every run; asserting it guarantees a false failure."""
    events = [_make("assert", 1, name="AC-10042", tag="span", role="generic",
                    ladder=[{"kind": "css", "value": "#order"}], text="AC-10042")]
    steps = R.to_steps(events)
    assert [s["action"] for s in steps] == [StepAction.EXPECT_VISIBLE]


def test_stable_text_is_asserted():
    events = [_make("assert", 1, name="Order confirmed", tag="h2", role="heading",
                    ladder=[{"kind": "role", "role": "heading", "name": "Order confirmed"}],
                    text="Order confirmed")]
    steps = R.to_steps(events)
    assert [s["action"] for s in steps] == [StepAction.EXPECT_VISIBLE, StepAction.EXPECT_TEXT]


def test_secret_steps_keep_the_generator_and_are_flagged(captured):
    proposal = R.build_proposal(captured, base_url="http://127.0.0.1:8765")
    secret = next(s for s in proposal["steps"] if (s["options"] or {}).get("secret"))
    assert "generate" in secret["value"]
    assert proposal["stats"]["secrets_protected"] == 1
    assert "credentials" in proposal["rationale"]


def test_a_test_with_no_assertions_says_so():
    """Silence here would let a click-through test look like verified coverage."""
    events = [_make("navigate", 1, url="https://app.example/a"),
              _make("click", 2, name="Next", role="button")]
    proposal = R.build_proposal(events)
    assert proposal["stats"]["assertions"] == 0
    assert "No assertions were captured" in proposal["rationale"]


def test_the_title_prefers_the_button_over_a_trailing_link(captured):
    """The session ends on a nav link, but the goal was the payment."""
    proposal = R.build_proposal(captured, base_url="http://127.0.0.1:8765")
    assert proposal["title"] == "Confirm payment"


# --------------------------------------------------------------------------- #
# App Model
# --------------------------------------------------------------------------- #
def test_recorded_elements_populate_the_app_model(db, project, captured):
    """Recording binds elements immediately, so a recorded test is healable
    before it has ever been run."""
    from galeqea.engine import discovery
    from galeqea.models import AppElement

    screen = discovery.observe_screen(
        db, project_id=project.id,
        observation={"url": "http://127.0.0.1:8765/index.html", "title": "Acme Checkout",
                     "roles": ["textbox", "textbox", "button", "link"]},
    )
    db.flush()

    for event in captured:
        element = event.element
        if not element.get("ladder"):
            continue
        discovery.observe_element(
            db, project_id=project.id, screen=screen,
            observation={
                "role": element.get("role", ""),
                "accessibleName": element.get("accessibleName", ""),
                "tag": element.get("tag", ""),
                "locator": element["ladder"][0],
                "fingerprint": element.get("fingerprint") or {},
            },
        )
    db.flush()

    stored = db.query(AppElement).filter(AppElement.project_id == project.id).all()
    names = {e.accessible_name for e in stored}
    assert "Email address" in names
    assert "Card number" in names
    card = next(e for e in stored if e.accessible_name == "Card number")
    assert card.locators[0] == {"kind": "testid", "value": "cc-number-v2"}


# --------------------------------------------------------------------------- #
# Promotion
# --------------------------------------------------------------------------- #
def test_promotion_is_idempotent_and_lands_as_proposed(db, project, captured):
    """Having driven the browser is not the same as having approved the test."""
    from galeqea.models import RecordingSession, TestStatus
    from galeqea.services import recording as service

    session = RecordingSession(
        project_id=project.id, base_url="http://127.0.0.1:8765",
        status="finished",
        proposal=R.build_proposal(captured, base_url="http://127.0.0.1:8765"),
    )
    db.add(session)
    db.flush()

    first = service.promote(db, session_id=session.id, project_id=project.id)
    assert first.status == TestStatus.PROPOSED
    assert len(first.steps) == session.proposal["stats"]["steps"]

    second = service.promote(db, session_id=session.id, project_id=project.id)
    assert second.id == first.id, "a second promote must not create a second test"
