"""Synthetic test data: deterministic, reproducible and safe by construction.

Every commercial AI testing suite ships a data generator, and almost all of them
share two flaws worth avoiding.

The first is *irreproducibility*. A generator seeded from the clock produces a
different `Ana` every run, so a failure caused by an apostrophe in a surname is
unreproducible - the one property a test datum most needs. Here every value is a
pure function of a seed string, so ``value("person_name", "REQ-014/customer/0")``
returns the same name on every machine, forever. A run that fails can be replayed
byte-for-byte.

The second is *safety*. Generators that "look real" get pointed at production and
mail real-looking addresses, or emit card numbers that route. Nothing here can:

* e-mail hosts come only from the RFC 2606 / RFC 6761 reserved names, which are
  guaranteed never to resolve;
* telephone numbers come only from the ranges regulators reserve for fiction -
  NANP ``555-01xx`` and Ofcom's ``07700 900xxx``;
* payment card numbers are Luhn-valid but carry major industry identifier ``9``,
  which ISO/IEC 7812 reserves for national assignment and no scheme issues, so
  the number can pass a checksum test and can never reach a payment network.

No name, address or identifier below was copied from a person or a data set;
they are constructed from invented syllables and generic street words.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, timedelta


# --------------------------------------------------------------------------- #
# Deterministic randomness
# --------------------------------------------------------------------------- #
def _stream(seed: str) -> list[int]:
    """A long, stable byte stream derived from ``seed``.

    blake2b rather than ``random.Random``: the CPython Mersenne stream is not
    contractually stable across versions, and a data set that silently changes
    under a Python upgrade would invalidate every stored expectation.
    """
    out = bytearray()
    counter = 0
    while len(out) < 64:
        out += hashlib.blake2b(f"{seed}#{counter}".encode(), digest_size=32).digest()
        counter += 1
    return list(out)


class _Draw:
    """Consumes the stream, so successive draws differ but stay reproducible."""

    def __init__(self, seed: str) -> None:
        self._bytes = _stream(seed)
        self._pos = 0

    def byte(self) -> int:
        value = self._bytes[self._pos % len(self._bytes)]
        self._pos += 1
        return value

    def below(self, n: int) -> int:
        if n <= 0:
            return 0
        # Two bytes so ranges above 256 stay reasonably uniform.
        return ((self.byte() << 8) | self.byte()) % n

    def pick(self, options: list[str]) -> str:
        return options[self.below(len(options))]

    def digits(self, n: int) -> str:
        return "".join(str(self.byte() % 10) for _ in range(n))


# --------------------------------------------------------------------------- #
# Invented corpora - no real person, company or address appears here
# --------------------------------------------------------------------------- #
_GIVEN = [
    "Ravi", "Mira", "Tomas", "Neela", "Ansel", "Priya", "Kaito", "Lucia",
    "Omar", "Freya", "Diego", "Sanne", "Yusuf", "Ilse", "Bram", "Noor",
    # Deliberately awkward: an apostrophe, a hyphen, a diacritic and a
    # single-character given name. These are the inputs that break forms.
    "D'Arcy", "Jean-Luc", "Zoë", "P",
]
_FAMILY = [
    "Halvard", "Okonkwo", "Marchetti", "Lindqvist", "Rahman", "Duarte",
    "Novak", "Fitzwilliam", "Bhandari", "Sørensen", "O'Rourke", "van der Meer",
    "Ferreira-Blake", "Ng",
]
_STREET_WORDS = ["Alder", "Kiln", "Harbour", "Foundry", "Meadow", "Quarry", "Linden", "Weaver"]
_STREET_TYPES = ["Street", "Lane", "Road", "Way", "Close", "Terrace"]
_CITIES_US = ["Fairhaven", "Kestrel Bay", "Northmoor", "Cedar Falls", "Ashport"]
_CITIES_GB = ["Wexbury", "Hollowmere", "Kirkstane", "Elderby", "Portlaw"]
_STATES = ["CA", "NY", "TX", "WA", "IL", "MA", "OR", "CO"]
_COMPANY_HEAD = ["Northwind", "Blue Kiln", "Harbourline", "Quarrystone", "Elderpath"]
_COMPANY_TAIL = ["Systems", "Logistics", "Holdings", "Labs", "Works"]

#: RFC 2606 and RFC 6761 reserve these; they are guaranteed never to resolve, so
#: a test that accidentally sends mail cannot reach a real inbox.
_SAFE_HOSTS = ["example.com", "example.org", "example.net", "test.invalid", "qa.example"]

_LOCALES = {"en-US", "en-GB", "generic"}


# --------------------------------------------------------------------------- #
# Semantic kinds
# --------------------------------------------------------------------------- #
#: Ordered because the first match wins: ``email_address`` must be read as an
#: e-mail, not as an address. Patterns are matched against a normalised field
#: name, so ``customerEmailAddress``, ``customer_email_address`` and
#: ``"Customer email address"`` all reduce to the same string.
_KIND_PATTERNS: list[tuple[str, str]] = [
    ("email", r"\b(e[- ]?mail|email)\b"),
    ("password", r"\b(password|passwd|pwd|passphrase)\b"),
    ("username", r"\b(username|user[- ]?name|login|handle|screen[- ]?name)\b"),
    ("phone", r"\b(phone|telephone|mobile|cell|msisdn|tel)\b"),
    # cvv and expiry are matched before the card number so that "card code" and
    # "card expiry" resolve to themselves rather than to the number - the bare
    # word "card" in a form field almost always means the PAN, but only once the
    # more specific card fields have had their turn.
    ("cvv", r"\b(cvv|cvc|security[- ]?code|card[- ]?code|card[- ]?verification)\b"),
    ("expiry", r"\b(expiry|expiration|exp[- ]?date|valid[- ]?thru|card[- ]?exp\w*)\b"),
    ("credit_card", r"\b(card[- ]?number|credit[- ]?card|debit[- ]?card|pan|cardno|card)\b"),
    ("postcode", r"\b(post[- ]?code|postal[- ]?code|zip|zip[- ]?code)\b"),
    ("country", r"\b(country|nation)\b"),
    ("state", r"\b(state|province|region|county)\b"),
    ("city", r"\b(city|town|locality)\b"),
    ("street", r"\b(street|address[- ]?line|addr1|address1|address)\b"),
    ("company", r"\b(company|organisation|organization|employer|business|firm)\b"),
    ("person_name", r"\b(first[- ]?name|last[- ]?name|given[- ]?name|surname|family[- ]?name|full[- ]?name|name)\b"),
    ("url", r"\b(url|website|web[- ]?site|homepage|link|uri)\b"),
    ("date_of_birth", r"\b(dob|date[- ]?of[- ]?birth|birth[- ]?date|birthday)\b"),
    ("datetime", r"\b(timestamp|datetime|date[- ]?time|created[- ]?at|updated[- ]?at)\b"),
    ("date", r"\b(date|day|deadline|due|from|until|start|end)\b"),
    ("currency", r"\b(amount|price|total|cost|salary|balance|fee|charge|subtotal)\b"),
    ("percentage", r"\b(percent|percentage|rate|discount)\b"),
    ("quantity", r"\b(quantity|qty|count|number[- ]?of|units|items)\b"),
    ("age", r"\b(age|years[- ]?old)\b"),
    ("uuid", r"\b(uuid|guid|correlation[- ]?id|trace[- ]?id)\b"),
    ("identifier", r"\b(id|identifier|reference|ref|code|sku|order[- ]?no)\b"),
    ("boolean", r"\b(is[- ]?\w+|has[- ]?\w+|enabled|active|accept|agree|consent|opt[- ]?in)\b"),
    ("ip_address", r"\b(ip|ip[- ]?address|host[- ]?ip)\b"),
    ("colour", r"\b(colour|color)\b"),
    ("description", r"\b(description|comment|notes?|message|body|bio|feedback|summary)\b"),
]

#: HTML input types and JSON Schema formats map straight through; they are
#: stronger evidence than a field name, so they are consulted first.
_TYPE_KIND = {
    "email": "email", "tel": "phone", "url": "url", "password": "password",
    "date": "date", "datetime-local": "datetime", "time": "time", "month": "month",
    "number": "number", "range": "number", "checkbox": "boolean", "color": "colour",
    "uuid": "uuid", "date-time": "datetime", "ipv4": "ip_address", "uri": "url",
    "hostname": "hostname",
}


def _normalise(name: str) -> str:
    """``customerEmailAddress`` / ``customer_email`` / ``Customer Email`` → one form."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name or "")
    return re.sub(r"[^a-z0-9]+", " ", spaced.lower()).strip()


def infer_kind(name: str = "", *, type_hint: str = "", enum: list | None = None) -> str:
    """Best semantic guess for a field, from its declared type then its name."""
    if enum:
        return "enum"
    hint = (type_hint or "").strip().lower()
    if hint in _TYPE_KIND:
        return _TYPE_KIND[hint]
    normal = _normalise(name)
    for kind, pattern in _KIND_PATTERNS:
        if re.search(pattern, normal):
            return kind
    if hint in {"integer", "int"}:
        return "integer"
    if hint in {"number", "float", "double", "decimal"}:
        return "number"
    if hint in {"boolean", "bool"}:
        return "boolean"
    return "text"


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def _luhn_complete(prefix: str, length: int) -> str:
    """Append the check digit that makes ``prefix`` a Luhn-valid number.

    Callers must pass a prefix in an unassigned major industry identifier; see
    the module docstring for why ``9`` is the only one used here.
    """
    body = prefix[: length - 1].ljust(length - 1, "0")
    total = 0
    for index, char in enumerate(reversed(body)):
        digit = int(char)
        if index % 2 == 0:            # position of the check digit is odd-indexed
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return body + str((10 - total % 10) % 10)


def value(kind: str, seed: str, *, locale: str = "en-US", constraints: dict | None = None) -> str:
    """A valid value of ``kind``, stable for a given ``seed``."""
    draw = _Draw(f"{locale}|{kind}|{seed}")
    limits = constraints or {}

    if kind == "person_name":
        return f"{draw.pick(_GIVEN)} {draw.pick(_FAMILY)}"
    if kind == "email":
        given = draw.pick(_GIVEN).lower()
        family = re.sub(r"[^a-z]", "", draw.pick(_FAMILY).lower())
        return f"{given}.{family}{draw.below(90) + 10}@{draw.pick(_SAFE_HOSTS)}"
    if kind == "username":
        return f"{draw.pick(_GIVEN).lower()}_{draw.below(9000) + 1000}"
    if kind == "password":
        # Satisfies the usual four-class policy without being guessable by shape.
        return f"{draw.pick(_GIVEN)}{draw.digits(3)}!{draw.pick(['Qx', 'Vz', 'Kw', 'Ry'])}"
    if kind == "phone":
        if locale == "en-GB":
            return f"07700 900{draw.digits(3)}"       # Ofcom drama range
        # NANP reserves exchange 555, numbers 0100-0199, for fictional use.
        return f"+1 (555) 555-01{draw.digits(2)}"
    if kind == "credit_card":
        return _luhn_complete("9" + draw.digits(14), 16)
    if kind == "cvv":
        return draw.digits(3)
    if kind == "expiry":
        return f"{draw.below(12) + 1:02d}/{(date.today().year % 100) + 2:02d}"
    if kind == "street":
        return f"{draw.below(300) + 1} {draw.pick(_STREET_WORDS)} {draw.pick(_STREET_TYPES)}"
    if kind == "city":
        return draw.pick(_CITIES_GB if locale == "en-GB" else _CITIES_US)
    if kind == "state":
        return draw.pick(_STATES)
    if kind == "country":
        return "United Kingdom" if locale == "en-GB" else "United States"
    if kind == "postcode":
        if locale == "en-GB":
            letters = "ABDEFGHJLNPQRSTUWXYZ"
            return (f"{letters[draw.below(20)]}{letters[draw.below(20)]}{draw.below(9) + 1} "
                    f"{draw.below(9)}{letters[draw.below(20)]}{letters[draw.below(20)]}")
        return draw.digits(5)
    if kind == "company":
        return f"{draw.pick(_COMPANY_HEAD)} {draw.pick(_COMPANY_TAIL)}"
    if kind == "url":
        return f"https://{draw.pick(_SAFE_HOSTS)}/{draw.pick(['docs', 'app', 'orders', 'p'])}/{draw.below(900) + 100}"
    if kind == "hostname":
        return draw.pick(_SAFE_HOSTS)
    if kind == "ip_address":
        # RFC 5737 documentation range: routable nowhere.
        return f"192.0.2.{draw.below(254) + 1}"
    if kind == "date_of_birth":
        return str(date.today() - timedelta(days=365 * (draw.below(50) + 20) + draw.below(360)))
    if kind == "date":
        return str(date.today() + timedelta(days=draw.below(60) + 1))
    if kind == "datetime":
        day = date.today() + timedelta(days=draw.below(30))
        return f"{day}T{draw.below(24):02d}:{draw.below(60):02d}:00Z"
    if kind == "time":
        return f"{draw.below(24):02d}:{draw.below(60):02d}"
    if kind == "month":
        return f"{date.today().year}-{draw.below(12) + 1:02d}"
    if kind == "currency":
        return f"{draw.below(90000) + 100}.{draw.below(100):02d}"
    if kind == "percentage":
        return str(draw.below(101))
    if kind in {"quantity", "integer", "number"}:
        low = _as_number(limits.get("minimum"), 1)
        high = _as_number(limits.get("maximum"), low + 999)
        span = max(1, int(high - low) + 1)
        picked = low + draw.below(span)
        return str(int(picked)) if kind != "number" or float(picked).is_integer() else str(picked)
    if kind == "age":
        return str(draw.below(60) + 18)
    if kind == "boolean":
        return "true"
    if kind == "uuid":
        raw = "".join(f"{draw.byte():02x}" for _ in range(16))
        return f"{raw[:8]}-{raw[8:12]}-4{raw[13:16]}-a{raw[17:20]}-{raw[20:32]}"
    if kind == "identifier":
        return f"{draw.pick(['AC', 'OR', 'PR', 'TX'])}-{draw.digits(6)}"
    if kind == "colour":
        return "#" + "".join(f"{draw.byte():02x}" for _ in range(3))
    if kind == "enum":
        options = [str(o) for o in (limits.get("enum") or [])]
        return draw.pick(options) if options else ""
    if kind == "description":
        return (f"{draw.pick(['Reported', 'Observed', 'Noted'])} during "
                f"{draw.pick(['checkout', 'onboarding', 'renewal', 'export'])}; "
                f"reference {draw.digits(4)}.")

    # Fall through: a plain string honouring any length constraint it was given.
    minimum = int(_as_number(limits.get("minLength"), 0))
    maximum = int(_as_number(limits.get("maxLength"), max(minimum, 12)))
    text = f"{draw.pick(_STREET_WORDS)}-{draw.digits(4)}"
    if len(text) < minimum:
        text = (text * (minimum // len(text) + 1))[:minimum]
    return text[:maximum] if maximum else text


def _as_number(raw, fallback):
    try:
        return float(raw) if raw is not None else fallback
    except (TypeError, ValueError):
        return fallback


# --------------------------------------------------------------------------- #
# Invalid variants
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Invalid:
    value: str
    why: str
    technique: str = "format partition"


#: Malformations that are genuinely *wrong* for the kind, each with the reason a
#: reviewer needs in order to agree it should be rejected. Generic mutations
#: (empty, whitespace, over-long) are added separately so they are not repeated
#: per kind.
_INVALID: dict[str, list[tuple[str, str]]] = {
    "email": [
        ("plainaddress", "no @ separator"),
        ("@example.com", "no local part"),
        ("user@", "no domain"),
        ("user@@example.com", "doubled @"),
        ("user name@example.com", "unquoted space in the local part"),
        ("user@example", "domain has no dot-separated TLD"),
    ],
    "phone": [
        ("12", "too short to be dialable"),
        ("+1 (555) 555-01AB", "letters where digits are required"),
        ("++15550100", "doubled international prefix"),
    ],
    "credit_card": [
        ("9000000000000000", "fails the Luhn checksum"),
        ("9999", "too short for any card scheme"),
        ("abcd efgh ijkl mnop", "non-numeric"),
    ],
    "cvv": [("12", "shorter than three digits"), ("12345", "longer than four digits"), ("abc", "non-numeric")],
    "expiry": [("13/30", "month 13 does not exist"), ("01/20", "already expired"), ("2030-01", "wrong format")],
    "url": [
        ("notaurl", "no scheme"),
        ("htp://example.com", "misspelled scheme"),
        ("javascript:alert(1)", "non-http scheme - must be rejected, not executed"),
    ],
    "date": [("2026-02-30", "30 February does not exist"), ("31/12/2026", "ambiguous day-first format"), ("yesterday", "not a date")],
    "date_of_birth": [("2099-01-01", "in the future"), ("1600-01-01", "implausibly distant past"), ("2026-13-01", "month 13")],
    "postcode": [("0", "too short"), ("!!!!!", "punctuation only")],
    "integer": [("3.5", "not an integer"), ("1e5", "exponent notation"), ("--1", "doubled sign")],
    "number": [("abc", "not numeric"), ("1,000.00", "thousands separator")],
    "quantity": [("-1", "negative quantity"), ("0.5", "fractional unit")],
    "age": [("-1", "negative age"), ("999", "implausible age"), ("twenty", "spelled out")],
    "currency": [("-5.00", "negative amount"), ("1.005", "more precision than a minor unit allows"), ("$5", "currency symbol inside the value")],
    "percentage": [("-1", "below zero"), ("101", "above one hundred")],
    "uuid": [("not-a-uuid", "wrong shape"), ("00000000-0000-0000-0000-00000000000", "one character short")],
    "boolean": [("maybe", "not a boolean"), ("2", "out of the boolean domain")],
    "ip_address": [("999.1.1.1", "octet above 255"), ("192.0.2", "only three octets")],
    "password": [("abc", "below any reasonable minimum length"), ("password", "a top-ranked common password")],
}

#: Inputs that must be *stored and rendered safely*, not rejected outright. Kept
#: apart from the invalid set because the expected outcome is the opposite: the
#: value should round-trip intact and appear escaped, never executed.
INJECTION_PROBES: list[tuple[str, str]] = [
    ("<script>alert(1)</script>", "must be escaped on output, never executed"),
    ("'; DROP TABLE users; --", "must be parameterised, never concatenated into SQL"),
    ("{{7*7}}", "must not be evaluated by a template engine"),
    ("../../etc/passwd", "must not traverse the filesystem"),
    ("‮exe.txt", "right-to-left override can disguise a filename"),
]


def invalid_variants(kind: str, *, constraints: dict | None = None, limit: int = 6) -> list[Invalid]:
    """Wrong values for ``kind``, each with the reason it is wrong."""
    limits = constraints or {}
    out = [Invalid(v, why) for v, why in _INVALID.get(kind, [])]

    minimum = limits.get("minLength")
    maximum = limits.get("maxLength")
    if isinstance(minimum, int) and minimum > 0:
        out.append(Invalid("x" * (minimum - 1), f"one character below the {minimum}-character minimum",
                           "boundary value analysis"))
    if isinstance(maximum, int) and maximum > 0:
        out.append(Invalid("x" * (maximum + 1), f"one character above the {maximum}-character maximum",
                           "boundary value analysis"))
    if limits.get("enum"):
        # Derived from a real member so the value is unmistakably adjacent to the
        # permitted set - the case that catches prefix matching and loose casts -
        # rather than a placeholder that reads as a bug in the generator.
        first = str(limits["enum"][0]) if limits["enum"] else "value"
        out.append(Invalid(f"{first}-x", f"'{first}-x' is outside the permitted set "
                                         f"{sorted(str(e) for e in limits['enum'])}",
                           "equivalence partitioning"))
    if not limits.get("optional"):
        out.append(Invalid("", "required field left empty", "equivalence partitioning"))
    out.append(Invalid("   ", "whitespace only - must not pass as present", "equivalence partitioning"))
    return _dedupe(out)[:limit]


def _dedupe(items: list[Invalid]) -> list[Invalid]:
    seen: set[str] = set()
    out: list[Invalid] = []
    for item in items:
        if item.value in seen:
            continue
        seen.add(item.value)
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Field-level convenience
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Field:
    name: str
    kind: str
    valid: str
    invalid: list[Invalid] = field(default_factory=list)
    locale: str = "en-US"

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "valid": self.valid,
            "locale": self.locale,
            "invalid": [{"value": i.value, "why": i.why, "technique": i.technique} for i in self.invalid],
        }


def describe_field(
    name: str,
    *,
    seed: str = "",
    type_hint: str = "",
    constraints: dict | None = None,
    locale: str = "en-US",
) -> Field:
    """Infer a field's kind and produce one valid value plus its invalid set."""
    if locale not in _LOCALES:
        locale = "en-US"
    limits = dict(constraints or {})
    kind = infer_kind(name, type_hint=type_hint, enum=limits.get("enum"))
    return Field(
        name=name,
        kind=kind,
        valid=value(kind, seed or name, locale=locale, constraints=limits),
        invalid=invalid_variants(kind, constraints=limits),
        locale=locale,
    )


def dataset(names: list[str], *, seed: str, rows: int = 3, locale: str = "en-US") -> list[dict]:
    """``rows`` distinct-but-reproducible records over the given field names."""
    out = []
    for index in range(max(1, rows)):
        record = {}
        for name in names:
            kind = infer_kind(name)
            record[name] = value(kind, f"{seed}/{index}", locale=locale)
        out.append(record)
    return out


#: Step values may carry a generator instead of a literal, e.g.
#: ``{"generate": {"kind": "email", "unique": true}}``. Resolution happens at
#: plan time so the runner still receives a plain string and the run record shows
#: exactly which value was used.
def resolve_step_value(spec: dict, *, seed: str, locale: str = "en-US") -> str:
    generator = (spec or {}).get("generate") or {}
    kind = generator.get("kind") or infer_kind(generator.get("field", ""))
    scope = seed if generator.get("unique") else (generator.get("field") or kind)
    return value(kind, f"{scope}", locale=generator.get("locale", locale),
                 constraints=generator.get("constraints"))
