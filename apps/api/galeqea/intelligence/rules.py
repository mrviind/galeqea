"""Atomic rule extraction (WO#9-B).

A requirement item is a paragraph; a *rule* is a single testable obligation with a
machine-readable shape. This decomposes requirement text into atomic rules
deterministically using must/shall/only/between/at least/one of/if…then, so the whole
"requirements → cases" pipeline produces rules and cases with **no model at all**.
A model, when configured, refines and adds rules the prose only implied; it never
replaces this floor.

Each rule carries a type (functional | validation | permission | nav | nonfunctional),
the input names it touches, a structured ``constraints`` shape, a design-technique
hint, and any ambiguities specific to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..engine.ingest import _ambiguities
from . import testdesign

# --------------------------------------------------------------------------- #
_PERMISSION = re.compile(
    r"\bonly\s+(?:an?\s+)?(?P<role>[\w /-]+?)\s+(?:can|may|is allowed to|is permitted to|shall be able to)\b",
    re.I,
)
_PROHIBITION = re.compile(r"\b(must not|shall not|may not|cannot|is not allowed to|is forbidden to)\b", re.I)
_ROLE_PROHIBITION = re.compile(
    r"\ba?\s*(?P<role>shopper|guest|admin|user|customer|member|visitor|operator|manager)\b[^.]*?"
    r"\b(must not|shall not|may not|cannot)\b", re.I)
_CONDITIONAL = re.compile(r"\bif\b(?P<cond>[^,]+?)(?:,|\bthen\b)(?P<then>.+)", re.I)
_NAV = re.compile(r"\b(redirect|navigate to|go to|take(?:s)? (?:the|them) to|return(?:s)? to)\b", re.I)
_REQUIRED = re.compile(r"\b(is required|are required|must be provided|mandatory|cannot be (?:empty|blank))\b", re.I)
_NFR = re.compile(
    r"\b(within\s+\d|under\s+\d|less than\s+\d|at least\s+\d+\s*(?:seconds?|minutes?|hours?|days?|years?)|"
    r"p\d\d|percentile|latency|throughput|uptime|availability|retain|retention)\b", re.I)
_MODAL = re.compile(r"\b(shall|must|will|is required to|needs to|has to|should)\b", re.I)


@dataclass(slots=True)
class ExtractedRule:
    text: str
    rule_type: str = "functional"          # functional|validation|permission|nav|nonfunctional
    inputs: list[str] = field(default_factory=list)
    constraints: dict = field(default_factory=dict)
    technique: str = ""                     # boundary_value|equivalence_partition|decision_table|pairwise|permission
    open_questions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "text": self.text, "rule_type": self.rule_type, "inputs": self.inputs,
            "constraints": self.constraints, "technique": self.technique,
            "open_questions": self.open_questions,
        }


def _sentences(text: str) -> list[str]:
    # Keep "if … then …" whole; otherwise split on sentence boundaries and on
    # obligation-joining "; ".
    parts = re.split(r"(?<=[.!?])\s+|\s*;\s*", text.strip())
    return [p.strip() for p in parts if len(p.strip()) >= 8]


def _classify(sentence: str) -> tuple[str, str]:
    """Return (rule_type, technique) for a single obligation sentence."""
    if _PERMISSION.search(sentence) or _ROLE_PROHIBITION.search(sentence):
        return "permission", "permission"
    if _NAV.search(sentence):
        return "nav", "decision_table" if _CONDITIONAL.search(sentence) else ""
    if _NFR.search(sentence):
        return "nonfunctional", ""
    if _CONDITIONAL.search(sentence):
        return "functional", "decision_table"
    if _REQUIRED.search(sentence) or testdesign.RANGE_PATTERNS[0][1].search(sentence) \
            or any(p.search(sentence) for _, p in testdesign.RANGE_PATTERNS) \
            or testdesign.ENUM_PATTERN.search(sentence):
        return "validation", ""
    return "functional", ""


def extract_rules(
    text: str, *, ref: str = "", section: str = "", source_anchor: dict | None = None
) -> list[ExtractedRule]:
    """Decompose one requirement's text into atomic rules. Deterministic."""
    rules: list[ExtractedRule] = []
    for sentence in _sentences(text):
        if not (_MODAL.search(sentence) or _PERMISSION.search(sentence)
                or _CONDITIONAL.search(sentence) or _REQUIRED.search(sentence)
                or any(p.search(sentence) for _, p in testdesign.RANGE_PATTERNS)
                or testdesign.ENUM_PATTERN.search(sentence)):
            continue

        rule_type, technique = _classify(sentence)
        analysis = testdesign.analyse(sentence)
        constraints: dict = {}
        inputs: list[str] = []
        if analysis.variables:
            inputs = list(dict.fromkeys(v.name for v in analysis.variables))
            constraints["variables"] = [v.as_dict() for v in analysis.variables]
        if analysis.decision_table:
            constraints["decision_table"] = [r.as_dict() for r in analysis.decision_table]
            technique = technique or "decision_table"
        if analysis.pairwise:
            constraints["pairwise"] = analysis.pairwise
            technique = "pairwise"
        # Choose the most specific technique the domain supports.
        if not technique and analysis.values:
            techs = set(analysis.techniques_applied)
            technique = ("boundary_value" if "boundary_value" in techs
                         else "equivalence_partition" if "equivalence_partition" in techs
                         else "")

        perm = _PERMISSION.search(sentence)
        if perm:
            constraints["role"] = perm.group("role").strip()
            constraints["negated"] = False
        role_proh = _ROLE_PROHIBITION.search(sentence)
        if role_proh:
            constraints["role"] = role_proh.group("role").strip()
            constraints["negated"] = True

        rules.append(ExtractedRule(
            text=sentence.strip(),
            rule_type=rule_type,
            inputs=inputs,
            constraints=constraints,
            technique=technique,
            open_questions=_ambiguities(sentence),
        ))

    # A requirement with no atomic obligation still yields one rule for coverage,
    # so every requirement has at least one rule to trace to.
    if not rules and text.strip():
        rules.append(ExtractedRule(text=text.strip()[:400], rule_type="functional"))
    return rules
