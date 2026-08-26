"""Quality-engineering tools the Copilot can call.

Both are deterministic. Neither needs a model to produce its output, which means
they keep working in No-AI mode and their results are reproducible — a generated
script that differs between two identical requests is not reviewable.

They are registered ``read_only``: one reads requirements, the other renders
text. Neither writes to the database, so neither passes through the approval
gate. Persisting a generated script *is* a state change, and that is
``create_test``'s job — already registered, already gated.
"""

from __future__ import annotations

import json
import re

from sqlalchemy import select

from ..ai.tools import ToolContext, registry
from ..models import RequirementItem


# --------------------------------------------------------------------------- #
# query_requirements
# --------------------------------------------------------------------------- #
@registry.register(
    "query_requirements",
    description=(
        "Look up a feature's requirements and their acceptance criteria. Call "
        "this BEFORE proposing tests or writing a script: it returns the "
        "criteria the tests must satisfy, plus any ambiguity the analyst could "
        "not resolve. If it returns no criteria, ask the user for them rather "
        "than inventing them."
    ),
    parameters={
        "properties": {
            "feature": {
                "type": "string",
                "description": "Feature name or keyword, matched against requirement titles and text.",
            },
            "ref": {
                "type": "string",
                "description": "Exact requirement reference, e.g. REQ-014. Overrides `feature` when given.",
            },
            "limit": {"type": "integer", "description": "Maximum requirements to return (default 10)."},
        },
        "required": ["feature"],
    },
    category="requirements",
    scopes=["requirements:read"],
    title="Query requirements and acceptance criteria",
    output_schema={
        "type": "object",
        "required": ["ok", "count", "requirements", "guidance"],
        "properties": {
            "ok": {"type": "boolean"},
            "count": {"type": "integer", "description": "How many requirements matched."},
            "acceptance_criteria_count": {"type": "integer"},
            "requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["ref", "title", "acceptance_criteria"],
                    "properties": {
                        "ref": {"type": "string", "description": "Customer reference, e.g. REQ-014."},
                        "title": {"type": "string"},
                        "text": {"type": "string"},
                        "kind": {"type": "string"},
                        "risk": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        "open_questions": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            "guidance": {
                "type": "string",
                "description": "What to do next — in particular, whether criteria are missing.",
            },
        },
    },
)
def query_requirements(args: dict, ctx: ToolContext) -> dict:
    feature = (args.get("feature") or "").strip()
    ref = (args.get("ref") or "").strip().upper()
    limit = max(1, min(int(args.get("limit") or 10), 50))

    stmt = select(RequirementItem).where(RequirementItem.project_id == ctx.project_id)
    if ref:
        stmt = stmt.where(RequirementItem.ref == ref)
    elif feature:
        like = f"%{feature.lower()}%"
        # Title and body both, because a feature name often appears only in the
        # prose of the requirement that governs it.
        stmt = stmt.where(
            RequirementItem.title.ilike(like) | RequirementItem.text.ilike(like)
        )

    items = list(ctx.db.execute(stmt.order_by(RequirementItem.ref).limit(limit)).scalars())

    requirements = [
        {
            "ref": item.ref,
            "title": item.title,
            "text": item.text[:1200],
            "kind": item.kind,
            "risk": item.risk,
            "acceptance_criteria": item.acceptance_criteria or [],
            # Surfaced, not hidden: an unresolved ambiguity is exactly the thing
            # the agent must ask about instead of guessing past.
            "open_questions": item.open_questions or [],
        }
        for item in items
    ]

    total_criteria = sum(len(r["acceptance_criteria"]) for r in requirements)
    return {
        "ok": True,
        "query": {"feature": feature, "ref": ref or None},
        "count": len(requirements),
        "requirements": requirements,
        "acceptance_criteria_count": total_criteria,
        "guidance": _requirements_guidance(requirements, total_criteria, feature),
        # Drives the Requirements pane of the workspace. Markdown rather than
        # the raw rows: the pane renders a document a human reads, and deciding
        # here how a requirement should read keeps that decision next to the
        # data instead of scattered through the frontend.
        "_ui": {
            "pane": "requirements",
            "title": ref or feature or "Requirements",
            "markdown": _requirements_markdown(requirements, feature, ref),
            "count": len(requirements),
        },
    }


def _requirements_markdown(requirements: list[dict], feature: str, ref: str) -> str:
    if not requirements:
        return (
            f"# No match for {ref or feature!r}\n\n"
            "Nothing in this project matches that. Upload the requirement document, "
            "or state the acceptance criteria directly, and ask again.\n\n"
            "> Criteria are not inferred from a title. A test built on a guess "
            "asserts a guess."
        )

    lines: list[str] = [f"# {ref or feature.title() or 'Requirements'}", ""]
    for item in requirements:
        lines += [f"## {item['ref']} — {item['title']}", ""]
        lines += [f"`{item['kind']}` · risk **{item['risk']}**", ""]
        if item["text"]:
            lines += [item["text"].strip(), ""]

        if item["acceptance_criteria"]:
            lines += ["**Acceptance criteria**", ""]
            lines += [f"{n}. {c}" for n, c in enumerate(item["acceptance_criteria"], 1)]
            lines.append("")
        else:
            lines += [
                "> **No acceptance criteria recorded.** Anything asserted against this "
                "requirement would be invented. Supply them before treating it as covered.",
                "",
            ]

        if item["open_questions"]:
            lines += ["**Open questions**", ""]
            lines += [f"- {q}" for q in item["open_questions"]]
            lines.append("")
    return "\n".join(lines)


def _requirements_guidance(requirements: list[dict], criteria: int, feature: str) -> str:
    """What the agent should do next, stated plainly in the result.

    Putting this in the payload rather than only in the system prompt matters:
    the model reads tool results far more reliably than it re-reads its
    instructions twenty turns later.
    """
    if not requirements:
        return (
            f"No requirement matches {feature!r} in this project. Do not invent "
            f"acceptance criteria. Ask the user to upload the requirement, or to "
            f"state the criteria explicitly, before proposing any test."
        )
    if not criteria:
        refs = ", ".join(r["ref"] for r in requirements[:5])
        return (
            f"Found {len(requirements)} requirement(s) ({refs}) but none carries "
            f"acceptance criteria. Ask the user for the criteria before writing "
            f"tests — a test derived from a title alone asserts nothing useful."
        )
    unresolved = [q for r in requirements for q in r["open_questions"]]
    if unresolved:
        return (
            f"{criteria} acceptance criteria found, but {len(unresolved)} open "
            f"question(s) remain unresolved. Raise them with the user before "
            f"asserting behaviour they do not specify."
        )
    return f"{criteria} acceptance criteria found. Base every assertion on one of them."


# --------------------------------------------------------------------------- #
# generate_playwright_script
# --------------------------------------------------------------------------- #
@registry.register(
    "generate_playwright_script",
    description=(
        "Render a BDD/Gherkin scenario as a Playwright TypeScript spec using the "
        "Page Object Model. Returns the page object and the spec as separate "
        "files. Locators are NEVER invented: any element the scenario does not "
        "pin down is emitted as a clearly marked TODO for a human to fill in. "
        "Call query_requirements first so the assertions come from real criteria."
    ),
    parameters={
        "properties": {
            "scenario": {
                "type": "string",
                "description": "The BDD scenario in Gherkin (Given / When / Then / And).",
            },
            "page_object": {
                "type": "string",
                "description": "Page object class name, e.g. CheckoutPage. Derived from the scenario when omitted.",
            },
            "base_path": {
                "type": "string",
                "description": "Route the page object navigates to, e.g. /checkout.",
            },
            "requirement_ref": {
                "type": "string",
                "description": "Requirement this scenario covers, e.g. REQ-014. Emitted as a traceability tag.",
            },
        },
        "required": ["scenario"],
    },
    category="authoring",
    scopes=["tests:read"],
    title="Generate a Playwright script from a scenario",
    output_schema={
        "type": "object",
        "required": ["ok"],
        "properties": {
            "ok": {"type": "boolean"},
            "error": {"type": "string", "description": "Present only when ok is false."},
            "page_object": {"$ref": "#/$defs/file"},
            "spec": {"$ref": "#/$defs/file"},
            "unresolved_locators": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Steps that named no element, so no locator was derived.",
            },
            "guidance": {"type": "string"},
        },
        "$defs": {
            "file": {
                "type": "object",
                "required": ["filename", "language", "code"],
                "properties": {
                    "filename": {"type": "string"},
                    "language": {"type": "string", "enum": ["typescript"]},
                    "code": {"type": "string"},
                },
            },
        },
    },
)
def generate_playwright_script(args: dict, ctx: ToolContext) -> dict:
    del ctx  # rendering is pure; nothing is read or written

    scenario = (args.get("scenario") or "").strip()
    if not scenario:
        return {"ok": False, "error": "No scenario was supplied."}

    steps = _parse_gherkin(scenario)
    if not steps:
        return {
            "ok": False,
            "error": (
                "No Given/When/Then steps were found. Supply the scenario in "
                "Gherkin so each step maps to an action or an assertion."
            ),
        }

    title = _scenario_title(scenario) or "Scenario"
    page_class = _pascal(args.get("page_object") or f"{_first_noun(title)} Page")
    base_path = (args.get("base_path") or "/").strip() or "/"
    ref = (args.get("requirement_ref") or "").strip().upper()

    actions = [s for s in steps if s["kind"] in {"given", "when"}]
    assertions = [s for s in steps if s["kind"] == "then"]

    methods, unresolved = _page_methods(actions, assertions)
    page_object = _render_page_object(page_class, base_path, methods)
    spec = _render_spec(title, page_class, base_path, steps, ref)

    page_file = {"filename": f"pages/{page_class}.ts", "language": "typescript", "code": page_object}
    spec_file = {"filename": f"tests/{_kebab(title)}.spec.ts", "language": "typescript", "code": spec}

    return {
        "ok": True,
        "page_object": page_file,
        "spec": spec_file,
        "unresolved_locators": unresolved,
        # Drives the Test Matrix pane. Both files travel, because reviewing a
        # spec without the page object it calls into is reviewing half of it.
        "_ui": {
            "pane": "test_matrix",
            "title": title,
            "files": [spec_file, page_file],
            "unresolved": unresolved,
            "requirement_ref": ref,
        },
        "guidance": (
            f"{len(unresolved)} locator(s) could not be derived from the scenario and are "
            f"marked TODO in {page_class}.ts. Fill them in against the real DOM — do not "
            f"guess them. Use the recorder (Author → Record a session) to capture them with "
            f"a full locator ladder."
            if unresolved else
            "Every locator was derived from a named element in the scenario. Verify them "
            "against the real DOM before committing."
        ),
    }


# --------------------------------------------------------------------------- #
# Gherkin parsing
# --------------------------------------------------------------------------- #
STEP = re.compile(r"^\s*(given|when|then|and|but)\b[:\s]*(.+?)\s*$", re.IGNORECASE)

#: Quoted spans, matched as *balanced pairs* rather than as a character class.
#:
#: A class like ``["\'](...)["\']`` looks equivalent and is not: it terminates
#: on the apostrophe inside ``"It's fine"``, yielding the element name ``It``.
#: Each quote style therefore closes only with its own partner.
QUOTED_PAIR = re.compile(
    r'"([^"]{1,60})"'
    r"|'([^']{1,60})'"
    r"|\u201c([^\u201d]{1,60})\u201d"
    r"|\u2018([^\u2019]{1,60})\u2019"
)


def _quoted(text: str) -> list[tuple[int, int, str]]:
    """Every quoted span as (start, end, value), in order of appearance."""
    out: list[tuple[int, int, str]] = []
    for match in QUOTED_PAIR.finditer(text):
        value = next((g for g in match.groups() if g is not None), None)
        if value:
            out.append((match.start(), match.end(), value.strip()))
    return out


def _parse_gherkin(scenario: str) -> list[dict]:
    """Split into steps, resolving And/But to whatever preceded them."""
    out: list[dict] = []
    previous = "given"
    for line in scenario.splitlines():
        match = STEP.match(line)
        if not match:
            continue
        keyword = (match.group(1) or "").lower()
        text = (match.group(2) or "").strip()
        if not text:
            continue
        kind = previous if keyword in {"and", "but"} else keyword
        previous = kind
        out.append({"kind": kind, "text": text, "keyword": keyword})
    return out


def _scenario_title(scenario: str) -> str:
    for line in scenario.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(("scenario:", "scenario outline:")):
            return stripped.split(":", 1)[1].strip()
    # No title line: use the first When, which is usually the point of the test.
    for step in _parse_gherkin(scenario):
        if step["kind"] == "when":
            return step["text"][:70]
    return ""


def _page_methods(actions: list[dict], assertions: list[dict]) -> tuple[list[dict], list[str]]:
    """One method per step, plus the locators each needs.

    A locator is only emitted when the scenario names the element — in quotes, or
    as a recognisable field phrase. Everything else becomes a TODO. Inventing
    `page.locator('.btn-primary')` because a button was mentioned is precisely
    the failure this refuses to commit.
    """
    methods: list[dict] = []
    unresolved: list[str] = []
    seen: set[str] = set()

    for step in actions + assertions:
        text = step["text"]
        name = _method_name(step)
        if name in seen:
            continue
        seen.add(name)

        target = _named_element(text)
        locator = _locator_for(text, target)
        if locator is None:
            unresolved.append(text)
        methods.append({
            "name": name,
            "step": text,
            "kind": step["kind"],
            "target": target,
            "locator": locator,
            "value": _value_for(text),
        })
    return methods, unresolved


#: Nouns that mark the preceding phrase as an element rather than a value.
ELEMENT_NOUN = r"(?:field|button|link|checkbox|dropdown|select|input|box|tab|menu|message|heading|label)"


def _named_element(text: str) -> str | None:
    """The element a step acts on — never the value it types into it.

    The distinction is the whole job. In::

        When the user enters "ravi@example.com" into the "Email address" field

    both strings are quoted, and taking the first produces
    ``getByLabel('ravi@example.com')`` — a locator that looks plausible, matches
    nothing, and names a field after its own contents. Worse, the value is often
    a number, and ``readonly 4242424242424242: Locator`` is not valid TypeScript.

    So a quoted span is only treated as an element when the sentence says it is
    one: an element noun follows it, or it sits after a transfer preposition.
    """
    spans = _quoted(text)

    # 1. A quoted span immediately followed by an element noun. Strongest signal.
    for _, end, value in spans:
        if re.match(rf"\s+{ELEMENT_NOUN}\b", text[end:], re.IGNORECASE):
            return value

    # 2. A quoted span preceded by into/in/on/to — the target of a transfer.
    for start, _, value in spans:
        if re.search(r"\b(?:into|in|on|to)\s+(?:the\s+)?$", text[:start], re.IGNORECASE):
            return value

    # 3. An unquoted phrase before an element noun: "the Continue button".
    match = re.search(rf"\bthe\s+([A-Za-z][\w\s-]{{1,40}}?)\s+{ELEMENT_NOUN}\b", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()

    # 4. A lone quoted span with no transfer verb: "clicks 'Save'".
    if len(spans) == 1 and not re.search(r"\b(?:enters?|types?|fills?|sets?)\b", text, re.IGNORECASE):
        return spans[0][2]

    return None


def _locator_for(text: str, target: str | None) -> str | None:
    """Role-based locator when the element is named; otherwise nothing."""
    if not target:
        return None
    lowered = text.lower()
    if re.search(r"\bbutton\b", lowered):
        return f"getByRole('button', {{ name: {_ts(target)} }})"
    if re.search(r"\blink\b", lowered):
        return f"getByRole('link', {{ name: {_ts(target)} }})"
    if re.search(r"\bcheckbox\b", lowered):
        return f"getByRole('checkbox', {{ name: {_ts(target)} }})"
    if re.search(r"\b(dropdown|select)\b", lowered):
        return f"getByRole('combobox', {{ name: {_ts(target)} }})"
    if re.search(r"\b(field|input|box|enters?|types?|fills?)\b", lowered):
        return f"getByLabel({_ts(target)})"
    if re.search(r"\b(sees?|shown|displayed|visible|message|heading)\b", lowered):
        return f"getByText({_ts(target)})"
    return f"getByRole('button', {{ name: {_ts(target)} }})"


def _value_for(text: str) -> str | None:
    """The value being entered, when the step spells one out.

    The first quoted span that follows an entry verb — which, given
    :func:`_named_element` takes the one after "into", is the other one.
    """
    verb = re.search(r"\b(?:enters?|types?|fills?|sets?)\b", text, re.IGNORECASE)
    if not verb:
        return None
    element = _named_element(text)
    for _, _, value in _quoted(text):
        if value != element:
            return value
    return None


def _method_name(step: dict) -> str:
    words = re.sub(r"[^A-Za-z0-9\s]", " ", step["text"]).split()
    if not words:
        return "step"
    head = words[0].lower()
    rest = "".join(w.capitalize() for w in words[1:6])
    if step["kind"] == "then" and not head.startswith("expect"):
        return f"expect{rest or head.capitalize()}"
    return f"{head}{rest}"


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def _render_page_object(page_class: str, base_path: str, methods: list[dict]) -> str:
    locators = [m for m in methods if m["locator"]]
    todos = [m for m in methods if not m["locator"]]

    lines = [
        "import { type Locator, type Page, expect } from '@playwright/test';",
        "",
        "/**",
        f" * Page object for {base_path}.",
        " *",
        " * Locators are declared once here and nowhere else, so a UI change is one",
        " * edit rather than a search across every spec that touches this screen.",
        " */",
        f"export class {page_class} {{",
        "  readonly page: Page;",
    ]

    for method in locators:
        lines.append(f"  readonly {_camel(method['target'] or method['name'])}: Locator;")
    if todos:
        lines.append("  // TODO: declare locators for the steps marked below.")

    lines += [
        "",
        "  constructor(page: Page) {",
        "    this.page = page;",
    ]
    for method in locators:
        lines.append(f"    this.{_camel(method['target'] or method['name'])} = page.{method['locator']};")
    lines += ["  }", "", "  async goto(): Promise<void> {", f"    await this.page.goto({_ts(base_path)});", "  }"]

    for method in methods:
        lines.append("")
        lines.append(f"  /** {method['kind'].capitalize()}: {method['step']} */")
        lines.append(f"  async {method['name']}(): Promise<void> {{")
        if not method["locator"]:
            # Never a fabricated selector. A failing TODO is honest; a guessed
            # locator that happens to match something else is a false pass.
            lines.append("    // TODO: this step does not name an element, so no locator could be")
            lines.append("    //       derived. Fill it in against the real DOM before enabling.")
            lines.append(f"    throw new Error('Unimplemented step: {_escape(method['step'])}');")
        else:
            field = _camel(method["target"] or method["name"])
            if method["kind"] == "then":
                lines.append(f"    await expect(this.{field}).toBeVisible();")
            elif method["value"] is not None:
                lines.append(f"    await this.{field}.fill({_ts(method['value'])});")
            else:
                lines.append(f"    await this.{field}.click();")
        lines.append("  }")

    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_spec(title: str, page_class: str, base_path: str, steps: list[dict], ref: str) -> str:
    del base_path
    instance = _camel(page_class)
    lines = [
        "import { test, expect } from '@playwright/test';",
        f"import {{ {page_class} }} from '../pages/{page_class}';",
        "",
    ]
    if ref:
        lines += [f"// Traceability: {ref}", ""]
    lines += [
        f"test.describe({_ts(title)}, () => {{",
        f"  let {instance}: {page_class};",
        "",
        "  test.beforeEach(async ({ page }) => {",
        f"    {instance} = new {page_class}(page);",
        f"    await {instance}.goto();",
        "  });",
        "",
    ]

    # Built outside the f-string: nested quotes inside an f-string expression are
    # a syntax error before Python 3.12, and this package targets 3.11.
    tag = f", {{ tag: {_ts('@' + ref)} }}" if ref else ""
    lines.append(f"  test({_ts(title)}{tag}, async () => {{")

    seen: set[str] = set()
    for step in steps:
        name = _method_name(step)
        lines.append(f"    // {step['keyword'].capitalize()} {step['text']}")
        if name in seen:
            lines.append(f"    // (covered by {instance}.{name}() above)")
            continue
        seen.add(name)
        lines.append(f"    await {instance}.{name}();")

    lines += ["  });", "});"]
    # expect is imported for the reader's convenience even when every assertion
    # lives in the page object; an unused import trips no-unused-vars in strict
    # configs, so it is referenced explicitly rather than left dangling.
    body = "\n".join(lines) + "\n"
    if "expect(" not in body:
        body = body.replace("import { test, expect } from '@playwright/test';",
                            "import { test } from '@playwright/test';")
    return body


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _ts(value: str) -> str:
    """A TypeScript single-quoted string literal, correctly escaped."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _pascal(value: str) -> str:
    words = re.sub(r"[^A-Za-z0-9]+", " ", value).split()
    return "".join(w[:1].upper() + w[1:] for w in words) or "AppPage"


def _camel(value: str) -> str:
    """A valid TypeScript identifier, always.

    A class member may not begin with a digit, so anything that would is
    prefixed. Emitting `readonly 4242…: Locator` produces a file that will not
    parse, and a generator that emits code the compiler rejects is worse than
    one that emits nothing.
    """
    pascal = _pascal(value)
    if not pascal:
        return "element"
    identifier = pascal[:1].lower() + pascal[1:]
    return f"field{pascal}" if identifier[0].isdigit() else identifier


def _kebab(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "scenario"


def _first_noun(title: str) -> str:
    words = re.sub(r"[^A-Za-z0-9\s]", " ", title).split()
    skip = {"a", "an", "the", "user", "should", "can", "is", "are", "with", "and", "to", "on", "for"}
    for word in words:
        if word.lower() not in skip and len(word) > 2:
            return word
    return "App"


__all__ = ["query_requirements", "generate_playwright_script", "json"]


# --------------------------------------------------------------------------- #
# generate_bdd_scenarios
# --------------------------------------------------------------------------- #
@registry.register(
    "generate_bdd_scenarios",
    description=(
        "Turn a requirement's acceptance criteria into Gherkin scenarios, ready "
        "for generate_playwright_script. Use it after query_requirements has "
        "returned criteria and before writing any test: it is the missing step "
        "between what the requirement says and what a script asserts. Each "
        "criterion becomes one Scenario, and where the requirement states an "
        "input domain (a length range, an enum, a threshold) a Scenario Outline "
        "is added whose Examples come from boundary value analysis and "
        "equivalence partitioning rather than from guesswork. Pass "
        "requirement_ref to read the criteria from the project, or criteria "
        "directly when the user supplied them in the conversation. A criterion "
        "whose action could not be derived is flagged rather than invented."
    ),
    parameters={
        "properties": {
            "requirement_ref": {
                "type": "string",
                "description": "Requirement to read criteria from, e.g. REQ-014. Overrides `criteria` when both are given.",
            },
            "feature": {
                "type": "string",
                "description": "Feature name for the Feature: line, e.g. Checkout. Derived from the requirement title when omitted.",
            },
            "criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Acceptance criteria stated by the user, when no requirement is ingested. One scenario per entry.",
            },
            "include_negative": {
                "type": "boolean",
                "description": "Also emit a negative scenario per criterion that names a rejection or error. Default true.",
            },
        },
        "required": [],
    },
    category="authoring",
    scopes=["requirements:read"],
    title="Generate Gherkin scenarios from acceptance criteria",
    input_examples=[
        {"requirement_ref": "REQ-014"},
        {"feature": "Sign in", "criteria": ["A valid password signs the user in", "A wrong password shows an error"]},
    ],
)
def generate_bdd_scenarios(args: dict, ctx: ToolContext) -> dict:
    from ..intelligence import testdesign

    ref = (args.get("ref") or args.get("requirement_ref") or "").strip().upper()
    feature = (args.get("feature") or "").strip()
    criteria = [c.strip() for c in (args.get("criteria") or []) if str(c).strip()]
    include_negative = args.get("include_negative", True) is not False
    requirement_text = ""

    if ref:
        if ctx is None:
            return {"ok": False, "error": "A requirement lookup needs a project context."}
        item = ctx.db.execute(
            select(RequirementItem).where(
                RequirementItem.project_id == ctx.project_id, RequirementItem.ref == ref
            )
        ).scalars().first()
        if item is None:
            return {
                "ok": False,
                "error": (
                    f"No requirement {ref} in this project. Call query_requirements to find "
                    "the right reference, or pass the criteria directly."
                ),
            }
        criteria = list(item.acceptance_criteria or [])
        feature = feature or _feature_from_title(item.title)
        requirement_text = item.text or ""
        if not criteria:
            return {
                "ok": False,
                "error": (
                    f"{ref} has no acceptance criteria recorded. A scenario derived from the "
                    "title alone asserts nothing — ask the user for the criteria first."
                ),
            }

    if not criteria:
        return {
            "ok": False,
            "error": (
                "No criteria to work from. Pass requirement_ref for an ingested requirement, "
                "or criteria for ones the user stated. Do not invent them."
            ),
        }

    feature = feature or "Feature"
    scenarios: list[dict] = []
    unresolved: list[str] = []

    for index, criterion in enumerate(criteria, 1):
        when, then, derived = _split_criterion(criterion)
        if not derived:
            unresolved.append(criterion)
        scenarios.append({
            "title": _scenario_name(criterion),
            "kind": "scenario",
            "criterion_index": index,
            "steps": [
                ("Given", f"the {feature.lower()} is available"),
                ("When", when),
                ("Then", then),
            ],
            "technique": "acceptance criterion",
            "derived": derived,
        })
        if include_negative and derived and _implies_acceptance(criterion):
            scenarios.append({
                "title": f"{_scenario_name(criterion)} — is refused when the condition is not met",
                "kind": "scenario",
                "criterion_index": index,
                "steps": [
                    ("Given", f"the {feature.lower()} is available"),
                    ("When", _negate(when)),
                    ("Then", "the user sees an actionable error"),
                    ("And", "nothing has been committed"),
                ],
                "technique": "negative path",
                "derived": derived,
            })

    # Input-domain scenarios: the same design techniques the requirement path
    # uses, rendered as an Outline so every derived value is one Examples row.
    analysis = testdesign.analyse(" ".join([requirement_text, *criteria]), subject=feature)
    # The analyser names a variable after the phrase it found it in, so the same
    # input can surface twice ("submits password", "password"). Normalise the
    # name and merge the rows, or the Outline is emitted twice for one field.
    merged: dict[str, list] = {}
    for variable in analysis.variables:
        clean = _clean_variable(variable.name)
        rows = [v for v in analysis.values if v.variable == variable.name]
        bucket = merged.setdefault(clean, [])
        seen = {r.value for r in bucket}
        bucket.extend(r for r in rows if r.value not in seen)
    for name, rows in merged.items():
        if len(rows) < 2:
            continue
        variable = type("V", (), {"name": name})()
        scenarios.append({
            "title": f"{variable.name} boundaries",
            "kind": "outline",
            "criterion_index": 0,
            "steps": [
                ("Given", f"the {feature.lower()} is available"),
                ("When", f'the user enters "<value>" as the {variable.name}'),
                ("Then", 'the result is "<expected>"'),
            ],
            "examples": [
                {"value": v.value, "partition": v.partition, "expected": v.expected, "label": v.label}
                for v in rows
            ],
            "technique": ", ".join(sorted({v.technique.replace("_", " ") for v in rows})),
            "derived": True,
        })

    code = _render_feature(feature, ref, scenarios)
    feature_file = {
        "filename": f"features/{_kebab(feature)}.feature",
        "language": "gherkin",
        "code": code,
    }
    return {
        "ok": True,
        "feature": feature,
        "requirement_ref": ref,
        "scenario_count": len(scenarios),
        "scenarios": [
            {k: v for k, v in s.items() if k != "steps"} | {"steps": [f"{kw} {txt}" for kw, txt in s["steps"]]}
            for s in scenarios
        ],
        "feature_file": feature_file,
        "unresolved": unresolved,
        "techniques_applied": sorted({s["technique"] for s in scenarios}),
        "guidance": (
            f"{len(unresolved)} criterion(s) did not state an action, so their When step is a "
            f"placeholder marked TODO — confirm the trigger with the user before scripting them. "
            if unresolved else
            "Every scenario's action was derived from its criterion. "
        ) + "Pass any scenario to generate_playwright_script to render it; review the "
            "Outline's Examples so a value the requirement did not specify is not asserted.",
        "_ui": {
            "pane": "test_matrix",
            "title": f"{feature} scenarios",
            "files": [feature_file],
            "unresolved": unresolved,
            "requirement_ref": ref,
        },
    }


#: "When A, B" / "When A then B" / "If A, then B". The separator is mandatory:
#: a version that accepted bare whitespace split "When the user submits…" into
#: When "the" / Then "user submits…", which is worse than no split at all.
TRIGGER = re.compile(
    r"^\s*(?:when|if|once|after|given that)\s+(.+?)\s*(?:,\s*(?:then\s+)?|\s+then\s+)(.+)$",
    re.IGNORECASE,
)
OUTCOME_VERB = re.compile(
    r"^(.+?)\s+(is|are|shows?|displays?|returns?|must|should|shall|will|can|cannot|"
    r"commits?|rejects?|accepts?)\b(.*)$", re.IGNORECASE,
)
#: Criteria that describe a *validation passing*. These have a real inverse -
#: the same input failing the check - so a negative scenario asserts something.
#: A criterion like "the confirmation shows the order number" does not, and
#: generating "…is refused when the condition is not met" for it is invention.
ACCEPTANCE = re.compile(
    r"\b(is|are|gets?|be)\s+(accepted|allowed|permitted|created|saved|signed in|logged in|"
    r"approved|valid|successful)\b|\b(passes|succeeds|signs in|logs in)\b",
    re.IGNORECASE,
)
REJECTION = re.compile(
    r"\b(reject|refus|declin|invalid|error|fail|not accept|must not|cannot|denied)", re.IGNORECASE,
)


def _split_criterion(criterion: str) -> tuple[str, str, bool]:
    """Derive (when, then, derived) from a criterion sentence.

    `derived` is False when the action had to be a placeholder. Saying so is the
    point: a Gherkin step that reads "When the scenario is exercised" is not a
    test, and the reviewer must know it was not derived rather than assume it.
    """
    text = criterion.strip().rstrip(".")
    match = TRIGGER.match(text)
    if match:
        return _lower_first(match.group(1)), _lower_first(match.group(2)), True
    match = OUTCOME_VERB.match(text)
    if match:
        subject = match.group(1).strip()
        return f"the user provides {_lower_first(subject)}", _lower_first(text), True
    return f"TODO: the action for \"{text}\" is not stated", _lower_first(text), False


def _implies_acceptance(criterion: str) -> bool:
    """Does this criterion have an inverse worth a scenario of its own?"""
    return bool(ACCEPTANCE.search(criterion)) and not REJECTION.search(criterion)


QUALIFIER = re.compile(r"\s+(?:between|that|which|with|of at least|of at most|longer|shorter|over|under)\b.*$", re.IGNORECASE)


def _negate(when: str) -> str:
    """The inverse action: the same subject, failing the stated condition.

    "submits a password between 8 and 64 characters" negated is "submits a
    password that does not meet the requirement" - the qualifying clause is
    what is being violated, so it is replaced rather than appended to.
    """
    if when.startswith("TODO"):
        return when
    subject = QUALIFIER.sub("", when).strip()
    return f"{subject} that does not meet the requirement"


def _feature_from_title(title: str) -> str:
    """"Checkout must accept a valid payment card" → "Checkout".

    A requirement title is an obligation; a Feature line is a noun. Using the
    whole title produces "Given the checkout must accept a valid payment card is
    available", which no reviewer should have to read.
    """
    match = re.match(r"^\s*(.+?)\s+(?:must|shall|should|can|will|needs? to|has to)\b", title, re.IGNORECASE)
    name = (match.group(1) if match else title).strip()
    return name[:1].upper() + name[1:] if name else "Feature"


LEADING_VERB = re.compile(r"^(?:submits?|enters?|provides?|types?|sets?|chooses?|selects?|uploads?)\s+", re.IGNORECASE)


def _clean_variable(name: str) -> str:
    return LEADING_VERB.sub("", name.strip()).strip() or name


def _scenario_name(criterion: str) -> str:
    return criterion.strip().rstrip(".")[:90]


def _lower_first(text: str) -> str:
    """Lower-case the first letter unless the word is an acronym ("API", "URL")."""
    text = text.strip()
    if len(text) >= 2 and text[0].isalpha() and text[1].isalpha() and text[:2].isupper():
        return text
    return text[:1].lower() + text[1:]


def _render_feature(feature: str, ref: str, scenarios: list[dict]) -> str:
    lines = [f"Feature: {feature}"]
    if ref:
        lines.append(f"  # Traceability: {ref}")
    for scenario in scenarios:
        lines.append("")
        lines.append(f"  # Technique: {scenario['technique']}")
        if not scenario["derived"]:
            lines.append("  # TODO: the action below was not stated in the criterion — confirm it.")
        keyword = "Scenario Outline" if scenario["kind"] == "outline" else "Scenario"
        lines.append(f"  {keyword}: {scenario['title']}")
        for kw, text in scenario["steps"]:
            lines.append(f"    {kw} {text}")
        if scenario["kind"] == "outline":
            lines.append("")
            lines.append("    Examples:")
            width = max(len(str(e["value"])) for e in scenario["examples"])
            lines.append(f"      | {'value'.ljust(width)} | partition | expected |")
            for ex in scenario["examples"]:
                lines.append(f"      | {str(ex['value']).ljust(width)} | {ex['partition']:<9} | {ex['expected']} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# generate_test_data
# --------------------------------------------------------------------------- #
@registry.register(
    "generate_test_data",
    description=(
        "Generate realistic, reproducible test data for named fields, with the "
        "invalid variants each field should reject. Use it when a test needs "
        "concrete values — an email, a card number, a postcode — instead of "
        "inventing them inline, and when a negative test needs to know what "
        "'wrong' looks like for a field. Every value is a pure function of the "
        "seed, so the same request returns the same data on every machine, "
        "which is what makes a failure reproducible. The data is safe by "
        "construction: reserved email domains that never resolve, telephone "
        "ranges regulators keep for fiction, and card numbers that pass a Luhn "
        "check but cannot route to any network."
    ),
    parameters={
        "properties": {
            "fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Field names as they appear in the form or API, e.g. ['customerEmail', 'card_number', 'postcode']. The kind of each is inferred from its name.",
            },
            "rows": {
                "type": "integer",
                "description": "How many distinct valid records to produce. Default 3.",
            },
            "locale": {
                "type": "string",
                "enum": ["en-US", "en-GB"],
                "description": "Shapes addresses, phone numbers and postcodes.",
            },
            "seed": {
                "type": "string",
                "description": "Reproducibility key. The same seed always yields the same data. Defaults to the field list.",
            },
            "include_invalid": {
                "type": "boolean",
                "description": "Also return invalid variants per field, each with the reason it is wrong. Default true.",
            },
        },
        "required": ["fields"],
    },
    category="authoring",
    scopes=["tests:read"],
    title="Generate reproducible test data",
    input_examples=[
        {"fields": ["customerEmail", "cardNumber", "postcode"], "locale": "en-GB"},
        {"fields": ["username", "password"], "rows": 5, "include_invalid": False},
    ],
)
def generate_test_data(args: dict, ctx: ToolContext) -> dict:
    from ..intelligence import testdata

    del ctx  # pure; nothing is read or written
    fields = [str(f).strip() for f in (args.get("fields") or []) if str(f).strip()]
    if not fields:
        return {"ok": False, "error": "Name at least one field."}
    rows = max(1, min(int(args.get("rows") or 3), 25))
    locale = args.get("locale") or "en-US"
    seed = (args.get("seed") or "/".join(fields)).strip()
    include_invalid = args.get("include_invalid", True) is not False

    records = testdata.dataset(fields, seed=seed, rows=rows, locale=locale)
    described = {
        name: testdata.describe_field(name, seed=f"{seed}/{name}", locale=locale)
        for name in fields
    }
    payload = {
        "ok": True,
        "seed": seed,
        "locale": locale,
        "fields": [
            {
                "name": name,
                "kind": described[name].kind,
                "invalid": (
                    [{"value": i.value, "why": i.why, "technique": i.technique}
                     for i in described[name].invalid]
                    if include_invalid else []
                ),
            }
            for name in fields
        ],
        "records": records,
        "guidance": (
            "Valid values go into happy-path fills; each invalid variant is one negative "
            "test with its reason as the expected error. Reuse the seed in the test so a "
            "failure reproduces with identical data."
        ),
    }
    data_file = {
        "filename": f"data/{_kebab(seed)[:40] or 'fixture'}.json",
        "language": "json",
        "code": json.dumps(
            {"seed": seed, "locale": locale, "records": records,
             "invalid": {f["name"]: f["invalid"] for f in payload["fields"]}},
            indent=2,
        ),
    }
    payload["_ui"] = {"pane": "test_matrix", "title": "Test data", "files": [data_file],
                      "unresolved": [], "requirement_ref": ""}
    return payload


# --------------------------------------------------------------------------- #
# review_test  (self-critique)
# --------------------------------------------------------------------------- #
from ..models import StepAction, TestCase  # noqa: E402

ASSERTION_ACTIONS = frozenset({
    StepAction.EXPECT_VISIBLE, StepAction.EXPECT_TEXT, StepAction.EXPECT_VALUE,
    StepAction.EXPECT_URL, StepAction.EXPECT_COUNT, StepAction.EXPECT_ATTRIBUTE,
    StepAction.EXPECT_SEMANTIC, StepAction.ASSERT_A11Y, StepAction.ASSERT_PERF,
})
ACTION_ACTIONS = frozenset({
    StepAction.CLICK, StepAction.DOUBLE_CLICK, StepAction.FILL, StepAction.TYPE,
    StepAction.PRESS, StepAction.SELECT, StepAction.CHECK, StepAction.UNCHECK,
    StepAction.UPLOAD, StepAction.API_REQUEST,
})


@registry.register(
    "review_test",
    description=(
        "Critique a test the way a senior reviewer would, before a human spends "
        "time on it. Use it on every test an agent generated, and on a recorded "
        "one, prior to filing it: it catches the failures that make a test worse "
        "than none — no assertion (a click-through that passes as long as nothing "
        "throws), an assertion that traces to no acceptance criterion, a step "
        "whose locator was guessed, an unlabelled TODO. It returns findings with "
        "a severity and the specific step each one is about, plus an overall "
        "verdict. The checks are deterministic, so the same test always gets the "
        "same review; a model, when configured, adds judgement on top but can "
        "never downgrade a structural finding."
    ),
    parameters={
        "properties": {
            "test_id_or_key": {
                "type": "string",
                "description": "The test to review, by key such as TST-T-0042 or by id.",
            },
            "proposal": {
                "type": "object",
                "description": "An un-persisted proposal to review instead of a stored test — the object returned by generate_playwright_script or a create_test draft.",
                "additionalProperties": True,
            },
        },
        "required": [],
    },
    category="tests",
    scopes=["tests:read"],
    title="Review a test for the failures that make it worse than none",
    input_examples=[{"test_id_or_key": "TST-T-0042"}],
)
def review_test(args: dict, ctx: ToolContext) -> dict:
    steps, meta = _load_reviewable(args, ctx)
    if steps is None:
        return meta  # an error dict

    findings: list[dict] = []

    assertions = [s for s in steps if s["action"] in ASSERTION_ACTIONS]
    actions = [s for s in steps if s["action"] in ACTION_ACTIONS]

    # 1. No assertion at all. The single most important check: a test that only
    #    acts proves the flow does not throw, not that it is correct.
    if not assertions:
        findings.append(_finding(
            "critical", None, "no_assertion",
            "This test performs actions but asserts nothing. It passes as long as "
            "no step errors, which proves the flow runs — not that it produces the "
            "right result. Add an assertion tied to an acceptance criterion.",
        ))

    # 2. An assertion that traces to no requirement. An untraceable assertion is
    #    a guess with good posture.
    if assertions and not meta.get("requirement_refs"):
        findings.append(_finding(
            "high", None, "untraceable",
            "The test asserts behaviour but traces to no requirement. Link it to "
            "the acceptance criterion it verifies, or the assertion is unaccountable.",
        ))

    # 3. Per-step problems.
    for step in steps:
        intent = (step.get("intent") or "").lower()
        expected = (step.get("expected") or "")
        ladder = (step.get("target") or {}).get("ladder") or []

        if "todo" in intent or "todo" in expected.lower() or "unimplemented" in expected.lower():
            findings.append(_finding(
                "high", step["index"], "unresolved_step",
                f"Step {step['index'] + 1} is an unresolved TODO — it will throw until "
                "filled in. Complete it against the real DOM or remove it.",
            ))

        # A guessed locator: a raw CSS/xpath rung with no role/testid fallback.
        if step["action"] in ACTION_ACTIONS and ladder:
            kinds = {r.get("kind") for r in ladder}
            if kinds <= {"css", "xpath"} and not (kinds & {"role", "testid", "label", "text"}):
                findings.append(_finding(
                    "medium", step["index"], "fragile_locator",
                    f"Step {step['index'] + 1} locates by CSS/XPath only, with no role "
                    "or test-id fallback. It will break at the next re-render. Record "
                    "the element to capture a healing ladder.",
                ))

        # An expectation step with an empty `expected` asserts nothing concrete.
        if step["action"] in ASSERTION_ACTIONS and not expected.strip() and not (step.get("value") or {}):
            findings.append(_finding(
                "medium", step["index"], "empty_assertion",
                f"Step {step['index'] + 1} is an assertion with nothing to assert. "
                "State the expected text, value or count.",
            ))

    # 4. Acts without ever asserting the outcome of the last action.
    if actions and assertions:
        last_action = max(s["index"] for s in actions)
        last_assert = max(s["index"] for s in assertions)
        if last_assert < last_action:
            findings.append(_finding(
                "low", last_action, "unverified_outcome",
                f"The final action (step {last_action + 1}) has no assertion after it. "
                "The test's last effect goes unverified.",
            ))

    severity_rank = {"critical": 3, "high": 2, "medium": 1, "low": 0}
    worst = max((severity_rank[f["severity"]] for f in findings), default=-1)
    verdict = (
        "blocked" if worst >= 3 else
        "needs_work" if worst >= 1 else
        "advisory" if findings else
        "sound"
    )

    return {
        "ok": True,
        "target": meta.get("label", "proposal"),
        "verdict": verdict,
        "assertion_count": len(assertions),
        "action_count": len(actions),
        "findings": findings,
        "guidance": _review_guidance(verdict, findings),
        "_ui": {
            "pane": "rca",  # findings read like telemetry: severity + a locus
            "title": f"Review · {meta.get('label', 'proposal')}",
            "review": {"verdict": verdict, "findings": findings},
        },
    }


def _load_reviewable(args: dict, ctx: ToolContext):
    """Normalise a stored test or an inline proposal into (steps, meta)."""
    proposal = args.get("proposal")
    if isinstance(proposal, dict) and proposal.get("steps"):
        steps = [_norm_step(i, s) for i, s in enumerate(proposal["steps"])]
        return steps, {"label": proposal.get("title", "proposal"),
                       "requirement_refs": proposal.get("requirement_refs") or []}

    identifier = (args.get("test_id_or_key") or "").strip()
    if not identifier:
        return None, {"ok": False, "error": "Give a test_id_or_key, or a proposal to review."}
    if ctx is None:
        return None, {"ok": False, "error": "Reviewing a stored test needs a project context."}

    case = ctx.db.execute(
        select(TestCase).where(
            TestCase.project_id == ctx.project_id,
            (TestCase.key == identifier) | (TestCase.id == identifier),
        )
    ).scalars().first()
    if case is None:
        return None, {"ok": False, "error": f"No test matching {identifier!r}."}

    steps = [_norm_step(s.index, {
        "action": s.action, "intent": s.intent, "expected": s.expected,
        "target": s.target, "value": s.value,
    }) for s in sorted(case.steps, key=lambda s: s.index)]
    return steps, {"label": case.key, "requirement_refs": case.requirement_refs or []}


def _norm_step(index: int, s: dict) -> dict:
    return {
        "index": s.get("index", index),
        "action": s.get("action", StepAction.NOTE),
        "intent": s.get("intent", ""),
        "expected": s.get("expected", ""),
        "target": s.get("target") or {},
        "value": s.get("value") or {},
    }


def _finding(severity: str, step_index: int | None, kind: str, message: str) -> dict:
    return {"severity": severity, "step": step_index, "kind": kind, "message": message}


def _review_guidance(verdict: str, findings: list[dict]) -> str:
    if verdict == "sound":
        return ("No structural problems found. The test asserts a traceable outcome with "
                "durable locators. A human should still confirm the assertion is the right one.")
    if verdict == "blocked":
        return ("This test should not be filed as-is: it has a critical gap (usually no "
                "assertion). Fix that before asking for review — a green run from it would "
                "prove nothing.")
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    summary = ", ".join(f"{n} {sev}" for sev, n in counts.items())
    return (f"{summary} finding(s). Address at least the high-severity ones before filing. "
            "Each names the step it is about.")


# --------------------------------------------------------------------------- #
# analyze_change_impact
# --------------------------------------------------------------------------- #
@registry.register(
    "analyze_change_impact",
    description=(
        "Given a set of changed files, report which tests the change puts at "
        "risk and, just as importantly, which it does not — so a pre-merge run "
        "can be short without leaving the change unguarded. Use it when a user "
        "names files they have changed, or a diff, and asks what to run. It ranks "
        "each test by how strongly its failure history correlates with the paths "
        "touched, always includes smoke and critical tests regardless, and states "
        "plainly what it is leaving out and why. Correlations are learned from "
        "past failures, so on a healthy suite that rarely fails the signal is weak "
        "and the recommendation is deliberately cautious — treat the result as a "
        "prioritisation, never as permission to skip everything it omits."
    ),
    parameters={
        "properties": {
            "changed_paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Repository-relative paths that changed, e.g. ['src/checkout/pay.ts', 'src/api/orders.ts'].",
            },
            "budget": {
                "type": "integer",
                "description": "Cap the recommended selection to this many tests. Omit for no cap.",
            },
        },
        "required": ["changed_paths"],
    },
    category="intelligence",
    scopes=["tests:read", "runs:read"],
    title="Analyze which tests a code change puts at risk",
    input_examples=[{"changed_paths": ["src/checkout/pay.ts"], "budget": 20}],
)
def analyze_change_impact(args: dict, ctx: ToolContext) -> dict:
    from ..intelligence.selection import select_for_change

    if ctx is None:
        return {"ok": False, "error": "Impact analysis needs a project context."}
    paths = [str(p).strip() for p in (args.get("changed_paths") or []) if str(p).strip()]
    if not paths:
        return {"ok": False, "error": "Name at least one changed path."}

    budget = args.get("budget")
    result = select_for_change(
        ctx.db, ctx.project_id, changed_paths=paths,
        budget=int(budget) if budget else None,
    )

    selected = result.get("selected", [])
    omitted = result.get("omitted", [])
    weak = not any(s.get("score", 0) >= 0.45 for s in selected)
    return {
        "ok": True,
        "changed_paths": paths,
        "selected_count": len(selected),
        "omitted_count": len(omitted),
        "selected": selected,
        "omitted": omitted[:20],
        "coverage_note": result.get("coverage_note", ""),
        "signal": "weak" if weak else "strong",
        "guidance": (
            
                "No test correlates strongly with these paths — the suite has little "
                "failure history against them. The selection is the safe-by-default set "
                "(smoke + critical). Do not read the omissions as safe to skip; there is "
                "simply no evidence either way yet."
                if weak else
                f"{len(selected)} test(s) correlate with the change and should run pre-merge. "
                f"{len(omitted)} were omitted as unrelated — the report says why for each."
            
        ),
    }


# --------------------------------------------------------------------------- #
# propose_plan  (plan mode)
# --------------------------------------------------------------------------- #
@registry.register(
    "propose_plan",
    description=(
        "Lay out the sequence of tool calls you intend to make for a multi-step "
        "task, for the user to see and confirm before any of it runs. Use it "
        "whenever a request needs three or more steps, or any step that writes — "
        "generating a suite of tests, importing a spec and filing everything, "
        "diagnosing then ticketing a failure. Each step names the tool, why it is "
        "there, and whether it changes state or needs approval. This does not "
        "execute anything: it returns the plan so the user can approve it, edit "
        "it, or stop. Calling it first on a big task is the difference between an "
        "agent that surprises people and one they trust."
    ),
    parameters={
        "properties": {
            "goal": {
                "type": "string",
                "description": "What the whole task is meant to achieve, in one sentence.",
            },
            "steps": {
                "type": "array",
                "description": "Ordered steps. Each is one intended tool call with the reason for it.",
                "items": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "description": "The tool this step will call."},
                        "why": {"type": "string", "description": "What this step contributes and what it depends on."},
                        "arguments_summary": {"type": "string", "description": "The key arguments, in words, for the human reading the plan."},
                        "arguments": {"type": "object", "description": "The actual arguments this step will run with. Include them so that confirming the plan runs exactly what was shown; omit only for a step whose inputs genuinely depend on an earlier step's output.", "additionalProperties": True},
                    },
                    "required": ["tool", "why"],
                },
            },
        },
        "required": ["goal", "steps"],
    },
    category="planning",
    scopes=[],
    title="Propose a plan for the user to confirm before acting",
    input_examples=[{
        "goal": "Cover REQ-014 with an executable, reviewed test",
        "steps": [
            {"tool": "query_requirements", "why": "read the acceptance criteria REQ-014 must satisfy", "arguments": {"feature": "checkout", "ref": "REQ-014"}},
            {"tool": "generate_bdd_scenarios", "why": "turn each criterion into a Gherkin scenario", "arguments": {"requirement_ref": "REQ-014"}},
        ],
    }],
)
def propose_plan(args: dict, ctx: ToolContext) -> dict:
    del ctx
    goal = (args.get("goal") or "").strip()
    raw_steps = args.get("steps") or []
    if not goal or not raw_steps:
        return {"ok": False, "error": "A plan needs a goal and at least one step."}

    steps: list[dict] = []
    writes_state = False
    for index, step in enumerate(raw_steps, 1):
        tool_name = (step.get("tool") or "").strip()
        tool = registry.get(tool_name)
        # Annotate each step from the registry's own metadata rather than trusting
        # the model's claim about what the tool does. A step that says it only
        # reads, while calling a gated tool, is corrected here.
        if tool is None:
            effect = "unknown"
        elif tool.requires_confirmation:
            effect = "needs approval"
            writes_state = True
        elif tool.read_only:
            effect = "read-only"
        else:
            effect = "writes"
            writes_state = True
        step_args = step.get("arguments") if isinstance(step.get("arguments"), dict) else {}
        steps.append({
            "index": index,
            "tool": tool_name,
            "why": (step.get("why") or "").strip(),
            "arguments_summary": (step.get("arguments_summary") or "").strip(),
            "arguments": step_args,
            # A write step with no concrete arguments cannot be executed verbatim
            # on confirmation; flag it so the plan is honest about what "proceed"
            # can actually run unattended.
            "executable": bool(step_args) or tool is not None and tool.read_only,
            "effect": effect,
            "known_tool": tool is not None,
        })

    unknown = [s["tool"] for s in steps if not s["known_tool"]]
    return {
        "ok": True,
        "goal": goal,
        "step_count": len(steps),
        "writes_state": writes_state,
        "steps": steps,
        "unknown_tools": unknown,
        "guidance": (
            (f"Warning: {', '.join(unknown)} is not a registered tool — revise the plan. "
             if unknown else "")
            + ("This plan changes state; the write steps will each still require approval "
               "when they run, so confirming the plan is not the same as approving those. "
               if writes_state else "This plan only reads; it can run without side effects. ")
            + "Present it to the user and wait for them to confirm, edit, or stop before "
              "executing the first step."
        ),
        "_ui": {
            "pane": "test_matrix",
            "title": f"Plan · {goal[:60]}",
            "plan": {"goal": goal, "steps": steps, "writes_state": writes_state},
        },
    }


# --------------------------------------------------------------------------- #
# escalate_to_human
# --------------------------------------------------------------------------- #
@registry.register(
    "escalate_to_human",
    description=(
        "Stop and hand the task to a person when you cannot complete it "
        "correctly on your own. Use it — do not guess past the problem — when a "
        "requirement is ambiguous and the answer changes the test, when a step "
        "needs a real locator you have not seen, when a decision is the user's to "
        "make (which environment, whether to overwrite), or when a tool keeps "
        "failing for a reason you cannot fix. State plainly what is blocking you "
        "and the specific question whose answer would unblock it. This records a "
        "notification for the user and ends your turn cleanly; it is the "
        "responsible end to a task, not a failure. Escalating with a precise "
        "question is always better than producing a plausible-but-wrong result."
    ),
    parameters={
        "properties": {
            "blocker": {
                "type": "string",
                "description": "What is preventing completion, in one or two sentences.",
            },
            "question": {
                "type": "string",
                "description": "The specific question whose answer would unblock you. Make it answerable in a sentence.",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "If the decision is a choice, the options — so the user can pick rather than compose an answer.",
            },
            "severity": {
                "type": "string",
                "enum": ["blocker", "question", "fyi"],
                "description": "blocker: cannot proceed. question: proceeding needs a decision. fyi: proceeding but flagging a risk.",
            },
        },
        "required": ["blocker", "question"],
    },
    category="planning",
    scopes=[],
    title="Hand the task to a human with a specific question",
    input_examples=[
        {"blocker": "REQ-014 does not state the maximum card length for all schemes.",
         "question": "Which card schemes must the checkout accept, and what is the length range for each?",
         "severity": "blocker"},
        {"blocker": "The 'Confirm' button has no test-id and I have not seen the DOM.",
         "question": "Should I record the checkout flow to capture the real locator?",
         "options": ["Record it now", "I'll paste the selector"], "severity": "question"},
    ],
)
def escalate_to_human(args: dict, ctx: ToolContext) -> dict:
    from ..core.events import Ev, Event, bus
    from ..models import Notification

    blocker = (args.get("blocker") or "").strip()
    question = (args.get("question") or "").strip()
    if not blocker or not question:
        return {"ok": False, "error": "Escalation needs both a blocker and a specific question."}

    options = [str(o).strip() for o in (args.get("options") or []) if str(o).strip()]
    severity = args.get("severity") or "question"
    severity_map = {"blocker": "warn", "question": "info", "fyi": "info"}

    if ctx is not None and getattr(ctx, "db", None) is not None:
        note = Notification(
            project_id=ctx.project_id,
            kind="escalation",
            title=f"The agent needs you: {question[:80]}",
            body=blocker + ("\n\nOptions: " + " · ".join(options) if options else ""),
            severity=severity_map.get(severity, "info"),
        )
        ctx.db.add(note)
        ctx.db.flush()
        # Surface it live too, so an open dock shows the escalation immediately
        # rather than only on the next poll.
        bus.publish_soon(Event(
            type=Ev.NOTIFICATION, project_id=ctx.project_id,
            payload={"kind": "escalation", "severity": severity, "title": note.title, "question": question},
        ))

    return {
        "ok": True,
        "escalated": True,
        "severity": severity,
        "blocker": blocker,
        "question": question,
        "options": options,
        # The agent should stop after this. The message is written so that if the
        # model does add a closing line, it reinforces the hand-off rather than
        # papering over it with a guess.
        "guidance": (
            "You have escalated. Do not now attempt the task anyway or invent an "
            "answer to your own question — end your turn by putting the question to "
            "the user, plainly, and wait for their reply."
        ),
        "_ui": {
            "pane": "rca",
            "title": "Escalation",
            "review": {
                "verdict": "blocked" if severity == "blocker" else "needs_work",
                "findings": [
                    {"severity": "high" if severity == "blocker" else "medium",
                     "step": None, "kind": "escalation",
                     "message": f"{blocker}  →  {question}"
                                + (f"  [{' / '.join(options)}]" if options else "")},
                ],
            },
        },
    }


# --------------------------------------------------------------------------- #
# judge_test_against_criteria
# --------------------------------------------------------------------------- #
#: Words that carry no distinguishing meaning when matching a criterion to a
#: step. Matching on these produces false coverage — every step "covers" every
#: criterion because they all contain "the" and "user".
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "be", "been", "being", "to", "of", "in", "on",
    "at", "and", "or", "but", "with", "for", "that", "this", "it", "its", "as",
    "user", "users", "system", "page", "should", "must", "shall", "will", "can",
    "when", "then", "given", "if", "so", "not", "no", "yes", "does", "do",
    "shows", "show", "sees", "see", "displayed", "display", "field", "button",
})


@registry.register(
    "judge_test_against_criteria",
    description=(
        "Check whether a test actually verifies each of a requirement's "
        "acceptance criteria — the gap review_test cannot see. A test can be "
        "structurally sound (it asserts something, its locators are durable) and "
        "still silently miss a criterion, which is the failure that lets a "
        "green run hide an untested obligation. This maps every criterion to the "
        "assertion step(s) that address it and reports the ones with no coverage. "
        "The matching is deterministic keyword overlap, so it is conservative: it "
        "will flag a criterion as uncovered rather than claim coverage it cannot "
        "see, and a human (or a model, when configured) confirms the borderline "
        "cases. Call it after generating or before approving a test that claims "
        "to cover a requirement."
    ),
    parameters={
        "properties": {
            "test_id_or_key": {
                "type": "string",
                "description": "The test to judge, by key such as TST-T-0042 or by id.",
            },
            "proposal": {
                "type": "object",
                "description": "An un-persisted test proposal to judge instead of a stored one.",
                "additionalProperties": True,
            },
            "requirement_ref": {
                "type": "string",
                "description": "The requirement whose criteria to judge against, e.g. REQ-014. Read from the project.",
            },
            "criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Criteria stated directly, when the requirement is not ingested. Overrides requirement_ref.",
            },
        },
        "required": [],
    },
    category="tests",
    scopes=["tests:read", "requirements:read"],
    title="Judge whether a test covers each acceptance criterion",
    input_examples=[{"test_id_or_key": "TST-T-0042", "requirement_ref": "REQ-014"}],
)
def judge_test_against_criteria(args: dict, ctx: ToolContext) -> dict:
    steps, meta = _load_reviewable(args, ctx)
    if steps is None:
        return meta  # error dict

    criteria, source_error = _resolve_criteria(args, ctx)
    if source_error:
        return source_error

    # Only assertion steps can *verify* a criterion. An action step performs
    # something; it does not check an outcome.
    assertions = [s for s in steps if s["action"] in ASSERTION_ACTIONS]
    assertion_texts = [
        _searchable(f"{s.get('intent', '')} {s.get('expected', '')} "
                    f"{(s.get('value') or {}).get('text', '')}")
        for s in assertions
    ]

    coverage: list[dict] = []
    for criterion in criteria:
        keywords = _keywords(criterion)
        matches = []
        for a_index, atext in enumerate(assertion_texts):
            overlap = keywords & atext
            # Two shared meaningful words is the threshold: one is coincidence
            # ("order" appears everywhere), two is a real topical match.
            if len(overlap) >= 2 or (keywords and keywords <= atext):
                matches.append({"step": assertions[a_index]["index"],
                                 "matched_on": sorted(overlap)})
        coverage.append({
            "criterion": criterion,
            "covered": bool(matches),
            "assertions": matches,
        })

    covered = [c for c in coverage if c["covered"]]
    uncovered = [c for c in coverage if not c["covered"]]

    verdict = (
        "no_criteria" if not criteria else
        "uncovered" if uncovered else
        "covered"
    )
    return {
        "ok": True,
        "target": meta.get("label", "proposal"),
        "criteria_count": len(criteria),
        "covered_count": len(covered),
        "uncovered_count": len(uncovered),
        "assertion_count": len(assertions),
        "verdict": verdict,
        "coverage": coverage,
        "guidance": _judge_guidance(verdict, uncovered, len(assertions)),
        "_ui": {
            "pane": "rca",
            "title": f"Criteria coverage · {meta.get('label', 'proposal')}",
            "review": {
                "verdict": "sound" if verdict == "covered" else
                           "needs_work" if verdict == "uncovered" else "advisory",
                "findings": [
                    {"severity": "high", "step": None, "kind": "uncovered_criterion",
                     "message": f"No assertion covers: “{c['criterion'][:90]}”"}
                    for c in uncovered
                ] or ([{"severity": "low", "step": None, "kind": "covered",
                        "message": f"All {len(criteria)} criteria have a matching assertion."}]
                      if verdict == "covered" else []),
            },
        },
    }


def _resolve_criteria(args: dict, ctx: ToolContext) -> tuple[list[str], dict | None]:
    """Criteria from the argument, or read from an ingested requirement."""
    direct = [c.strip() for c in (args.get("criteria") or []) if str(c).strip()]
    if direct:
        return direct, None

    ref = (args.get("requirement_ref") or "").strip().upper()
    # Fall back to the test's own linked requirements when none is named.
    if not ref:
        proposal = args.get("proposal") or {}
        refs = proposal.get("requirement_refs") if isinstance(proposal, dict) else None
        if not refs and ctx is not None and args.get("test_id_or_key"):
            case = ctx.db.execute(
                select(TestCase).where(
                    TestCase.project_id == ctx.project_id,
                    (TestCase.key == args["test_id_or_key"]) | (TestCase.id == args["test_id_or_key"]),
                )
            ).scalars().first()
            refs = case.requirement_refs if case else None
        ref = (refs[0].upper() if refs else "")

    if not ref:
        return [], {
            "ok": False,
            "error": (
                "No criteria to judge against. Pass requirement_ref, pass criteria directly, "
                "or judge a test that links a requirement. Do not judge against invented criteria."
            ),
        }
    if ctx is None:
        return [], {"ok": False, "error": "Reading a requirement's criteria needs a project context."}

    item = ctx.db.execute(
        select(RequirementItem).where(
            RequirementItem.project_id == ctx.project_id, RequirementItem.ref == ref
        )
    ).scalars().first()
    if item is None:
        return [], {"ok": False, "error": f"No requirement {ref} in this project."}
    criteria = list(item.acceptance_criteria or [])
    if not criteria:
        return [], {
            "ok": False,
            "error": f"{ref} has no acceptance criteria recorded — there is nothing to judge coverage against.",
        }
    return criteria, None


def _keywords(text: str) -> frozenset[str]:
    return _searchable(text)


def _searchable(text: str) -> frozenset[str]:
    """Meaningful lowercase word stems of a piece of text.

    Numbers are kept (a boundary like "8" or "64" is exactly what distinguishes
    one criterion from another); stopwords and sub-3-character tokens are dropped.
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    return frozenset(
        _stem(w) for w in words
        if w not in _STOPWORDS and (len(w) >= 3 or w.isdigit())
    )


def _stem(word: str) -> str:
    """A crude suffix strip so "accepts"/"accepted"/"accepting" match."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _judge_guidance(verdict: str, uncovered: list[dict], assertions: int) -> str:
    if verdict == "covered":
        return (
            "Every acceptance criterion has at least one assertion that appears to cover it. "
            "The match is by keyword overlap, so confirm the borderline ones actually verify "
            "the criterion rather than merely mentioning the same words."
        )
    if verdict == "no_criteria":
        return "There were no criteria to judge against."
    refs = "; ".join(f"“{c['criterion'][:70]}”" for c in uncovered[:3])
    tail = f" (+{len(uncovered) - 3} more)" if len(uncovered) > 3 else ""
    lead = (
        "This test asserts nothing, so it covers no criteria at all — see review_test."
        if assertions == 0 else
        f"{len(uncovered)} criterion(s) have no matching assertion: {refs}{tail}."
    )
    return lead + " Add an assertion for each, or say why it is out of scope, before filing."


# --------------------------------------------------------------------------- #
# check_run_health  (flaky-aware pre-run advice)
# --------------------------------------------------------------------------- #
@registry.register(
    "check_run_health",
    description=(
        "Before running or re-running tests, report how much of the selection is "
        "known-flaky — tests that change verdict without the code changing. Use it "
        "ahead of run_tests, and especially before a re-run: blindly re-running a "
        "suite that is a third flaky wastes time and produces a red result that "
        "proves nothing about the change. It resolves the same selection run_tests "
        "would, scores each test's flakiness from its run history, and recommends "
        "whether to run as-is, quarantine the worst offenders first, or narrow the "
        "selection. The scores are measured, not guessed: a test with little "
        "history scores low for lack of evidence, so this never invents flakiness "
        "it cannot see."
    ),
    parameters={
        "properties": {
            "suite": {"type": "string", "description": "Named suite to check, mirroring run_tests."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Only tests carrying all of these tags."},
            "keys": {"type": "array", "items": {"type": "string"}, "description": "Explicit test keys, e.g. ['TST-T-0042']."},
            "rerun_failed_from": {"type": "string", "description": "A run id, to check the set that a 'rerun failed' would execute."},
            "flaky_threshold": {"type": "number", "description": "Flake score at or above which a test is called flaky. Default 0.3."},
        },
        "required": [],
    },
    category="intelligence",
    scopes=["tests:read", "runs:read"],
    title="Check how flaky a run's selection is before running it",
    input_examples=[{"suite": "regression"}, {"rerun_failed_from": "run_abc123"}],
)
def check_run_health(args: dict, ctx: ToolContext) -> dict:
    from ..ai.toolset import _selection_from_args
    from ..engine.plan import select_tests
    from ..intelligence.flaky import assess
    from ..models import TestStat

    if ctx is None:
        return {"ok": False, "error": "Checking run health needs a project context."}

    selection = _selection_from_args(ctx, args)
    cases = select_tests(ctx.db, ctx.project_id, selection)
    if not cases:
        return {
            "ok": True, "total": 0, "flaky_count": 0, "recommendation": "empty",
            "flaky": [], "quarantined_in_selection": [],
            "guidance": (
                "The selection resolves to no runnable tests. Check the suite name, tags or "
                "keys — there is nothing to run, flaky or otherwise."
            ),
        }

    threshold = float(args.get("flaky_threshold") or 0.3)
    stats = {
        s.test_case_id: s
        for s in ctx.db.execute(
            select(TestStat).where(TestStat.project_id == ctx.project_id)
        ).scalars()
    }

    flaky: list[dict] = []
    quarantined: list[dict] = []
    for case in cases:
        if getattr(case, "quarantined", False):
            quarantined.append({"key": case.key, "title": case.title})
            continue
        stat = stats.get(case.id)
        if stat is None:
            continue
        verdict = assess(stat)
        if verdict.score >= threshold:
            flaky.append({
                "key": case.key,
                "title": case.title,
                "flake_score": round(verdict.score, 3),
                "confidence": round(getattr(verdict, "confidence", 0.0), 3),
                "reasons": list(getattr(verdict, "reasons", []) or [])[:3],
            })

    flaky.sort(key=lambda f: f["flake_score"], reverse=True)
    runnable = len(cases) - len(quarantined)
    flaky_share = (len(flaky) / runnable) if runnable else 0.0

    # The recommendation is a rule, so it is consistent and needs no model. The
    # thresholds are deliberately conservative: quarantine only when flakiness
    # dominates, because quarantining a test that is merely occasionally flaky
    # loses real coverage.
    if runnable == 0:
        recommendation = "all_quarantined"
    elif flaky_share >= 0.5:
        recommendation = "quarantine_first"
    elif flaky_share >= 0.2:
        recommendation = "run_but_expect_noise"
    else:
        recommendation = "run"

    return {
        "ok": True,
        "selection_origin": selection.get("origin", "explicit"),
        "total": len(cases),
        "runnable": runnable,
        "flaky_count": len(flaky),
        "flaky_share": round(flaky_share, 2),
        "quarantined_in_selection": quarantined,
        "flaky": flaky[:20],
        "recommendation": recommendation,
        "guidance": _health_guidance(recommendation, flaky, quarantined, runnable),
        "_ui": {
            "pane": "rca",
            "title": "Run health check",
            "review": {
                "verdict": {
                    "run": "sound", "run_but_expect_noise": "advisory",
                    "quarantine_first": "needs_work", "all_quarantined": "blocked",
                    "empty": "advisory",
                }.get(recommendation, "advisory"),
                "findings": [
                    {"severity": "medium", "step": None, "kind": "flaky_test",
                     "message": f"{f['key']} — flake score {f['flake_score']} "
                                + (f"({', '.join(f['reasons'])})" if f["reasons"] else "")}
                    for f in flaky[:8]
                ],
            },
        },
    }


def _health_guidance(recommendation: str, flaky: list[dict], quarantined: list[dict], runnable: int) -> str:
    worst = ", ".join(f["key"] for f in flaky[:3])
    q_note = (f" {len(quarantined)} already-quarantined test(s) will be skipped." if quarantined else "")
    if recommendation == "all_quarantined":
        return "Every test in this selection is quarantined — running it would execute nothing." + q_note
    if recommendation == "quarantine_first":
        return (
            f"{len(flaky)} of {runnable} runnable tests are flaky ({worst}) — over half the selection. "
            "Re-running this as-is will likely go red for reasons unrelated to the code. Quarantine "
            "the worst offenders or narrow the selection before running." + q_note
        )
    if recommendation == "run_but_expect_noise":
        return (
            f"{len(flaky)} of {runnable} tests are flaky ({worst}). Safe to run, but treat a failure "
            "in those as suspect until confirmed — re-check them rather than trusting one red result." + q_note
        )
    if recommendation == "empty":
        return "Nothing to run."
    return (f"The selection is clean — no test above the flake threshold across {runnable} runnable "
            f"tests. Good to run." + q_note)
