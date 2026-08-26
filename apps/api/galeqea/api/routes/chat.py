from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...ai.orchestrator import Orchestrator, persist_exchange
from ...core.events import Ev, Event, bus
from ...db import get_db
from ...models import ChatSession, Project, User
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}/chat", tags=["chat"])


@router.get("/sessions")
def list_sessions(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    rows = db.execute(
        select(ChatSession).where(
            ChatSession.project_id == project.id, ChatSession.archived.is_(False)
        ).order_by(ChatSession.updated_at.desc())
    ).scalars()
    return [
        {"id": s.id, "title": s.title, "pinned": s.pinned, "usage": s.token_usage,
         "updated_at": s.updated_at.isoformat(), "messages": len(s.messages)}
        for s in rows
    ]


@router.post("/sessions")
def create_session(
    payload: dict | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    payload = payload or {}
    session = ChatSession(
        project_id=project.id, user_id=user.id,
        title=payload.get("title") or "New conversation",
    )
    db.add(session)
    db.commit()
    return {"id": session.id, "title": session.title}


@router.get("/sessions/{session_id}")
def read_session(
    session_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)
):
    session = db.get(ChatSession, session_id)
    if session is None or session.project_id != project.id:
        raise HTTPException(404, "conversation not found")
    return {
        "id": session.id, "title": session.title, "usage": session.token_usage,
        "messages": [
            {"id": m.id, "role": m.role, "agent_role": m.agent_role, "content": m.content,
             "blocks": m.blocks, "tool_calls": m.tool_calls, "attachments": m.attachments,
             "usage": m.usage, "error": m.error, "at": m.created_at.isoformat()}
            for m in sorted(session.messages, key=lambda m: m.created_at)
        ],
    }


@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: str,
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    session = db.get(ChatSession, session_id)
    if session is None or session.project_id != project.id:
        raise HTTPException(404, "conversation not found")
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "message text is required")

    # The client may name a provider and model; it can never supply a key.
    # for_selection unseals the matching credential from the vault server-side,
    # enforces the monthly budget, and falls back to the project default if the
    # selection is stale.
    from ...ai.providers.registry import for_selection

    chosen = for_selection(
        db, project.id, payload.get("provider"), payload.get("model"),
    )
    orchestrator = Orchestrator(db, provider=chosen, project_id=project.id)
    reply = await orchestrator.handle(
        session=session, user=user, text=text, attachments=payload.get("attachments"),
    )
    user_message, assistant_message = persist_exchange(
        db, session, user_text=text, reply=reply, user_id=user.id,
        attachments=payload.get("attachments"),
    )
    db.commit()

    await bus.publish(Event(
        type=Ev.CHAT_MESSAGE, project_id=project.id, session_id=session.id,
        payload={
            "id": assistant_message.id, "role": "assistant", "content": reply.text,
            "blocks": reply.blocks, "path": reply.path, "warnings": reply.warnings,
        },
    ))

    return {
        "user_message_id": user_message.id,
        "message": {
            "id": assistant_message.id, "role": "assistant", "content": reply.text,
            "blocks": reply.blocks, "tool_calls": reply.tool_calls,
            "at": assistant_message.created_at.isoformat(),
        },
        **reply.as_dict(),
    }


@router.delete("/sessions/{session_id}")
def archive_session(
    session_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)
):
    session = db.get(ChatSession, session_id)
    if session is None or session.project_id != project.id:
        raise HTTPException(404, "conversation not found")
    session.archived = True
    db.commit()
    return {"archived": session_id}


@router.post("/preview-command")
def preview_command(
    payload: dict, project: Project = Depends(get_project), db: Session = Depends(get_db)
):
    """What would this message do? Used by the UI to show intent before sending."""
    from ...ai.router import route
    from ...models import Run

    last = db.execute(
        select(Run).where(Run.project_id == project.id)
        .order_by(Run.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    routed = route(payload.get("text", ""), last_run_id=last.id if last else None)
    return {
        "intent": routed.intent, "confidence": routed.confidence, "tool": routed.tool,
        "arguments": routed.arguments, "explanation": routed.explanation,
        "path": "router" if routed.confident else "agent",
    }
