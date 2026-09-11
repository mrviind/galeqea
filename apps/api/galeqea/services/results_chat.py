"""Deterministic chat verb: "push run #42 to xray plan APP-10" → a gated results push."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import approvals
from ..models import Project, Run, User

_PROVIDER_ALIASES = {"xray": "xray", "zephyr": "zephyr_scale", "zephyr scale": "zephyr_scale",
                     "zephyr_scale": "zephyr_scale", "testrail": "testrail"}


def try_handle(db: Session, project: Project, text: str, user: User) -> tuple[str, list[dict]] | None:
    m = re.search(r"push\s+run\s+#?(\d+)\s+(?:results\s+)?(?:to|into)\s+"
                  r"(xray|zephyr(?:\s*scale)?|zephyr_scale|testrail)", text, re.I)
    if not m:
        return None
    number = int(m.group(1))
    provider = _PROVIDER_ALIASES[m.group(2).lower().replace("  ", " ").strip()]

    run = db.execute(select(Run).where(Run.project_id == project.id, Run.number == number)
                     ).scalars().first()
    if run is None:
        return (f"No run #{number} in this project.", [])

    # optional target: "plan APP-10", "cycle Regression", "run Smoke"
    target = ""
    mt = re.search(r"(?:plan|cycle|run|testplan)\s+([A-Za-z0-9][\w\- ]*)$", text, re.I)
    if mt:
        target = mt.group(1).strip()

    from ..services import results_push
    if provider not in results_push.connected_result_targets(db, project.id):
        pretty = {"zephyr_scale": "Zephyr Scale"}.get(provider, provider.title())
        return (f"{pretty} isn't connected. Add it under Settings → Integrations first.", [])

    req = approvals.request(
        db, action="results.push",
        title=f"Push run #{number} to {provider}" + (f" ({target})" if target else ""),
        project_id=project.id, resource_type="run", resource_id=run.id,
        payload={"arguments": {"run_id": run.id, "provider": provider, "target": target}},
        requested_by=user.id, requested_by_kind="agent")
    db.commit()
    return (f"Queued a push of run #{number} to {provider}"
            + (f" ({target})" if target else "")
            + f". Approval #{req.id} is waiting. Nothing is sent until a human accepts.",
            [{"type": "approval_pending", "approval_id": req.id, "action": "results.push",
              "provider": provider, "run": number}])
