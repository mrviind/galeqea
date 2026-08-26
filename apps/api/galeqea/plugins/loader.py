"""Plugin discovery, installation and hot loading."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..core import audit
from ..models import PluginRecord
from .sdk import CAPABILITIES, GaleQEAPlugin, PluginContext, PluginManifest

MANIFEST_NAME = "galeqea.plugin.json"


@dataclass(slots=True)
class LoadedPlugin:
    manifest: PluginManifest
    instance: GaleQEAPlugin
    context: PluginContext


_LOADED: dict[str, LoadedPlugin] = {}


def discover(directory: Path | None = None) -> list[dict]:
    """Find plugins on disk without loading any code."""
    root = Path(directory or settings.plugins_dir)
    found: list[dict] = []
    for manifest_path in sorted(root.glob(f"*/{MANIFEST_NAME}")):
        try:
            payload = json.loads(manifest_path.read_text())
            manifest = PluginManifest.from_dict(payload)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            found.append({"path": str(manifest_path.parent), "error": str(exc)})
            continue
        found.append({
            "path": str(manifest_path.parent),
            "manifest": manifest.as_dict(),
            "permissions_explained": {
                p: CAPABILITIES.get(p, "unknown") for p in manifest.permissions
            },
        })
    return found


def install(db: Session, path: Path, *, actor_id: str | None = None) -> PluginRecord:
    """Register a plugin. Always installs **disabled** - an admin grants it."""
    manifest_path = Path(path) / MANIFEST_NAME
    if not manifest_path.exists():
        raise ValueError(f"no {MANIFEST_NAME} found in {path}")
    manifest = PluginManifest.from_dict(json.loads(manifest_path.read_text()))

    entry = Path(path) / manifest.entrypoint
    if not entry.exists():
        raise ValueError(f"entrypoint {manifest.entrypoint} does not exist in {path}")
    checksum = hashlib.sha256(entry.read_bytes()).hexdigest()

    record = db.execute(
        select(PluginRecord).where(PluginRecord.slug == manifest.slug)
    ).scalar_one_or_none()
    if record is None:
        record = PluginRecord(slug=manifest.slug)
        db.add(record)

    changed_code = record.checksum and record.checksum != checksum
    record.name = manifest.name
    record.version = manifest.version
    record.kind = manifest.kind
    record.manifest = manifest.as_dict()
    record.entrypoint = manifest.entrypoint
    record.source_path = str(path)
    record.checksum = checksum
    if changed_code:
        # Code changed under an existing grant - revoke and require re-approval
        # rather than silently running new code with old permissions.
        record.enabled = False
        record.granted_permissions = []
        record.install_error = (
            "the plugin's code changed since it was approved; permissions were revoked "
            "and it must be re-enabled"
        )
    db.flush()

    audit.record(
        db, action="plugin.installed", actor_id=actor_id, resource_type="plugin",
        resource_id=record.id,
        detail={"slug": manifest.slug, "version": manifest.version,
                "requested_permissions": manifest.permissions,
                "code_changed": bool(changed_code)},
    )
    return record


def enable(
    db: Session, slug: str, *, granted: list[str], actor_id: str | None = None
) -> PluginRecord:
    record = db.execute(
        select(PluginRecord).where(PluginRecord.slug == slug)
    ).scalar_one_or_none()
    if record is None:
        raise ValueError(f"plugin {slug!r} is not installed")

    requested = set((record.manifest or {}).get("permissions", []))
    over_grant = set(granted) - requested
    if over_grant:
        raise ValueError(
            f"cannot grant capabilities the plugin never requested: {', '.join(sorted(over_grant))}"
        )
    record.granted_permissions = sorted(set(granted))
    record.enabled = True
    record.install_error = ""
    db.flush()

    audit.record(
        db, action="plugin.enabled", actor_id=actor_id, resource_type="plugin",
        resource_id=record.id,
        detail={"slug": slug, "granted": record.granted_permissions},
    )
    return record


def load(record: PluginRecord, *, project_id: str = "", services: dict | None = None) -> LoadedPlugin:
    """Import and instantiate an enabled plugin. Hot-reloads on checksum change."""
    if not record.enabled:
        raise PermissionError(f"plugin {record.slug!r} is not enabled")

    cached = _LOADED.get(record.slug)
    if cached and cached.manifest.version == record.version:
        return cached

    entry = Path(record.source_path) / record.entrypoint
    module_name = f"galeqea_plugin_{record.slug.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not load {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    factory = getattr(module, "plugin", None)
    if factory is None:
        raise ImportError(
            f"{record.entrypoint} must expose a module-level `plugin` "
            "(an instance or a zero-argument factory)"
        )
    instance = factory() if callable(factory) else factory
    if not isinstance(instance, GaleQEAPlugin):
        raise TypeError(f"{record.slug} does not subclass GaleQEAPlugin")

    manifest = PluginManifest.from_dict(record.manifest)
    context = PluginContext(
        granted=record.granted_permissions or [],
        project_id=project_id,
        services=_permitted_services(record.granted_permissions or [], services or {}),
    )
    instance.manifest = manifest
    instance.setup(context)

    loaded = LoadedPlugin(manifest=manifest, instance=instance, context=context)
    _LOADED[record.slug] = loaded
    record.load_count += 1
    return loaded


def _permitted_services(granted: list[str], services: dict) -> dict:
    """Only hand over the services matching granted capabilities."""
    mapping = {
        "read:tests": ["tests"],
        "read:runs": ["runs"],
        "read:requirements": ["requirements"],
        "write:proposals": ["proposals"],
        "network:outbound": ["http"],
        "fs:artifacts": ["artifacts"],
    }
    allowed: set[str] = set()
    for capability in granted:
        allowed.update(mapping.get(capability, []))
    return {k: v for k, v in services.items() if k in allowed}


def unload(slug: str) -> bool:
    loaded = _LOADED.pop(slug, None)
    if loaded is None:
        return False
    try:
        loaded.instance.teardown()
    except Exception:  # noqa: BLE001 - a bad teardown must not block unloading
        pass
    return True


def loaded_plugins() -> list[dict]:
    return [
        {"slug": slug, "name": p.manifest.name, "version": p.manifest.version,
         "kind": p.manifest.kind, "granted": sorted(p.context._granted)}
        for slug, p in _LOADED.items()
    ]


def dispatch_run_finished(db: Session, run: dict, results: list[dict]) -> list[dict]:
    """Fan a finished run out to every enabled reporter plugin."""
    from .sdk import Reporter

    outcomes: list[dict] = []
    records = db.execute(
        select(PluginRecord).where(
            PluginRecord.enabled.is_(True), PluginRecord.kind == "reporter"
        )
    ).scalars()
    for record in records:
        try:
            loaded = load(record)
            if isinstance(loaded.instance, Reporter):
                loaded.instance.on_run_finished(run, results, loaded.context)
                outcomes.append({"slug": record.slug, "ok": True})
        except Exception as exc:  # noqa: BLE001 - one bad plugin must not fail a run
            outcomes.append({"slug": record.slug, "ok": False, "error": str(exc)})
    return outcomes
