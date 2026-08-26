"""Failure signatures.

"Is this failure new?" is the single most valuable question after a red run, and
it is only answerable if two failures of the same underlying cause hash to the
same value. Raw error text does not: it carries timestamps, ids, ports, memory
addresses and coordinates that differ every run. Normalising those away first is
what turns triage from a guess into a lookup.
"""

from __future__ import annotations

import hashlib
import re

#: Ordered because later patterns assume earlier ones have already run.
NORMALISERS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{32,64}\b", re.I), "<hash>"),
    # Correlation/request ids: 8+ hex characters containing at least one digit.
    # Requiring a digit keeps ordinary words ("deadbeef", "feedface") out.
    (re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{8,31}\b", re.I), "<id>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*"), "<timestamp>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"), "<ip>"),
    (re.compile(r"https?://[^\s'\"<>]+"), "<url>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<addr>"),
    (re.compile(r"\b\d+(?:\.\d+)?ms\b"), "<duration>"),
    (re.compile(r"\bport\s+\d+\b", re.I), "port <n>"),
    (re.compile(r"\(\s*-?\d+\s*,\s*-?\d+\s*\)"), "(<x>,<y>)"),
    (re.compile(r"\b\d{3,}\b"), "<n>"),
    (re.compile(r"/(?:Users|home)/[^/\s]+"), "<homedir>"),
    (re.compile(r"\s+"), " "),
]

#: Error classes that almost always mean "the environment", not "the product".
ENVIRONMENTAL = (
    "econnrefused", "enotfound", "etimedout", "econnreset", "socket hang up",
    "net::err_", "browser has been closed", "target page, context or browser has been closed",
    "navigation timeout", "dns", "certificate", "ssl", "proxy",
)

#: Signals that point at the *test* rather than the application.
TEST_DEFECT = (
    "could not find the element", "locator ladder", "strict mode violation",
    "step has no locator", "unsupported action", "resolved to 2 elements",
)


def normalize(message: str) -> str:
    text = (message or "").strip().lower()
    for pattern, replacement in NORMALISERS:
        text = pattern.sub(replacement, text)
    return text.strip()[:600]


def compute_signature(error_type: str, message: str, test_key: str = "") -> str:
    """Stable identity for a failure.

    ``test_key`` is included so the same generic timeout in two different tests
    is triaged separately - they usually have different causes even though the
    message is identical.
    """
    basis = f"{(error_type or '').lower()}|{normalize(message)}|{test_key}"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def classify_error(error_type: str, message: str) -> str:
    """Coarse bucket used to route triage and RCA."""
    text = f"{error_type} {message}".lower()
    if any(marker in text for marker in ENVIRONMENTAL):
        return "environment"
    if any(marker in text for marker in TEST_DEFECT):
        return "test_defect"
    if "timeout" in text or "exceeded" in text:
        return "timing"
    if "expected" in text and ("got" in text or "received" in text):
        return "assertion"
    if "http 5" in text or "500" in text or "502" in text or "503" in text:
        return "server_error"
    if "http 4" in text or "403" in text or "401" in text:
        return "auth_or_permission"
    if "accessibility" in text:
        return "accessibility"
    if "performance budget" in text:
        return "performance"
    return "unknown"


def similarity(a: str, b: str) -> float:
    """Token Jaccard over normalised messages; used to cluster near-identical failures."""
    ta = set(normalize(a).split())
    tb = set(normalize(b).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
