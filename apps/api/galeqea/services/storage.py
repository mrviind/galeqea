"""Artifact storage: one interface, two backends.

Reports, share pages and run artifacts (screenshots / video / trace / logs) go
through this seam rather than touching the filesystem directly, so the same code
serves a laptop (``LocalStorage`` under ``~/.galeqea``) and an enterprise
(``S3Storage``, any S3-compatible endpoint: AWS, SeaweedFS, R2, Ceph…) with only
a config change. Keys are POSIX-style paths. Objects are always fronted by the API
(``url()`` returns an ``/api/...`` path), so an S3 bucket stays private; a caller
that wants a direct link can ask ``S3Storage.presigned_url`` for a short-lived one.
"""

from __future__ import annotations

import shutil
from abc import ABC, abstractmethod
from pathlib import Path

from ..config import settings


class Storage(ABC):
    #: True for a networked object store (S3): artifacts are uploaded rather than
    #: left on local disk. Lets callers branch without importing a concrete class.
    is_remote: bool = False

    @abstractmethod
    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        """Store ``data`` at ``key``; return the key."""

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def url(self, key: str) -> str:
        """A URL a browser can fetch the object from."""


def _safe_key(key: str) -> str:
    # No traversal, no absolute paths: a key is a relative POSIX path.
    parts = [p for p in key.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


class LocalStorage(Storage):
    """Objects under a base directory, served back through the API."""

    def __init__(self, base_dir: Path | str, base_url: str = "") -> None:
        self.base = Path(base_dir)
        self.base.mkdir(parents=True, exist_ok=True)
        self.base_url = base_url.rstrip("/")

    def _path(self, key: str) -> Path:
        return self.base / _safe_key(key)

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return _safe_key(key)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()

    def url(self, key: str) -> str:
        return f"{self.base_url}/api/shared/{_safe_key(key)}"


class S3Storage(Storage):
    """Any S3-compatible endpoint (AWS S3, SeaweedFS, RustFS, Cloudflare R2, Ceph…).

    Objects are private in the bucket; a browser never talks to S3 with static
    creds. ``url()`` returns an **API-fronted** path: the API streams the object
    (or 302s to a short-lived presigned URL via ``presigned_url``), so the same
    report/share/artifact code works whether the backend is disk or S3.
    """

    is_remote = True

    def __init__(
        self,
        *,
        bucket: str = "",
        endpoint: str = "",
        region: str = "us-east-1",
        access_key: str = "",
        secret_key: str = "",
        force_path_style: bool = False,
        base_url: str = "",
        ensure_bucket: bool = True,
    ) -> None:
        import boto3
        from botocore.config import Config as BotoConfig

        self.bucket = bucket or settings.s3_bucket
        if not self.bucket:
            raise ValueError("S3 storage needs GALEQEA_S3_BUCKET.")
        self.base_url = base_url.rstrip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            region_name=region or None,
            aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None,
            config=BotoConfig(
                s3={"addressing_style": "path" if force_path_style else "auto"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        if ensure_bucket:
            self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            try:
                self._client.create_bucket(Bucket=self.bucket)
            except ClientError:  # pragma: no cover - a pre-existing bucket we can't head is fine
                pass

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        key = _safe_key(key)
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=_safe_key(key))
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=_safe_key(key))
            return True
        except ClientError:
            return False

    def delete(self, key: str) -> None:
        key = _safe_key(key)
        # A key that names a "directory" (a run's whole artifact tree) deletes the
        # whole prefix; a plain key deletes just itself.
        paginator = self._client.get_paginator("list_objects_v2")
        to_delete: list[dict] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=key):
            for obj in page.get("Contents", []):
                if obj["Key"] == key or obj["Key"].startswith(key + "/"):
                    to_delete.append({"Key": obj["Key"]})
        for i in range(0, len(to_delete), 1000):
            self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": to_delete[i:i + 1000]})
        if not to_delete:
            self._client.delete_object(Bucket=self.bucket, Key=key)

    def url(self, key: str) -> str:
        # Always API-fronted so the bucket stays private and the URL is stable.
        return f"{self.base_url}/api/shared/{_safe_key(key)}"

    def presigned_url(self, key: str, *, expires: int | None = None) -> str:
        """A short-lived signed URL a browser can fetch directly from S3."""
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": _safe_key(key)},
            ExpiresIn=expires or settings.s3_url_ttl_seconds,
        )


_storage: Storage | None = None


def get_storage() -> Storage:
    """The configured storage backend (local by default)."""
    global _storage
    if _storage is None:
        backend = getattr(settings, "storage_backend", "local") or "local"
        if backend == "s3":
            _storage = S3Storage(
                bucket=settings.s3_bucket,
                endpoint=settings.s3_endpoint,
                region=settings.s3_region,
                access_key=settings.s3_access_key_id,
                secret_key=settings.s3_secret_access_key,
                force_path_style=settings.s3_force_path_style,
                base_url=settings.base_url,
            )
        else:
            _storage = LocalStorage(Path(settings.home) / "shared", settings.base_url)
    return _storage


def reset_storage() -> None:
    """Test seam: drop the cached backend so a new home is picked up."""
    global _storage
    _storage = None
