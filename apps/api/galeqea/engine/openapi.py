"""OpenAPI specifications into executable API tests.

An OpenAPI document already states most of what a test suite needs to know: the
operations, which parameters are required, their types and bounds, which status
codes are legitimate and what the response body must look like. Turning that
into tests needs no model at all, which is why this runs in the default No-AI
install.

What is generated per operation:

* **contract** - a valid call, asserting the declared success status *and* that
  the response body conforms to the declared schema. The second half is the part
  hand-written API suites almost always skip, and it is the half that catches a
  backend quietly renaming a field.
* **required-missing** - one call per required parameter, omitted, expecting a
  4xx. A surprising number of APIs return 200 with a null.
* **type-violation** and **boundary** - the schema's own ``minimum``/``maximum``/
  ``minLength``/``maxLength``/``enum`` read as test design input, so the values
  come from boundary value analysis rather than from guessing.
* **unauthenticated** - when the operation declares security, a call with the
  credentials stripped, expecting 401 or 403. Never 200.
* **injection** - probe strings that must be stored and escaped rather than
  executed or reflected. Asserted as "must not 500 and must not echo", which is
  true regardless of what the endpoint does with the value.

Two safety rules are enforced while parsing. Remote ``$ref`` targets are refused
rather than fetched: a specification is untrusted input, and dereferencing a URL
inside it would let a document choose what this process connects to. Reference
cycles are bounded, so a self-referential schema cannot hang the parser.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

from ..intelligence import testdata
from ..models import StepAction, TestCategory

MAX_REF_DEPTH = 8
SUCCESS = re.compile(r"^2\d\d$")
CLIENT_ERROR = re.compile(r"^4\d\d$")


class SpecError(ValueError):
    """The document is not a usable OpenAPI specification."""


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class Parameter:
    name: str
    location: str                      # path | query | header | cookie
    required: bool
    schema: dict = field(default_factory=dict)
    description: str = ""

    @property
    def constraints(self) -> dict:
        keep = ("minLength", "maxLength", "minimum", "maximum", "enum", "pattern")
        out = {k: self.schema[k] for k in keep if k in self.schema}
        if not self.required:
            out["optional"] = True
        return out

    @property
    def type_hint(self) -> str:
        return str(self.schema.get("format") or self.schema.get("type") or "")


@dataclass(slots=True)
class Operation:
    method: str
    path: str
    operation_id: str
    summary: str
    description: str
    parameters: list[Parameter]
    body_schema: dict | None
    body_required: bool
    body_content_type: str
    responses: dict
    secured: bool
    tags: list[str]

    @property
    def label(self) -> str:
        return f"{self.method} {self.path}"

    def success_status(self) -> int:
        codes = sorted(int(c) for c in self.responses if SUCCESS.match(str(c)))
        if codes:
            return codes[0]
        if "default" in self.responses:
            return 200
        return 201 if self.method == "POST" else 200

    def error_statuses(self) -> list[int]:
        declared = sorted(int(c) for c in self.responses if CLIENT_ERROR.match(str(c)))
        # 422 is what most schema-validating frameworks return and specs rarely
        # declare it; accepting both keeps the test honest instead of brittle.
        return declared or [400, 422]

    def renders_html(self) -> bool:
        """Does any declared response return HTML?

        This decides whether reflecting an input back is a defect. In an HTML
        response it is a cross-site-scripting vector; in a JSON response echoing
        the stored value verbatim is exactly correct, so asserting against it
        would fail a well-behaved API.
        """
        for response in self.responses.values():
            if not isinstance(response, dict):
                continue
            for content_type in (response.get("content") or {}):
                if "html" in content_type:
                    return True
        return False

    def success_schema(self) -> dict | None:
        for code, response in self.responses.items():
            if not SUCCESS.match(str(code)):
                continue
            for content_type, media in (response.get("content") or {}).items():
                if "json" in content_type and media.get("schema"):
                    return media["schema"]
        return None


@dataclass(slots=True)
class Spec:
    title: str
    version: str
    servers: list[str]
    operations: list[Operation]
    issues: list[str] = field(default_factory=list)


def load(text: str | bytes) -> dict:
    """Parse a specification from JSON or YAML."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SpecError(f"not valid JSON: {exc}") from exc
    try:
        import yaml
    except ImportError as exc:                                  # pragma: no cover
        raise SpecError("this document is not JSON and PyYAML is not installed") from exc
    try:
        # safe_load, never load: a specification is untrusted input and the full
        # YAML loader can construct arbitrary Python objects.
        parsed = yaml.safe_load(stripped)
    except yaml.YAMLError as exc:
        raise SpecError(f"not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SpecError("the document does not describe an object")
    return parsed


class _Resolver:
    """Local ``$ref`` resolution with cycle and remote-reference protection."""

    def __init__(self, root: dict) -> None:
        self.root = root
        self.refused: list[str] = []

    def resolve(self, node, depth: int = 0):
        if depth > MAX_REF_DEPTH or not isinstance(node, dict):
            return node if not isinstance(node, list) else [self.resolve(n, depth + 1) for n in node]
        ref = node.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#/"):
                # Refusing rather than fetching: the document decides the URL.
                self.refused.append(ref)
                return {"description": f"unresolved external reference {ref}"}
            target = self.root
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(target, dict) or part not in target:
                    self.refused.append(ref)
                    return {"description": f"broken reference {ref}"}
                target = target[part]
            merged = self.resolve(target, depth + 1)
            rest = {k: v for k, v in node.items() if k != "$ref"}
            return {**merged, **rest} if isinstance(merged, dict) else merged
        return {k: self.resolve(v, depth + 1) for k, v in node.items()}


def parse(document: dict) -> Spec:
    version = str(document.get("openapi") or document.get("swagger") or "")
    if not version:
        raise SpecError("missing an 'openapi' or 'swagger' version field")
    if version.startswith("2"):
        raise SpecError(
            "Swagger 2.0 is not supported; convert the document to OpenAPI 3.x first"
        )
    paths = document.get("paths")
    if not isinstance(paths, dict) or not paths:
        raise SpecError("the document declares no paths")

    resolver = _Resolver(document)
    info = document.get("info") or {}
    servers = [s.get("url", "") for s in (document.get("servers") or []) if isinstance(s, dict)]
    global_security = bool(document.get("security"))

    operations: list[Operation] = []
    issues: list[str] = []

    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        shared = [_parameter(resolver.resolve(p)) for p in (item.get("parameters") or [])]
        for method in ("get", "post", "put", "patch", "delete", "head", "options"):
            raw = item.get(method)
            if not isinstance(raw, dict):
                continue
            op = resolver.resolve(raw)
            params = shared + [_parameter(p) for p in (op.get("parameters") or [])]
            body_schema, body_required, content_type = _request_body(op.get("requestBody"))
            declared_security = op.get("security")
            secured = bool(declared_security) if declared_security is not None else global_security
            operations.append(Operation(
                method=method.upper(),
                path=path,
                operation_id=op.get("operationId") or f"{method}{re.sub(r'[^A-Za-z0-9]+', '_', path)}",
                summary=op.get("summary", "") or "",
                description=op.get("description", "") or "",
                parameters=[p for p in params if p is not None],
                body_schema=body_schema,
                body_required=body_required,
                body_content_type=content_type,
                responses=op.get("responses") or {},
                secured=secured,
                tags=[str(t) for t in (op.get("tags") or [])],
            ))
            if not op.get("responses"):
                issues.append(f"{method.upper()} {path} declares no responses; status assertions are a guess")
            elif not any(SUCCESS.match(str(c)) for c in op["responses"]):
                issues.append(f"{method.upper()} {path} declares no 2xx response")

    if resolver.refused:
        unique = sorted(set(resolver.refused))
        issues.append(
            f"{len(unique)} reference(s) were not resolved (external references are "
            f"never fetched): {', '.join(unique[:5])}"
        )
    if not operations:
        raise SpecError("no operations found under 'paths'")

    return Spec(
        title=info.get("title") or "API",
        version=str(info.get("version") or ""),
        servers=servers,
        operations=operations,
        issues=issues,
    )


def _parameter(raw) -> Parameter | None:
    if not isinstance(raw, dict) or not raw.get("name"):
        return None
    return Parameter(
        name=str(raw["name"]),
        location=str(raw.get("in") or "query"),
        required=bool(raw.get("required")) or raw.get("in") == "path",
        schema=raw.get("schema") or {},
        description=str(raw.get("description") or ""),
    )


def _request_body(raw) -> tuple[dict | None, bool, str]:
    if not isinstance(raw, dict):
        return None, False, "application/json"
    for content_type, media in (raw.get("content") or {}).items():
        if "json" in content_type and isinstance(media, dict) and media.get("schema"):
            return media["schema"], bool(raw.get("required")), content_type
    return None, bool(raw.get("required")), "application/json"


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def _sample_body(schema: dict | None, seed: str, locale: str, depth: int = 0) -> object:
    """A valid instance of ``schema``, using semantically appropriate data."""
    if not isinstance(schema, dict) or depth > 5:
        return {}
    if "example" in schema:
        return schema["example"]
    kind_types = [] if schema.get("type") is None else [schema["type"]]
    declared = kind_types[0] if kind_types else ("object" if schema.get("properties") else "string")

    if declared == "object":
        required = set(schema.get("required") or [])
        out: dict = {}
        for name, sub in (schema.get("properties") or {}).items():
            # Optional fields are included so the happy path exercises the full
            # shape; a caller wanting the minimal body can drop them.
            out[name] = _property_value(name, sub, f"{seed}/{name}", locale, depth)
        for name in required - set(out):
            out[name] = testdata.value("text", f"{seed}/{name}", locale=locale)
        return out
    if declared == "array":
        return [_sample_body(schema.get("items") or {}, f"{seed}/0", locale, depth + 1)]
    return _property_value("value", schema, seed, locale, depth)


def _property_value(name: str, schema: dict, seed: str, locale: str, depth: int):
    if not isinstance(schema, dict):
        return testdata.value("text", seed, locale=locale)
    declared = schema.get("type")
    if declared in {"object", "array"} or schema.get("properties") or schema.get("items"):
        return _sample_body(schema, seed, locale, depth + 1)
    if declared == "boolean":
        return True
    constraints = {k: schema[k] for k in ("minLength", "maxLength", "minimum", "maximum", "enum")
                   if k in schema}
    kind = testdata.infer_kind(
        name,
        type_hint=str(schema.get("format") or declared or ""),
        enum=schema.get("enum"),
    )
    raw = testdata.value(kind, seed, locale=locale, constraints=constraints)
    if declared == "integer":
        return int(float(re.sub(r"[^0-9.\-]", "", raw) or 1))
    if declared == "number":
        return float(re.sub(r"[^0-9.\-]", "", raw) or 1)
    return raw


def _url(op: Operation, values: dict[str, object]) -> str:
    """Substitute path templates and append the query string.

    Values are percent-encoded, which matters most for the hostile ones: an
    unencoded ``'; DROP TABLE users; --`` produces a malformed URL that never
    reaches the endpoint, so the probe would pass without having tested anything.
    Encoding delivers the exact bytes to the server, which is the point.
    """
    url = op.path
    for param in op.parameters:
        if param.location == "path":
            raw = str(values.get(param.name, "1"))
            url = url.replace("{" + param.name + "}", quote(raw, safe=""))
    query = [(p.name, values[p.name]) for p in op.parameters
             if p.location == "query" and p.name in values]
    if query:
        url += "?" + urlencode([(k, str(v)) for k, v in query], quote_via=quote)
    return url


def _headers(op: Operation, values: dict, *, authenticated: bool) -> dict:
    headers = {p.name: str(values.get(p.name, "")) for p in op.parameters
               if p.location == "header" and p.name in values}
    if op.body_schema:
        headers["Content-Type"] = op.body_content_type
    if op.secured and authenticated:
        # A placeholder the reviewer replaces or maps to a vault secret. Emitting
        # a fabricated token that silently 401s would make every secured test
        # look like a product defect.
        headers["Authorization"] = "${GALEQEA_API_TOKEN}"
    return headers


def _api_step(op: Operation, *, intent: str, expected: str, url: str, headers: dict,
              body: object | None, expect: dict) -> dict:
    value: dict = {"method": op.method, "url": url, "headers": headers, **expect}
    if body is not None:
        value["body"] = body
    return {"action": StepAction.API_REQUEST, "intent": intent, "expected": expected, "value": value}


def _proposal(op: Operation, *, title: str, technique: str, rationale: str,
              steps: list[dict], priority: str, risk: str, extra_tags: list[str]) -> dict:
    return {
        "title": title,
        "category": TestCategory.AUTOMATED,
        "priority": priority,
        "risk": risk,
        "rationale": rationale,
        "requirement_refs": [],
        "tags": ["api", *(t.lower().replace(" ", "-") for t in op.tags[:2]), *extra_tags],
        "steps": steps,
        "source": "openapi",
        "technique": technique,
    }


def generate(
    spec: Spec,
    *,
    seed: str = "openapi",
    locale: str = "en-US",
    include_injection: bool = True,
    max_negative_per_operation: int = 4,
) -> list[dict]:
    """Turn a parsed specification into reviewable test proposals."""
    proposals: list[dict] = []

    for op in spec.operations:
        values = {p.name: _param_value(p, seed, locale) for p in op.parameters}
        body = _sample_body(op.body_schema, f"{seed}/{op.operation_id}", locale) if op.body_schema else None
        base_headers = _headers(op, values, authenticated=True)
        success = op.success_status()
        schema = op.success_schema()
        writes = op.method in {"POST", "PUT", "PATCH", "DELETE"}
        risk = "high" if writes else "medium"

        # ---- contract ---------------------------------------------------- #
        expect: dict = {"expect_status": success}
        if schema:
            expect["expect_schema"] = schema
            expect["expect_content_type"] = "json"
        proposals.append(_proposal(
            op,
            title=f"{op.label} — contract: valid request returns {success} and a conforming body",
            technique="contract conformance",
            rationale=(
                f"{op.summary or 'The operation'} must honour its own specification. "
                + ("The response body is checked against the declared schema, so a renamed "
                   "or retyped field fails here rather than in a consumer."
                   if schema else
                   "The specification declares no JSON response schema, so only the status "
                   "code can be asserted — worth fixing in the spec.")
            ),
            steps=[_api_step(
                op,
                intent=f"Call {op.label} with valid parameters",
                expected=f"HTTP {success}" + (" and a body matching the declared schema" if schema else ""),
                url=_url(op, values), headers=base_headers, body=body, expect=expect,
            )],
            priority="high" if writes else "medium", risk=risk, extra_tags=["contract"],
        ))

        negatives = 0

        # ---- required parameter omitted ---------------------------------- #
        for param in op.parameters:
            if negatives >= max_negative_per_operation:
                break
            if not param.required or param.location == "path":
                continue        # a missing path segment changes the route, not the payload
            reduced = {k: v for k, v in values.items() if k != param.name}
            proposals.append(_proposal(
                op,
                title=f"{op.label} — rejects a request missing required '{param.name}'",
                technique="equivalence partitioning",
                rationale=(
                    f"'{param.name}' is declared required. Omitting it must produce a client "
                    f"error, not a success with a defaulted or null value."
                ),
                steps=[_api_step(
                    op,
                    intent=f"Call {op.label} without '{param.name}'",
                    expected=f"HTTP {' or '.join(str(c) for c in op.error_statuses())}",
                    url=_url(op, reduced),
                    headers=_headers(op, reduced, authenticated=True),
                    body=body,
                    expect={"expect_status_in": op.error_statuses()},
                )],
                priority="medium", risk=risk, extra_tags=["negative", "required"],
            ))
            negatives += 1

        # ---- required body properties omitted ---------------------------- #
        # Most validation on a write endpoint lives in the body, not in the
        # query string. Skipping it would leave POST and PUT covered by a single
        # happy-path call, which is the commonest gap in generated API suites.
        for name, _sub in _body_fields(op.body_schema):
            if negatives >= max_negative_per_operation or not isinstance(body, dict):
                break
            if name not in (op.body_schema.get("required") or []):
                continue
            reduced = {k: v for k, v in body.items() if k != name}
            proposals.append(_proposal(
                op,
                title=f"{op.label} — rejects a body missing required '{name}'",
                technique="equivalence partitioning",
                rationale=(
                    f"'{name}' is required by the request schema. Omitting it must produce a "
                    f"client error rather than a record persisted with a null."
                ),
                steps=[_api_step(
                    op,
                    intent=f"Call {op.label} with '{name}' absent from the body",
                    expected=f"HTTP {' or '.join(str(c) for c in op.error_statuses())}",
                    url=_url(op, values), headers=base_headers, body=reduced,
                    expect={"expect_status_in": op.error_statuses()},
                )],
                priority="high", risk=risk, extra_tags=["negative", "required", "body"],
            ))
            negatives += 1

        # ---- invalid body property values -------------------------------- #
        for name, sub in _body_fields(op.body_schema):
            if negatives >= max_negative_per_operation or not isinstance(body, dict):
                break
            constraints = {k: sub[k] for k in ("minLength", "maxLength", "minimum", "maximum", "enum")
                           if k in sub}
            if name not in (op.body_schema.get("required") or []):
                constraints["optional"] = True
            kind = testdata.infer_kind(name, type_hint=str(sub.get("format") or sub.get("type") or ""),
                                       enum=sub.get("enum"))
            bad = next((b for b in testdata.invalid_variants(kind, constraints=constraints, limit=3)
                        if b.value.strip()), None)
            if bad is None:
                continue
            proposals.append(_proposal(
                op,
                title=f"{op.label} — rejects body field '{name}' = {_short(bad.value)}",
                technique=bad.technique,
                rationale=f"{bad.why.capitalize()}. The API must reject it rather than store it.",
                steps=[_api_step(
                    op,
                    intent=f"Call {op.label} with an invalid '{name}'",
                    expected=f"HTTP {' or '.join(str(c) for c in op.error_statuses())}",
                    url=_url(op, values), headers=base_headers, body={**body, name: bad.value},
                    expect={"expect_status_in": op.error_statuses()},
                )],
                priority="medium", risk=risk, extra_tags=["negative", "boundary", "body"],
            ))
            negatives += 1

        # ---- boundaries and enums from the schema ------------------------ #
        for param in op.parameters:
            if negatives >= max_negative_per_operation:
                break
            for bad in testdata.invalid_variants(
                testdata.infer_kind(param.name, type_hint=param.type_hint, enum=param.schema.get("enum")),
                constraints=param.constraints, limit=2,
            )[:1]:
                if bad.value == "":
                    continue    # already covered by the required-parameter case
                mutated = {**values, param.name: bad.value}
                proposals.append(_proposal(
                    op,
                    title=f"{op.label} — rejects '{param.name}' = {_short(bad.value)}",
                    technique=bad.technique,
                    rationale=f"{bad.why.capitalize()}. The API must reject it rather than coerce it.",
                    steps=[_api_step(
                        op,
                        intent=f"Call {op.label} with an invalid '{param.name}'",
                        expected=f"HTTP {' or '.join(str(c) for c in op.error_statuses())}",
                        url=_url(op, mutated),
                        headers=_headers(op, mutated, authenticated=True),
                        body=body,
                        expect={"expect_status_in": op.error_statuses()},
                    )],
                    priority="medium", risk=risk, extra_tags=["negative", "boundary"],
                ))
                negatives += 1

        # ---- authentication ---------------------------------------------- #
        if op.secured:
            proposals.append(_proposal(
                op,
                title=f"{op.label} — rejects an unauthenticated request",
                technique="security",
                rationale=(
                    "The operation declares a security requirement. Without credentials it "
                    "must answer 401 or 403 — never 200, and never a 500 that leaks a stack "
                    "trace."
                ),
                steps=[_api_step(
                    op,
                    intent=f"Call {op.label} with no credentials",
                    expected="HTTP 401 or 403",
                    url=_url(op, values),
                    headers=_headers(op, values, authenticated=False),
                    body=body,
                    expect={"expect_status_in": [401, 403]},
                )],
                priority="high", risk="high", extra_tags=["negative", "auth"],
            ))

        # ---- injection probes -------------------------------------------- #
        if include_injection:
            target = next((p.name for p in op.parameters
                           if p.schema.get("type") in (None, "string") and p.location in {"query", "path"}), None)
            # A write endpoint usually has no string in the query string at all;
            # its untrusted input arrives in the body, so probe there instead.
            in_body = target is None and isinstance(body, dict)
            if in_body:
                target = next((name for name, sub in _body_fields(op.body_schema)
                               if sub.get("type") in (None, "string") and not sub.get("format")), None)
            if target is not None:
                steps = []
                for probe, why in testdata.INJECTION_PROBES[:3]:
                    mutated = values if in_body else {**values, target: probe}
                    probe_body = {**body, target: probe} if in_body else body
                    steps.append(_api_step(
                        op,
                        intent=f"Send {_short(probe)} as '{target}'",
                        expected=f"a 4xx, or a 2xx with the value stored safely — {why}",
                        url=_url(op, mutated),
                        headers=_headers(op, mutated, authenticated=True),
                        body=probe_body,
                        # Any 4xx or 2xx is acceptable; a 5xx means the input
                        # reached something that could not cope with it. The
                        # reflection check is added only for HTML responses -
                        # see Operation.renders_html.
                        expect={"expect_status_in": [200, 201, 202, 204, 400, 403, 404, 422],
                                **({"forbid_body_contains": [probe]} if op.renders_html() else {})},
                    ))
                proposals.append(_proposal(
                    op,
                    title=f"{op.label} — handles hostile input in '{target}' safely",
                    technique="security",
                    rationale=(
                        "Untrusted values must be handled, not crashed on: a 5xx means the "
                        "input reached a layer that could not cope with it. "
                        + ("This operation returns HTML, so the probe must also not appear "
                           "unescaped in the response."
                           if op.renders_html() else
                           "This operation returns JSON, where echoing the stored value back "
                           "is correct behaviour, so reflection is not asserted — check how a "
                           "consumer renders it.")
                    ),
                    steps=steps, priority="high", risk="high", extra_tags=["negative", "security"],
                ))

    return proposals


def _body_fields(schema: dict | None) -> list[tuple[str, dict]]:
    """Top-level properties of a request body, required ones first.

    Only the top level: nested-object mutation multiplies the case count far
    faster than it adds signal, and a reviewer approving forty near-identical
    proposals stops reading them.
    """
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    items = [(name, sub) for name, sub in properties.items() if isinstance(sub, dict)]
    return sorted(items, key=lambda pair: pair[0] not in required)


def _param_value(param: Parameter, seed: str, locale: str):
    schema = param.schema or {}
    return _property_value(param.name, schema, f"{seed}/{param.name}", locale, 0)


def _short(value: object, limit: int = 28) -> str:
    text = str(value)
    return repr(text if len(text) <= limit else text[: limit - 1] + "…")
