"""In-container P2-1 verification: trigger a run + webhook + PDF that a WORKER executes."""
import asyncio
from galeqea.db import session_scope
from galeqea.models import Project, User, Journey, Run, WebhookEndpoint
from galeqea.services import journeys, plan_approval, website_test as wt, test_plan as tp
from galeqea.core import approvals

TARGET = "http://demo"

async def main():
    with session_scope() as db:
        proj = db.query(Project).first()
        if proj is None:
            proj = Project(key="DEMO", name="Demo"); db.add(proj); db.flush()
        pid = proj.id
        human = db.query(User).filter(User.role.in_(["owner","admin"]), User.is_machine==False).first()
        did = human.id if human else None
        # webhook endpoint (points at the demo nginx; a 404 is fine, it proves the worker attempts it)
        if not db.query(WebhookEndpoint).filter_by(project_id=pid).first():
            from galeqea.services.webhooks import new_secret
            db.add(WebhookEndpoint(project_id=pid, url="http://demo/webhook", secret=new_secret(),
                                   events=["run.finished"], active=True))
        db.flush()
        disc = wt.discover_pages(TARGET, limit=3)
        print("DISCOVER ok:", disc.get("ok"), "pages:", len(disc.get("pages", [])))
        typed = tp.build_typed_plan(disc, version=1)
        plan = {**wt.build_plan(disc), "typed": typed}
        jrn = journeys.start(db, pid, disc.get("base", TARGET))
        from galeqea.models import JourneyStage
        journeys.advance(db, jrn, JourneyStage.PLAN, discovery=disc, plan=plan, plan_version=1)
        req = plan_approval.request_plan_approval(db, proj, jrn)
        db.commit(); jid, rid = jrn.id, req.id

    with session_scope() as db:
        approvals.approve(db, rid, db.get(User, did))
        keys = db.get(Journey, jid).test_ids
        print("APPROVED tests:", len(keys))

    from galeqea.services.runs import start_run
    with session_scope() as db:
        jrn = db.get(Journey, jid)
        started = await start_run(db, project_id=jrn.project_id, selection={"keys": jrn.test_ids},
                                  base_url=TARGET, trigger="floor", title="P2-1 worker verify")
        print("RUN enqueued: #", started.number, "id", started.id, "job", (started.ci_metadata or {}).get("job_id"))

asyncio.run(main())
