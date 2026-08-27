"""Deterministic plain-English command routing.

This is what lets the chat interface work with **no model at all**. A large
share of what people actually type at a test platform is a command with a
recognisable shape - "run the UAT button tests", "rerun only failed", "schedule
smoke nightly at 2am", "why did checkout fail" - and matching those with rules
is faster, free, offline and perfectly predictable.

When a model is configured this router still runs first: a confident rule match
is dispatched directly, saving a round trip. Only genuinely open-ended requests
reach the agent loop. That ordering is why QE Agent feels instant on the common
path instead of thinking for four seconds before clicking a button.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Ordered. First confident match wins, so specific patterns precede general ones.
INTENTS: list[tuple[str, re.Pattern[str], float]] = [
    ("rerun_failed", re.compile(
        r"\b(re-?run|retry)\b.{0,20}\b(only\s+)?(the\s+)?(failed|failures|failing)\b", re.I), 0.95),
    ("rerun_last", re.compile(r"\b(re-?run|run)\b.{0,15}\b(again|last|previous)\s*(run)?\b", re.I), 0.9),
    ("schedule", re.compile(r"\bschedule\b|\bevery (day|night|morning|hour|week)\b|\bnightly\b|\bcron\b", re.I), 0.9),
    ("cancel", re.compile(r"\b(cancel|stop|abort|kill)\b.{0,20}\b(run|test|execution)\b", re.I), 0.9),
    ("explore", re.compile(
        r"\b(explore|exploratory|poke around|have a look around|go find bugs|"
        r"hunt for (bugs|issues|problems))\b", re.I), 0.88),
    ("findings", re.compile(
        r"\b(findings?|what did (you|exploration) find|anything (surprising|broken))\b", re.I), 0.85),
    ("rca", re.compile(
        r"\b(why|root cause|rca|what (went wrong|broke|happened)|diagnose|explain the failure)\b", re.I), 0.85),
    ("coverage", re.compile(r"\bcoverage\b|\bwhat('s| is) (not |un)tested\b|\bgaps?\b|\btraceability\b", re.I), 0.85),
    ("flaky", re.compile(r"\bflak(y|iness)\b|\bunstable tests?\b|\bquarantine\b", re.I), 0.85),
    ("list_tests", re.compile(
        r"\b(list|show|what)\b.{0,25}\b(tests?|test cases?|suites?)\b", re.I), 0.8),
    ("status", re.compile(
        r"\b(status|how did|results?|last run|latest run|summary)\b", re.I), 0.7),
    ("approvals", re.compile(r"\b(pending )?approvals?\b|\bawaiting review\b|\bwhat needs (my )?approval\b", re.I), 0.85),
    ("analyze_requirements", re.compile(
        r"\b(analy[sz]e|read|ingest|process)\b.{0,25}\b(requirements?|document|spec|prd)\b", re.I), 0.85),
    ("generate_tests", re.compile(
        r"\b(generate|propose|create|write|draft)\b.{0,25}\b(tests?|test cases?)\b", re.I), 0.85),
    ("select_for_change", re.compile(
        r"\b(which|what) tests?\b.{0,30}\b(changed?|diff|commit|impact)\b", re.I), 0.8),
    # Most general: keep last so it cannot shadow the specific run intents.
    ("run", re.compile(r"^\s*(please\s+)?(run|execute|kick off|start|launch|trigger)\b", re.I), 0.85),
]

TAG_HINT = re.compile(r"\b(uat|smoke|regression|sanity|e2e|critical|nightly|api|a11y|accessibility)\b", re.I)
KEY_HINT = re.compile(r"\b([A-Z][A-Z0-9]{1,9}-T-\d{1,6})\b")
ENV_HINT = re.compile(r"\b(?:on|in|against)\s+(prod(?:uction)?|staging|uat|qa|dev(?:elopment)?|test|local)\b", re.I)
BROWSER_HINT = re.compile(r"\b(chromium|chrome|firefox|webkit|safari|edge)\b", re.I)
CRON_TIME = re.compile(r"\bat\s+(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)?\b", re.I)
#: Timing language belongs to the schedule, never to the test-name search.
SCHEDULE_NOISE = re.compile(
    r"\b(schedule|scheduled|cron|every|each|nightly|daily|weekly|monthly|"
    r"night|day|week|month|hour|morning|evening|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b",
    re.I,
)

BROWSER_ALIASES = {"chrome": "chromium", "safari": "webkit", "edge": "chromium"}

#: Words that describe *how* to run, not *what* to run - stripped before the
#: remaining text is used as a test-name search.
NOISE = re.compile(
    r"\b(please|run|execute|kick off|start|launch|trigger|the|my|our|all|test|tests|"
    r"testing|case|cases|suite|now|again|for|me|on|in|against|and|with)\b", re.I
)


@dataclass(slots=True)
class RoutedCommand:
    intent: str
    confidence: float
    tool: str = ""
    arguments: dict = field(default_factory=dict)
    explanation: str = ""

    @property
    def confident(self) -> bool:
        return self.confidence >= 0.75 and bool(self.tool)


def route(text: str, *, last_run_id: str | None = None) -> RoutedCommand:
    message = (text or "").strip()
    if not message:
        return RoutedCommand(intent="empty", confidence=0.0)

    for intent, pattern, confidence in INTENTS:
        if not pattern.search(message):
            continue
        return _build(intent, confidence, message, last_run_id)

    return RoutedCommand(intent="unknown", confidence=0.0)


def _build(intent: str, confidence: float, message: str, last_run_id: str | None) -> RoutedCommand:
    if intent == "rerun_failed":
        if not last_run_id:
            return RoutedCommand(
                intent, 0.5,
                explanation="I need a previous run to re-run failures from; none found yet.",
            )
        return RoutedCommand(
            intent, confidence, tool="run_tests",
            arguments={"rerun_failed_from": last_run_id, **_modifiers(message)},
            explanation="Re-running only the tests that failed in the last run.",
        )

    if intent == "rerun_last":
        return RoutedCommand(
            intent, confidence, tool="run_tests",
            arguments={"selection": "", **_modifiers(message)},
            explanation="Re-running the previous selection.",
        )

    if intent == "run":
        args = {**_selection(message), **_modifiers(message)}
        return RoutedCommand(
            intent, confidence, tool="run_tests", arguments=args,
            explanation=_describe_selection(args),
        )

    if intent == "cancel":
        return RoutedCommand(
            intent, confidence, tool="cancel_run",
            arguments={"run_id": last_run_id or ""},
            explanation="Cancelling the active run.",
        )

    if intent == "schedule":
        cron = _cron_from(message)
        args = {"name": _schedule_name(message), "cron": cron, **_selection(message)}
        return RoutedCommand(
            intent, confidence if cron else 0.6, tool="schedule_run", arguments=args,
            explanation=f"Proposing a schedule with cron '{cron}'." if cron
            else "I recognised a scheduling request but not the timing.",
        )

    if intent == "explore":
        # Everything after the verb is the charter - it is the user's own words
        # about what matters, and rewriting it would lose that.
        charter = re.sub(
            r"^\s*(please\s+)?(go\s+)?(and\s+)?(explore|exploratory test|poke around|"
            r"have a look around|go find bugs|hunt for (bugs|issues|problems))\s*",
            "", message, flags=re.I,
        ).strip(" .,:")
        # "go find bugs **in** the account settings" leaves a dangling
        # preposition that reads as a fragment in the session header.
        charter = re.sub(r"^(in|on|around|at|through|within)\s+", "", charter, flags=re.I).strip()
        charter = charter or "Explore the application and report anything surprising."
        args: dict = {"charter": charter}
        if env := ENV_HINT.search(message):
            args["environment"] = env.group(1).lower()
        if re.search(r"\b(staging|uat|qa|dev|local|test)\b", message, re.I):
            # Only offered where it is defensible: never inferred for production.
            args["allow_transactional"] = True
        return RoutedCommand(
            intent, confidence, tool="explore", arguments=args,
            explanation=f"Exploring: {charter[:70]}",
        )

    if intent == "findings":
        return RoutedCommand(intent, confidence, tool="get_findings", arguments={"status": "new"},
                             explanation="Fetching exploratory findings.")

    if intent == "list_tests":
        return RoutedCommand(
            intent, confidence, tool="list_tests", arguments=_list_filters(message),
            explanation="Listing matching test cases.",
        )

    if intent == "coverage":
        return RoutedCommand(intent, confidence, tool="get_coverage",
                             explanation="Computing requirement coverage and gaps.")

    if intent == "flaky":
        return RoutedCommand(intent, confidence, tool="get_flaky_tests",
                             explanation="Scoring test stability from run history.")

    if intent == "status":
        return RoutedCommand(intent, confidence, tool="get_run", arguments={"latest": True},
                             explanation="Fetching the latest run.")

    if intent == "select_for_change":
        return RoutedCommand(intent, confidence, tool="select_tests_for_change", arguments={},
                             explanation="Ranking the suite against the change.")

    # rca / approvals / analyze_requirements / generate_tests need context the
    # router cannot supply on its own, so they are handed to the agent (or to a
    # deterministic fallback) with the intent already identified.
    return RoutedCommand(intent, confidence * 0.8, explanation="")


# --------------------------------------------------------------------------- #
def _selection(message: str) -> dict:
    args: dict = {}
    if keys := KEY_HINT.findall(message):
        args["keys"] = keys
        return args

    # Strip the *modifier* phrases before the noise words, otherwise removing
    # "on" destroys the "on staging" pattern and the environment name survives
    # into the free-text search, silently narrowing the run.
    residue = ENV_HINT.sub(" ", message)
    residue = BROWSER_HINT.sub(" ", residue)
    residue = SCHEDULE_NOISE.sub(" ", residue)

    tags = {t.lower() for t in TAG_HINT.findall(residue)}
    if tags:
        args["tags"] = sorted(tags)
        residue = TAG_HINT.sub(" ", residue)

    residue = NOISE.sub(" ", residue)
    residue = re.sub(r"\s+", " ", residue).strip(" .,:;\"'")

    # A free-text match ANDs with the tag filter, so adding leftover words on top
    # of a tag would quietly exclude tests the user asked for.
    if len(residue) > 2:
        args["selection"] = residue
    return args


def _modifiers(message: str) -> dict:
    out: dict = {}
    if env := ENV_HINT.search(message):
        value = env.group(1).lower()
        out["environment"] = {"production": "prod", "development": "dev"}.get(value, value)
    if browsers := BROWSER_HINT.findall(message):
        out["browsers"] = sorted({BROWSER_ALIASES.get(b.lower(), b.lower()) for b in browsers})
    return out


def _list_filters(message: str) -> dict:
    filters: dict = {}
    lowered = message.lower()
    for category in ("manual", "exploratory", "automated"):
        if category in lowered:
            filters["category"] = category
            break
    for status in ("proposed", "approved", "rejected", "draft"):
        if status in lowered:
            filters["status"] = status
            break
    if tags := TAG_HINT.findall(message):
        filters["tag"] = tags[0].lower()
    return filters


def _cron_from(message: str) -> str:
    lowered = message.lower()
    hour, minute = 2, 0
    if match := CRON_TIME.search(message):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").lower()
        if meridiem == "pm" and hour < 12:
            hour += 12
        elif meridiem == "am" and hour == 12:
            hour = 0

    if "hour" in lowered and "every" in lowered:
        return f"{minute} * * * *"
    if "week" in lowered:
        return f"{minute} {hour} * * 1"
    if "month" in lowered:
        return f"{minute} {hour} 1 * *"
    if any(word in lowered for word in ("night", "nightly", "daily", "every day", "each day")):
        return f"{minute} {hour} * * *"
    if CRON_TIME.search(message):
        return f"{minute} {hour} * * *"
    return ""


def _schedule_name(message: str) -> str:
    tags = TAG_HINT.findall(message)
    label = tags[0].lower() if tags else "scheduled"
    return f"{label} run"


def _describe_selection(args: dict) -> str:
    if args.get("keys"):
        return f"Running {', '.join(args['keys'])}."
    if args.get("tags"):
        return f"Running tests tagged {', '.join(args['tags'])}."
    if args.get("selection"):
        return f"Running tests matching “{args['selection']}”."
    return "Running all approved automated tests."
