"""Outbound webhooks: signature, delivery, retry, and the log.

Delivery is exercised against a real local HTTP receiver (not a mock), so the
HMAC signature and retry-with-backoff are verified end to end, and the delivery
log records every outcome.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from galeqea.core.events import Ev, Event
from galeqea.models import WebhookDelivery, WebhookEndpoint
from galeqea.services import webhooks


# --------------------------------------------------------------------------- #
# A scriptable local receiver
# --------------------------------------------------------------------------- #
class _Receiver:
    def __init__(self, statuses: list[int]):
        self.statuses = list(statuses)
        self.requests: list[dict] = []
        self._port = _free_port()
        self._server = HTTPServer(("127.0.0.1", self._port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/hook"

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()

    def _handler(self):
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length)
                receiver.requests.append({
                    "body": body,
                    "webhook_id": self.headers.get("webhook-id", ""),
                    "webhook_timestamp": self.headers.get("webhook-timestamp", ""),
                    "signature": self.headers.get("webhook-signature", ""),
                    "event": self.headers.get("x-galeqea-event", ""),
                })
                code = receiver.statuses.pop(0) if receiver.statuses else 200
                self.send_response(code)
                self.end_headers()

        return Handler


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def receiver_factory():
    made: list[_Receiver] = []

    def make(statuses):
        r = _Receiver(statuses).start()
        made.append(r)
        return r

    yield make
    for r in made:
        r.stop()


# --------------------------------------------------------------------------- #
# Unit: signing and event mapping
# --------------------------------------------------------------------------- #
def test_sign_is_standard_webhooks_signature():
    import base64

    secret = webhooks.new_secret()
    body = b'{"a":1}'
    sig = webhooks.sign(secret, "msg_1", "1700000000", body)
    key = base64.b64decode(secret[len("whsec_"):])
    expected = "v1," + base64.b64encode(
        hmac.new(key, b"msg_1.1700000000." + body, hashlib.sha256).digest()
    ).decode()
    assert sig == expected


def test_a_failed_run_maps_to_both_finished_and_failed():
    ev = Event(type=Ev.RUN_FINISHED, project_id="p", run_id="r", payload={"status": "failed"})
    assert webhooks._event_names(ev) == ["run.finished", "run.failed"]
    ok = Event(type=Ev.RUN_FINISHED, project_id="p", run_id="r", payload={"status": "passed"})
    assert webhooks._event_names(ok) == ["run.finished"]


# --------------------------------------------------------------------------- #
# Delivery against a live receiver
# --------------------------------------------------------------------------- #
def _endpoint(db, project, url, events, secret="topsecret"):
    ep = WebhookEndpoint(project_id=project.id, url=url, secret=secret, events=events, active=True)
    db.add(ep)
    db.commit()
    return ep


async def test_delivery_is_signed_and_logged(db, project, receiver_factory):
    rec = receiver_factory([200])
    ep = _endpoint(db, project, rec.url, ["run.finished"])
    event = Event(type=Ev.RUN_FINISHED, project_id=project.id, run_id=None, payload={"status": "passed"})

    await webhooks.deliver(event)

    assert len(rec.requests) == 1
    req = rec.requests[0]
    assert req["event"] == "run.finished"
    assert req["webhook_id"] and req["webhook_timestamp"]
    # The signature the receiver got verifies against the shared secret.
    assert req["signature"] == webhooks.sign(
        "topsecret", req["webhook_id"], req["webhook_timestamp"], req["body"],
    )
    assert json.loads(req["body"])["event"] == "run.finished"

    db.expire_all()
    deliveries = list(db.query(WebhookDelivery).filter_by(endpoint_id=ep.id))
    assert deliveries and deliveries[0].status == "delivered"


async def test_delivery_retries_then_succeeds(db, project, receiver_factory):
    rec = receiver_factory([500, 200])  # fail once, then succeed
    ep = _endpoint(db, project, rec.url, ["run.finished"])
    event = Event(type=Ev.RUN_FINISHED, project_id=project.id, payload={"status": "passed"})

    await webhooks.deliver(event)

    assert len(rec.requests) == 2  # retried
    db.expire_all()
    d = next(iter(db.query(WebhookDelivery).filter_by(endpoint_id=ep.id)))
    assert d.status == "delivered" and d.attempts == 2


async def test_delivery_gives_up_and_logs_failure(db, project, receiver_factory):
    rec = receiver_factory([500, 500, 500])
    ep = _endpoint(db, project, rec.url, ["run.finished"])
    event = Event(type=Ev.RUN_FINISHED, project_id=project.id, payload={"status": "passed"})

    await webhooks.deliver(event)

    db.expire_all()
    d = next(iter(db.query(WebhookDelivery).filter_by(endpoint_id=ep.id)))
    assert d.status == "failed" and d.attempts == 3 and d.response_code == 500


async def test_only_subscribed_endpoints_receive(db, project, receiver_factory):
    rec = receiver_factory([200])
    _endpoint(db, project, rec.url, ["approval.requested"])  # NOT subscribed to run.finished
    event = Event(type=Ev.RUN_FINISHED, project_id=project.id, payload={"status": "passed"})

    await webhooks.deliver(event)
    assert rec.requests == []


# --------------------------------------------------------------------------- #
# CRUD endpoints
# --------------------------------------------------------------------------- #
def test_webhook_crud_reveals_secret_once(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app

    client = TestClient(app)
    base = f"/api/projects/{project.id}/webhooks"

    created = client.post(base, json={"url": "https://example.com/hook", "events": ["run.failed"]})
    assert created.status_code == 200
    assert created.json()["secret"]  # returned once

    listed = client.get(base).json()
    assert listed["webhooks"][0]["secret_hint"].endswith("…")
    assert "secret" not in listed["webhooks"][0]  # never returned again
    assert "run.failed" in listed["available_events"]

    bad = client.post(base, json={"url": "https://x", "events": ["not.an.event"]})
    assert bad.status_code == 400
