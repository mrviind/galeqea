"""Run artifacts through the storage seam.

The runner writes screenshots / video / trace / HAR / console logs to local files
and reports their paths. This records each as an ``Artifact`` row and, when the
configured backend is S3, uploads the bytes to the bucket and stores the object
key instead of a laptop-local path, so an artifact survives the container it was
produced in and can be served or expired uniformly.

On the local backend nothing changes: the artifact keeps its filesystem path and
is served straight off disk (the fast path for a single-box install).
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Artifact
from .storage import get_storage


def guess_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def record_artifact(
    db: Session, *, run_id: str, run_test_id: str | None, art: dict
) -> Artifact:
    """Persist one runner artifact, routing its bytes through the storage backend.

    Returns the (unflushed) ``Artifact`` added to ``db``.
    """
    src = Path(art.get("path", ""))
    kind = art.get("kind", "screenshot")
    label = art.get("label", "")
    size = src.stat().st_size if src.exists() else 0

    record = Artifact(run_id=run_id, run_test_id=run_test_id, kind=kind, label=label)
    storage = get_storage()

    if storage.is_remote and src.exists():
        # An object key that groups by run then test, so a whole run's artifacts
        # are one prefix the retention sweeper can drop in one call.
        key = f"artifacts/{run_id}/{run_test_id or 'run'}/{src.name}"
        storage.put(key, src.read_bytes(), content_type=guess_type(src.name))
        record.path = key
        record.size_bytes = size
        record.meta = {"backend": "s3", "filename": src.name}
        # The bytes live in the bucket now; the local copy is scratch.
        try:
            src.unlink()
        except OSError:
            pass
    else:
        record.path = str(src)
        record.size_bytes = size
        record.meta = {"backend": "local"}

    db.add(record)
    return record


def open_artifact(artifact: Artifact) -> tuple[bytes, str, str]:
    """Return (bytes, content_type, filename) for a stored artifact, from whichever
    backend holds it. Raises FileNotFoundError if the object is gone."""
    if (artifact.meta or {}).get("backend") == "s3":
        data = get_storage().get(artifact.path)
        filename = (artifact.meta or {}).get("filename") or artifact.path.rsplit("/", 1)[-1]
        return data, guess_type(filename), filename
    path = Path(artifact.path)
    if not path.is_file():
        raise FileNotFoundError(artifact.path)
    return path.read_bytes(), guess_type(path.name), path.name


def delete_artifact_bytes(artifact: Artifact) -> None:
    """Remove an artifact's stored bytes from whichever backend holds them."""
    if (artifact.meta or {}).get("backend") == "s3":
        try:
            get_storage().delete(artifact.path)
        except Exception:  # noqa: BLE001 - best effort; the row is going away regardless
            pass
        return
    path = Path(artifact.path)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
