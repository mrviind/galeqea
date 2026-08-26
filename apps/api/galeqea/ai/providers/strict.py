"""Make a tool schema eligible for Anthropic's strict tool use.

`strict: true` constrains the model's sampling to schema-valid output, which
eliminates the whole class of "passengers": "two" failures. It comes with a
supported JSON Schema subset, and a schema outside that subset is rejected by
the API rather than degraded — so the conversion has to be done here, once, and
has to say honestly when a schema cannot be made strict at all.

The rules, from the structured-outputs documentation:

* every object must carry ``additionalProperties: false``;
* ``minimum`` / ``maximum`` / ``multipleOf`` / ``minLength`` / ``maxLength`` /
  ``pattern`` are unsupported — the SDK's own approach is to drop them from the
  wire schema, fold them into the description, and validate locally afterwards,
  which is what this does;
* ``minItems`` may only be 0 or 1;
* string ``format`` is limited to a fixed list;
* ``$ref`` must be local; recursion is unsupported.

A schema that declares ``additionalProperties: true`` is a *free-form* object by
contract — a locator ladder, a step's ``value`` bag — and forcing it closed would
change what the tool accepts. Those tools stay non-strict, and the registry's
own argument validation covers them instead.
"""

from __future__ import annotations

import copy
from typing import Any

#: Constraint keywords strict mode rejects. Each is folded into the property's
#: description so the model still sees the rule, and enforced by the registry's
#: local validation so a violation is still caught.
FOLDED_CONSTRAINTS: dict[str, str] = {
    "minimum": "at least {}",
    "maximum": "at most {}",
    "exclusiveMinimum": "greater than {}",
    "exclusiveMaximum": "less than {}",
    "multipleOf": "a multiple of {}",
    "minLength": "at least {} characters",
    "maxLength": "at most {} characters",
    "pattern": "matching the pattern {}",
}

SUPPORTED_FORMATS = {
    "date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid",
}


class NotStrictable(ValueError):
    """The schema's contract cannot be expressed in the strict subset."""


def to_strict(schema: dict) -> dict:
    """Return a strict-eligible copy of ``schema``, or raise :class:`NotStrictable`.

    The input is never mutated: the registry keeps the original for local
    validation, and only the wire copy is simplified.
    """
    return _convert(copy.deepcopy(schema), path="$", depth=0)


def is_strictable(schema: dict) -> bool:
    try:
        to_strict(schema)
    except NotStrictable:
        return False
    return True


def _convert(node: Any, *, path: str, depth: int) -> Any:
    if depth > 24:
        raise NotStrictable(f"{path}: nesting too deep to be a non-recursive schema")
    if isinstance(node, list):
        return [_convert(item, path=f"{path}[{i}]", depth=depth + 1) for i, item in enumerate(node)]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str) and not ref.startswith("#"):
        raise NotStrictable(f"{path}: external $ref {ref!r} is not supported")

    node_type = node.get("type")

    if node_type == "object" or "properties" in node:
        extra = node.get("additionalProperties")
        if extra is True or isinstance(extra, dict):
            # An open object is part of the tool's contract; closing it would
            # silently reject arguments the tool documents as accepted.
            raise NotStrictable(f"{path}: free-form object (additionalProperties) cannot be strict")
        node["additionalProperties"] = False
        properties = node.get("properties") or {}
        for name, sub in properties.items():
            properties[name] = _convert(sub, path=f"{path}.{name}", depth=depth + 1)

    if node_type == "array":
        min_items = node.get("minItems")
        if isinstance(min_items, int) and min_items > 1:
            _fold(node, f"at least {min_items} items")
            node["minItems"] = 1
        if "items" in node:
            node["items"] = _convert(node["items"], path=f"{path}[]", depth=depth + 1)

    if node_type == "string":
        fmt = node.get("format")
        if fmt and fmt not in SUPPORTED_FORMATS:
            _fold(node, f"format: {fmt}")
            del node["format"]

    for keyword, phrase in FOLDED_CONSTRAINTS.items():
        if keyword in node:
            _fold(node, phrase.format(node.pop(keyword)))

    for combinator in ("anyOf", "oneOf", "allOf"):
        if combinator in node:
            node[combinator] = [
                _convert(item, path=f"{path}.{combinator}[{i}]", depth=depth + 1)
                for i, item in enumerate(node[combinator])
            ]

    for key in ("$defs", "definitions"):
        if key in node:
            node[key] = {
                name: _convert(sub, path=f"{path}.{key}.{name}", depth=depth + 1)
                for name, sub in node[key].items()
            }

    return node


def _fold(node: dict, rule: str) -> None:
    """Move a constraint the wire cannot carry into the description."""
    existing = (node.get("description") or "").rstrip()
    suffix = f"Must be {rule}."
    node["description"] = f"{existing} {suffix}".strip() if existing else suffix
