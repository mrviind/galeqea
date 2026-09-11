"""Push a run's results to a test-management system: Xray, Zephyr Scale or TestRail.

One gate (``results.push``), one idempotency contract: a ``RunExport`` row keyed by
(run, provider, target) means re-pushing the same run to the same place returns the
stored execution key instead of creating a duplicate. Each backend matches results to
its own test identity (Xray key / definition, Zephyr PROJ-T key, TestRail case id).
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit
from ..integrations import testrail as testrail_be
from ..integrations import xray as xray_be
from ..integrations import zephyr as zephyr_be
from ..integrations.base import IntegrationError
from ..models import IntegrationConnection, Project, Run, RunExport

_PROVIDERS = ("xray", "zephyr_scale", "testrail")


class ResultsPushError(RuntimeError):
    """Safe-to-surface problem pushing results."""


def _idempotency_key(run_id: str, provider: str, target: str) -> str:
    return hashlib.sha256(f"{run_id}|{provider}|{target}".encode()).hexdigest()[:32]


def connected_result_targets(db: Session, project_id: str) -> list[str]:
    return [c.provider for c in db.execute(select(IntegrationConnection).where(
        IntegrationConnection.project_id == project_id,
        IntegrationConnection.provider.in_(_PROVIDERS),
        IntegrationConnection.enabled.is_(True))).scalars()]


def push(db: Session, project: Project, *, run_id: str, provider: str, target: str = "",
         environments: str = "", actor=None, force: bool = False) -> dict:
    if provider not in _PROVIDERS:
        raise ResultsPushError(f"unknown results target {provider!r} "
                               f"(one of {', '.join(_PROVIDERS)})")
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        raise ResultsPushError("unknown run")

    idem = _idempotency_key(run_id, provider, target)
    existing = db.execute(select(RunExport).where(
        RunExport.run_id == run_id, RunExport.provider == provider,
        RunExport.idempotency_key == idem)).scalars().first()
    if existing is not None and existing.status == "pushed" and not force:
        return {"ok": True, "provider": provider, "exec_key": existing.exec_key,
                "url": existing.url, "pushed": existing.pushed, "cached": True}

    try:
        if provider == "xray":
            out = xray_be.push_results(db, project_id=project.id, run_id=run_id,
                                       test_plan_key=target, environments=environments)
        elif provider == "zephyr_scale":
            junit = _junit(db, project, run)
            out = zephyr_be.push_junit(db, project_id=project.id, junit_xml=junit,
                                       cycle_name=target)
        else:  # testrail
            out = testrail_be.push_results(db, project_id=project.id, run_id=run_id,
                                           run_name=target)
    except IntegrationError as exc:
        raise ResultsPushError(str(exc)) from exc

    row = existing or RunExport(project_id=project.id, run_id=run_id, provider=provider,
                                target=target, idempotency_key=idem)
    row.exec_key = out.get("exec_key") or out.get("test_execution_key") or ""
    row.url = out.get("url", "")
    row.pushed = out.get("pushed", 0)
    row.status = "pushed"
    if existing is None:
        db.add(row)
    db.flush()
    audit.record(db, action="results.pushed", actor_id=getattr(actor, "id", None),
                 actor_label=getattr(actor, "email", ""), project_id=project.id,
                 resource_type="run", resource_id=run_id,
                 detail={"provider": provider, "target": target,
                         "exec_key": row.exec_key, "pushed": row.pushed})
    return {"ok": True, "provider": provider, "exec_key": row.exec_key, "url": row.url,
            "pushed": row.pushed, "cached": False}


def _junit(db: Session, project: Project, run: Run) -> str:
    from ..reports.runs import build_run_report, run_report_junit
    return run_report_junit(build_run_report(db, project, run))


def export_card(row: RunExport) -> dict:
    return {"type": "results_push_card", "provider": row.provider, "exec_key": row.exec_key,
            "url": row.url, "pushed": row.pushed, "target": row.target, "status": row.status}
