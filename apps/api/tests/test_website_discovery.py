"""Discovery: URL canonicalisation, dedupe, classification, and a live crawl.

The normaliser and dedupe key are the load-bearing pieces: a site with tracking
params and inconsistent trailing slashes must not test the same page ten times,
and www/apex must count as one site. The classification tests pin the contract
that auth walls, HTTP errors and off-site redirects become *findings*, never
silent drops or crashes. The integration test proves the whole path end to end
against the bundled demo app (browser-driven when the runner is present, HTTP
fallback otherwise; either way it must find both pages).
"""

from __future__ import annotations

import shutil
import socket
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from galeqea.services import website_test as wt

# --------------------------------------------------------------------------- #
# normalize_page_url
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw, expected", [
    ("https://x.com/a#section", "https://x.com/a"),                       # fragment dropped
    ("https://x.com/a/", "https://x.com/a/"),                             # trailing slash PRESERVED (slash-significant routes)
    ("https://x.com/", "https://x.com/"),                                 # root slash kept
    ("https://x.com/a?utm_source=nl&utm_medium=email", "https://x.com/a"),  # utm_* stripped
    ("https://x.com/a?gclid=123&fbclid=xyz", "https://x.com/a"),          # tracking stripped
    ("https://x.com/a?id=7&page=2", "https://x.com/a?id=7&page=2"),       # real params kept
    ("https://x.com/a?page=2&id=7", "https://x.com/a?id=7&page=2"),       # params sorted
    ("https://x.com/a?keep=1&utm_term=z", "https://x.com/a?keep=1"),      # mixed
])
def test_normalize_page_url(raw, expected):
    assert wt.normalize_page_url(raw) == expected


# --------------------------------------------------------------------------- #
# dedupe_key
# --------------------------------------------------------------------------- #

def test_dedupe_www_and_apex_are_one_page():
    assert wt.dedupe_key("https://www.x.com/a") == wt.dedupe_key("https://x.com/a/")


def test_dedupe_ignores_fragment_and_tracking():
    assert wt.dedupe_key("https://x.com/a#top") == wt.dedupe_key("https://x.com/a?utm_source=q")


def test_dedupe_distinguishes_real_pages_and_real_query():
    assert wt.dedupe_key("https://x.com/a") != wt.dedupe_key("https://x.com/b")
    assert wt.dedupe_key("https://x.com/a?id=1") != wt.dedupe_key("https://x.com/a?id=2")


# --------------------------------------------------------------------------- #
# discover_pages: classification of a browser-crawl result
# --------------------------------------------------------------------------- #

def _browser_result(pages, **extra):
    return {"ok": True, "method": "browser", "origin": "https://x.com",
            "start": "https://x.com/", "pages": pages, "forms": 0,
            "findings": extra.get("findings", []), "truncated": extra.get("truncated", False),
            "discovered": extra.get("discovered", len(pages))}


def _page(url, status=200, auth=False, cross=False, error=None):
    return {"url": url, "path": url, "status": status, "authGated": auth,
            "crossOrigin": cross, "error": error, "depth": 0}


def test_discover_filters_auth_error_and_offsite(monkeypatch):
    result = _browser_result([
        _page("https://x.com/"),
        _page("https://x.com/login", auth=True),
        _page("https://x.com/broken", status=500),
        _page("https://x.com/gone", status=404),
        _page("https://other.com/", cross=True),
        _page("https://x.com/dead", error="Timeout"),
        _page("https://x.com/about"),
    ])
    monkeypatch.setattr(wt, "_discover_via_runner", lambda *a, **k: result)

    out = wt.discover_pages("https://x.com")
    assert out["method"] == "browser"
    assert out["pages"] == ["https://x.com/", "https://x.com/about"]  # only healthy pages
    assert out["skipped"] == {"auth": 1, "error": 3, "offsite": 1}


def test_discover_dedupes_testable_pages(monkeypatch):
    result = _browser_result([
        _page("https://x.com/a"),
        _page("https://x.com/a/"),                # same page, trailing slash
        _page("https://x.com/a?utm_source=nl"),   # same page, tracking param
        _page("https://x.com/b"),
    ])
    monkeypatch.setattr(wt, "_discover_via_runner", lambda *a, **k: result)

    out = wt.discover_pages("https://x.com")
    assert out["pages"] == ["https://x.com/a", "https://x.com/b"]


def test_discover_preserves_trailing_slash_for_testing(monkeypatch):
    # Regression: /a/ (200) must not be tested as /a (404 on slash-significant
    # servers). The slash is ignored only for dedupe, never for the tested URL.
    monkeypatch.setattr(wt, "_discover_via_runner",
                        lambda *a, **k: _browser_result([_page("https://x.com/a/")]))
    assert wt.discover_pages("https://x.com")["pages"] == ["https://x.com/a/"]


def test_discover_caps_at_limit_and_flags_truncation(monkeypatch):
    pages = [_page(f"https://x.com/p{i}") for i in range(20)]
    monkeypatch.setattr(wt, "_discover_via_runner", lambda *a, **k: _browser_result(pages, discovered=20))

    out = wt.discover_pages("https://x.com", limit=8)
    assert len(out["pages"]) == 8
    assert out["truncated"] is True


def test_discover_passes_findings_through(monkeypatch):
    findings = [{"kind": "auth_gated", "url": "https://x.com/login", "detail": "login required"}]
    result = _browser_result([_page("https://x.com/"), _page("https://x.com/login", auth=True)],
                             findings=findings)
    monkeypatch.setattr(wt, "_discover_via_runner", lambda *a, **k: result)

    out = wt.discover_pages("https://x.com")
    assert out["findings"] == findings


def test_discover_falls_back_to_http_when_runner_absent(monkeypatch):
    monkeypatch.setattr(wt, "_discover_via_runner", lambda *a, **k: None)
    called = {}

    def fake_http(url, limit, timeout):
        called["http"] = True
        return {"ok": True, "base": url, "pages": [url], "forms": 0, "status": 200,
                "method": "http", "findings": [], "skipped": {}, "truncated": False, "discovered": 1}

    monkeypatch.setattr(wt, "_http_discover", fake_http)
    out = wt.discover_pages("https://x.com")
    assert called.get("http") and out["method"] == "http"


# --------------------------------------------------------------------------- #
# build_plan: skipped/truncated become visible notes
# --------------------------------------------------------------------------- #

def test_build_plan_surfaces_skips_and_truncation():
    discovery = {"base": "https://x.com/", "pages": ["https://x.com/"], "forms": 1,
                 "method": "browser", "truncated": True,
                 "skipped": {"auth": 2, "error": 1, "offsite": 3}}
    plan = wt.build_plan(discovery)
    joined = " ".join(plan["notes"]).lower()
    assert "auth-gated" in joined and "error" in joined and "off-site" in joined
    assert any("first" in n for n in plan["notes"])  # truncation note


def test_build_plan_has_no_notes_for_a_clean_crawl():
    discovery = {"base": "https://x.com/", "pages": ["https://x.com/", "https://x.com/a"],
                 "forms": 0, "method": "browser", "truncated": False,
                 "skipped": {"auth": 0, "error": 0, "offsite": 0}}
    assert wt.build_plan(discovery)["notes"] == []


# --------------------------------------------------------------------------- #
# integration: crawl the bundled demo app for real
# --------------------------------------------------------------------------- #

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def demo_server():
    """Serve examples/demo-app on a loopback port for the duration of a test."""
    root = Path(__file__).resolve().parents[3] / "examples" / "demo-app"
    if not (root / "index.html").exists():
        pytest.skip("bundled demo app not found")
    port = _free_port()
    handler = partial(SimpleHTTPRequestHandler, directory=str(root))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


@pytest.mark.integration
def test_discover_finds_both_demo_pages(demo_server):
    """End-to-end: the crawl must find both demo pages. Browser-driven when the
    runner is installed, HTTP fallback otherwise; the result is the same set."""
    if not shutil.which("node"):
        pytest.skip("node not available")
    out = wt.discover_pages(demo_server, limit=40)
    assert out["ok"]
    paths = {p.rsplit("/", 1)[-1] or "index" for p in out["pages"]}
    assert "" in {p.rstrip("/").rsplit("/", 1)[-1] for p in out["pages"]} or "index" in paths
    assert any(p.endswith("/account.html") for p in out["pages"]), out["pages"]
    assert out["method"] in ("browser", "http")
