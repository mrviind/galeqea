"""WO#8-A read_artifact: on-demand ranged reads, confined to the artifacts dir."""

from __future__ import annotations

import asyncio

import galeqea.ai.toolset  # noqa: F401  (registers read_artifact)
from galeqea.ai.tools import ToolContext, registry
from galeqea.config import settings


def _read(db, project, args):
    ctx = ToolContext(db=db, project_id=project.id, actor_kind="agent")
    return asyncio.run(registry.invoke("read_artifact", args, ctx))


def test_reads_a_line_range(db, project):
    art = settings.artifacts_dir / "explore-x" / "aria.txt"
    art.parent.mkdir(parents=True, exist_ok=True)
    art.write_text("\n".join(f"line {i}" for i in range(1, 201)))

    out = _read(db, project, {"path": str(art), "range": "10-12"})
    assert out["ok"] and out["total_lines"] == 200
    assert out["content"] == "line 10\nline 11\nline 12"
    assert out["truncated"] is True

    # default range caps at the first 120 lines
    d = _read(db, project, {"path": str(art)})
    assert d["range"] == "1-120"


def test_confined_to_artifacts_dir(db, project):
    out = _read(db, project, {"path": "/etc/passwd"})
    assert out["ok"] is False and "outside" in out["error"]


def test_missing_artifact(db, project):
    out = _read(db, project, {"path": str(settings.artifacts_dir / "nope.txt")})
    assert out["ok"] is False and "no such" in out["error"]
