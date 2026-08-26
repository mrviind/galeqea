"""Classical test design techniques, applied to requirement text.

This is the part of "AI generates test cases" that does not actually need AI.
The techniques that find real defects — equivalence partitioning, boundary value
analysis, decision tables — are *mechanical* once you know the input domain, and
the input domain is usually stated in the requirement itself: "between 8 and 64
characters", "one of Draft, Submitted or Approved", "at most 5 MB".

So the domain is parsed deterministically and the techniques are applied by
rule. A model, when configured, is used for the part it is genuinely better at:
reading domain constraints that are implied rather than written down. What it is
*not* asked to do is invent boundary values, because off-by-one arithmetic is
not something to delegate to a language model.

The output says which technique produced each value. A test whose provenance is
"the AI suggested it" cannot be reviewed; "max + 1, from boundary value
analysis" can.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- #
# Domain extraction
# --------------------------------------------------------------------------- #
UNITS = (
    "characters?|chars?|letters?|digits?|items?|entries|rows?|files?|"
    "bytes?|kb|mb|gb|kilobytes?|megabytes?|gigabytes?|"
    "seconds?|secs?|ms|milliseconds?|minutes?|mins?|hours?|days?|weeks?|months?|years?|"
    "percent|%|attempts?|retries|results?"
)

NUMBER = r"(\d+(?:[.,]\d+)?)"

RANGE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("between", re.compile(
        rf"\bbetween\s+{NUMBER}\s*(?:and|to|-|–)\s*{NUMBER}\s*({UNITS})?", re.I)),
    ("from_to", re.compile(
        rf"\bfrom\s+{NUMBER}\s+to\s+{NUMBER}\s*({UNITS})?", re.I)),
    ("min", re.compile(
        rf"\b(?:at least|minimum(?: of)?|no fewer than|not less than|greater than or equal to|"
        rf"longer than|more than)\s+{NUMBER}\s*({UNITS})?", re.I)),
    ("max", re.compile(
        rf"\b(?:at most|maximum(?: of)?|no more than|not more than|up to|less than or equal to|"
        rf"shorter than|fewer than|under|within)\s+{NUMBER}\s*({UNITS})?", re.I)),
    ("exactly", re.compile(rf"\bexactly\s+{NUMBER}\s*({UNITS})?", re.I)),
]

ENUM_PATTERN = re.compile(
    r"\b(?:one of|any of|either)\s*:?\s*((?:[\"'`]?[\w \-]+[\"'`]?\s*(?:,|、|/|\bor\b|\band\b)\s*){1,8}[\"'`]?[\w \-]+[\"'`]?)",
    re.I,
)

FORMAT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", re.compile(r"\be-?mail\s*(address)?\b", re.I)),
    ("url", re.compile(r"\b(url|link|website|web address)\b", re.I)),
    ("phone", re.compile(r"\b(phone|telephone|mobile)\s*(number)?\b", re.I)),
    ("date", re.compile(r"\b(date|date of birth|dob|expiry)\b", re.I)),
    ("card", re.compile(r"\b(card number|credit card|pan)\b", re.I)),
    ("postcode", re.compile(r"\b(post(al)?\s*code|zip\s*code)\b", re.I)),
    ("password", re.compile(r"\bpassword\b", re.I)),
]

OPTIONALITY = re.compile(r"\b(required|mandatory|must be provided|cannot be (empty|blank)|optional)\b", re.I)

#: A rejection verb inverts a threshold's meaning. "at least 5" is a minimum;
#: "reject anything of more than 5 MB" states a *maximum* of 5 using the same
#: comparative. Reading the comparative alone gets this exactly backwards, which
#: then produces boundary values on the wrong side of the limit.
NEGATION = re.compile(
    r"\b(reject|refuse|deny|disallow|block|prevent|decline|must not|shall not|"
    r"cannot|can't|may not|not allowed|not permitted|fail(?:s|ed)? if|error if)\b",
    re.I,
)
#: Verbs that are not part of a variable's name.
NAME_VERBS = {
    "reject", "refuse", "deny", "disallow", "block", "prevent", "decline",
    "accept", "allow", "permit", "require", "upload", "provide", "enter",
    "submit", "display", "show", "return", "validate", "check", "ensure",
}

#: Conditions that combine, for a decision table.
CONDITION_SPLIT = re.compile(r"\b(?:and|or)\b|,\s*(?=(?:if|when|unless)\b)", re.I)
CONDITION_LEAD = re.compile(r"\b(?:if|when|unless|provided that|as long as|only if)\b\s*(.+)", re.I)


@dataclass(slots=True)
class Variable:
    name: str
    kind: str                      # numeric | enum | format | boolean
    minimum: float | None = None
    maximum: float | None = None
    unit: str = ""
    values: list[str] = field(default_factory=list)
    fmt: str = ""
    required: bool | None = None
    source_phrase: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "minimum": self.minimum,
            "maximum": self.maximum, "unit": self.unit, "values": self.values,
            "format": self.fmt, "required": self.required,
            "source_phrase": self.source_phrase,
        }


@dataclass(slots=True)
class TestValue:
    variable: str
    value: str
    label: str
    partition: str                 # valid | invalid
    technique: str                 # boundary_value | equivalence_partition | decision_table
    expected: str

    def as_dict(self) -> dict:
        return {
            "variable": self.variable, "value": self.value, "label": self.label,
            "partition": self.partition, "technique": self.technique,
            "expected": self.expected,
        }


@dataclass(slots=True)
class DecisionRow:
    conditions: dict[str, bool]
    expected: str

    def as_dict(self) -> dict:
        return {"conditions": self.conditions, "expected": self.expected}


@dataclass(slots=True)
class DesignAnalysis:
    variables: list[Variable] = field(default_factory=list)
    values: list[TestValue] = field(default_factory=list)
    decision_table: list[DecisionRow] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def techniques_applied(self) -> list[str]:
        applied = sorted({v.technique for v in self.values})
        if self.decision_table:
            applied.append("decision_table")
        return sorted(set(applied))

    def as_dict(self) -> dict:
        return {
            "variables": [v.as_dict() for v in self.variables],
            "values": [v.as_dict() for v in self.values],
            "decision_table": [r.as_dict() for r in self.decision_table],
            "conditions": self.conditions,
            "techniques_applied": self.techniques_applied,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
def analyse(text: str, *, subject: str = "input") -> DesignAnalysis:
    """Extract the input domain and apply the classical techniques to it."""
    analysis = DesignAnalysis()
    if not text:
        return analysis

    analysis.variables = _dedupe_variables(_extract_variables(text, subject=subject))
    for variable in analysis.variables:
        analysis.values.extend(_derive_values(variable))
    analysis.values = _dedupe_values(analysis.values)

    analysis.conditions = _extract_conditions(text)
    analysis.decision_table = _decision_table(analysis.conditions)

    if not analysis.variables:
        analysis.notes.append(
            "No numeric range, enumeration or format constraint is stated, so "
            "boundary and partition analysis has nothing to work from. If limits "
            "exist, adding them to the requirement makes them testable."
        )
    if len(analysis.conditions) > 4:
        analysis.notes.append(
            f"{len(analysis.conditions)} conditions combine here. The decision "
            "table is capped at 8 rows; consider splitting the requirement."
        )
    return analysis


def _extract_variables(text: str, *, subject: str) -> list[Variable]:
    variables: list[Variable] = []

    for kind, pattern in RANGE_PATTERNS:
        for match in pattern.finditer(text):
            groups = match.groups()
            unit = (groups[-1] or "").strip().lower()
            name = _variable_name(text, match.start(), unit, subject)

            if kind in {"between", "from_to"}:
                low, high = _number(groups[0]), _number(groups[1])
                variables.append(Variable(name, "numeric", minimum=min(low, high),
                                          maximum=max(low, high), unit=unit,
                                          source_phrase=match.group(0).strip()))
            elif kind in {"min", "max"}:
                value = _number(groups[0])
                # A rejection verb nearby flips which side of the threshold is
                # valid: "reject more than 5 MB" is a maximum, not a minimum.
                inverted = _is_negated(text, match.start())
                effective = ("max" if kind == "min" else "min") if inverted else kind
                phrase = match.group(0).strip()
                if inverted:
                    phrase += " (read as a limit: the sentence rejects beyond it)"
                if effective == "min":
                    variables.append(Variable(name, "numeric", minimum=value, unit=unit,
                                              source_phrase=phrase))
                else:
                    variables.append(Variable(name, "numeric", maximum=value, unit=unit,
                                              source_phrase=phrase))
            elif kind == "exactly":
                value = _number(groups[0])
                variables.append(Variable(name, "numeric", minimum=value, maximum=value,
                                          unit=unit, source_phrase=match.group(0).strip()))

    variables = _merge_ranges(variables)

    for match in ENUM_PATTERN.finditer(text):
        options = _split_options(match.group(1))
        if len(options) >= 2:
            variables.append(Variable(
                name=_variable_name(text, match.start(), "", subject),
                kind="enum", values=options, source_phrase=match.group(0).strip()[:120],
            ))

    for name, pattern in FORMAT_PATTERNS:
        match = pattern.search(text)
        if match:
            variables.append(Variable(
                name=match.group(0).strip().lower(), kind="format", fmt=name,
                source_phrase=match.group(0).strip(),
            ))

    optional = OPTIONALITY.search(text)
    if optional:
        required = "optional" not in optional.group(0).lower()
        for variable in variables:
            if variable.required is None:
                variable.required = required
        if not variables:
            variables.append(Variable(name=subject, kind="boolean", required=required,
                                      source_phrase=optional.group(0)))

    return variables[:8]


def _dedupe_variables(variables: list[Variable]) -> list[Variable]:
    """A phrase repeated in the source must not become two variables."""
    seen: set[tuple] = set()
    unique: list[Variable] = []
    for variable in variables:
        key = (variable.kind, variable.minimum, variable.maximum,
               variable.unit, variable.fmt, tuple(variable.values))
        if key in seen:
            continue
        seen.add(key)
        unique.append(variable)
    return unique


def _dedupe_values(values: list[TestValue]) -> list[TestValue]:
    seen: set[tuple] = set()
    unique: list[TestValue] = []
    for value in values:
        key = (value.variable, value.value, value.label)
        if key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return unique


def _merge_ranges(variables: list[Variable]) -> list[Variable]:
    """A requirement saying "at least 8 and at most 64" describes one variable."""
    numeric = [v for v in variables if v.kind == "numeric"]
    others = [v for v in variables if v.kind != "numeric"]
    merged: dict[str, Variable] = {}

    for variable in numeric:
        key = f"{variable.name}|{variable.unit}"
        if key not in merged:
            merged[key] = variable
            continue
        existing = merged[key]
        if variable.minimum is not None:
            existing.minimum = variable.minimum if existing.minimum is None else min(existing.minimum, variable.minimum)
        if variable.maximum is not None:
            existing.maximum = variable.maximum if existing.maximum is None else max(existing.maximum, variable.maximum)
        existing.source_phrase = f"{existing.source_phrase}; {variable.source_phrase}"

    return [*merged.values(), *others]


def _derive_values(variable: Variable) -> list[TestValue]:
    if variable.kind == "numeric":
        return _numeric_values(variable)
    if variable.kind == "enum":
        return _enum_values(variable)
    if variable.kind == "format":
        return _format_values(variable)
    if variable.kind == "boolean" and variable.required:
        return [
            TestValue(variable.name, "", "omitted", "invalid", "equivalence_partition",
                      "rejected with a message naming the missing field"),
            TestValue(variable.name, "(a valid value)", "supplied", "valid",
                      "equivalence_partition", "accepted"),
        ]
    return []


def _numeric_values(variable: Variable) -> list[TestValue]:
    """Three-value boundary analysis plus the partitions either side.

    Off-by-one is the defect this finds, so the values either side of each
    boundary matter more than the boundary itself. They are computed, never
    guessed: a language model doing this arithmetic is a language model
    occasionally getting it wrong.
    """
    values: list[TestValue] = []
    name, unit = variable.name, variable.unit
    integral = _is_integral(unit)
    step = 1 if integral else 0.1

    accepted = f"accepted ({_range_phrase(variable)})"
    rejected = "rejected with a message stating the permitted range"

    if variable.minimum is not None:
        low = variable.minimum
        values += [
            TestValue(name, _fmt(low - step, integral), "just below the minimum",
                      "invalid", "boundary_value", rejected),
            TestValue(name, _fmt(low, integral), "the minimum", "valid",
                      "boundary_value", accepted),
            TestValue(name, _fmt(low + step, integral), "just above the minimum",
                      "valid", "boundary_value", accepted),
        ]
        if low > 0:
            values.append(TestValue(name, "0", "zero", "invalid",
                                    "equivalence_partition", rejected))

    if variable.maximum is not None:
        high = variable.maximum
        values += [
            TestValue(name, _fmt(high - step, integral), "just below the maximum",
                      "valid", "boundary_value", accepted),
            TestValue(name, _fmt(high, integral), "the maximum", "valid",
                      "boundary_value", accepted),
            TestValue(name, _fmt(high + step, integral), "just above the maximum",
                      "invalid", "boundary_value", rejected),
        ]

    if variable.minimum is not None and variable.maximum is not None:
        midpoint = (variable.minimum + variable.maximum) / 2
        values.append(TestValue(name, _fmt(midpoint, integral), "a typical value in range",
                                "valid", "equivalence_partition", accepted))

    values.append(TestValue(name, "-1", "negative", "invalid",
                            "equivalence_partition", rejected))
    values.append(TestValue(name, "abc", "not a number", "invalid",
                            "equivalence_partition",
                            "rejected without a server error"))
    return values


def _enum_values(variable: Variable) -> list[TestValue]:
    values = [
        TestValue(variable.name, option, "a permitted value", "valid",
                  "equivalence_partition", "accepted")
        for option in variable.values
    ]
    values.append(TestValue(variable.name, "__not_a_member__", "outside the set",
                            "invalid", "equivalence_partition",
                            "rejected; the set of permitted values is stated"))
    if variable.values:
        first = variable.values[0]
        values.append(TestValue(
            variable.name,
            first.upper() if first.islower() else first.lower(),
            "correct value, wrong case",
            # Not "invalid": the requirement does not say. Asserting a verdict
            # the specification never gave would bake a guess into a test.
            "unspecified",
            "equivalence_partition",
            "behaviour is undefined — the requirement does not say whether "
            "matching is case sensitive. Ask before asserting either way.",
        ))
    return values


FORMAT_CASES: dict[str, list[tuple[str, str, str]]] = {
    "email": [
        ("user@example.com", "well formed", "valid"),
        ("user+tag@sub.example.co.uk", "well formed, unusual but legal", "valid"),
        ("user@", "missing domain", "invalid"),
        ("@example.com", "missing local part", "invalid"),
        ("user example.com", "missing @", "invalid"),
        ("  user@example.com  ", "surrounded by whitespace", "valid"),
    ],
    "url": [
        ("https://example.com/path?q=1", "well formed", "valid"),
        ("example.com", "no scheme", "invalid"),
        ("javascript:alert(1)", "dangerous scheme", "invalid"),
    ],
    "phone": [
        ("+441234567890", "international format", "valid"),
        ("01234 567890", "national format with a space", "valid"),
        ("12345", "too short", "invalid"),
        ("not-a-number", "letters", "invalid"),
    ],
    "date": [
        ("2026-02-29", "a leap day in a non-leap year", "invalid"),
        ("2024-02-29", "a real leap day", "valid"),
        ("2026-13-01", "month out of range", "invalid"),
        ("31/12/2026", "a different locale's order", "invalid"),
    ],
    "card": [
        ("4242424242424242", "passes the Luhn check", "valid"),
        ("4242424242424241", "fails the Luhn check", "invalid"),
        ("4242 4242 4242 4242", "spaced as a human types it", "valid"),
        ("42424242424242421234", "too long", "invalid"),
    ],
    "postcode": [
        ("SW1A 1AA", "well formed", "valid"),
        ("SW1A1AA", "no space", "valid"),
        ("XX0 0XX", "well formed but does not exist", "invalid"),
    ],
    "password": [
        ("correct horse battery staple", "long passphrase", "valid"),
        ("        ", "whitespace only", "invalid"),
        ("' OR 1=1 --", "injection attempt", "invalid"),
    ],
}


def _format_values(variable: Variable) -> list[TestValue]:
    cases = FORMAT_CASES.get(variable.fmt, [])
    return [
        TestValue(
            variable.name, value, label, partition, "equivalence_partition",
            "accepted" if partition == "valid" else "rejected with a specific message",
        )
        for value, label, partition in cases
    ]


# --------------------------------------------------------------------------- #
def _extract_conditions(text: str) -> list[str]:
    """Conditions from an "if A and B, then C" sentence — A and B, not C.

    Two things must be bounded or the consequence and the next requirement both
    end up in the table: the clause stops at the first "then" *or* comma (the
    classic shape puts the consequence after it), and the search never crosses a
    sentence boundary.
    """
    match = CONDITION_LEAD.search(text)
    if not match:
        return []

    clause = match.group(1)
    # Stay within one sentence: the next requirement is not a condition of this one.
    clause = re.split(r"(?<=[.!?])\s+", clause)[0]
    clause = re.split(r"\bthen\b|,", clause, maxsplit=1, flags=re.I)[0]

    parts = [p.strip(" ,.") for p in CONDITION_SPLIT.split(clause)]
    seen: set[str] = set()
    conditions: list[str] = []
    for part in parts:
        key = part.lower()
        if 3 < len(part) < 120 and key not in seen:
            seen.add(key)
            conditions.append(part)
    return conditions[:4]


def _decision_table(conditions: list[str]) -> list[DecisionRow]:
    """Enumerate condition combinations, capped so the table stays readable."""
    if len(conditions) < 2:
        return []
    rows: list[DecisionRow] = []
    for mask in range(2 ** len(conditions)):
        combination = {
            condition: bool(mask & (1 << index))
            for index, condition in enumerate(conditions)
        }
        rows.append(DecisionRow(
            conditions=combination,
            expected=("the described outcome occurs" if all(combination.values())
                      else "the described outcome does NOT occur"),
        ))
        if len(rows) >= 8:
            break
    return rows


# --------------------------------------------------------------------------- #
def _is_negated(text: str, position: int) -> bool:
    """Is this threshold governed by a rejection verb in the same clause?"""
    clause_start = max(
        text.rfind(".", 0, position) + 1,
        text.rfind(";", 0, position) + 1,
    )
    return bool(NEGATION.search(text[clause_start:position]))


def _variable_name(text: str, position: int, unit: str, subject: str) -> str:
    """Name the constrained thing from the words immediately before the phrase."""
    preceding = text[max(0, position - 70) : position]
    words = re.findall(r"[A-Za-z][\w'-]*", preceding)
    stop = {
        "the", "a", "an", "must", "shall", "should", "be", "is", "are", "of", "to",
        "with", "and", "or", "at", "in", "for", "have", "has", "contain", "system",
        "user", "it", "that", "which", "than", "not", "can", "may", "will",
    } | NAME_VERBS
    meaningful = [w for w in words if w.lower() not in stop]
    if meaningful:
        return " ".join(meaningful[-2:]).lower()
    return f"{subject} {unit}".strip() or subject


def _split_options(blob: str) -> list[str]:
    parts = re.split(r",|\bor\b|\band\b|/", blob, flags=re.I)
    return [p.strip(" \"'`.") for p in parts if 0 < len(p.strip(" \"'`.")) < 40][:8]


def _number(raw: str) -> float:
    return float(raw.replace(",", ""))


def _is_integral(unit: str) -> bool:
    return unit == "" or not unit.startswith(("second", "sec", "ms", "milli", "percent", "%"))


def _fmt(value: float, integral: bool) -> str:
    if integral:
        return str(int(round(value)))
    return f"{value:.1f}".rstrip("0").rstrip(".")


def _range_phrase(variable: Variable) -> str:
    unit = f" {variable.unit}" if variable.unit else ""
    if variable.minimum is not None and variable.maximum is not None:
        return f"{_fmt(variable.minimum, True)}–{_fmt(variable.maximum, True)}{unit} is permitted"
    if variable.minimum is not None:
        return f"at least {_fmt(variable.minimum, True)}{unit}"
    return f"at most {_fmt(variable.maximum or 0, True)}{unit}"


def summarise(analysis: DesignAnalysis) -> str:
    if not analysis.variables and not analysis.decision_table:
        return "No testable input domain could be derived from the wording."
    parts = []
    if analysis.variables:
        parts.append(f"{len(analysis.variables)} input variable(s)")
    valid = sum(1 for v in analysis.values if v.partition == "valid")
    invalid = sum(1 for v in analysis.values if v.partition == "invalid")
    unspecified = sum(1 for v in analysis.values if v.partition == "unspecified")
    if analysis.values:
        phrase = f"{valid} valid and {invalid} invalid value(s)"
        if unspecified:
            phrase += f" ({unspecified} where the requirement is silent)"
        parts.append(phrase)
    if analysis.decision_table:
        parts.append(f"a {len(analysis.decision_table)}-row decision table")
    return (
        ", ".join(parts)
        + f" via {', '.join(t.replace('_', ' ') for t in analysis.techniques_applied)}."
    )
