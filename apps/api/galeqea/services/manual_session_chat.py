"""Deterministic chat verbs for tester-authored exploratory (SBTM) sessions.

"start exploratory session on <charter>, 30 min" opens one; while it's running,
"note: …" and "bug: …" append timestamped entries; "end session" produces the report.
The note/bug verbs only fire while a session is active, so they never hijack normal chat.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..models import Project, User
from . import manual_session


def try_handle(db: Session, project: Project, text: str, user: User) -> tuple[str, list[dict]] | None:
    t = text.strip()
    low = t.lower()

    m = re.search(r"start\s+(?:an?\s+)?(?:exploratory|sbtm|exploration)\s+session\s+"
                  r"(?:on|for|about)?\s*(.*)$", low)
    if m:
        rest = m.group(1).strip()
        minutes = 30
        mm = re.search(r",?\s*(\d+)\s*min", rest)
        if mm:
            minutes = int(mm.group(1))
            rest = rest[:mm.start()].strip().rstrip(",")
        # preserve the original casing of the charter
        charter = t[t.lower().find(rest):][:len(rest)] if rest else "Exploratory session"
        try:
            session = manual_session.start(db, project, charter=charter or "Exploratory session",
                                           minutes=minutes, actor=user)
        except manual_session.ManualSessionError as exc:
            return (str(exc), [])
        db.commit()
        return (f"Started a {minutes}-minute exploratory session. Charter: “{session.charter}”. "
                "Log findings as you go with \"note: …\" and \"bug: …\", then \"end session\".",
                [manual_session.session_card(session)])

    if re.search(r"\bend\s+(?:the\s+)?(?:exploratory|sbtm|exploration)?\s*session\b", low) or \
       low in ("end session", "stop session"):
        try:
            rep = manual_session.end(db, project, actor=user)
        except manual_session.ManualSessionError as exc:
            return (str(exc), [])
        db.commit()
        from ..models import ExplorationSession
        session = db.get(ExplorationSession, rep["session_id"])
        return (f"Session ended: {rep['notes']} note(s), {rep['bugs']} bug(s) over "
                f"{rep['elapsed_minutes']} min.",
                [manual_session.session_card(session)])

    # note:/bug: only while a session is active, so normal chat is untouched
    m = re.match(r"(note|bug|observation|idea|question)\s*[:\-]\s*(.+)$", t, re.I)
    if m and manual_session.active(db, project.id) is not None:
        kind = "bug" if m.group(1).lower() == "bug" else "note"
        session = manual_session.add_entry(db, project, kind=kind, text=m.group(2).strip())
        db.commit()
        rep = manual_session.report(session)
        total = rep["notes"] + rep["bugs"]
        timebox = rep.get("timebox_minutes")
        left = ""
        if timebox is not None:
            remaining = timebox - rep["elapsed_minutes"]
            left = (f" · {remaining} min left" if remaining >= 0
                    else f" · {-remaining} min over")
        verb = "Bug logged" if kind == "bug" else "Noted"
        return (f"{verb} · {total} entr{'y' if total == 1 else 'ies'} in session "
                f"“{session.charter}”{left}.",
                [manual_session.session_card(session)])

    return None
