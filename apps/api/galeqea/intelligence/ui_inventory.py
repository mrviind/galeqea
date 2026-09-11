"""Vision "UI inventory" for wireframes and screenshots (WO#9-A, optional/model).

A picture of a screen carries requirements the prose never wrote down: a field, a
validation, a state. This turns an image into a structured inventory of controls,
folds it into the requirement set as its own chunk, and cross-checks it against the
text so a reviewer sees the gaps in *both* directions:

* the UI has a field the PRD never mentions (a hidden requirement), and
* the PRD names a rule with no control to exercise it (a missing screen).

The model step is optional. Everything below the extractor, the chunk rendering and
the gap analysis, is deterministic and runs with no model, so it is fully testable.
"""

from __future__ import annotations

import json
import re

from ..ai.providers.base import LLMProvider, Message, NoAIModeError, ProviderError, Role

_SYSTEM = (
    "You inventory the UI in a screenshot or wireframe for a QA engineer. Report only "
    "what is visibly present. Do not invent fields. Be terse and literal."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "screen": {"type": "string"},
        "controls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "type": {"type": "string"},   # text|email|password|select|checkbox|button|radio|...
                    "validation": {"type": "string"},
                    "states": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["label", "type"],
                "additionalProperties": False,
            },
        },
        "nav_targets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["screen", "controls"],
    "additionalProperties": False,
}


async def build_inventory(
    provider: LLMProvider | None, *, image_b64: str, screen_hint: str = ""
) -> dict:
    """Extract a structured UI inventory from one image. ``{}`` without a model."""
    if provider is None:
        return {}
    prompt = "Inventory every visible control on this screen."
    if screen_hint:
        prompt += f" It is described elsewhere as: {screen_hint!r}."
    try:
        result = await provider.complete(
            [Message(role=Role.USER, content=prompt, images=[image_b64])],
            system=_SYSTEM, max_tokens=1200, temperature=0.1, response_format=_SCHEMA,
        )
    except (NoAIModeError, ProviderError):
        return {}
    return _parse(result.text)


def _parse(text: str) -> dict:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.split("```", 2)[1].removeprefix("json").strip().rsplit("```", 1)[0]
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict) or "controls" not in data:
        return {}
    controls = [c for c in data.get("controls", []) if isinstance(c, dict) and c.get("label")]
    return {"screen": str(data.get("screen", "")).strip(),
            "controls": controls,
            "nav_targets": [str(n) for n in data.get("nav_targets", []) if str(n).strip()]}


def inventory_chunk(inventory: dict) -> str:
    """Render an inventory as a requirements chunk (Markdown), so it embeds and
    traces like any other requirement item. Deterministic."""
    if not inventory or not inventory.get("controls"):
        return ""
    screen = inventory.get("screen") or "Screen"
    lines = [f"## UI inventory: {screen}", ""]
    for c in inventory["controls"]:
        bits = [f"**{c['label']}**", f"({c.get('type', 'control')})"]
        if c.get("validation"):
            bits.append(f"must {c['validation']}")
        if c.get("states"):
            bits.append(f"[states: {', '.join(c['states'])}]")
        lines.append(f"- {' '.join(bits)}")
    if inventory.get("nav_targets"):
        lines += ["", f"Navigates to: {', '.join(inventory['nav_targets'])}."]
    return "\n".join(lines)


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def inventory_gaps(inventory: dict, requirement_texts: list[str]) -> list[dict]:
    """Cross-check the UI inventory against the text rules, both directions.

    A control whose label words appear in no requirement is a *hidden* requirement;
    a requirement mentioning an input/field/button with no matching control is a
    *missing screen*. Deterministic: the value is in surfacing the mismatch, not in
    a model's opinion about it.
    """
    findings: list[dict] = []
    corpus = _tokens(" ".join(requirement_texts))
    for c in inventory.get("controls", []):
        label_words = _tokens(c.get("label", ""))
        if label_words and not (label_words & corpus):
            findings.append({
                "kind": "ui_not_in_text",
                "control": c.get("label"),
                "message": f"the UI has a {c.get('type', 'control')} \"{c.get('label')}\" "
                           "that no requirement mentions",
            })
    inv_words: set[str] = set()
    for c in inventory.get("controls", []):
        inv_words |= _tokens(c.get("label", ""))
    _NOUNS = ("field", "button", "input", "checkbox", "dropdown", "menu", "toggle", "link")
    for text in requirement_texts:
        low = text.lower()
        if any(n in low for n in _NOUNS):
            named = _tokens(text) - {"the", "a", "an", "field", "button", "input"}
            if inv_words and not (named & inv_words):
                findings.append({
                    "kind": "text_not_in_ui",
                    "requirement": text[:120],
                    "message": "a requirement names a control the UI inventory does not show",
                })
    # Keep the report tight: at most a handful per direction.
    ui = [f for f in findings if f["kind"] == "ui_not_in_text"][:8]
    txt = [f for f in findings if f["kind"] == "text_not_in_ui"][:5]
    return ui + txt
