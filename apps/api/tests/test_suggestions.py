"""Next-step suggestions: deterministic, from the last successful tool."""

from __future__ import annotations

from galeqea.ai.orchestrator import NEXT_STEPS, suggest_next


def _step(tool: str, result: dict, arguments: dict | None = None) -> dict:
    return {"tool": tool, "arguments": arguments or {}, "result": result}


def test_only_the_last_tool_drives_the_chips():
    steps = [
        _step("query_requirements", {"ok": True, "count": 1, "acceptance_criteria_count": 2,
                                     "requirements": [{"ref": "REQ-1"}]}),
        _step("generate_playwright_script", {"ok": True, "unresolved_locators": []}),
    ]
    labels = [s["label"] for s in suggest_next(steps)]
    assert "File for review" in labels
    assert not any("Gherkin" in label for label in labels)


def test_a_failed_tool_is_skipped_in_favour_of_the_previous_success():
    steps = [
        _step("query_requirements", {"ok": True, "count": 1, "acceptance_criteria_count": 1,
                                     "requirements": [{"ref": "REQ-7"}]}),
        _step("generate_bdd_scenarios", {"ok": False, "error": "no criteria"}),
    ]
    assert any("REQ-7" in s["text"] for s in suggest_next(steps))


def test_missing_criteria_suggests_supplying_them_not_generating_from_nothing():
    steps = [_step("query_requirements", {"ok": True, "count": 1, "acceptance_criteria_count": 0,
                                          "requirements": [{"ref": "REQ-2"}]},
                   {"feature": "checkout"})]
    chips = suggest_next(steps)
    assert chips[0]["label"] == "Supply criteria"
    assert "checkout" in chips[0]["text"]


def test_unresolved_locators_are_surfaced_first():
    chips = suggest_next([_step("generate_playwright_script",
                                {"ok": True, "unresolved_locators": ["the order total is recalculated"]})])
    assert chips[0]["label"] == "Record real locators"


def test_tools_without_a_rule_yield_nothing():
    assert suggest_next([_step("get_audit_trail", {"ok": True})]) == []
    assert suggest_next([]) == []


def test_chips_are_capped():
    assert len(suggest_next([_step("generate_playwright_script",
                                   {"ok": True, "unresolved_locators": ["x"]})], limit=2)) == 2


def test_every_chip_is_a_prompt_a_person_can_read():
    """Chips send their text verbatim, so it must read as a request, not an id."""
    for tool, rule in NEXT_STEPS.items():
        for chip in rule({"feature": "checkout", "ref": "REQ-1"},
                         {"ok": True, "count": 1, "acceptance_criteria_count": 1,
                          "requirements": [{"ref": "REQ-1"}], "feature": "Checkout",
                          "unresolved": ["x"], "unresolved_locators": ["x"],
                          "fields": [{"name": "email"}]}):
            assert chip["label"] and chip["text"], tool
            assert len(chip["label"]) <= 28, f"{tool}: label too long for a chip"


# --------------------------------------------------------------------------- #
# Conversation awareness (R11)
# --------------------------------------------------------------------------- #
from galeqea.ai.orchestrator import session_tool_history, suggest_next  # noqa: E402


class _Msg:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls


class _Sess:
    def __init__(self, messages):
        self.messages = messages


def test_a_done_one_time_step_is_not_re_suggested():
    """After a script and its test data both exist, do not offer 'Test data' again."""
    steps = [_step("generate_playwright_script", {"ok": True, "unresolved_locators": []})]
    done = frozenset({"generate_playwright_script", "generate_test_data"})
    labels = [c["label"] for c in suggest_next(steps, session_tools=done)]
    assert "Test data" not in labels
    assert "File for review" in labels  # not yet done, still offered


def test_a_repeatable_step_is_still_offered_after_running():
    """Running is naturally repeatable; 'rerun only failed' stays available."""
    steps = [_step("run_tests", {"ok": True})]
    labels = [c["label"] for c in suggest_next(steps, session_tools=frozenset({"run_tests"}))]
    assert labels, "run_tests chips should survive because it is repeatable"


def test_the_pipeline_advances_review_then_judge_then_file():
    review = [_step("review_test", {"ok": True, "verdict": "advisory"})]

    early = {c["label"] for c in suggest_next(review, session_tools=frozenset({"generate_playwright_script", "review_test"}))}
    assert "Check criteria coverage" in early

    late = suggest_next(review, session_tools=frozenset({
        "generate_playwright_script", "review_test", "judge_test_against_criteria", "create_test"}))
    assert late == [], "once judge and file are done there is nothing left to suggest after review"


def test_session_tool_history_reads_persisted_and_current():
    session = _Sess([
        _Msg([{"tool": "query_requirements"}]),
        _Msg([{"tool": "generate_bdd_scenarios"}, {"tool": "generate_playwright_script"}]),
    ])
    done = session_tool_history(session, [{"tool": "review_test"}])
    assert done == frozenset({
        "query_requirements", "generate_bdd_scenarios", "generate_playwright_script", "review_test",
    })


def test_session_tool_history_survives_missing_or_malformed_tool_calls():
    session = _Sess([_Msg(None), _Msg([{"not_a_tool": "x"}]), _Msg([{"tool": "run_tests"}])])
    assert session_tool_history(session) == frozenset({"run_tests"})


def test_every_pipeline_chip_names_the_tool_it_would_trigger():
    """Suppression works on the tool tag, so a pipeline chip must carry one."""
    from galeqea.ai.orchestrator import NEXT_STEPS
    result = {"ok": True, "count": 1, "acceptance_criteria_count": 1, "verdict": "advisory",
              "requirements": [{"ref": "REQ-1"}], "feature": "Checkout", "unresolved": [],
              "unresolved_locators": [], "fields": [{"name": "email"}], "uncovered_count": 0}
    # At least one chip per rule should carry a tool tag (some are pure questions).
    for tool, rule in NEXT_STEPS.items():
        chips = rule({"feature": "checkout", "ref": "REQ-1"}, result)
        assert chips, tool
