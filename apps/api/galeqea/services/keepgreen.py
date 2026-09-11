"""Keep-green: turn a one-off Golden Path run into an ongoing guarantee.

Three routes to the same promise: a **schedule** (a recurring run of the built
suite), a **GitHub Actions** workflow (gate every PR on the readiness verdict), and
**routing** (fire the existing webhooks, Slack included, when a run finishes or
fails). All deterministic; the schedule reuses the runner, the workflow is a
copy-paste artifact, the routing is the webhook system already in place.
"""

from __future__ import annotations

from urllib.parse import urlparse

from ..core import audit
from ..models import Schedule
from .scheduler import describe_cron, register

#: Sensible default: every weekday at 08:00 UTC.
DEFAULT_CRON = "0 8 * * 1-5"


def _automated_keys(db, project_id: str, journey) -> list[str]:
    from .golden_run import runnable_keys
    return runnable_keys(db, project_id, journey)


def setup_schedule(db, project, journey, *, cron: str = DEFAULT_CRON,
                   actor: str | None = None) -> dict:
    """Create (or update) a recurring run of the journey's built suite."""
    from sqlalchemy import select
    name = f"Keep green: {journey.target}"
    existing = db.execute(select(Schedule).where(
        Schedule.project_id == project.id, Schedule.name == name)).scalar_one_or_none()
    keys = _automated_keys(db, project.id, journey)
    fields = dict(cron=cron, timezone="UTC", environment=journey.environment,
                  selection={"keys": keys}, enabled=True)
    if existing is not None:
        for k, v in fields.items():
            setattr(existing, k, v)
        schedule = existing
    else:
        schedule = Schedule(project_id=project.id, name=name, created_by=actor, **fields)
        db.add(schedule)
    db.flush()
    register(schedule)
    audit.record(db, action="schedule.created", actor_id=actor, project_id=project.id,
                 resource_type="schedule", resource_id=schedule.id,
                 detail={"name": name, "cron": cron, "tests": len(keys)})
    db.commit()
    return {"id": schedule.id, "name": name, "cron": cron,
            "human_cron": describe_cron(cron), "tests": len(keys)}


def github_action_yaml(project, journey) -> str:
    """A GitHub Actions workflow that runs the Golden Path against the target on a
    schedule and on every PR, and fails the check unless readiness is Go."""
    target = journey.target
    host = urlparse(target).hostname or "your-app"
    slug = host.replace(".", "-")
    return f"""# .github/workflows/galeqea-{slug}.yml
name: GaleQEA ({host})
on:
  schedule:
    - cron: "{DEFAULT_CRON}"
  pull_request:
  workflow_dispatch:

jobs:
  golden-path:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
          cache: pip
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
      - name: Install GaleQEA and Chromium
        run: |
          pip install galeqea
          npx playwright install --with-deps chromium
      - name: Run the Golden Path
        env:
          GALEQEA_TARGET: {target}
          # Login credentials come from repo secrets, never the command line:
          GALEQEA_CRED_FORM_USER: ${{{{ secrets.GALEQEA_CRED_FORM_USER }}}}
          GALEQEA_CRED_FORM_PASS: ${{{{ secrets.GALEQEA_CRED_FORM_PASS }}}}
          GALEQEA_CRED_BASIC_USER: ${{{{ secrets.GALEQEA_CRED_BASIC_USER }}}}
          GALEQEA_CRED_BASIC_PASS: ${{{{ secrets.GALEQEA_CRED_BASIC_PASS }}}}
        run: |
          galeqea test "$GALEQEA_TARGET" --wait --format junit --out results.xml --credentials-from-env
          galeqea readiness "$GALEQEA_TARGET" --format md > readiness.md
          galeqea runs report latest --format md --out report.md
      - name: Gate the build on readiness
        run: galeqea readiness "{target}" --fail-on no_go
      - name: Upload results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: galeqea-results
          path: |
            results.xml
            report.md
      - name: Comment the report on the PR
        if: always() && github.event_name == 'pull_request'
        run: gh pr comment "${{{{ github.event.pull_request.number }}}}" --body-file report.md
        env:
          GH_TOKEN: ${{{{ secrets.GITHUB_TOKEN }}}}
"""


def keep_green(db, project, journey, *, cron: str = DEFAULT_CRON,
               actor: str | None = None) -> dict:
    """Set up the schedule and return everything the Keep-green card needs."""
    from sqlalchemy import func, select

    from ..models import WebhookEndpoint
    schedule = setup_schedule(db, project, journey, cron=cron, actor=actor)
    webhook_count = db.execute(select(func.count(WebhookEndpoint.id)).where(
        WebhookEndpoint.project_id == project.id, WebhookEndpoint.active.is_(True))).scalar_one()
    return {
        "target": journey.target,
        "schedule": schedule,
        "github_action": github_action_yaml(project, journey),
        "webhooks_active": int(webhook_count),
    }
