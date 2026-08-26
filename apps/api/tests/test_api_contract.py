"""OpenAPI import, contract-test generation and the synthetic data factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from galeqea.engine import openapi
from galeqea.intelligence import testdata

SPEC = Path(__file__).parent / "fixtures_openapi.yaml"


@pytest.fixture()
def spec() -> openapi.Spec:
    return openapi.parse(openapi.load(SPEC.read_text()))


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
def test_local_refs_resolve(spec):
    post = next(o for o in spec.operations if o.method == "POST")
    assert set(post.body_schema["required"]) == {"customerEmail", "totalAmount"}
    assert post.success_schema()["properties"]["status"]["enum"] == ["open", "shipped", "cancelled"]


def test_remote_refs_are_refused_never_fetched():
    """A specification is untrusted input; dereferencing a URL inside it would
    let the document choose what this process connects to."""
    document = {
        "openapi": "3.0.0",
        "paths": {"/x": {"get": {"responses": {"200": {
            "description": "ok",
            "content": {"application/json": {
                "schema": {"$ref": "https://evil.example/schema.json"}}},
        }}}}},
    }
    parsed = openapi.parse(document)
    assert any("never fetched" in issue for issue in parsed.issues)
    assert "evil.example" in " ".join(parsed.issues)


def test_reference_cycles_terminate():
    document = {
        "openapi": "3.0.0",
        "components": {"schemas": {"Node": {
            "type": "object", "properties": {"child": {"$ref": "#/components/schemas/Node"}}}}},
        "paths": {"/n": {"get": {"responses": {"200": {"description": "ok", "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Node"}}}}}}}},
    }
    parsed = openapi.parse(document)          # must not hang or recurse forever
    assert len(parsed.operations) == 1


def test_swagger_2_is_rejected_with_a_usable_message():
    with pytest.raises(openapi.SpecError, match="Swagger 2.0"):
        openapi.parse({"swagger": "2.0", "paths": {"/a": {}}})


def test_explicit_empty_security_overrides_the_global_requirement(spec):
    """`security: []` on an operation means *no* auth, not "inherit"."""
    public = next(o for o in spec.operations if o.path == "/orders/{orderId}")
    assert public.secured is False
    private = next(o for o in spec.operations if o.path == "/orders" and o.method == "GET")
    assert private.secured is True


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #
def test_every_operation_gets_a_contract_test(spec):
    proposals = openapi.generate(spec, seed="t")
    contracts = [p for p in proposals if p["technique"] == "contract conformance"]
    assert len(contracts) == len(spec.operations)


def test_the_contract_test_asserts_the_response_schema(spec):
    proposals = openapi.generate(spec, seed="t")
    step = next(p for p in proposals if p["title"].startswith("GET /orders —"))["steps"][0]
    assert step["value"]["expect_status"] == 200
    assert step["value"]["expect_schema"]["required"] == ["items", "total"]


def test_required_body_fields_get_their_own_negative_test(spec):
    """Most validation on a write endpoint lives in the body, not the query."""
    proposals = openapi.generate(spec, seed="t")
    titles = [p["title"] for p in proposals]
    assert any("missing required 'customerEmail'" in t for t in titles)
    assert any("missing required 'totalAmount'" in t for t in titles)
    omitted = next(p for p in proposals if "missing required 'customerEmail'" in p["title"])
    assert "customerEmail" not in omitted["steps"][0]["value"]["body"]
    assert omitted["steps"][0]["value"]["expect_status_in"] == [422]


def test_secured_operations_get_an_unauthenticated_test(spec):
    proposals = openapi.generate(spec, seed="t")
    auth = [p for p in proposals if "unauthenticated" in p["title"]]
    assert {p["title"].split(" —")[0] for p in auth} == {"GET /orders", "POST /orders"}
    assert auth[0]["steps"][0]["value"]["expect_status_in"] == [401, 403]
    assert "Authorization" not in auth[0]["steps"][0]["value"]["headers"]


def test_public_operations_get_no_auth_test(spec):
    proposals = openapi.generate(spec, seed="t")
    assert not any("GET /orders/{orderId} — rejects an unauthenticated" in p["title"]
                   for p in proposals)


def test_hostile_values_are_percent_encoded(spec):
    """An unencoded probe produces a malformed URL that never reaches the
    endpoint, so the test would pass without having tested anything."""
    proposals = openapi.generate(spec, seed="t")
    probe = next(p for p in proposals if "hostile input in 'status'" in p["title"])
    for step in probe["steps"]:
        url = step["value"]["url"]
        assert " " not in url
        assert "<" not in url
    assert "%3Cscript%3E" in probe["steps"][0]["value"]["url"]


def test_reflection_is_only_asserted_for_html_responses(spec):
    """Echoing a stored value back inside JSON is correct behaviour; asserting
    against it would fail a conformant API."""
    json_probe = next(p for p in openapi.generate(spec, seed="t") if "hostile" in p["title"])
    assert "forbid_body_contains" not in json_probe["steps"][0]["value"]

    html = {
        "openapi": "3.0.0",
        "paths": {"/search": {"get": {
            "parameters": [{"name": "q", "in": "query", "schema": {"type": "string"}}],
            "responses": {"200": {"description": "ok", "content": {"text/html": {}}}},
        }}},
    }
    probe = next(p for p in openapi.generate(openapi.parse(html), seed="t") if "hostile" in p["title"])
    assert probe["steps"][0]["value"]["forbid_body_contains"]


def test_enum_violations_use_a_value_adjacent_to_the_set(spec):
    proposals = openapi.generate(spec, seed="t")
    enum_case = next(p for p in proposals if "rejects 'status'" in p["title"])
    assert "open-x" in enum_case["title"]


def test_generation_is_reproducible(spec):
    a = openapi.generate(spec, seed="fixed")
    b = openapi.generate(spec, seed="fixed")
    assert a == b


# --------------------------------------------------------------------------- #
# Synthetic data
# --------------------------------------------------------------------------- #
def test_values_are_a_pure_function_of_the_seed():
    """A generator seeded from the clock makes failures unreproducible."""
    assert testdata.value("person_name", "s") == testdata.value("person_name", "s")
    assert testdata.value("person_name", "s") != testdata.value("person_name", "t")


def test_card_numbers_are_luhn_valid_but_cannot_route():
    for index in range(100):
        number = testdata.value("credit_card", f"seed{index}")
        assert len(number) == 16
        # ISO/IEC 7812 reserves major industry identifier 9 for national
        # assignment; no scheme issues in it, so this can never reach a network.
        assert number[0] == "9"
        total = 0
        for position, char in enumerate(reversed(number)):
            digit = int(char)
            if position % 2 == 1:
                digit = digit * 2 - 9 if digit * 2 > 9 else digit * 2
            total += digit
        assert total % 10 == 0, f"{number} fails Luhn"


def test_emails_only_use_reserved_domains():
    """RFC 2606 / RFC 6761 names never resolve, so a stray send reaches nobody."""
    allowed = ("example.com", "example.org", "example.net", "test.invalid", "qa.example")
    for index in range(60):
        assert testdata.value("email", f"s{index}").endswith(allowed)


def test_phone_numbers_use_the_ranges_regulators_reserve_for_fiction():
    assert "555-01" in testdata.value("phone", "a")
    assert testdata.value("phone", "a", locale="en-GB").startswith("07700 900")


def test_field_kinds_are_inferred_from_names_in_any_casing():
    for name in ("customerEmailAddress", "customer_email_address", "Customer Email Address"):
        assert testdata.infer_kind(name) == "email"


def test_more_specific_card_fields_win_over_the_bare_word():
    assert testdata.infer_kind("card") == "credit_card"
    assert testdata.infer_kind("cardCode") == "cvv"
    assert testdata.infer_kind("card expiry") == "expiry"
    assert testdata.infer_kind("cardholder name") == "person_name"


def test_declared_types_outrank_field_names():
    assert testdata.infer_kind("start", type_hint="date") == "date"
    assert testdata.infer_kind("anything", type_hint="email") == "email"


def test_invalid_variants_carry_the_reason_they_are_wrong():
    variants = testdata.invalid_variants("email")
    assert variants, "email must have known malformations"
    assert all(v.why for v in variants)
    assert any("@" in v.why or "domain" in v.why for v in variants)


def test_boundary_variants_come_from_the_declared_limits():
    variants = testdata.invalid_variants("text", constraints={"minLength": 8, "maxLength": 64})
    lengths = {len(v.value) for v in variants}
    assert 7 in lengths and 65 in lengths


def test_generated_values_honour_their_constraints():
    for index in range(30):
        produced = int(testdata.value("integer", f"s{index}", constraints={"minimum": 5, "maximum": 9}))
        assert 5 <= produced <= 9


def test_step_generator_references_resolve():
    resolved = testdata.resolve_step_value(
        {"generate": {"kind": "email", "field": "email"}}, seed="run/1"
    )
    assert "@" in resolved
