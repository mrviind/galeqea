"""Deterministic finding checks for exploratory sessions.

These need no model and produce no false confidence: each one is a fact about
what the page did. A model can add judgement on top ("this error message is
confusing"), but it should never be *required* to notice that a request 500ed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field


@dataclass(slots=True)
class Finding:
    kind: str
    severity: str
    title: str
    detail: str
    url: str = ""
    confidence: float = 1.0
    found_by: str = "deterministic"
    evidence: dict = field(default_factory=dict)

    @property
    def signature(self) -> str:
        """Identity for de-duplication: the same defect found twice is one finding."""
        return hashlib.blake2b(
            f"{self.kind}|{self.title}".encode(), digest_size=16
        ).hexdigest()

    def as_dict(self) -> dict:
        return {
            "kind": self.kind, "severity": self.severity, "title": self.title,
            "detail": self.detail, "url": self.url, "confidence": self.confidence,
            "found_by": self.found_by, "evidence": self.evidence,
            "signature": self.signature,
        }


#: Slower than this and a user notices the wait.
SLOW_MS = 3000


#: Pages that are not the application under test. Reporting a "dead end" on
#: about:blank is a false positive that teaches people to ignore the list.
NON_APP_URL = re.compile(r"^(about:|chrome-error://|data:|$)", re.IGNORECASE)


def check(observation: dict, previous: dict | None = None) -> list[Finding]:
    """Everything checkable about one observed page state."""
    url = observation.get("url", "")
    if NON_APP_URL.match(url or ""):
        return []
    found: list[Finding] = []

    for error in observation.get("consoleErrors") or []:
        text = str(error.get("text", ""))[:400]
        found.append(Finding(
            kind="console_error",
            severity="medium",
            title=f"Console error: {text[:120]}",
            detail=f"The page logged an uncaught error while it was open.\n\n{text}",
            url=url,
            evidence={"text": text},
        ))

    for failure in observation.get("networkFailures") or []:
        status = failure.get("status")
        target = str(failure.get("url", ""))[:300]
        if status and int(status) >= 500:
            found.append(Finding(
                kind="server_error",
                severity="high",
                title=f"HTTP {status} from {_endpoint(target)}",
                detail=(
                    f"A request the page made returned {status}. Whatever the UI "
                    "showed afterwards, the data behind it did not arrive."
                ),
                url=url,
                evidence={"request": target, "status": status},
            ))
        elif failure.get("failure"):
            found.append(Finding(
                kind="broken_link",
                severity="medium",
                title=f"Request failed: {_endpoint(target)}",
                detail=f"{failure.get('failure')} while loading {target}",
                url=url,
                evidence={"request": target},
            ))

    for violation in observation.get("a11y") or []:
        found.append(Finding(
            kind="accessibility",
            severity="medium" if violation.get("rule") != "button-name" else "high",
            title=f"Accessibility: {violation.get('rule')}",
            detail=(
                f"{_a11y_explanation(violation.get('rule', ''))}\n\n"
                f"{violation.get('node', '')[:200]}"
            ),
            url=url,
            evidence=violation,
        ))

    load_ms = observation.get("loadMs") or 0
    if load_ms > SLOW_MS:
        found.append(Finding(
            kind="slow_response",
            severity="low",
            title=f"Slow screen: {load_ms}ms to become interactive",
            detail=f"This screen took {load_ms}ms, past the {SLOW_MS}ms point where a user notices.",
            url=url,
            confidence=0.8,
            evidence={"load_ms": load_ms},
        ))

    candidates = observation.get("candidates") or []
    if not candidates and not observation.get("isTerminal"):
        found.append(Finding(
            kind="dead_end",
            severity="medium",
            title="Dead end: no way forward from this screen",
            detail=(
                "This screen offers no links, buttons or inputs. A user who "
                "arrives here has to use the browser's back button to escape."
            ),
            url=url,
            confidence=0.7,
        ))

    unlabelled = [c for c in candidates if not (c.get("name") or "").strip()]
    if unlabelled:
        roles = sorted({c.get("role", "?") for c in unlabelled})
        found.append(Finding(
            kind="unlabelled_control",
            severity="medium",
            title=f"{len(unlabelled)} control(s) with no accessible name",
            detail=(
                f"Controls of type {', '.join(roles)} have no name a screen reader "
                "can announce. They are also unaddressable by any durable locator, "
                "so tests against them will be brittle."
            ),
            url=url,
            evidence={"roles": roles, "count": len(unlabelled)},
        ))

    # Data loss: a field that had a value before the action and lost it after,
    # without the page navigating anywhere.
    if previous and previous.get("url") == url:
        before = {c.get("name"): c.get("value") for c in previous.get("candidates") or [] if c.get("value")}
        after = {c.get("name"): c.get("value") for c in candidates}
        lost = [name for name, value in before.items() if value and not after.get(name)]
        if lost:
            found.append(Finding(
                kind="data_loss",
                severity="high",
                title=f"Input cleared without navigating: {', '.join(filter(None, lost))[:120]}",
                detail=(
                    "Field(s) the user had filled were emptied while staying on the "
                    "same screen. Silently discarding typed input is one of the most "
                    "reliably infuriating things an application can do."
                ),
                url=url,
                evidence={"fields": lost},
            ))

    return found


def _endpoint(url: str) -> str:
    without_scheme = url.split("://", 1)[-1]
    path = "/" + without_scheme.split("/", 1)[1] if "/" in without_scheme else without_scheme
    return path.split("?")[0][:80]


def _a11y_explanation(rule: str) -> str:
    return {
        "image-alt": "An image has no alt text, so it is invisible to a screen reader.",
        "form-label": "A form control has no label, so its purpose is unannounced.",
        "button-name": "A button has no accessible name — it announces as just 'button'.",
        "heading-order": "Heading levels skip a level, which breaks document navigation.",
        "html-lang": "The page declares no language, so screen readers guess the pronunciation.",
    }.get(rule, "An accessibility rule was violated.")


def dedupe(findings: list[Finding]) -> list[Finding]:
    """One defect, one report — however many times exploration walked into it."""
    seen: set[str] = set()
    unique: list[Finding] = []
    for finding in findings:
        if finding.signature in seen:
            continue
        seen.add(finding.signature)
        unique.append(finding)
    return unique
