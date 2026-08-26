"""Run supervisor: spawns the Playwright runner and mediates its questions.

The runner executes; the supervisor decides. Everything that needs a database, a
model or a policy - healing, semantic judging, human handoff - is answered here
over the NDJSON channel, which keeps the browser process free of credentials and
free of any notion of approval rules.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..core.events import Ev, Event, bus
from ..db import session_scope
from ..models import (
    Artifact,
    Run,
    RunStatus,
    RunStepRecord,
    RunTest,
    TestCase,
)
from ..models.base import utcnow
from .healing import HealingEngine
from .plan import PlanCompiler, select_tests

#: Live handoff sessions awaiting a human. Keyed by run id.
_HANDOFFS: dict[str, asyncio.Future] = {}
#: Cancellation flags, set by the API layer.
_CANCELLED: set[str] = set()


@dataclass(slots=True)
class RunContext:
    run_id: str
    project_id: str
    artifacts_dir: Path
    healing: HealingEngine | None = None
    judge: object | None = None
    run_test_ids: dict[str, str] = field(default_factory=dict)
    handoff_waiters: dict[str, asyncio.Future] = field(default_factory=dict)
    stderr_tail: list[str] = field(default_factory=list)
    #: Set when this process is an exploratory session rather than a test run.
    exploration_session_id: str | None = None
    recording_session_id: str | None = None
    #: App Model discovery state for this run.
    current_screen_id: str | None = None
    previous_screen_id: str | None = None
    discovered: dict[str, int] = field(default_factory=lambda: {"screens": 0, "elements": 0, "links": 0})


class RunSupervisor:
    def __init__(self, *, provider=None):
        self.provider = provider

    # ------------------------------------------------------------------ #
    async def execute(self, run_id: str) -> dict:
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is None:
                raise ValueError(f"unknown run {run_id}")
            project_id = run.project_id
            artifacts_dir = Path(settings.artifacts_dir) / run_id
            artifacts_dir.mkdir(parents=True, exist_ok=True)

            cases = self._resolve_cases(db, run)
            plan = PlanCompiler(db).compile_run(run, cases, artifacts_dir=str(artifacts_dir))

            run.status = RunStatus.RUNNING
            run.started_at = utcnow()
            run.totals = {
                "total": len(plan["tests"]),
                "passed": 0, "failed": 0, "skipped": len(plan["_skipped"]),
                "needs_review": 0, "flaky": 0,
            }
            db.flush()

        await bus.publish(Event(
            type=Ev.RUN_STARTED, project_id=project_id, run_id=run_id,
            payload={
                "run_id": run_id,
                "test_count": len(plan["tests"]),
                "browsers": plan["browsers"],
                "base_url": plan["baseUrl"],
                "skipped": plan["_skipped"],
            },
        ))

        if not plan["tests"]:
            return await self._finish(run_id, project_id, error="no runnable tests matched the selection")

        ctx = RunContext(run_id=run_id, project_id=project_id, artifacts_dir=artifacts_dir)
        return await self._drive(plan, ctx)

    # ------------------------------------------------------------------ #
    async def explore(self, plan: dict, *, session_id: str, project_id: str) -> None:
        """Drive an exploratory session over the same runner protocol.

        Exploration reuses everything execution already has - the subprocess,
        the NDJSON channel, the request/response ask() - and differs only in
        which questions the runner asks. That is the payoff of keeping policy on
        the server: a whole new mode needed no new transport.
        """
        ctx = RunContext(
            run_id=plan["runId"],
            project_id=project_id,
            artifacts_dir=Path(plan["artifactsDir"]),
        )
        ctx.exploration_session_id = session_id
        await self._drive(plan, ctx)

    async def record(self, plan: dict, *, session_id: str, project_id: str) -> None:
        """Drive a recording session over the same runner protocol.

        A third mode on one transport. What makes recording worth routing through
        the supervisor rather than straight to a file is the App Model: every
        element the tester touches is observed and stored as it happens, so the
        recorded test is bound to durable element identities from the moment it
        is created rather than at its first successful run.
        """
        ctx = RunContext(
            run_id=plan["runId"],
            project_id=project_id,
            artifacts_dir=Path(plan["artifactsDir"]),
        )
        ctx.recording_session_id = session_id
        await self._drive(plan, ctx)

    # --- recording event handlers -------------------------------------- #
    async def _on_record_start(self, event: dict, ctx: RunContext) -> None:
        from ..services import recording

        await recording.on_start(event, project_id=ctx.project_id)
        await bus.publish(Event(
            type=Ev.RUN_PROGRESS, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"phase": "recording", "startUrl": event.get("startUrl")},
        ))

    async def _on_recorded_action(self, event: dict, ctx: RunContext) -> None:
        from ..models import AppScreen
        from ..services import recording
        from . import discovery

        # Bind the touched element into the App Model straight away. The capture
        # script already produced the ladder and fingerprint in the page, so this
        # is the same observation an executed step would make - which is exactly
        # why a recorded test is healable before it has ever been run.
        element = event.get("element") or {}
        if element.get("ladder") and ctx.current_screen_id:
            with session_scope() as db:
                screen = db.get(AppScreen, ctx.current_screen_id)
                if screen is not None:
                    before = len(screen.elements)
                    discovery.observe_element(db, project_id=ctx.project_id, screen=screen,
                                              observation={
                                                  "role": element.get("role", ""),
                                                  "accessibleName": element.get("accessibleName", ""),
                                                  "tag": element.get("tag", ""),
                                                  "intent": event.get("kind", ""),
                                                  "locator": (element.get("ladder") or [{}])[0],
                                                  "fingerprint": element.get("fingerprint") or {},
                                                  "box": (element.get("fingerprint") or {}).get("box"),
                                              })
                    if len(screen.elements) > before:
                        ctx.discovered["elements"] += 1

        await recording.on_action(event, project_id=ctx.project_id)
        await bus.publish(Event(
            type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"level": "info",
                     "message": f"recorded {event.get('kind')} "
                                f"{(element.get('accessibleName') or event.get('url') or '')[:60]}"},
        ))

    async def _on_record_end(self, event: dict, ctx: RunContext) -> None:
        from ..services import recording

        await recording.on_end(event, project_id=ctx.project_id,
                               session_id=ctx.recording_session_id)

    async def _on_record_error(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"level": "error", "message": event.get("message", "recording error")},
        ))

    # ------------------------------------------------------------------ #
    def _resolve_cases(self, db: Session, run: Run) -> list[TestCase]:
        return select_tests(db, run.project_id, run.selection or {})

    async def _drive(self, plan: dict, ctx: RunContext) -> dict:
        # The plan goes to a file, not to stdin: stdin stays open for the whole
        # run as the reply channel for heal/judge/handoff requests, so a runner
        # that waited for stdin EOF to read its plan would deadlock immediately.
        plan_path = ctx.artifacts_dir / "plan.json"
        plan_path.write_text(json.dumps(plan))

        argv = [settings.runner_command, settings.runner_entry, "--plan", str(plan_path)]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            return await self._finish(
                ctx.run_id, ctx.project_id,
                error=(
                    f"could not start the test runner ({shlex.join(argv)}): {exc}. "
                    "Install Node 18+ and run `npm install` in apps/runner."
                ),
            )

        stderr_task = asyncio.create_task(self._drain_stderr(proc, ctx))
        ctx.stderr_tail = []
        try:
            await self._pump(proc, ctx)
        finally:
            stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stderr_task
            with contextlib.suppress(ProcessLookupError):
                if proc.returncode is None:
                    proc.terminate()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=15)

        await self._report_discovery(ctx)
        if ctx.exploration_session_id:
            return {}
        return await self._finish(
            ctx.run_id, ctx.project_id,
            exit_code=proc.returncode,
            stderr_tail=ctx.stderr_tail,
            expected=len(plan["tests"]),
        )

    async def _drain_stderr(self, proc, ctx: RunContext) -> None:
        async for raw in proc.stderr:
            line = raw.decode(errors="replace").rstrip()
            if not line:
                continue
            ctx.stderr_tail.append(line[:500])
            del ctx.stderr_tail[:-15]
            await bus.publish(Event(
                type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
                payload={"level": "stderr", "message": line[:2000]},
            ))

    # ------------------------------------------------------------------ #
    async def _pump(self, proc, ctx: RunContext) -> None:
        async for raw in proc.stdout:
            if ctx.run_id in _CANCELLED:
                proc.stdin.write(json.dumps({"type": "cancel"}).encode() + b"\n")
                await proc.stdin.drain()
                _CANCELLED.discard(ctx.run_id)

            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                await bus.publish(Event(
                    type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
                    payload={"level": "raw", "message": line[:2000]},
                ))
                continue

            reply = await self._handle(event, ctx)
            if reply is not None:
                proc.stdin.write(json.dumps(reply).encode() + b"\n")
                await proc.stdin.drain()

            # Finalise on the run's own terminal event rather than waiting for
            # the process to exit, so a runner that lingers cannot stall a run.
            if event.get("type") == "run_end":
                break

    async def _handle(self, event: dict, ctx: RunContext) -> dict | None:
        kind = event.get("type")
        handler = getattr(self, f"_on_{kind}", None)
        if handler is None:
            await bus.publish(Event(
                type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
                payload={"level": "debug", "message": f"unhandled runner event: {kind}"},
            ))
            return None
        return await handler(event, ctx)

    # --- runner event handlers ----------------------------------------- #
    async def _on_log(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"level": event.get("level", "info"), "message": event.get("message", "")},
        ))

    async def _on_run_start(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_PROGRESS, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"phase": "browser_launched", **{k: event[k] for k in ("testCount", "browsers") if k in event}},
        ))

    async def _on_run_progress(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_PROGRESS, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"completed": event.get("completed"), "total": event.get("total"),
                     "totals": event.get("totals", {})},
        ))

    async def _on_test_start(self, event: dict, ctx: RunContext) -> None:
        with session_scope() as db:
            record = RunTest(
                run_id=ctx.run_id,
                test_case_id=event.get("testCaseId") or "",
                test_key=event.get("key", ""),
                title=event.get("title", ""),
                browser=event.get("browser", "chromium"),
                status=RunStatus.RUNNING,
                attempt=event.get("attempt", 1),
                started_at=utcnow(),
            )
            db.add(record)
            db.flush()
            ctx.run_test_ids[event["testId"]] = record.id

        await bus.publish(Event(
            type=Ev.RUN_TEST_STARTED, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"test_id": event["testId"], "key": event.get("key"),
                     "title": event.get("title"), "browser": event.get("browser")},
        ))

    async def _on_step_start(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_STEP, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"phase": "start", "test_id": event.get("testId"),
                     "index": event.get("index"), "action": event.get("action"),
                     "intent": event.get("intent")},
        ))

    async def _on_step_end(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_STEP, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"phase": "end", "test_id": event.get("testId"),
                     "index": event.get("index"), "action": event.get("action"),
                     "intent": event.get("intent"), "status": event.get("status"),
                     "duration_ms": event.get("durationMs"),
                     "error": event.get("errorMessage", ""),
                     "healed": bool(event.get("healApplied"))},
        ))

    async def _on_test_end(self, event: dict, ctx: RunContext) -> None:
        run_test_id = ctx.run_test_ids.get(event["testId"])
        from ..intelligence.signatures import compute_signature

        with session_scope() as db:
            record = db.get(RunTest, run_test_id) if run_test_id else None
            if record is None:
                return
            record.status = event.get("status", RunStatus.FAILED)
            record.finished_at = utcnow()
            record.duration_ms = event.get("durationMs", 0)
            record.error_message = (event.get("errorMessage") or "")[:4000]
            record.error_type = event.get("errorType", "")
            record.console_errors = event.get("consoleErrors", [])
            record.network_failures = event.get("networkFailures", [])
            record.healed = bool(event.get("heals"))
            record.failure_signature = compute_signature(
                record.error_type, record.error_message, record.test_key
            ) if record.error_message else ""

            for step in event.get("steps", []):
                db.add(RunStepRecord(
                    run_test_id=record.id,
                    step_id=step.get("stepId"),
                    index=step.get("index", 0),
                    action=step.get("action", ""),
                    intent=step.get("intent", ""),
                    status=step.get("status", "passed"),
                    duration_ms=step.get("durationMs", 0),
                    resolved_locator=(step.get("resolvedLocator") or "")[:1000],
                    heal_applied=step.get("healApplied"),
                    error_message=(step.get("errorMessage") or "")[:2000],
                    logs=step.get("logs", []),
                    artifacts=step.get("artifacts", []),
                ))

            for art in event.get("artifacts", []):
                path = Path(art.get("path", ""))
                db.add(Artifact(
                    run_id=ctx.run_id,
                    run_test_id=record.id,
                    kind=art.get("kind", "screenshot"),
                    path=str(path),
                    label=art.get("label", ""),
                    size_bytes=path.stat().st_size if path.exists() else 0,
                ))

        await bus.publish(Event(
            type=Ev.RUN_TEST_FINISHED, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"test_id": event["testId"], "key": event.get("key"),
                     "status": event.get("status"), "duration_ms": event.get("durationMs"),
                     "error": (event.get("errorMessage") or "")[:600],
                     "healed": bool(event.get("heals"))},
        ))

    async def _on_artifact(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_ARTIFACT, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"test_id": event.get("testId"), "kind": event.get("kind"),
                     "path": event.get("path"), "label": event.get("label")},
        ))

    # --- App Model discovery ------------------------------------------- #
    async def _on_screen_observed(self, event: dict, ctx: RunContext) -> None:
        """A run landed on a screen. Record it and the edge that led here."""
        from . import discovery

        with session_scope() as db:
            before = discovery.route_signature(event.get("fromRoute") or "")
            screen = discovery.observe_screen(
                db, project_id=ctx.project_id, observation=event
            )
            if screen.visit_count == 1:
                ctx.discovered["screens"] += 1

            ctx.previous_screen_id = ctx.current_screen_id
            ctx.current_screen_id = screen.id
            if ctx.previous_screen_id and before:
                discovery.observe_transition(
                    db,
                    project_id=ctx.project_id,
                    from_screen_id=ctx.previous_screen_id,
                    to_screen_id=screen.id,
                    via_element_id=None,
                    action=event.get("action", "navigate"),
                )

    async def _on_element_observed(self, event: dict, ctx: RunContext) -> None:
        """A step resolved an element. Record it against the current screen."""
        from ..models import AppScreen
        from . import discovery

        if not ctx.current_screen_id:
            return
        with session_scope() as db:
            screen = db.get(AppScreen, ctx.current_screen_id)
            if screen is None:
                return
            before = len(screen.elements)
            element = discovery.observe_element(
                db, project_id=ctx.project_id, screen=screen, observation=event
            )
            if len(screen.elements) > before:
                ctx.discovered["elements"] += 1
            if discovery.maybe_link_step(
                db, step_id=event.get("stepId"), element=element,
                locator=event.get("locator") or {},
            ):
                ctx.discovered["links"] += 1

    async def _on_locator_drift(self, event: dict, ctx: RunContext) -> None:
        # The test passed on a fallback rung. Worth knowing before it breaks.
        await bus.publish(Event(
            type=Ev.HEAL_PROPOSED, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"kind": "drift", **{k: event.get(k) for k in ("testId", "from", "to", "rungIndex")}},
        ))

    async def _on_heal_applied(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.HEAL_PROPOSED, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"kind": "applied_transiently", **event},
        ))

    async def _on_perf_metrics(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"level": "metrics", "message": json.dumps(event.get("metrics", {}))},
        ))

    async def _on_run_error(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.AGENT_ERROR, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"message": event.get("message", "")[:2000]},
        ))

    async def _on_run_end(self, event: dict, ctx: RunContext) -> None:
        with session_scope() as db:
            run = db.get(Run, ctx.run_id)
            if run:
                run.totals = {**(run.totals or {}), **event.get("totals", {})}

    # --- request/response handlers -------------------------------------- #
    async def _on_heal_request(self, event: dict, ctx: RunContext) -> dict:
        with session_scope() as db:
            engine = HealingEngine(db, provider=self.provider, project_id=ctx.project_id)
            outcome = await engine.heal(event)

        await bus.publish(Event(
            type=Ev.HEAL_PROPOSED, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={
                "kind": "proposal", "ok": outcome.ok, "strategy": outcome.strategy,
                "score": outcome.score, "reason": outcome.reason,
                "intent": event.get("intent"), "test_id": event.get("testId"),
                "candidates": outcome.candidates[:3],
            },
        ))
        return outcome.as_response(event["requestId"])

    async def _on_judge_request(self, event: dict, ctx: RunContext) -> dict:
        from ..intelligence.judge import judge_step

        with session_scope() as db:
            verdict = await judge_step(
                db, provider=self.provider, project_id=ctx.project_id,
                run_test_id=ctx.run_test_ids.get(event.get("testId", "")),
                question=event.get("question", ""),
                aria_snapshot=event.get("ariaSnapshot", ""),
                url=event.get("url", ""),
                step_index=event.get("stepIndex", 0),
            )
        return {"requestId": event["requestId"], **verdict}

    async def _on_handoff_request(self, event: dict, ctx: RunContext) -> dict:
        """Park the browser and wait for a person.

        This is the escape hatch that keeps an automation suite usable against
        real-world blockers - SSO, MFA, CAPTCHA, a shadow-DOM widget no snapshot
        can see - instead of declaring those journeys untestable.
        """
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        key = f"{ctx.run_id}:{event.get('testId')}:{event.get('stepIndex')}"
        _HANDOFFS[key] = waiter

        with session_scope() as db:
            run = db.get(Run, ctx.run_id)
            if run:
                run.status = RunStatus.BLOCKED

        await bus.publish(Event(
            type=Ev.RUN_HANDOFF, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={
                "handoff_key": key, "test_id": event.get("testId"),
                "step_index": event.get("stepIndex"), "reason": event.get("reason"),
                "url": event.get("url"), "instructions": event.get("instructions"),
            },
        ))

        try:
            await asyncio.wait_for(waiter, timeout=900)
            resumed = True
            reason = ""
        except TimeoutError:
            resumed = False
            reason = "no human resumed the session within 15 minutes"
        finally:
            _HANDOFFS.pop(key, None)
            with session_scope() as db:
                run = db.get(Run, ctx.run_id)
                if run and run.status == RunStatus.BLOCKED:
                    run.status = RunStatus.RUNNING

        return {"requestId": event["requestId"], "ok": resumed, "reason": reason}

    # --- exploratory sessions ------------------------------------------- #
    async def _on_explore_started(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"level": "explore", "message":
                     f"exploring {event.get('url')} — {event.get('charter', '')[:120]}"},
        ))

    async def _on_explore_decide(self, event: dict, ctx: RunContext) -> dict:
        from ..services import exploration

        return await exploration.decide(event, project_id=ctx.project_id)

    async def _on_explore_step(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"level": "explore", "message":
                     f"{event.get('step'):>3} {event.get('action')} "
                     f"{event.get('target', '')[:40]} — {event.get('rationale', '')[:90]}"},
        ))

    async def _on_explore_log(self, event: dict, ctx: RunContext) -> None:
        await bus.publish(Event(
            type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"level": "explore", "message": event.get("message", "")},
        ))

    async def _on_explore_finished(self, event: dict, ctx: RunContext) -> None:
        from ..services import exploration

        await exploration.finish(
            session_id=event.get("sessionId", ""), project_id=ctx.project_id, payload=event
        )

    async def _on_visual_snapshot(self, event: dict, ctx: RunContext) -> None:
        from ..intelligence.visual import record_snapshot

        with session_scope() as db:
            result = await record_snapshot(
                db, provider=self.provider, project_id=ctx.project_id,
                name=event.get("name", ""), image_path=event.get("path", ""),
                aria_snapshot=event.get("ariaSnapshot", ""), url=event.get("url", ""),
                run_id=ctx.run_id,
                run_test_id=ctx.run_test_ids.get(event.get("testId", "")),
                browser=event.get("browser", "chromium"),
            )

        if result.get("baseline_created"):
            message = f"visual baseline established for '{result['name']}'"
        elif result.get("changed"):
            message = f"visual change on '{result['name']}' ({result['severity']}): {result['summary']}"
        else:
            message = f"visual check passed for '{result['name']}'"

        await bus.publish(Event(
            type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={"level": "visual", "message": message},
        ))
        if result.get("needs_human"):
            await bus.publish(Event(
                type=Ev.NOTIFICATION, project_id=ctx.project_id, run_id=ctx.run_id,
                payload={"kind": "visual_review", **result},
            ))

    async def _on_handoff_started(self, event: dict, ctx: RunContext) -> None:
        return None

    async def _on_handoff_ended(self, event: dict, ctx: RunContext) -> None:
        return None

    async def _on_self_test(self, event: dict, ctx: RunContext) -> None:
        return None

    async def _on_self_test_done(self, event: dict, ctx: RunContext) -> None:
        return None

    # ------------------------------------------------------------------ #
    async def _finish(
        self,
        run_id: str,
        project_id: str,
        *,
        error: str = "",
        exit_code: int | None = None,
        stderr_tail: list[str] | None = None,
        expected: int = 0,
    ) -> dict:
        from ..intelligence.triage import triage_run

        with session_scope() as db:
            run = db.get(Run, run_id)
            if run is None:
                return {}

            # A run that produced no results is an infrastructure failure, not a
            # pass. Reporting green here would be the single most damaging bug
            # this system could have: it manufactures confidence from nothing.
            if not error and expected:
                executed = db.execute(
                    select(RunTest).where(RunTest.run_id == run_id)
                ).scalars().first()
                if executed is None:
                    detail = "; ".join(stderr_tail or []) or "the runner produced no output"
                    error = (
                        f"the runner exited (code {exit_code}) without executing any of the "
                        f"{expected} selected test(s), so this run proves nothing. {detail}"
                    )
            run.finished_at = utcnow()
            run.duration_ms = int(
                ((run.finished_at - run.started_at).total_seconds() * 1000) if run.started_at else 0
            )
            if error:
                run.status = RunStatus.ERROR
                run.error = error
            else:
                summary = triage_run(db, run)
                run.triage = summary
                totals = run.totals or {}
                # `summary["flaky"]` is a list of entries, not a count - reading
                # it as a number silently mis-states the run's outcome.
                flaky_count = len(summary.get("flaky") or [])
                real_failures = len(summary.get("new") or []) + len(summary.get("test_defect") or [])
                if summary.get("blocked"):
                    run.status = RunStatus.BLOCKED
                elif totals.get("failed", 0) or totals.get("error", 0):
                    run.status = RunStatus.FLAKY if (flaky_count and not real_failures) else RunStatus.FAILED
                elif totals.get("needs_review", 0):
                    run.status = RunStatus.NEEDS_REVIEW
                else:
                    run.status = RunStatus.PASSED
            payload = {
                "run_id": run.id, "status": run.status, "totals": run.totals,
                "triage": run.triage, "duration_ms": run.duration_ms, "error": run.error,
            }

        await bus.publish(Event(
            type=Ev.RUN_FINISHED, project_id=project_id, run_id=run_id, payload=payload
        ))
        return payload

    async def _report_discovery(self, ctx: RunContext) -> None:
        if not any(ctx.discovered.values()):
            return
        await bus.publish(Event(
            type=Ev.RUN_LOG, project_id=ctx.project_id, run_id=ctx.run_id,
            payload={
                "level": "discovery",
                "message": (
                    f"App Model: +{ctx.discovered['screens']} screen(s), "
                    f"+{ctx.discovered['elements']} element(s), "
                    f"{ctx.discovered['links']} step(s) bound"
                ),
            },
        ))


# --------------------------------------------------------------------------- #
def resume_handoff(handoff_key: str) -> bool:
    """Called by the API when a human clicks Resume."""
    waiter = _HANDOFFS.get(handoff_key)
    if waiter is None or waiter.done():
        return False
    waiter.get_loop().call_soon_threadsafe(waiter.set_result, True)
    return True


def cancel_run(run_id: str) -> None:
    _CANCELLED.add(run_id)


def active_handoffs() -> list[str]:
    return list(_HANDOFFS)
