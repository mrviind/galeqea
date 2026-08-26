"""Agent memory.

Memory that a user cannot open, correct or delete is a liability in a tool whose
output goes into a compliance record: a wrong "fact" learned once would quietly
distort every future test proposal. So every item here is a row with a key, a
source, a confidence and an owner - readable, editable, exportable and
deletable from the UI - and writes go through the approval gate like any other.

Recall blends semantic similarity with pinning and recency. The embedding falls
back to the deterministic local encoder, so memory works in No-AI mode too.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import MemoryItem
from ..models.base import utcnow
from .embeddings import cosine, local_embed

MAX_CONTEXT_ITEMS = 8


@dataclass(slots=True)
class MemoryStore:
    db: Session
    project_id: str

    # ------------------------------------------------------------------ #
    def write(
        self,
        *,
        key: str,
        content: str,
        kind: str = "fact",
        source: str = "",
        confidence: float = 0.7,
        scope: str = "project",
        session_id: str | None = None,
        created_by_kind: str = "agent",
    ) -> MemoryItem:
        existing = self.db.execute(
            select(MemoryItem).where(
                MemoryItem.project_id == self.project_id,
                MemoryItem.key == key,
                MemoryItem.scope == scope,
            )
        ).scalar_one_or_none()

        if existing:
            # Updating in place rather than appending keeps memory from silently
            # accumulating contradictory versions of the same fact.
            existing.content = content
            existing.confidence = max(existing.confidence, confidence)
            existing.source = source or existing.source
            existing.embedding = local_embed(f"{key} {content}")
            self.db.flush()
            return existing

        item = MemoryItem(
            project_id=self.project_id,
            scope=scope,
            session_id=session_id,
            kind=kind,
            key=key,
            content=content,
            source=source,
            confidence=confidence,
            created_by_kind=created_by_kind,
            embedding=local_embed(f"{key} {content}"),
        )
        self.db.add(item)
        self.db.flush()
        return item

    def forget(self, memory_id: str) -> bool:
        item = self.db.get(MemoryItem, memory_id)
        if item is None:
            return False
        self.db.delete(item)
        self.db.flush()
        return True

    # ------------------------------------------------------------------ #
    def recall(self, query: str, *, limit: int = 5, kinds: list[str] | None = None) -> list[MemoryItem]:
        stmt = select(MemoryItem).where(MemoryItem.project_id == self.project_id)
        if kinds:
            stmt = stmt.where(MemoryItem.kind.in_(kinds))
        items = list(self.db.execute(stmt).scalars())
        if not items:
            return []

        query_vec = local_embed(query)
        now = utcnow()
        scored: list[tuple[float, MemoryItem]] = []
        for item in items:
            if item.expires_at and item.expires_at < now:
                continue
            similarity = cosine(query_vec, item.embedding or []) if item.embedding else 0.0
            score = similarity * item.confidence
            if item.pinned:
                score += 0.5  # a pinned item is a user instruction, not a guess
            scored.append((score, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        chosen = [item for score, item in scored[:limit] if score > 0.05 or item.pinned]
        for item in chosen:
            item.hits += 1
        self.db.flush()
        return chosen

    def context_block(self, query: str, *, limit: int = MAX_CONTEXT_ITEMS) -> str:
        """Render recalled memory for a system prompt, provenance included."""
        items = self.recall(query, limit=limit)
        if not items:
            return ""
        lines = [
            f"- [{i.kind}] {i.key}: {i.content}"
            + (f" (source: {i.source})" if i.source else "")
            for i in items
        ]
        return (
            "Project knowledge recalled from memory. Treat these as prior "
            "observations that may be stale, not as ground truth - verify before "
            "relying on any of them:\n" + "\n".join(lines)
        )

    def export(self) -> list[dict]:
        items = list(
            self.db.execute(
                select(MemoryItem).where(MemoryItem.project_id == self.project_id)
                .order_by(MemoryItem.created_at)
            ).scalars()
        )
        return [
            {
                "key": i.key, "kind": i.kind, "content": i.content, "source": i.source,
                "confidence": i.confidence, "pinned": i.pinned, "hits": i.hits,
                "created_by": i.created_by_kind, "created_at": i.created_at.isoformat(),
            }
            for i in items
        ]
