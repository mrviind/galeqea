"""Turning a recorded session into a test worth keeping.

A raw capture stream is a transcript, and a transcript makes a bad test. Someone
signing in generates roughly this:

    click(input#email) · fill(input#email, "a") · fill(input#email, "an") · …
    · click(button) · submit(form) · navigate(/dashboard)

Replaying that verbatim is slow, fragile and unreadable. What a maintainer wants
is four steps: fill the e-mail, fill the password, press Sign in, assert the
dashboard loaded. Getting from one to the other is this module's whole job, and
the rules are deliberately conservative - when a collapse would change what the
test exercises, the step is kept.

Two things are never inferred. Values from password and payment fields are not in
the stream to begin with (the capture script replaces them at source), so they
arrive here as generator references and stay that way. And assertions are only
created where the tester asked for one with Alt+click, or where a navigation
genuinely changed the URL - a recorder that invents assertions produces a test
that fails for reasons nobody chose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..models import StepAction

#: Keys that mean something a test should reproduce. Everything else a person
#: types is already captured as the field's final value.
MEANINGFUL_KEYS = {"Enter", "Escape", "Tab"}


@dataclass(slots=True)
class Recorded:
    """One captured interaction, normalised out of the runner's NDJSON."""

    kind: str
    index: int = 0
    url: str = ""
    title: str = ""
    element: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    @property
    def ladder(self) -> list:
        return list(self.element.get("ladder") or [])

    @property
    def name(self) -> str:
        return (self.element.get("accessibleName") or "").strip()

    @property
    def role(self) -> str:
        return self.element.get("role") or ""

    @property
    def tag(self) -> str:
        return self.element.get("tag") or ""

    @property
    def input_type(self) -> str:
        return self.element.get("type") or ""

    def same_element(self, other: Recorded | None) -> bool:
        """Do two events refer to the same element?

        Compared on the top ladder rung rather than on the fingerprint: the
        fingerprint contains the element's text and box, both of which change
        while someone types into it, so comparing those would report that a field
        stopped being itself halfway through being filled.
        """
        if other is None or not self.ladder or not other.ladder:
            return False
        return self.ladder[0] == other.ladder[0]


def parse_events(events: list[dict]) -> list[Recorded]:
    """Normalise the runner's ``recorded_action`` events."""
    known = {"kind", "index", "url", "title", "element", "sessionId", "frameUrl", "isMainFrame"}
    out: list[Recorded] = []
    for event in events:
        if event.get("type") not in (None, "recorded_action"):
            continue
        kind = event.get("kind")
        if not kind:
            continue
        out.append(Recorded(
            kind=kind,
            index=int(event.get("index") or 0),
            url=event.get("url") or "",
            title=event.get("title") or "",
            element=event.get("element") or {},
            extra={k: v for k, v in event.items() if k not in known and k != "type"},
        ))
    return sorted(out, key=lambda r: r.index)


# --------------------------------------------------------------------------- #
# Compression
# --------------------------------------------------------------------------- #
def compress(events: list[Recorded]) -> tuple[list[Recorded], list[str]]:
    """Collapse the transcript. Returns the kept events and what was dropped."""
    kept: list[Recorded] = []
    notes: list[str] = []
    dropped = {"focus_click": 0, "keystroke": 0, "redundant_submit": 0, "duplicate_nav": 0}

    for event in events:
        previous = kept[-1] if kept else None

        # A click that only put the caret in a field, immediately followed by
        # typing into that same field. Playwright's fill focuses on its own.
        if event.kind == "fill" and previous is not None and previous.kind == "click" \
                and event.same_element(previous):
            kept.pop()
            dropped["focus_click"] += 1
            kept.append(event)
            continue

        # Successive edits to one field: only the value it ended up with matters.
        if event.kind == "fill" and previous is not None and previous.kind == "fill" \
                and event.same_element(previous):
            kept[-1] = event
            dropped["keystroke"] += 1
            continue

        # A form submit that follows the click or Enter that caused it is the
        # same intent recorded twice.
        if event.kind == "submit" and previous is not None and previous.kind in {"click", "press"}:
            dropped["redundant_submit"] += 1
            continue

        # Navigations that report the same URL twice (redirect chains, hash
        # changes, framework routers announcing themselves).
        if event.kind == "navigate" and previous is not None and previous.kind == "navigate" \
                and _same_location(previous.url, event.url):
            kept[-1] = event
            dropped["duplicate_nav"] += 1
            continue

        kept.append(event)

    for reason, count in dropped.items():
        if count:
            notes.append(f"{count} {reason.replace('_', ' ')} event(s) collapsed")
    return kept, notes


def _same_location(a: str, b: str) -> bool:
    left, right = urlparse(a), urlparse(b)
    return (left.netloc, left.path.rstrip("/")) == (right.netloc, right.path.rstrip("/"))


# --------------------------------------------------------------------------- #
# Step rendering
# --------------------------------------------------------------------------- #
#: Text that is an order number, a total, a date or a count rather than a name.
#: Using it as an element's label produces a step description that goes stale the
#: next time the page renders, even though the locator underneath still works.
VOLATILE_TEXT = re.compile(r"^[A-Za-z]{0,5}[-\u2013\s]?[\d][\d\s\-.,/:£$€%]*$")


def _label(event: Recorded) -> str:
    """How to refer to an element in an intent line a person will read."""
    if event.name and not VOLATILE_TEXT.match(event.name.strip()):
        return f"'{event.name[:60]}'"
    ladder = event.ladder
    if ladder:
        rung = ladder[0]
        if rung.get("kind") == "testid":
            return f"the element tagged '{rung['value']}'"
        if rung.get("value"):
            return f"'{str(rung['value'])[:60]}'"
    return f"the {event.tag or 'element'}"


def _relative(url: str, base: str) -> str:
    """Store paths, not absolute URLs, so a test is not welded to one host."""
    if not url:
        return ""
    parsed = urlparse(url)
    if base:
        base_parsed = urlparse(base)
        if base_parsed.netloc and base_parsed.netloc == parsed.netloc:
            return parsed.path + (f"?{parsed.query}" if parsed.query else "") or "/"
    return url


def to_steps(events: list[Recorded], *, base_url: str = "") -> list[dict]:
    """Render compressed events as typed test steps."""
    steps: list[dict] = []
    first_navigation = True

    def add(action: str, intent: str, *, expected: str = "", target=None, value=None, options=None):
        steps.append({
            "action": action,
            "intent": intent,
            "expected": expected,
            "target": target or {},
            "value": value or {},
            "options": options or {},
        })

    for event in events:
        target = {"ladder": event.ladder, "recorded_name": event.name, "role": event.role} \
            if event.ladder else {}

        if event.kind == "navigate":
            path = _relative(event.url, base_url)
            if first_navigation:
                first_navigation = False
                add(StepAction.GOTO, f"Open {path or event.url}",
                    expected=f"the page at {path or event.url} loads",
                    value={"url": path or event.url})
            else:
                # A navigation the tester caused is an *outcome*, so it is
                # asserted rather than re-performed. Re-navigating would skip
                # whatever the click was supposed to do.
                add(StepAction.EXPECT_URL, f"The application navigates to {path}",
                    expected=f"the URL is {path}",
                    value={"url": path, "mode": "contains"})
            continue

        if event.kind == "click":
            add(StepAction.CLICK, f"Click {_label(event)}",
                expected="the control responds", target=target)
            continue

        if event.kind == "toggle":
            checked = bool(event.extra.get("checked"))
            add(StepAction.CHECK if checked else StepAction.UNCHECK,
                f"{'Check' if checked else 'Clear'} {_label(event)}",
                expected=f"{_label(event)} is {'selected' if checked else 'cleared'}",
                target=target)
            continue

        if event.kind == "fill":
            value = dict(event.extra.get("value") or {})
            if value.pop("secret", False):
                # The value was never captured. The step carries the generator
                # reference the capture script substituted at source.
                add(StepAction.FILL, f"Enter a value into {_label(event)}",
                    expected="the field accepts the value",
                    target=target, value=value,
                    options={"secret": True})
            else:
                shown = str(value.get("text", ""))
                add(StepAction.FILL,
                    f"Enter {shown[:40]!r} into {_label(event)}" if shown
                    else f"Clear {_label(event)}",
                    expected="the field accepts the value",
                    target=target, value=value)
            continue

        if event.kind == "select":
            selected = event.extra.get("selected") or []
            labels = [s.get("label") or s.get("value") for s in selected]
            add(StepAction.SELECT,
                f"Select {', '.join(str(x) for x in labels)[:60]!r} in {_label(event)}",
                expected="the selection is applied",
                target=target,
                value={"options": [s.get("value") for s in selected]})
            continue

        if event.kind == "upload":
            files = event.extra.get("files") or []
            add(StepAction.UPLOAD, f"Attach {', '.join(files)[:60]} to {_label(event)}",
                expected="the file is accepted",
                target=target, value={"files": files})
            continue

        if event.kind == "press":
            key = event.extra.get("key", "Enter")
            if key not in MEANINGFUL_KEYS:
                continue
            add(StepAction.PRESS, f"Press {key} in {_label(event)}",
                expected="the key is handled", target=target, value={"key": key})
            continue

        if event.kind == "submit":
            add(StepAction.CLICK, f"Submit {_label(event)}",
                expected="the form is submitted", target=target)
            continue

        if event.kind == "assert":
            add(StepAction.EXPECT_VISIBLE, f"{_label(event)} is visible",
                expected=f"{_label(event)} is present on screen", target=target)
            text = (event.element.get("fingerprint") or {}).get("text") or event.name
            # Only assert text that is stable enough to be worth asserting: long
            # blocks are prone to copy edits, and anything containing a number is
            # usually a count, a total or a timestamp that changes per run.
            if text and len(text) <= 80 and not re.search(r"\d", text):
                add(StepAction.EXPECT_TEXT, f"{_label(event)} reads {text[:60]!r}",
                    expected=f"the text is {text[:60]!r}",
                    target=target, value={"text": text})
            continue

        if event.kind == "new_page":
            add(StepAction.NOTE,
                f"The application opened a second window at {event.url or 'an unknown URL'}",
                expected="continue in the new window - multi-window flows need authoring by hand")
            continue

    return steps


# --------------------------------------------------------------------------- #
# Proposal
# --------------------------------------------------------------------------- #
def title_for(events: list[Recorded], fallback: str = "Recorded session") -> str:
    """Name the test after what the person appeared to be doing.

    Buttons outrank links, and the last one wins. A session usually ends with
    some incidental navigation - clicking away to check something - and naming
    the test after that trailing link ("Account settings") buries what it
    actually covers ("Confirm payment"). The last *button* pressed is the goal
    far more reliably than the last thing clicked.
    """
    def named(kinds: set[str], roles: set[str] | None = None) -> list[Recorded]:
        return [e for e in events
                if e.kind in kinds and e.name and (roles is None or e.role in roles)]

    for candidates in (named({"click", "submit", "press"}, {"button"}),
                       named({"click", "submit", "press"})):
        if candidates:
            return candidates[-1].name[:70]
    navigations = [e for e in events if e.kind == "navigate"]
    if navigations:
        path = urlparse(navigations[-1].url).path.strip("/") or "home"
        return f"Walk through {path}"
    return fallback


def build_proposal(
    events: list[Recorded],
    *,
    base_url: str = "",
    title: str = "",
    charter: str = "",
) -> dict:
    """One reviewable test proposal from a recorded session."""
    compressed, notes = compress(events)
    steps = to_steps(compressed, base_url=base_url)
    secrets = sum(1 for s in steps if (s.get("options") or {}).get("secret"))
    assertions = sum(1 for s in steps
                     if s["action"] in {StepAction.EXPECT_VISIBLE, StepAction.EXPECT_TEXT,
                                        StepAction.EXPECT_URL})

    rationale = (
        f"Recorded from a live session: {len(events)} captured interactions compressed to "
        f"{len(steps)} steps. Every step carries the full locator ladder observed at record "
        f"time, so the elements are bound to the App Model and repairable."
    )
    if not assertions:
        # Said plainly rather than papered over with an invented assertion: a
        # test that only clicks passes as long as nothing throws.
        rationale += (
            " No assertions were captured — this test proves the flow completes without "
            "an error, not that it produced the right result. Alt+click during recording, "
            "or add expectations before approving."
        )
    if secrets:
        rationale += (
            f" {secrets} field(s) were recognised as credentials; their values were never "
            "read and the steps reference a generator instead."
        )

    return {
        "title": title or title_for(compressed),
        "category": "automated",
        "priority": "medium",
        "risk": "medium",
        "rationale": rationale,
        "requirement_refs": [],
        "tags": ["recorded"],
        "charter": charter,
        "steps": steps,
        "source": "recording",
        "technique": "session recording",
        "stats": {
            "captured": len(events),
            "steps": len(steps),
            "assertions": assertions,
            "secrets_protected": secrets,
            "compression_notes": notes,
        },
    }
