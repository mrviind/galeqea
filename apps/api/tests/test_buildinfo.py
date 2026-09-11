"""Build provenance carries the API drift guard, so a stale process can't hide."""

from __future__ import annotations

from galeqea import buildinfo


def test_health_provenance_includes_the_drift_fields():
    d = buildinfo.as_dict()
    assert {"sha", "started_at", "api_started_at", "newest_source_mtime", "stale_api"} <= set(d)
    assert isinstance(d["stale_api"], bool)


def test_newest_source_mtime_sees_the_package():
    # The package's own files exist, so the newest source mtime is a real time.
    assert buildinfo.newest_source_mtime() > 0


def test_a_freshly_imported_process_is_not_stale():
    # This process started after the sources on disk, so it is current.
    assert buildinfo.is_stale() is False
