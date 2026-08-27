"""QE Agent command line.

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
    help="AI-first, local-first, open-source test automation.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def up(
    host: str = typer.Option(settings.host, help="Bind address."),
    port: int = typer.Option(settings.port, help="Port to serve on."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (development)."),
) -> None:
    """Start QE Agent: API, UI and scheduler in one process."""
    import uvicorn

    from .db import init_db

    init_db()
    _preflight()
    console.print(
        Panel.fit(
            f"[bold]QE Agent {__version__}[/bold]\n"
            f"[dim]open[/dim] http://{host}:{port}\n"
            f"[dim]mode[/dim] {settings.ai_mode.value}"
            f"{'' if settings.ai_enabled else '  (every core feature works without a model)'}\n"
            f"[dim]data[/dim] {settings.home}",
            border_style="magenta",
        )
    )
    uvicorn.run(
        "galeqea.main:app", host=host, port=port, reload=reload,
        log_level="info", access_log=False,
    )


@app.command()
def doctor() -> None:
    """Check that everything QE Agent needs is present, and say what to do if not."""
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
                      "QE Agent still runs — the affected features are disabled, not broken.")
    raise typer.Exit(0)


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
def export(
    test: str = typer.Argument(..., help="Test key or id."),
    target: str = typer.Option("playwright", help="playwright|playwright_python|robot|cucumber"),
    project: str = typer.Option("", help="Project id or key."),
    out: Path | None = typer.Option(None, help="Write to a file instead of stdout."),
) -> None:
    """Export a stored test as runnable source with no QE Agent dependency."""
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
            table.add_row(f"{m['slug']} {m['version']}", m["kind"], ", ".join(m["permissions"]) or "—")
    console.print(table)


@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"QE Agent {__version__}")


# --------------------------------------------------------------------------- #
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
         else "No-AI mode — core features fully available"),
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
        console.print("[dim]Everything else — authoring, review, reporting, MCP — works now.[/dim]\n")


if __name__ == "__main__":
    app()
