"""GaleQEA command line.

``galeqea up`` is the whole getting-started story: it initialises storage,
checks the runner, and serves the API and the built UI from one process on one
port. No database to provision, no services to wire, no cloud account.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .config import settings

app = typer.Typer(
    name="galeqea",
    help="AI-first, open-source test automation agent.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

db_app = typer.Typer(name="db", help="Database migrations.", no_args_is_help=True)
app.add_typer(db_app, name="db")

plan_app = typer.Typer(name="plan", help="Golden Path plan proposal and approval.",
                       no_args_is_help=True)
app.add_typer(plan_app, name="plan")

release_app = typer.Typer(name="release", help="Release milestones and reports.",
                          no_args_is_help=True)
app.add_typer(release_app, name="release")


@release_app.command("list")
def release_list(project: str = typer.Option("", help="Project id or key.")) -> None:
    """List a project's release milestones."""
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import Milestone

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        rows = db.execute(select(Milestone).where(Milestone.project_id == proj.id)
                          .order_by(Milestone.created_at.desc())).scalars().all()
        if not rows:
            console.print("[dim]No releases yet. Create one in the chat "
                          "(\"create release 1.4\").[/dim]")
            return
        for m in rows:
            signed = f" · signed {m.signoff['decision']}" if m.signoff else ""
            console.print(f"[magenta]{m.version}[/magenta]  {m.name}  ([dim]{m.status}{signed}[/dim])")


@release_app.command("report")
def release_report_cmd(
    version: str = typer.Argument(..., help="The release version, e.g. 1.4."),
    project: str = typer.Option("", help="Project id or key."),
    fmt: str = typer.Option("md", "--format", help="md|json|html report format."),
    out: Path | None = typer.Option(None, help="Write the report to a file."),
) -> None:
    """Render a release/milestone report (readiness + metrics)."""
    import json as _json

    from .db import init_db, session_scope
    from .reports import release_report
    from .services import release as release_svc

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        m = release_svc.milestone_for(db, proj.id, version)
        if m is None:
            console.print(f"[red]No release {version}.[/red]")
            raise typer.Exit(3)
        if fmt == "json":
            content = _json.dumps(release_report.build(db, m), indent=2, default=str)
        elif fmt == "html":
            content = release_report.to_html(db, m)
        else:
            content = release_report.to_markdown(db, m)
    _emit(content, out, f"release report for {version} ({fmt})")


defect_app = typer.Typer(name="defect", help="File and track defects from failures.",
                         no_args_is_help=True)
app.add_typer(defect_app, name="defect")


@defect_app.command("list")
def defect_list(project: str = typer.Option("", help="Project id or key.")) -> None:
    """List a project's tracked defects and their cached status."""
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import DefectMap

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        rows = db.execute(select(DefectMap).where(DefectMap.project_id == proj.id)
                          .order_by(DefectMap.last_seen.desc())).scalars().all()
        if not rows:
            console.print("[dim]No defects filed yet.[/dim]")
            return
        for dm in rows:
            state = "resolved" if dm.resolved else (dm.status_cached or "open")
            seen = f" · seen {dm.reopened_count + 1}×" if dm.reopened_count else ""
            console.print(f"[magenta]{dm.issue_key}[/magenta] ([dim]{dm.provider}[/dim]) "
                          f"{state}{seen}  {dm.url}")


@defect_app.command("file")
def defect_file(
    run_test_id: str = typer.Argument(..., help="The failing result (RunTest id)."),
    project: str = typer.Option("", help="Project id or key."),
    provider: str = typer.Option("", help="jira|github|gitlab (default: the connected one)."),
) -> None:
    """Propose filing a bug for a failing result. Files an approval; a human accepts it."""
    from .core import approvals
    from .db import init_db, session_scope
    from .models import RunTest
    from .services import defects

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        rt = db.get(RunTest, run_test_id)
        if rt is None or rt.run is None or rt.run.project_id != proj.id:
            console.print("[red]No such result in this project.[/red]")
            raise typer.Exit(3)
        tracker = defects.connected_tracker(db, proj.id, prefer=provider or None)
        if tracker is None:
            console.print("[red]No issue tracker connected.[/red] Add one in Settings → "
                          "Integrations.")
            raise typer.Exit(4)
        actor = _cli_principal(db)
        req = approvals.request(
            db, action="defect.create",
            title=f"File a bug for {rt.test_key or rt.title} ({tracker})",
            project_id=proj.id, resource_type="run_test", resource_id=rt.id,
            payload={"arguments": {"run_test_id": rt.id, "provider": tracker}},
            requested_by=getattr(actor, "id", None), requested_by_kind="human",
        )
        console.print(f"[green]Queued[/green] a {tracker} bug as approval "
                      f"[magenta]{req.id}[/magenta]. Accept it in the Approvals view "
                      f"or with `galeqea approvals`.")


jira_app = typer.Typer(name="jira", help="Jira story import and staleness checks.",
                       no_args_is_help=True)
app.add_typer(jira_app, name="jira")


@jira_app.command("import")
def jira_import_cmd(
    selector: str = typer.Argument(..., help="Sprint name / 'open sprint' / 'fixVersion X' / JQL."),
    project: str = typer.Option("", help="Project id or key."),
) -> None:
    """Import Jira stories as requirements (idempotent by issue key)."""
    from .db import init_db, session_scope
    from .services import story_import

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        try:
            out = story_import.import_stories(db, proj, selector=selector,
                                              actor=_cli_principal(db))
        except story_import.StoryImportError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(3) from exc
        console.print(f"[green]Imported[/green] {out['count']} issue(s): "
                      f"{len(out['imported'])} new, {len(out['updated'])} updated, "
                      f"{len(out['stale'])} stale.  [dim]JQL:[/dim] {out['jql']}")


@jira_app.command("stale")
def jira_stale_cmd(project: str = typer.Option("", help="Project id or key.")) -> None:
    """Re-check imported stories and flag any whose description changed."""
    from .db import init_db, session_scope
    from .services import story_import

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        try:
            out = story_import.detect_stale(db, proj)
        except story_import.StoryImportError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(3) from exc
        if out["changed"]:
            console.print(f"[yellow]{len(out['changed'])} changed:[/yellow] "
                          f"{', '.join(out['changed'])}")
        else:
            console.print(f"[dim]Checked {out['checked']}. None changed.[/dim]")


results_app = typer.Typer(name="results", help="Push run results to a test-management system.",
                          no_args_is_help=True)
app.add_typer(results_app, name="results")


@results_app.command("push")
def results_push_cmd(
    run: int = typer.Argument(..., help="The run NUMBER to push."),
    provider: str = typer.Option(..., help="xray | zephyr_scale | testrail."),
    project: str = typer.Option("", help="Project id or key."),
    target: str = typer.Option("", help="Xray test plan key / Zephyr cycle / TestRail run name."),
    environments: str = typer.Option("", help="Xray test environments, ';'-separated."),
) -> None:
    """Push a run's results directly (CLI is a trusted principal, so no approval queue)."""
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import Run
    from .services import results_push

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        run_row = db.execute(select(Run).where(Run.project_id == proj.id,
                                               Run.number == run)).scalars().first()
        if run_row is None:
            console.print(f"[red]No run #{run}.[/red]")
            raise typer.Exit(3)
        try:
            out = results_push.push(db, proj, run_id=run_row.id, provider=provider,
                                    target=target, environments=environments,
                                    actor=_cli_principal(db))
        except results_push.ResultsPushError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(4) from exc
        cached = " [dim](already pushed, returned cached key)[/dim]" if out.get("cached") else ""
        console.print(f"[green]Pushed[/green] {out['pushed']} result(s) to {provider} "
                      f"→ [magenta]{out['exec_key'] or '-'}[/magenta]{cached}")


@release_app.command("publish")
def release_publish_cmd(
    version: str = typer.Argument(..., help="The release version, e.g. 1.4."),
    space: str = typer.Option("", "--space", help="Confluence space key (else the connection's)."),
    project: str = typer.Option("", help="Project id or key."),
) -> None:
    """Publish a release report to Confluence (CLI is trusted, so no approval queue)."""
    from .db import init_db, session_scope
    from .services import confluence_publish

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        try:
            out = confluence_publish.publish_release_report(
                db, proj, version=version, space_key=space, actor=_cli_principal(db))
        except confluence_publish.PublishError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(3) from exc
        console.print(f"[green]Published[/green] release {version} → "
                      f"[magenta]{out['url']}[/magenta] (v{out['page_version']})")


@app.command("evidence")
def evidence_cmd(
    result_id: str = typer.Argument(..., help="The failing result (RunTest id)."),
    out: Path = typer.Option(Path("evidence.zip"), "--out", "-o", help="Where to write the zip."),
    project: str = typer.Option("", help="Project id or key."),
) -> None:
    """Write a one-click evidence bundle (zip) for a failing result."""
    from .db import init_db, session_scope
    from .services import evidence_bundle

    init_db()
    with session_scope() as db:
        try:
            data, name = evidence_bundle.build_zip(db, result_id)
        except evidence_bundle.EvidenceError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(3) from exc
        target = out if out.suffix == ".zip" else out / name
        target.write_bytes(data)
        console.print(f"[green]Wrote[/green] {target} ({len(data)} bytes)")


bench_app = typer.Typer(name="bench", help="Token-efficiency benchmarks.",
                        no_args_is_help=True)
app.add_typer(bench_app, name="bench")


@bench_app.command("tokens")
def bench_tokens(fmt: str = typer.Option("", "--format", help="'json' for machine output.")) -> None:
    """Measure page-state token reduction (WO#8). Non-zero exit if under target."""
    import subprocess
    from pathlib import Path
    bench = Path(__file__).resolve().parents[3] / "apps" / "runner" / "bench-tokens.mjs"
    if not bench.is_file():
        console.print("[red]bench-tokens.mjs not found[/red]")
        raise typer.Exit(2)
    args = ["node", str(bench)] + (["--json"] if fmt == "json" else [])
    raise typer.Exit(subprocess.call(args))


import_app = typer.Typer(name="import", help="Import tests/results from other tools.",
                         no_args_is_help=True)
app.add_typer(import_app, name="import")


@import_app.command("file")
def import_file_cmd(
    path: Path = typer.Argument(..., help="A .feature, .csv, or JUnit .xml file."),
    project: str = typer.Option("", help="Project id or key."),
    fmt: str = typer.Option("", "--format", help="Override format (gherkin|csv|junit)."),
    mapping: str = typer.Option("", help="CSV column mapping as JSON, e.g. '{\"title\":\"Name\"}'."),
) -> None:
    """Import test cases (→ proposals) or results (→ a run) from a file."""
    import json as _json

    from .db import init_db, session_scope
    from .services import tms_import

    init_db()
    if not path.is_file():
        console.print(f"[red]No such file: {path}[/red]")
        raise typer.Exit(2)
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        try:
            out = tms_import.import_file(
                db, proj, filename=path.name, content=path.read_text(errors="replace"),
                actor=_cli_principal(db), fmt=fmt,
                mapping=_json.loads(mapping) if mapping else None)
        except tms_import.TmsImportError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(3) from exc
        if out.get("needs_mapping"):
            console.print(f"[yellow]CSV needs a column mapping.[/yellow] Headers: "
                          f"{', '.join(out['headers'])}\nSuggested: {out['suggested']}\n"
                          f"Re-run with --mapping '{_json.dumps(out['suggested'])}'.")
            return
        if out.get("run_id"):
            console.print(f"[green]Imported[/green] {out['count']} result(s) → run "
                          f"#{out['run_number']} ({out['passed']} passed, {out['failed']} failed).")
        else:
            console.print(f"[green]Imported[/green] {out['count']} test case(s) as proposals "
                          f"from {out['source']}. Review them in the board.")


def _cli_principal(db):
    """The human deciding from the CLI: the API-token's user (set GALEQEA_API_TOKEN,
    the multi-user/CI path) or, on a single-user install, the local owner."""
    import os

    from .core.security import _bootstrap_owner, resolve_api_token

    raw = os.environ.get("GALEQEA_API_TOKEN", "")
    if raw:
        resolved = resolve_api_token(db, raw)
        if resolved is None:
            console.print("[red]GALEQEA_API_TOKEN is invalid or revoked.[/red]")
            raise typer.Exit(2)
        return resolved[0]
    if settings.single_user_mode:
        return _bootstrap_owner(db)
    console.print("[red]Approving needs a human principal.[/red] Set GALEQEA_API_TOKEN "
                  "to a human user's API token.")
    raise typer.Exit(2)


@plan_app.command("propose")
def plan_propose(
    url: str = typer.Argument(..., help="The URL to propose a Golden Path plan for."),
    project: str = typer.Option("", help="Project id or key."),
) -> None:
    """Crawl a site and file its Golden Path plan for approval (deterministic, no model)."""
    from .db import init_db, session_scope
    from .services import plan_approval

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        try:
            journey, req = plan_approval.propose(db, proj, url)
        except ValueError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        console.print(f"[green]Proposed[/green] a Golden Path plan for {journey.target}")
        console.print(f"[dim]{req.summary}[/dim]")
        console.print(f"Approval id: [magenta]{req.id}[/magenta]")
        console.print(f"Approve with: [cyan]galeqea plan approve {url}[/cyan]")


@plan_app.command("approve")
def plan_approve(
    target: str = typer.Argument(..., help="The target URL (or a journey id) whose plan to approve."),
    project: str = typer.Option("", help="Project id or key."),
    comment: str = typer.Option("", help="An optional decision note."),
) -> None:
    """Approve a pending Golden Path plan, using the same gate the UI and API use."""
    from .core import approvals
    from .core.approvals import ApprovalError, SelfApprovalError
    from .db import init_db, session_scope
    from .models import Journey
    from .services import journeys, plan_approval

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        journey = db.get(Journey, target) if target.startswith(("jrn", "jou")) else \
            journeys.for_target(db, proj.id, target)
        if journey is None:
            console.print(f"[red]No journey for {target}.[/red] Propose a plan first.")
            raise typer.Exit(3)
        req = plan_approval.pending_for_journey(db, journey)
        if req is None:
            console.print(f"[yellow]No plan is pending approval for {journey.target}.[/yellow]")
            raise typer.Exit(3)
        principal = _cli_principal(db)
        try:
            outcome = approvals.approve(db, req.id, principal, comment=comment)
        except SelfApprovalError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        except ApprovalError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
        keys = outcome.result.get("keys", [])
        console.print(f"[green]Approved[/green] the plan for {journey.target}. "
                      f"{len(keys)} test(s) now runnable. Run: [cyan]galeqea test {journey.target}[/cyan]")


@db_app.command("upgrade")
def db_upgrade(revision: str = typer.Argument("head", help="Target revision.")) -> None:
    """Apply migrations up to a revision (default: head)."""
    from alembic import command

    from .db import _alembic_cfg
    command.upgrade(_alembic_cfg(), revision)
    console.print(f"[green]migrated to[/green] {revision}")


@db_app.command("current")
def db_current() -> None:
    """Show the revision the database is currently at."""
    from alembic import command

    from .db import _alembic_cfg
    command.current(_alembic_cfg(), verbose=True)


@db_app.command("history")
def db_history() -> None:
    """Show the migration history."""
    from alembic import command

    from .db import _alembic_cfg
    command.history(_alembic_cfg())


@app.command()
def up(
    host: str = typer.Option(settings.host, help="Bind address."),
    port: int = typer.Option(settings.port, help="Port to serve on."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (development)."),
) -> None:
    """Start GaleQEA: API, UI and scheduler in one process."""
    import uvicorn

    from . import buildinfo
    from .db import run_migrations

    run_migrations()  # apply any pending schema migrations before serving
    _preflight()
    console.print(
        Panel.fit(
            f"[bold]GaleQEA {__version__}[/bold]\n"
            f"[dim]open[/dim] http://{host}:{port}\n"
            f"[dim]build[/dim] {buildinfo.SHA} · UI built {buildinfo.UI_BUILT_AT or 'unknown'}\n"
            f"[dim]mode[/dim] {settings.ai_mode.value}"
            f"{'' if settings.ai_enabled else '  (every core feature works without a model)'}\n"
            f"[dim]data[/dim] {settings.home}",
            border_style="magenta",
        )
    )
    if buildinfo.UI_BUILT_AT is None:
        console.print(
            f"[yellow]No built UI found[/yellow]. Run `make build` so :{port} serves the app, "
            "not just the API."
        )
    # A fresh `up` is by definition current; this only fires if the process is
    # started against sources that somehow post-date it, and reminds the operator
    # the drift guard is live (see /readyz `stale_api` and `galeqea doctor`).
    if buildinfo.is_stale():
        console.print("[bold yellow]Warning:[/bold yellow] source files are newer than this "
                      "process. Restart if you just edited code.")
    uvicorn.run(
        "galeqea.main:app", host=host, port=port, reload=reload,
        log_level="info", access_log=False,
    )


@app.command()
def worker(
    concurrency: int = typer.Option(0, help="Parallel jobs (0 = GALEQEA_WORKER_CONCURRENCY)."),
) -> None:
    """Run a durable background-job worker for the Postgres (`full`) profile.

    On the zero-config SQLite install the in-process queue runs *inside* ``galeqea up``,
    so a separate worker is unnecessary. This command says so and exits cleanly. On
    Postgres it consumes runs, retention, PDF renders and webhook deliveries with
    LISTEN/NOTIFY + SKIP LOCKED, and scales by running more copies."""
    import asyncio

    from .config import settings
    from .db import run_migrations
    from .jobs import get_queue

    run_migrations()
    queue = get_queue()
    if queue.kind != "procrastinate":
        console.print("[dim]In-process queue (SQLite): background jobs run inside "
                      "`galeqea up`. No separate worker is needed.[/dim]")
        raise typer.Exit(0)

    n = concurrency or settings.worker_concurrency
    console.print(Panel.fit(
        f"[bold]GaleQEA worker[/bold]\nqueue: procrastinate (Postgres)\nconcurrency: {n}",
        border_style="magenta"))

    async def _go() -> None:
        from .core import bus_bridge
        await queue.apply_schema()        # idempotent, creates procrastinate tables
        bus_bridge.install_worker_sink()  # mirror this worker's run events to the web
        await queue.run_worker(concurrency=n)

    try:
        asyncio.run(_go())
    except KeyboardInterrupt:
        console.print("worker stopped")


@app.command()
def doctor() -> None:
    """Check that everything GaleQEA needs is present, and say what to do if not."""
    from . import buildinfo

    console.print(f"[dim]build[/dim] {buildinfo.as_line()}\n")
    table = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
    for column in ("Check", "Status", "Detail"):
        table.add_column(column)

    ok = True
    for label, passed, detail in _checks():
        ok = ok and passed
        table.add_row(label, "[green]ok[/green]" if passed else "[yellow]missing[/yellow]", detail)

    console.print(table)
    if not ok:
        console.print("\n[yellow]Some optional capabilities are unavailable.[/yellow] "
                      "GaleQEA still runs. The affected features are disabled, not broken.")

    # Drift guard: is a *running* API serving code older than the files on disk?
    # doctor inspects the live server (not itself), so this catches the classic
    # "edited a .py, forgot to restart uvicorn" that unit tests can't.
    stale = _running_api_is_stale()
    if stale is True:
        console.print(
            f"\n[bold red]STALE API[/bold red]: the server on :{settings.port} is serving "
            "code older than the source on disk. Restart it; it is not running what you edited."
        )
        raise typer.Exit(2)
    if stale is False:
        console.print(f"[green]API fresh[/green]. The server on :{settings.port} is serving current code.")
    raise typer.Exit(0)


def _running_api_is_stale() -> bool | None:
    """True/False if a local API answers /api/health; None if none is running."""
    import json as _json
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{settings.port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=1) as resp:  # noqa: S310 - localhost only
            return bool(_json.loads(resp.read()).get("stale_api"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


@app.command()
def mcp(project: str = typer.Option("", help="Project id or key. Defaults to the first project.")) -> None:
    """Run the MCP server over stdio, for Claude Code, Cursor, VS Code and others."""
    from .mcp_server.stdio import main

    main(project)


@app.command("mcp-config")
def mcp_config() -> None:
    """Print the MCP client configuration snippet to paste into your host."""
    console.print_json(json.dumps({
        "mcpServers": {
            "galeqea": {
                "command": sys.executable,
                "args": ["-m", "galeqea.cli", "mcp"],
                "env": {"GALEQEA_HOME": str(settings.home)},
            }
        }
    }, indent=2))


@app.command()
def run(
    selection: str = typer.Argument("", help='Plain English, e.g. "the smoke tests".'),
    project: str = typer.Option("", help="Project id or key."),
    environment: str = typer.Option("", help="Environment name."),
    tags: str = typer.Option("", help="Comma-separated tags."),
    changed: str = typer.Option("", help="Comma-separated changed paths; enables predictive selection."),
    wait: bool = typer.Option(True, help="Wait for the run to finish."),
) -> None:
    """Start a run from the terminal or from CI."""
    import asyncio

    from .db import init_db, session_scope

    init_db()

    async def _go() -> int:
        with session_scope() as db:
            proj = _resolve_project(db, project)
            if proj is None:
                console.print("[red]No project found.[/red] Create one in the UI first.")
                return 2

            picker: dict = {}
            if changed:
                from .intelligence.selection import select_for_change

                result = select_for_change(db, proj.id, changed_paths=changed.split(","))
                picker = {"test_ids": result["selected_ids"]}
                console.print(f"[dim]{result['coverage_note']}[/dim]")
            else:
                if tags:
                    picker["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
                if selection:
                    picker["text"] = selection

            from .services.runs import start_run

            started = await start_run(
                db, project_id=proj.id, selection=picker, environment=environment,
                trigger="cli", command=selection, title=selection or "CLI run",
            )
            run_id, number = started.id, started.number

        console.print(f"[magenta]Run #{number}[/magenta] started ({run_id})")
        if not wait:
            return 0


        from .models import TERMINAL_RUN_STATES, Run

        while True:
            await asyncio.sleep(1.5)
            with session_scope() as db:
                current = db.get(Run, run_id)
                status, totals = current.status, current.totals or {}
                headline = (current.triage or {}).get("headline", "")
                error = current.error
            if status in {s.value for s in TERMINAL_RUN_STATES} or status in {"skipped", "blocked"}:
                break

        colour = "green" if status == "passed" else "yellow" if status in {"flaky", "needs_review"} else "red"
        console.print(f"[{colour}]{status}[/{colour}]  {json.dumps(totals)}")
        if headline:
            console.print(f"[dim]{headline}[/dim]")
        if error:
            console.print(f"[red]{error}[/red]")
        # Non-zero exit on failure so CI fails the build.
        return 0 if status in {"passed", "flaky"} else 1

    raise typer.Exit(asyncio.run(_go()))


@app.command()
def test(
    url: str = typer.Argument(..., help="The URL to run the Golden Path against."),
    project: str = typer.Option("", help="Project id or key."),
    env: str = typer.Option("", help="Environment name."),
    wait: bool = typer.Option(True, help="Wait for the run to finish."),
    fmt: str = typer.Option("junit", "--format", help="junit|json|md report format."),
    out: Path | None = typer.Option(None, help="Write the report to a file."),
    credentials_from_env: bool = typer.Option(
        False, help="Load login credentials from GALEQEA_CRED_{FORM,BASIC}_{USER,PASS}."),
) -> None:
    """Run the Golden Path for a URL headlessly (for CI). Reuses the human-approved
    plan for that target; exits 3 if none has been approved. It never auto-approves."""
    import asyncio

    from .db import init_db, session_scope

    init_db()

    async def _go() -> int:
        from .services import access
        from .services.runs import start_run

        with session_scope() as db:
            proj = _resolve_project(db, project)
            if proj is None:
                console.print("[red]No project found.[/red]")
                return 2
            journey, keys = _ci_plan_keys(db, proj, url)
            if not keys:
                console.print(
                    f"[red]No approved plan for {url}.[/red] Approve one in the UI "
                    "(or run the Golden Path there once), then CI can reuse it. "
                    "GaleQEA never auto-approves a plan in CI.")
                return 3
            if credentials_from_env and journey:
                _load_credentials_from_env(db, proj, journey)
                await access.perform_login(db, project=proj, journey=journey)
            started = await start_run(
                db, project_id=proj.id, selection={"keys": keys},
                environment=env or journey.environment, base_url=url, trigger="ci",
                title=f"Golden Path: {url} (CI)", command=f"galeqea test {url}",
                auth=access.run_auth(journey))
            run_id = started.id
            console.print(f"[magenta]Run #{started.number}[/magenta] started against {url}")

        status = await _await_run(run_id) if wait else "running"

        from .models import Run
        from .reports import report_v2
        from .reports.runs import run_report_junit
        with session_scope() as db:
            proj = _resolve_project(db, project)
            run = db.get(Run, run_id)
            report = report_v2.build(db, proj, run)
            content = _render(report, fmt, report_v2.to_markdown, run_report_junit)
        _emit(content, out, f"golden-path report for {url} ({fmt})")
        return 0 if status in {"passed", "flaky"} else 1

    raise typer.Exit(asyncio.run(_go()))


@app.command()
def readiness(
    target: str = typer.Argument("latest", help="Run id, 'latest', or a URL."),
    project: str = typer.Option("", help="Project id or key."),
    fail_on: str = typer.Option("", "--fail-on", help="Exit 1 on this verdict (e.g. no_go)."),
    fmt: str = typer.Option("table", "--format", help="table|json|md."),
) -> None:
    """Print a run's Go/No-Go readiness. In CI, `--fail-on no_go` gates the build."""
    from .db import init_db, session_scope
    from .services import journeys, readiness

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print("[red]No project found.[/red]")
            raise typer.Exit(2)
        run = _resolve_run_target(db, proj, target)
        if run is None:
            console.print(f"[red]No run for {target!r}.[/red]")
            raise typer.Exit(2)
        journey = (journeys.for_target(db, proj.id, run.base_url)
                   or journeys.active_journey(db, proj.id))
        result = readiness.evaluate(db, journey, run)

    if fmt == "json":
        console.print_json(json.dumps(result))
    elif fmt == "md":
        console.print("\n".join(
            f"- {'PASS' if c['pass'] else 'FAIL'} {c['label']}: {c['actual']}"
            for c in result["criteria"]))
    else:
        table = Table(show_header=True, header_style="bold", box=None)
        for col in ("", "Criterion", "Actual", "Threshold"):
            table.add_column(col)
        for c in result["criteria"]:
            table.add_row("[green]✓[/green]" if c["pass"] else "[red]✗[/red]",
                          c["label"], str(c["actual"]), str(c["threshold"]))
        console.print(table)
    verdict = result["verdict"].replace("_", "-").upper()
    colour = "green" if result["verdict"] == "go" else "red"
    console.print(f"\nReadiness: [{colour}]{verdict}[/{colour}]")
    if fail_on and result["verdict"] == fail_on:
        raise typer.Exit(1)
    raise typer.Exit(0)


@app.command()
def export(
    test: str = typer.Argument(..., help="Test key or id."),
    target: str = typer.Option("playwright", help="playwright|playwright_python|robot|cucumber"),
    project: str = typer.Option("", help="Project id or key."),
    out: Path | None = typer.Option(None, help="Write to a file instead of stdout."),
) -> None:
    """Export a stored test as runnable source with no GaleQEA dependency."""
    from .ai.toolset import _lookup_test
    from .db import init_db, session_scope
    from .engine.codegen import render

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        case = _lookup_test(db, proj.id if proj else "", test)
        if case is None:
            console.print(f"[red]No test matching {test!r}.[/red]")
            raise typer.Exit(2)
        code = render(case, target=target,
                      base_url=(proj.environments or {}).get(proj.default_environment, "") if proj else "")
    if out:
        out.write_text(code)
        console.print(f"[green]wrote[/green] {out}")
    else:
        console.print(code)


@app.command()
def audit(
    project: str = typer.Option("", help="Project id or key."),
    verify_only: bool = typer.Option(False, help="Only print the chain verification result."),
) -> None:
    """Verify the audit ledger's hash chain and print recent entries."""
    from .core.audit import verify_chain
    from .db import init_db, session_scope

    init_db()
    with session_scope() as db:
        result = verify_chain(db)
        console.print(
            f"[green]Ledger verified[/green]: {result.checked} entries, chain intact."
            if result.ok else
            f"[red]LEDGER BROKEN[/red] at entry {result.first_bad_seq}: {result.reason}"
        )
        if verify_only:
            raise typer.Exit(0 if result.ok else 1)

        from sqlalchemy import select

        from .models import AuditEvent

        rows = db.execute(
            select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(20)
        ).scalars()
        table = Table(box=None, header_style="bold")
        for column in ("seq", "when", "actor", "action", "resource"):
            table.add_column(column)
        for e in rows:
            table.add_row(
                str(e.seq), e.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                f"{e.actor_label or e.actor_id or 'system'} ({e.actor_kind})",
                e.action, f"{e.resource_type}:{(e.resource_id or '')[:10]}",
            )
        console.print(table)
    raise typer.Exit(0 if result.ok else 1)


@app.command()
def plugins(
    install: Path | None = typer.Option(None, help="Install a plugin from a directory."),
    enable: str = typer.Option("", help="Enable an installed plugin by slug."),
    grant: str = typer.Option("", help="Comma-separated capabilities to grant when enabling."),
) -> None:
    """Discover, install and enable plugins."""
    from .db import init_db, session_scope
    from .plugins import loader

    init_db()
    if install:
        with session_scope() as db:
            record = loader.install(db, install)
            console.print(f"[green]installed[/green] {record.slug} {record.version} "
                          f"[dim](disabled until you grant its capabilities)[/dim]")
        return
    if enable:
        with session_scope() as db:
            record = loader.enable(db, enable, granted=[g for g in grant.split(",") if g])
            console.print(f"[green]enabled[/green] {record.slug} with {record.granted_permissions}")
        return

    table = Table(box=None, header_style="bold")
    for column in ("plugin", "kind", "requests"):
        table.add_column(column)
    for found in loader.discover():
        if "error" in found:
            table.add_row(found["path"], "[red]invalid[/red]", found["error"])
        else:
            m = found["manifest"]
            table.add_row(f"{m['slug']} {m['version']}", m["kind"], ", ".join(m["permissions"]) or "-")
    console.print(table)


@app.command()
def version() -> None:
    """Print the version, code SHA and UI build time of this install."""
    from . import buildinfo

    console.print(f"GaleQEA {buildinfo.as_line()}")


@app.command()
def report(
    run: str = typer.Argument(..., help="Run number or id."),
    project: str = typer.Option("", help="Project id or key."),
    out: Path = typer.Option(Path("test-report.docx"), help="Output .docx file."),
) -> None:
    """Render a run's branded, client-ready Word test report (with screenshot evidence)."""
    from .db import init_db, session_scope
    from .reports.report_docx import build_run_docx

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        run_row = _resolve_run(db, proj, run)
        if run_row is None:
            console.print(f"[red]No run {run!r} in {proj.key}.[/red]")
            raise typer.Exit(2)
        data = build_run_docx(db, proj, run_row, target=run_row.base_url)
    out.write_bytes(data)
    console.print(f"[green]Wrote[/green] {out}  ({len(data) // 1024} KB) for run #{run_row.number}")


@app.command()
def reset(
    demo: bool = typer.Option(False, "--demo", help="Clear the demo project's approvals, chat and runs, then reseed clean starter requirements."),
    project: str = typer.Option("DEMO", help="Project key or id to reset."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Clear a project's transient data (approvals, chat, runs) and reseed a clean demo.

    Destructive for the *named project only*: it deletes that project's approval
    queue, chat history and run history (the junk that accumulates from poking at
    the demo) and nothing else. Other projects are never touched. It asks first
    unless --yes is given. Use it to get the demo project back to a clean slate.
    """
    from sqlalchemy import delete, select

    from .db import init_db, session_scope
    from .models import (
        ApprovalRequest,
        ChatMessage,
        ChatSession,
        Run,
        RunStepRecord,
        RunTest,
    )

    if not demo:
        console.print("[yellow]Nothing to do.[/yellow] Pass --demo to reset the demo project.")
        raise typer.Exit(0)

    init_db()
    with session_scope() as db:
        proj = _resolve_project(db, project)
        if proj is None:
            console.print(f"[red]No project {project!r}.[/red] Nothing was changed.")
            raise typer.Exit(2)
        if not yes:
            typer.confirm(
                f"This deletes all approvals, chat and runs for {proj.key} ({proj.name}). Continue?",
                abort=True,
            )
        pid = proj.id
        run_ids = select(Run.id).where(Run.project_id == pid).scalar_subquery()
        rt_ids = select(RunTest.id).where(RunTest.run_id.in_(run_ids)).scalar_subquery()
        sess_ids = select(ChatSession.id).where(ChatSession.project_id == pid).scalar_subquery()

        cleared = {
            "run steps": db.execute(delete(RunStepRecord).where(RunStepRecord.run_test_id.in_(rt_ids))).rowcount,
            "run tests": db.execute(delete(RunTest).where(RunTest.run_id.in_(run_ids))).rowcount,
            "runs": db.execute(delete(Run).where(Run.project_id == pid)).rowcount,
            "chat messages": db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(sess_ids))).rowcount,
            "chat sessions": db.execute(delete(ChatSession).where(ChatSession.project_id == pid)).rowcount,
            "approvals": db.execute(delete(ApprovalRequest).where(ApprovalRequest.project_id == pid)).rowcount,
        }
        seeded = _reseed_demo(db, proj)

    summary = ", ".join(f"{n} {name}" for name, n in cleared.items() if n) or "nothing to clear"
    console.print(f"[green]Reset {proj.key}[/green]. Cleared {summary}.")
    console.print(f"[dim]{seeded} starter requirement(s) in place; chat starts fresh.[/dim]")


@app.command()
def retention() -> None:
    """Expire artifacts past each project's retention window, now.

    The same sweep runs daily on the server. Set a project's window by PATCHing its
    settings with ``retention_days`` (unset = keep forever).
    """
    from .db import init_db
    from .services import retention as _retention

    init_db()
    summary = _retention.sweep_now()
    total = summary.pop("_total", 0)
    if total:
        detail = ", ".join(f"{k}: {n}" for k, n in summary.items())
        console.print(f"[green]Swept {total} artifact(s)[/green]: {detail}.")
    else:
        console.print("[dim]Nothing to expire. No project is past its retention window.[/dim]")


def _reseed_demo(db, proj) -> int:
    """Idempotently ensure a small, clean set of starter requirements. Returns the
    number seeded (0 if the project already has requirements, since reset never
    duplicates or overwrites real authored work)."""
    from sqlalchemy import select

    from .models import DocKind, RequirementDoc, RequirementItem

    if db.execute(
        select(RequirementItem).where(RequirementItem.project_id == proj.id).limit(1)
    ).scalar_one_or_none():
        return 0

    doc = RequirementDoc(project_id=proj.id, title="Starter requirements", kind=DocKind.REQUIREMENT)
    db.add(doc)
    db.flush()
    seed = [
        ("REQ-1", "critical", "A visitor can sign in with email and password",
         ["Valid credentials reach the dashboard", "Invalid credentials show an error and stay on the page"]),
        ("REQ-2", "high", "A signed-in user can update their profile",
         ["Saving valid changes persists them", "Empty required fields are rejected"]),
        ("REQ-3", "medium", "The home page loads for anonymous visitors",
         ["Returns 200 and renders the main content"]),
    ]
    for ref, risk, title, criteria in seed:
        db.add(RequirementItem(doc_id=doc.id, project_id=proj.id, ref=ref, risk=risk,
                               title=title, acceptance_criteria=criteria))
    return len(seed)


# --------------------------------------------------------------------------- #
async def _await_run(run_id: str, *, poll: float = 1.5, timeout: float = 900.0) -> str:
    """Poll a run to a terminal state and return its status (for `galeqea test`)."""
    import asyncio

    from .db import session_scope
    from .models import TERMINAL_RUN_STATES, Run

    waited = 0.0
    terminal = {s.value for s in TERMINAL_RUN_STATES} | {"skipped", "blocked"}
    while waited < timeout:
        await asyncio.sleep(poll)
        waited += poll
        with session_scope() as db:
            run = db.get(Run, run_id)
            if run and run.status in terminal:
                return run.status
    return "running"


def _load_credentials_from_env(db, project, journey) -> None:
    """Store login credentials passed to CI as env vars, never on the command line."""
    import os

    from .services import access
    form_u, form_p = os.environ.get("GALEQEA_CRED_FORM_USER"), os.environ.get("GALEQEA_CRED_FORM_PASS")
    if form_u and form_p:
        access.store_credentials(db, journey, username=form_u, password=form_p, kind="form")
    basic_u, basic_p = os.environ.get("GALEQEA_CRED_BASIC_USER"), os.environ.get("GALEQEA_CRED_BASIC_PASS")
    if basic_u and basic_p:
        access.store_credentials(db, journey, username=basic_u, password=basic_p, kind="basic")


def _ci_plan_keys(db, project, url: str):
    """(journey, keys) for a CI run of `url`, strictly target-scoped. Only the
    approved, automated tests of the journey for *this* target (matched by canonical
    target, and by each test's own provenance.journey_id) are eligible. An approved
    plan for a different target must never make this URL runnable."""
    from .models import TestCase, TestCategory, TestStatus
    from .services import journeys

    journey = journeys.for_target(db, project.id, url)
    if journey is None or not journey.test_ids:
        return None, []
    from sqlalchemy import select
    cases = db.execute(select(TestCase).where(
        TestCase.project_id == project.id, TestCase.key.in_(journey.test_ids))).scalars()
    keys = [c.key for c in cases
            if c.status == TestStatus.APPROVED
            and c.category == TestCategory.AUTOMATED
            and (c.provenance or {}).get("journey_id") == journey.id]
    return journey, keys


def _resolve_run_target(db, project, target: str):
    """A run by id, the latest golden-path/ci run ('latest'), or the latest run for a
    URL."""
    from sqlalchemy import desc, select

    from .models import Run
    if target == "latest":
        return db.execute(
            select(Run).where(Run.project_id == project.id,
                              Run.trigger.in_(("golden_path", "ci")))
            .order_by(desc(Run.number)).limit(1)).scalars().first()
    if target.startswith("http"):
        return db.execute(
            select(Run).where(Run.project_id == project.id, Run.base_url == target)
            .order_by(desc(Run.number)).limit(1)).scalars().first()
    return _resolve_run(db, project, target)


def _resolve_project(db, identifier: str):
    from sqlalchemy import select

    from .models import Project

    if identifier:
        found = db.get(Project, identifier)
        if found:
            return found
        return db.execute(
            select(Project).where(Project.key == identifier.upper())
        ).scalar_one_or_none()
    return db.execute(select(Project).limit(1)).scalar_one_or_none()


def _checks() -> list[tuple[str, bool, str]]:
    node = shutil.which(settings.runner_command)
    runner = Path(settings.runner_entry)
    node_modules = runner.parent.parent / "node_modules"
    web_dist = Path(__file__).resolve().parents[2] / "web" / "dist"

    node_version = ""
    if node:
        try:
            node_version = subprocess.run(
                [node, "--version"], capture_output=True, text=True, timeout=10
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            node_version = "unknown"

    return [
        ("Python", True, sys.version.split()[0]),
        ("Storage", settings.home.exists(), str(settings.home)),
        ("Database", True, settings.database_url.split("///")[-1]),
        ("Node", bool(node), node_version or "install Node 18+ to run browser tests"),
        ("Runner", runner.exists(), str(runner) if runner.exists() else "apps/runner is missing"),
        ("Runner deps", node_modules.exists(), "run `npm install` in apps/runner" if not node_modules.exists() else "installed"),
        ("Browsers", _browsers_installed(), "run `npx playwright install chromium`" if not _browsers_installed() else "installed"),
        ("Web UI", web_dist.exists(), "run `npm run build` in apps/web" if not web_dist.exists() else "built"),
        ("Model", settings.ai_enabled, settings.provider if settings.ai_enabled
         else "No-AI mode: core features fully available"),
    ]


def _browsers_installed() -> bool:
    for base in (Path.home() / "Library/Caches/ms-playwright", Path.home() / ".cache/ms-playwright"):
        if base.exists() and any(base.glob("chromium-*")):
            return True
    return False


def _preflight() -> None:
    problems = [
        f"  · {label}: {detail}" for label, passed, detail in _checks()
        if not passed and label in {"Node", "Runner deps", "Browsers"}
    ]
    if problems:
        console.print("[yellow]Browser execution is unavailable until these are resolved:[/yellow]")
        console.print("\n".join(problems))
        console.print("[dim]Everything else (authoring, review, reporting, MCP) works now.[/dim]\n")


# --------------------------------------------------------------------------- #
# Agent-friendly reporting surface.
#
# The rule for every command below: a command that produces a *document*
# (context, any report, an export) writes it to ``--out`` and prints only the
# path plus a one-line summary; without ``--out`` it prints the document. List
# commands print one terse line per row. Reads never call a model.
# --------------------------------------------------------------------------- #
def _line(text: str) -> None:
    """Print one line verbatim: no markup, no highlighting, never wrapped, so a
    path or a machine-readable row survives being piped or captured intact."""
    console.print(text, markup=False, highlight=False, soft_wrap=True)


def _emit(content: str, out: Path | None, summary: str) -> None:
    """Write a document to ``out`` (printing only path + summary), or to stdout."""
    if out:
        out.write_text(content)
        _line(f"wrote {out}: {summary}")
    else:
        _line(content)


def _render(report: dict, fmt: str, md_fn, junit_fn=None) -> str:
    """Render a report dict to the requested format, or exit(2) with a clear message."""
    if fmt == "json":
        return json.dumps(report, indent=2, default=str)
    if fmt == "md":
        return md_fn(report)
    if fmt == "junit":
        if junit_fn is None:
            console.print("[red]The junit format is only available for `runs report`.[/red]")
            raise typer.Exit(2)
        return junit_fn(report)
    console.print(f"[red]Unknown format {fmt!r}.[/red] Use json, md or junit.")
    raise typer.Exit(2)


def _project_or_exit(db, identifier: str):
    """Resolve a project or print a clear message and exit(2)."""
    proj = _resolve_project(db, identifier)
    if proj is None:
        console.print("[red]No project found.[/red]")
        raise typer.Exit(2)
    return proj


def _resolve_run(db, project, run_id: str):
    """Load a run by id, by number, or 'latest' within the project."""
    from sqlalchemy import desc, select

    from .models import Run

    if run_id == "latest":
        return db.execute(
            select(Run).where(Run.project_id == project.id)
            .order_by(desc(Run.number)).limit(1)).scalars().first()
    run = db.get(Run, run_id)
    if run is not None and run.project_id == project.id:
        return run
    if str(run_id).isdigit():
        return db.execute(
            select(Run).where(Run.project_id == project.id, Run.number == int(run_id))
        ).scalar_one_or_none()
    return None


def _run_line(run) -> str:
    totals = run.totals or {}
    return f"#{run.number} {run.status} {totals.get('passed', 0)}/{totals.get('total', 0)} {run.environment}"


def _project_report(project: str, fmt: str, out: Path | None, builder, md_fn, name: str) -> None:
    """Build a project-level report, render it, and emit it."""
    from .db import init_db, session_scope

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        report = builder(db, proj)
    _emit(_render(report, fmt, md_fn), out, f"{name} report ({fmt})")


@app.command()
def context(
    project: str = typer.Option("", help="Project id or key."),
    out: Path | None = typer.Option(None, help="Write to a file instead of stdout."),
) -> None:
    """Write a single-read project briefing (Markdown) for a coding agent."""
    from .db import init_db, session_scope
    from .reports.context import project_context_markdown

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        md = project_context_markdown(db, proj)
    _emit(md, out, "project briefing")


# --------------------------------------------------------------------------- #
runs_app = typer.Typer(help="Inspect runs and their reports.", no_args_is_help=True, add_completion=False)


@runs_app.command("list")
def runs_list(
    project: str = typer.Option("", help="Project id or key."),
    limit: int = typer.Option(20, help="How many recent runs to show."),
) -> None:
    """Recent runs, newest first."""
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import Run

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        for run in db.execute(
            select(Run).where(Run.project_id == proj.id).order_by(Run.number.desc()).limit(limit)
        ).scalars():
            _line(_run_line(run))


@runs_app.command("get")
def runs_get(
    run_id: str = typer.Argument(..., help="Run id or number."),
    project: str = typer.Option("", help="Project id or key."),
) -> None:
    """One run's summary line plus its failed test keys."""
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import RunTest

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        run = _resolve_run(db, proj, run_id)
        if run is None:
            console.print(f"[red]No run {run_id!r}.[/red]")
            raise typer.Exit(2)
        _line(_run_line(run))
        failed = db.execute(
            select(RunTest.test_key)
            .where(RunTest.run_id == run.id, RunTest.status.in_(["failed", "error"]))
            .order_by(RunTest.test_key)
        ).scalars().all()
    if failed:
        _line("failed: " + ", ".join(failed))
    else:
        console.print("[dim]no failed tests[/dim]")


@runs_app.command("report")
def runs_report(
    run_id: str = typer.Argument(..., help="Run id or number."),
    project: str = typer.Option("", help="Project id or key."),
    fmt: str = typer.Option("json", "--format", help="json|md|junit."),
    out: Path | None = typer.Option(None, help="Write to a file instead of stdout."),
) -> None:
    """Build a run report in json, md or junit (the canonical v2 report)."""
    from .db import init_db, session_scope
    from .reports import report_v2
    from .reports.runs import run_report_junit

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        run = _resolve_run(db, proj, run_id)
        if run is None:
            console.print(f"[red]No run {run_id!r}.[/red]")
            raise typer.Exit(2)
        report = report_v2.build(db, proj, run)
        number = run.number
    _emit(_render(report, fmt, report_v2.to_markdown, run_report_junit), out, f"run #{number} report ({fmt})")


app.add_typer(runs_app, name="runs")


# --------------------------------------------------------------------------- #
tests_app = typer.Typer(help="Inspect and export test cases.", no_args_is_help=True, add_completion=False)


@tests_app.command("list")
def tests_list(
    project: str = typer.Option("", help="Project id or key."),
) -> None:
    """Every test case: key, status and title."""
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import TestCase

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        for case in db.execute(
            select(TestCase).where(TestCase.project_id == proj.id).order_by(TestCase.key)
        ).scalars():
            tech = (case.provenance or {}).get("technique")
            extra = f"  covers={','.join(case.covers)}" if case.covers else ""
            extra += f"  [{tech}]" if tech else ""
            _line(f"{case.key} [{case.status}] {case.title}{extra}")


@tests_app.command("export")
def tests_export(
    test: str = typer.Argument(..., help="Test key or id."),
    target: str = typer.Option("playwright", help="playwright|playwright_python|robot|cucumber"),
    project: str = typer.Option("", help="Project id or key."),
    out: Path | None = typer.Option(None, help="Write to a file instead of stdout."),
) -> None:
    """Export a stored test as runnable source (same rendering as `galeqea export`)."""
    from .ai.toolset import _lookup_test
    from .db import init_db, session_scope
    from .engine.codegen import render

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        case = _lookup_test(db, proj.id, test)
        if case is None:
            console.print(f"[red]No test matching {test!r}.[/red]")
            raise typer.Exit(2)
        code = render(case, target=target,
                      base_url=(proj.environments or {}).get(proj.default_environment, ""))
        key = case.key
    _emit(code, out, f"{key} as {target}")



@tests_app.command("export-bulk")
def tests_export_bulk(
    fmt: str = typer.Option("testrail_csv", "--format",
                            help="testrail_csv|testrail_steps_csv|xray_csv|qase_json|gherkin|playwright"),
    ref: str = typer.Option("", help="Scope to a requirement ref or rule id."),
    status: str = typer.Option("", help="Scope to a test status."),
    project: str = typer.Option("", help="Project id or key."),
    out: Path | None = typer.Option(None, help="Write to a file instead of stdout."),
) -> None:
    """Export many cases to a TMS/import format (TestRail/Xray CSV, Qase JSON, Gherkin)."""
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .exporters import export_cases
    from .models import TestCase

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        stmt = select(TestCase).where(TestCase.project_id == proj.id)
        if status:
            stmt = stmt.where(TestCase.status == status)
        cases = list(db.execute(stmt.order_by(TestCase.key)).scalars())
        if ref:
            needle = ref.upper()
            cases = [c for c in cases if needle in {r.upper() for r in (c.requirement_refs or [])}
                     or any(needle in cov.upper() for cov in (c.covers or []))]
        base = (proj.environments or {}).get(proj.default_environment, "")
        content, filename = export_cases(cases, fmt, base_url=base)
    _emit(content, out, f"{len(cases)} case(s) as {fmt}")


app.add_typer(tests_app, name="tests")

requirements_app = typer.Typer(help="Tidy requirement documents.", no_args_is_help=True,
                               add_completion=False)


@requirements_app.command("dedupe")
def requirements_dedupe(
    project: str = typer.Option("", help="Project id or key."),
    apply: bool = typer.Option(False, "--apply", help="Actually remove duplicates (default: dry run)."),
) -> None:
    """Remove byte-identical duplicate requirement documents, keeping the newest."""
    from .db import init_db, session_scope
    from .services import requirements as req

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        result = req.dedupe_requirement_docs(db, project_id=proj.id, apply=apply)
        db.commit()
    verb = "Removed" if apply else "Would remove"
    _line(f"{verb} {sum(len(g['duplicates']) for g in result['duplicate_groups'])} duplicate "
          f"doc(s) across {len(result['duplicate_groups'])} group(s)."
          + ("" if apply else "  Re-run with --apply."))


@requirements_app.command("archive-doc")
def requirements_archive_doc(
    doc_id: str = typer.Argument(..., help="Requirement document id."),
    project: str = typer.Option("", help="Project id or key."),
) -> None:
    """Archive (hide from the matrix, reversibly) one requirement document."""
    from .db import init_db, session_scope
    from .services import requirements as req

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        result = req.archive_requirement_doc(db, project_id=proj.id, doc_id=doc_id)
        db.commit()
    if not result.get("ok"):
        console.print(f"[red]{result.get('error')}[/red]")
        raise typer.Exit(2)
    _line(f"Archived {result['title']} ({result['doc_id']}).")


app.add_typer(requirements_app, name="requirements")


# --------------------------------------------------------------------------- #
@app.command()
def coverage(
    project: str = typer.Option("", help="Project id or key."),
    fmt: str = typer.Option("json", "--format", help="json|md."),
    out: Path | None = typer.Option(None, help="Write to a file instead of stdout."),
) -> None:
    """Requirements-to-tests coverage for a project."""
    from .reports.coverage import build_coverage_report, coverage_report_markdown

    _project_report(project, fmt, out, build_coverage_report, coverage_report_markdown, "coverage")


@app.command()
def flaky(
    project: str = typer.Option("", help="Project id or key."),
    fmt: str = typer.Option("json", "--format", help="json|md."),
    out: Path | None = typer.Option(None, help="Write to a file instead of stdout."),
) -> None:
    """The project's flakiest tests, ranked."""
    from .reports.flaky import build_flaky_report, flaky_report_markdown

    _project_report(project, fmt, out, build_flaky_report, flaky_report_markdown, "flaky")


@app.command()
def traceability(
    project: str = typer.Option("", help="Project id or key."),
    fmt: str = typer.Option("json", "--format", help="json|md."),
    out: Path | None = typer.Option(None, help="Write to a file instead of stdout."),
) -> None:
    """Requirement-to-test-to-run traceability for a project."""
    from .reports.traceability import build_traceability_report, traceability_report_markdown

    _project_report(project, fmt, out, build_traceability_report, traceability_report_markdown, "traceability")


@app.command()
def rca(
    run_id: str = typer.Argument(..., help="Run id or number."),
    project: str = typer.Option("", help="Project id or key."),
    fmt: str = typer.Option("json", "--format", help="json|md."),
    out: Path | None = typer.Option(None, help="Write to a file instead of stdout."),
) -> None:
    """Root-cause analysis for a run's failures."""
    from .db import init_db, session_scope
    from .reports.rca import build_rca_report, rca_report_markdown

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        run = _resolve_run(db, proj, run_id)
        if run is None:
            console.print(f"[red]No run {run_id!r}.[/red]")
            raise typer.Exit(2)
        report = build_rca_report(db, proj, run)
        number = run.number
    _emit(_render(report, fmt, rca_report_markdown), out, f"run #{number} rca ({fmt})")


# --------------------------------------------------------------------------- #
approvals_app = typer.Typer(help="Review the approval queue.", no_args_is_help=True, add_completion=False)


@approvals_app.command("list")
def approvals_list(
    project: str = typer.Option("", help="Project id or key."),
) -> None:
    """Pending approval requests, one per line."""
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import ApprovalRequest, ApprovalStatus

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        for a in db.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.project_id == proj.id, ApprovalRequest.status == ApprovalStatus.PENDING)
            .order_by(ApprovalRequest.created_at.desc())
        ).scalars():
            _line(f"{a.action} {a.resource_type}:{a.resource_id or ''} by {a.requested_by or 'unknown'}")


app.add_typer(approvals_app, name="approvals")


# --------------------------------------------------------------------------- #
schedule_app = typer.Typer(help="Inspect scheduled runs.", no_args_is_help=True, add_completion=False)


@schedule_app.command("list")
def schedule_list(
    project: str = typer.Option("", help="Project id or key."),
) -> None:
    """Scheduled runs, one per line."""
    from sqlalchemy import select

    from .db import init_db, session_scope
    from .models import Schedule

    init_db()
    with session_scope() as db:
        proj = _project_or_exit(db, project)
        for s in db.execute(
            select(Schedule).where(Schedule.project_id == proj.id).order_by(Schedule.name)
        ).scalars():
            _line(f"{s.name} {s.cron} {'on' if s.enabled else 'off'} {s.environment}")


app.add_typer(schedule_app, name="schedule")


if __name__ == "__main__":
    app()
