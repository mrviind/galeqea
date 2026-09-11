"""Provenance for the running instance: *what code is this process serving?*

That is the question a stale, ``--reload``-less uvicorn silently gets wrong: the
process keeps answering long after the files on disk moved on, and nothing in the
UI betrays the gap. Here it is made visible. Everything is resolved **once at
import** (a running server's provenance does not change under it) and surfaced in
three places that a drifting deploy would otherwise hide behind:

* ``GET /api/health``: ``sha`` / ``built_at`` / ``started_at``
* the ``up`` banner and ``doctor`` output
* the UI's Settings → About line

``sha`` carries a ``-dirty`` suffix when the working tree has uncommitted changes,
so "the SHA looks right" can never quietly mean "…but that's not what's running".
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

from . import __version__

# apps/api/galeqea/buildinfo.py → parents: [galeqea, api, apps, <repo root>]
_REPO_ROOT = Path(__file__).resolve().parents[3]
_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
#: Source trees whose files, if newer than this process, mean the running API is
#: stale: someone edited code and didn't restart. The Python package and the
#: runner it shells out to are the API process's own executable surface.
_SOURCE_ROOTS = [Path(__file__).resolve().parent, _REPO_ROOT / "apps" / "runner" / "src"]
_SOURCE_EXTS = {".py", ".mjs", ".js"}


def newest_source_mtime() -> float:
    """The newest mtime across the API's own source. Read live, so a file changed
    *after* the process started is what flags the drift."""
    newest = 0.0
    for root in _SOURCE_ROOTS:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if p.suffix not in _SOURCE_EXTS:
                continue
            if "__pycache__" in p.parts or "node_modules" in p.parts:
                continue
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                pass
    return newest


def _git_sha() -> str:
    """Short HEAD SHA of the code on disk, ``-dirty`` if the tree is modified.

    A packaged install with no ``.git`` (or no ``git`` on PATH) returns
    ``"unknown"`` rather than raising; provenance is best-effort, never a
    startup blocker.
    """
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=2,
        )
        if head.returncode != 0:
            return "unknown"
        sha = head.stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=2,
        )
        if dirty.returncode == 0 and dirty.stdout.strip():
            sha += "-dirty"
        return sha or "unknown"
    except Exception:  # noqa: BLE001 - git absence must never break startup
        return "unknown"


def _ui_built_at() -> str | None:
    """When the served UI bundle was last built (mtime of ``web/dist/index.html``).

    This is the value that catches the classic drift: a server started before the
    UI was rebuilt serves a bundle older than the code it thinks it ships.
    """
    index = _WEB_DIST / "index.html"
    try:
        if index.exists():
            return datetime.fromtimestamp(
                index.stat().st_mtime, tz=UTC
            ).isoformat()
    except Exception:  # noqa: BLE001
        pass
    return None


# SHA and start time belong to the running *process* and cannot change under
# it, so they are snapshotted once. The UI bundle is different: it is served as
# static files that can be rebuilt while the server keeps running, so its build
# time is read fresh on every call; otherwise a rebuild-without-restart would be
# reported as the old time, misdirecting the very drift check this exists for.
VERSION: str = __version__
SHA: str = _git_sha()
UI_BUILT_AT: str | None = _ui_built_at()  # snapshot at start, for the `up` banner
_STARTED = datetime.now(UTC)
STARTED_AT: str = _STARTED.isoformat()
_STARTED_TS: float = _STARTED.timestamp()


def is_stale() -> bool:
    """True when a source file is newer than this process, so the API is serving old
    code. The classic drift, now caught for the API and not only the UI bundle.

    A packaged install (no ``.git``, a built image or a wheel) has immutable
    source that cannot change under the process, so drift is impossible; there the
    image-layer mtimes are meaningless and the check is disabled. Drift detection
    is for a developer running an un-restarted server against files they just
    edited, which by definition has a git tree.
    """
    if SHA == "unknown":
        return False
    newest = newest_source_mtime()
    return bool(newest and newest > _STARTED_TS + 1)  # 1s slack for the write race


def as_dict() -> dict:
    """The provenance block for ``/api/health``: UI build and source drift live."""
    newest = newest_source_mtime()
    return {
        "version": VERSION,
        "sha": SHA,
        "built_at": _ui_built_at(),
        "started_at": STARTED_AT,
        "api_started_at": STARTED_AT,
        "newest_source_mtime": (datetime.fromtimestamp(newest, tz=UTC).isoformat()
                                if newest else None),
        "stale_api": is_stale(),
    }


def as_line() -> str:
    """One-line human form: ``0.1.0 · a1b2c3d · UI built 2026-08-29T…``."""
    built = _ui_built_at() or "unknown"
    return f"{VERSION} · {SHA} · UI built {built}"
