"""P1-2: S3 artifact storage + retention.

The live S3 round-trip runs only when GALEQEA_TEST_S3_ENDPOINT is set (the docker
`full` profile verifies it against SeaweedFS); everything else is backend-agnostic
and runs in-process with a fake remote store.
"""

from __future__ import annotations

import os
from datetime import timedelta

import pytest

from galeqea.models import Artifact, Run
from galeqea.models.base import new_id, utcnow
from galeqea.services import artifacts as artifacts_svc
from galeqea.services import retention, storage


class FakeRemoteStorage(storage.Storage):
    """In-memory stand-in for S3; exercises the storage-agnostic artifact path."""

    is_remote = True

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put(self, key, data, *, content_type="application/octet-stream"):
        self.objects[key] = data
        return key

    def get(self, key):
        return self.objects[key]

    def exists(self, key):
        return key in self.objects

    def delete(self, key):
        for k in [k for k in self.objects if k == key or k.startswith(key + "/")]:
            del self.objects[k]

    def url(self, key):
        return f"/api/shared/{key}"


@pytest.fixture()
def remote_storage(monkeypatch):
    fake = FakeRemoteStorage()
    monkeypatch.setattr(storage, "_storage", fake)
    yield fake
    storage.reset_storage()


# --- backend selection & URL fronting -------------------------------------- #

def test_default_backend_is_local():
    storage.reset_storage()
    assert storage.get_storage().is_remote is False
    storage.reset_storage()


def test_s3_url_is_api_fronted_and_key_is_sanitised(monkeypatch):
    import galeqea.services.storage as s

    class _Client:
        def head_bucket(self, **_):
            return {}

    monkeypatch.setattr("boto3.client", lambda *a, **k: _Client())
    store = s.S3Storage(bucket="b", base_url="https://ci.example")
    # API-fronted (never a raw bucket URL) and traversal-proof.
    assert store.url("shares/tok/x.html") == "https://ci.example/api/shared/shares/tok/x.html"
    assert store.url("../../etc/passwd") == "https://ci.example/api/shared/etc/passwd"


# --- artifact routing ------------------------------------------------------ #

def test_record_artifact_local_keeps_fs_path(db, project, tmp_path):
    storage.reset_storage()
    f = tmp_path / "shot.png"
    f.write_bytes(b"png-bytes")
    run = Run(project_id=project.id, number=1, status="passed", title="r")
    db.add(run); db.commit()

    art = artifacts_svc.record_artifact(
        db, run_id=run.id, run_test_id=None,
        art={"path": str(f), "kind": "screenshot", "label": "home"})
    db.commit()
    assert art.meta["backend"] == "local"
    assert art.path == str(f)
    assert art.size_bytes == len(b"png-bytes")
    assert f.exists()  # local backend leaves the file in place


def test_record_artifact_remote_uploads_and_records_key(db, project, tmp_path, remote_storage):
    f = tmp_path / "video.webm"
    f.write_bytes(b"webm-bytes")
    run = Run(project_id=project.id, number=2, status="passed", title="r")
    db.add(run); db.commit()

    art = artifacts_svc.record_artifact(
        db, run_id=run.id, run_test_id="rt-123",
        art={"path": str(f), "kind": "video", "label": "flow"})
    db.commit()

    assert art.meta["backend"] == "s3"
    assert art.path == f"artifacts/{run.id}/rt-123/video.webm"
    assert remote_storage.objects[art.path] == b"webm-bytes"
    assert not f.exists()  # the local scratch copy is removed after upload

    # open_artifact reads it back from whichever backend holds it.
    data, ctype, filename = artifacts_svc.open_artifact(art)
    assert data == b"webm-bytes" and filename == "video.webm" and ctype == "video/webm"


# --- retention ------------------------------------------------------------- #

def test_retention_expires_old_keeps_new_and_forever(db, project, remote_storage):
    project.settings = {"retention_days": 30}
    db.add(project)
    run = Run(project_id=project.id, number=3, status="passed", title="r")
    db.add(run); db.commit()

    def _art(name, age_days):
        a = Artifact(run_id=run.id, kind="screenshot", label=name,
                     path=f"artifacts/{run.id}/rt/{name}", size_bytes=3,
                     meta={"backend": "s3", "filename": name})
        remote_storage.objects[a.path] = b"xxx"
        db.add(a); db.flush()
        a.created_at = utcnow() - timedelta(days=age_days)
        return a

    old = _art("old.png", 60)
    fresh = _art("fresh.png", 5)
    db.commit()

    summary = retention.sweep(db, now=utcnow())
    assert summary["_total"] == 1
    assert db.get(Artifact, old.id) is None          # expired row gone
    assert old.path not in remote_storage.objects    # expired bytes gone
    assert db.get(Artifact, fresh.id) is not None     # within the window, kept
    assert fresh.path in remote_storage.objects

    # A project with no retention_days keeps everything forever.
    project.settings = {}
    db.add(project); db.commit()
    assert retention.sweep(db, now=utcnow())["_total"] == 0
    assert db.get(Artifact, fresh.id) is not None


def test_blank_bool_env_does_not_crash_boot(monkeypatch):
    # Regression: docker-compose passes `GALEQEA_S3_FORCE_PATH_STYLE: ${...:-}`,
    # i.e. an empty string in the zero-config profile. pydantic can't parse "" as a
    # bool, which crashlooped the container on config load. A blank bool env must
    # fall back to the field default instead of raising.
    from galeqea.config import Settings

    monkeypatch.setenv("GALEQEA_S3_FORCE_PATH_STYLE", "")
    monkeypatch.setenv("GALEQEA_WEB_RESEARCH_ENABLED", "")
    s = Settings()
    assert s.s3_force_path_style is False
    assert s.web_research_enabled is False


def test_retention_days_parsing(project):
    for raw, expected in [(30, 30), ("14", 14), (0, None), (-1, None), (None, None), ("x", None)]:
        project.settings = {"retention_days": raw} if raw is not None else {}
        assert retention.retention_days(project) == expected


# --- live round-trip (opt-in) ---------------------------------------------- #

@pytest.mark.skipif(not os.environ.get("GALEQEA_TEST_S3_ENDPOINT"),
                    reason="set GALEQEA_TEST_S3_ENDPOINT to run the live S3 round-trip")
def test_live_s3_roundtrip():
    store = storage.S3Storage(
        bucket=os.environ.get("GALEQEA_TEST_S3_BUCKET", "galeqea-test"),
        endpoint=os.environ["GALEQEA_TEST_S3_ENDPOINT"],
        access_key=os.environ.get("GALEQEA_TEST_S3_KEY", "galeqea"),
        secret_key=os.environ.get("GALEQEA_TEST_S3_SECRET", "galeqea-secret"),
        force_path_style=True,
    )
    key = f"artifacts/live/{new_id()}.txt"
    store.put(key, b"hello-s3", content_type="text/plain")
    assert store.exists(key)
    assert store.get(key) == b"hello-s3"
    assert "/api/shared/" in store.url(key) or store.presigned_url(key)
    store.delete(key)
    assert not store.exists(key)
