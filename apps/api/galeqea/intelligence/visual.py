"""Semantic visual regression.

Pixel diffing produces a red rectangle and a shrug: it cannot tell a font
hinting change from a missing checkout button, so teams learn to ignore it.
GaleQEA compares three layers and only escalates when the cheap ones disagree:

1. **Structural** - diff the accessibility snapshots. Catches a vanished button,
   a renamed heading, a control that lost its label. Deterministic, offline, and
   immune to anti-aliasing.
2. **Perceptual** - a difference hash over the image. Catches gross layout
   breakage without flagging sub-pixel noise.
3. **Semantic judgement** - only when the first two disagree or the change is
   ambiguous, a model is asked what changed and whether a human should care.

The result is a diff that says *what* changed in words, not a percentage.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai.providers.base import LLMProvider
from ..models import VisualBaseline, VisualComparison

#: Playwright's aria snapshot emits two shapes, and the first version of this
#: only read one of them:
#:
#:     - textbox "Card number"          <- a named control
#:     - text: Your order is confirmed  <- content with no accessible name
#:
#: Reading only the quoted form meant a paragraph changing from "Your order is
#: confirmed" to "Your order failed" registered as no structural change at all.
#: Attribute lines (- /url:, - /placeholder:) are excluded by the (?!/) guard.
ARIA_LINE = re.compile(
    r"^\s*-\s+(?!/)(\w[\w-]*)"      # role
    r'(?:\s+\"([^\"]*)\")?'        # optional quoted accessible name
    r"(?::\s*(?!$)(.+?))?"         # or trailing text content
    r"(?:\s*\[[^\]]*\])?"          # aria attributes, e.g. [level=1] [checked]
    r"\s*:?\s*$",
    re.MULTILINE,
)


@dataclass(slots=True)
class VisualDiff:
    changed: bool
    severity: str = "none"          # none | cosmetic | notable | breaking
    summary: str = ""
    structural: dict = field(default_factory=dict)
    perceptual_distance: int = 0
    needs_human: bool = False
    judged_by: str = "deterministic"

    def as_dict(self) -> dict:
        return {
            "changed": self.changed, "severity": self.severity, "summary": self.summary,
            "structural": self.structural,
            "perceptual_distance": self.perceptual_distance,
            "needs_human": self.needs_human,
            "judged_by": self.judged_by,
        }


def parse_aria(snapshot: str) -> list[tuple[str, str]]:
    """Flatten an aria snapshot into (role, label) pairs for set comparison."""
    pairs: list[tuple[str, str]] = []
    for match in ARIA_LINE.finditer(snapshot or ""):
        role = match.group(1)
        label = (match.group(2) or match.group(3) or "").strip()
        # Structural nodes with no label carry no signal and only add noise to
        # the diff when a wrapper element is added or removed.
        if not label and role in {"generic", "paragraph", "list", "listitem", "group"}:
            continue
        pairs.append((role, label[:200]))
    return pairs


def structural_diff(before: str, after: str) -> dict:
    old, new = parse_aria(before), parse_aria(after)
    old_set, new_set = set(old), set(new)
    removed = sorted(old_set - new_set)
    added = sorted(new_set - old_set)

    # An interactive control disappearing is categorically worse than a text
    # tweak, so it is separated out rather than folded into a count.
    interactive = {"button", "link", "textbox", "checkbox", "combobox", "radio", "tab", "menuitem"}
    lost_controls = [f"{role}: {name}" for role, name in removed if role in interactive and name]
    gained_controls = [f"{role}: {name}" for role, name in added if role in interactive and name]

    return {
        "removed": [f"{r}: {n}" for r, n in removed[:20]],
        "added": [f"{r}: {n}" for r, n in added[:20]],
        "lost_controls": lost_controls,
        "gained_controls": gained_controls,
        "node_delta": len(new) - len(old),
    }


def difference_hash(image_path: str, size: int = 9) -> str:
    """64-bit dHash; falls back to a byte hash when Pillow is unavailable."""
    from .pixels import perceptual_hash

    return perceptual_hash(image_path, size=size)


def hamming(a: str, b: str) -> int:
    if not a or not b or len(a) != len(b):
        return 64
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


async def compare(
    db: Session,
    *,
    provider: LLMProvider | None,
    project_id: str,
    name: str,
    image_path: str,
    aria_snapshot: str,
    diff_path: str = "",
) -> tuple[VisualDiff, VisualBaseline | None, dict]:
    """Measure a candidate against its baseline across all three layers."""
    from .pixels import compare_images
    from .pixels import describe as describe_pixels

    baseline = db.execute(
        select(VisualBaseline).where(
            VisualBaseline.project_id == project_id, VisualBaseline.name == name
        ).order_by(VisualBaseline.version.desc())
    ).scalars().first()

    if baseline is None:
        return (
            VisualDiff(changed=False, severity="none",
                       summary="no baseline yet — this run establishes one"),
            None,
            {},
        )

    structural = structural_diff(baseline.aria_snapshot, aria_snapshot)
    pixels = compare_images(baseline.image_path, image_path, diff_path)
    distance = hamming(baseline.perceptual_hash, difference_hash(image_path))
    pixel_note = describe_pixels(pixels)

    # A control disappearing outranks everything else, and it is the one thing a
    # pixel percentage can hide: a removed button is a small fraction of a page.
    if structural["lost_controls"]:
        diff = VisualDiff(
            changed=True, severity="breaking",
            summary=(
                f"{len(structural['lost_controls'])} interactive control(s) disappeared: "
                + ", ".join(structural["lost_controls"][:3])
                + f". {pixel_note}."
            ),
            structural=structural, perceptual_distance=distance, needs_human=True,
        )
        return diff, baseline, pixels.as_dict()

    if pixels.dimensions_changed:
        return (
            VisualDiff(
                changed=True, severity="notable",
                summary=f"The screen {pixel_note}.",
                structural=structural, perceptual_distance=distance, needs_human=True,
            ),
            baseline,
            pixels.as_dict(),
        )

    text_changed = bool(structural["removed"] or structural["added"])
    pixels_changed = bool(pixels.regions) if pixels.available else distance > 4

    if not text_changed and not pixels_changed:
        return (
            VisualDiff(changed=False, severity="none",
                       summary="visually and structurally unchanged",
                       structural=structural, perceptual_distance=distance),
            baseline,
            pixels.as_dict(),
        )

    if not text_changed:
        # Pixels moved, semantics did not. Almost always styling - reported, but
        # not escalated to a human decision.
        return (
            VisualDiff(
                changed=True, severity="cosmetic",
                summary=(
                    f"Appearance shifted but every element and label is unchanged — "
                    f"most likely styling. {pixel_note}."
                ),
                structural=structural, perceptual_distance=distance,
            ),
            baseline,
            pixels.as_dict(),
        )

    diff = VisualDiff(
        changed=True, severity="notable",
        summary=(
            f"{len(structural['removed'])} element(s) removed, "
            f"{len(structural['added'])} added. {pixel_note}."
        ),
        structural=structural, perceptual_distance=distance, needs_human=True,
    )

    if provider is not None:
        verdict = await _judge_change(provider, name, structural, distance)
        if verdict:
            diff.severity = verdict.get("severity", diff.severity)
            diff.summary = f"{verdict.get('summary', diff.summary)} {pixel_note}."
            diff.needs_human = verdict.get("severity") in {"notable", "breaking"}
            diff.judged_by = "model"

    return diff, baseline, pixels.as_dict()


async def _judge_change(
    provider: LLMProvider, name: str, structural: dict, distance: int
) -> dict | None:
    import json

    from ..ai.providers.base import Message, NoAIModeError, ProviderError, Role

    system = (
        "You assess a UI change detected by comparing two accessibility snapshots.\n"
        "Return JSON: {\"severity\": \"cosmetic\"|\"notable\"|\"breaking\", "
        "\"summary\": \"<one sentence a reviewer can act on>\"}\n\n"
        "- 'cosmetic': wording, ordering or styling that preserves meaning and capability.\n"
        "- 'notable': content a user would notice and might question.\n"
        "- 'breaking': a user can no longer do something they previously could.\n"
        "Describe the change in product terms, not DOM terms."
    )
    user = (
        f"Screen: {name}\nPerceptual distance: {distance}/64\n"
        f"Removed: {json.dumps(structural['removed'][:15])}\n"
        f"Added: {json.dumps(structural['added'][:15])}\n"
        f"Lost controls: {json.dumps(structural['lost_controls'])}\n"
        f"Gained controls: {json.dumps(structural['gained_controls'])}"
    )
    try:
        result = await provider.complete(
            [Message(role=Role.USER, content=user)], system=system, max_tokens=400,
            response_format={
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["cosmetic", "notable", "breaking"]},
                    "summary": {"type": "string"},
                },
                "required": ["severity", "summary"],
                "additionalProperties": False,
            },
        )
        body = result.text.strip()
        if body.startswith("```"):
            body = body.split("```", 2)[1].removeprefix("json").strip().rsplit("```", 1)[0]
        return json.loads(body)
    except (NoAIModeError, ProviderError, ValueError):
        return None


async def record_snapshot(
    db: Session,
    *,
    provider: LLMProvider | None,
    project_id: str,
    name: str,
    image_path: str,
    aria_snapshot: str,
    url: str = "",
    run_id: str | None = None,
    run_test_id: str | None = None,
    browser: str = "chromium",
) -> dict:
    """Compare a candidate and persist the result for review.

    The first version computed a diff, returned it, and dropped it on the floor
    - so there was nothing to review and the whole feature was unreachable. The
    comparison is now a row, and an unchanged screen is recorded as
    ``auto_passed`` rather than not recorded at all, so "did this screen get
    checked?" has an answer.
    """
    diff_path = str(Path(image_path).with_name(f"{Path(image_path).stem}-diff.png"))
    diff, baseline, pixels = await compare(
        db, provider=provider, project_id=project_id, name=name,
        image_path=image_path, aria_snapshot=aria_snapshot, diff_path=diff_path,
    )

    if baseline is None:
        # The first sighting establishes the reference. It is approved by
        # construction: there is nothing yet to have regressed from.
        baseline = VisualBaseline(
            project_id=project_id, name=name, image_path=image_path,
            perceptual_hash=difference_hash(image_path), aria_snapshot=aria_snapshot,
            browser=browser, version=1,
        )
        db.add(baseline)
        db.flush()
        return {"name": name, "url": url, "baseline_created": True, **diff.as_dict()}

    comparison = VisualComparison(
        project_id=project_id,
        baseline_id=baseline.id,
        name=name,
        run_id=run_id,
        run_test_id=run_test_id,
        url=url[:600],
        browser=browser,
        candidate_path=image_path,
        baseline_path=baseline.image_path,
        diff_path=pixels.get("diff_path", ""),
        candidate_aria=aria_snapshot[:20000],
        severity=diff.severity,
        summary=diff.summary,
        structural=diff.structural,
        regions=pixels.get("regions", []),
        changed_pct=pixels.get("changed_pct", 0.0),
        perceptual_distance=diff.perceptual_distance,
        dimensions_changed=pixels.get("dimensions_changed", False),
        # An unchanged screen must not land in the review queue; it would train
        # people to click through the queue without reading it.
        status="auto_passed" if not diff.changed else "new",
        judged_by=diff.judged_by,
    )
    db.add(comparison)
    db.flush()

    return {
        "name": name, "url": url, "comparison_id": comparison.id,
        "baseline_created": False, **diff.as_dict(),
    }


def approve_baseline(
    db: Session, *, comparison_id: str, approved_by: str, comment: str = ""
) -> VisualBaseline:
    """Accept a candidate as the new reference.

    A new version rather than an overwrite: accepting a change should never
    destroy the evidence of what it replaced.
    """
    comparison = db.get(VisualComparison, comparison_id)
    if comparison is None:
        raise ValueError(f"unknown comparison {comparison_id}")

    current = db.execute(
        select(VisualBaseline).where(
            VisualBaseline.project_id == comparison.project_id,
            VisualBaseline.name == comparison.name,
        ).order_by(VisualBaseline.version.desc())
    ).scalars().first()

    baseline = VisualBaseline(
        project_id=comparison.project_id,
        name=comparison.name,
        image_path=comparison.candidate_path,
        perceptual_hash=difference_hash(comparison.candidate_path),
        aria_snapshot=comparison.candidate_aria,
        browser=comparison.browser,
        approved_by=approved_by,
        version=(current.version + 1) if current else 1,
    )
    db.add(baseline)
    comparison.status = "accepted"
    comparison.reviewed_by = approved_by
    comparison.review_comment = comment
    db.flush()
    return baseline


def reject_change(
    db: Session, *, comparison_id: str, rejected_by: str, comment: str = ""
) -> VisualComparison:
    """Mark a change as a defect. The baseline is deliberately left alone."""
    comparison = db.get(VisualComparison, comparison_id)
    if comparison is None:
        raise ValueError(f"unknown comparison {comparison_id}")
    comparison.status = "rejected"
    comparison.reviewed_by = rejected_by
    comparison.review_comment = comment
    db.flush()
    return comparison
