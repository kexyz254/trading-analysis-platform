import json
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from services.nexuss_bridge.app import create_app
from services.nexuss_bridge.protocol import Auth, signature, signed_headers

SECRET = b"test-only-bridge-key-do-not-use-live-12345"


def make_client(tmp_path):
    def upstream(request):
        return httpx.Response(200, json={"score": 40, "circuit_breaker_tripped": True,
                                        "api_key": "must-not-leave-TAS",
                                        "reasons": ["password=must-not-leave-TAS"]})
    return TestClient(create_app(SECRET, str(tmp_path / "bridge.db"),
                                transport=httpx.MockTransport(upstream)))


def test_unsigned_and_tampered_requests_fail(tmp_path):
    client = make_client(tmp_path)
    target = "/agent/v1/evidence/health"
    assert client.get(target).status_code == 401
    headers = signed_headers(SECRET, "GET", target)
    assert client.get(target + "?x=1", headers=headers).status_code == 401


def test_projection_and_persistent_replay(tmp_path):
    client = make_client(tmp_path)
    target = "/agent/v1/evidence/health"
    headers = signed_headers(SECRET, "GET", target)
    response = client.get(target, headers=headers)
    assert response.status_code == 200
    assert response.json()["data"] == {"score": 40, "circuit_breaker_tripped": True}
    assert "must-not-leave" not in response.text
    assert make_client(tmp_path).get(target, headers=headers).status_code == 401


def test_no_execution_or_arbitrary_proxy(tmp_path):
    client = make_client(tmp_path)
    for method, target in [("POST", "/shell"), ("POST", "/agent/v1/execute"),
                           ("GET", "/agent/v1/evidence/wallet")]:
        response = client.request(method, target, headers=signed_headers(SECRET, method, target))
        assert response.status_code == 404


def test_feedback_durable_idempotent_and_advisory(tmp_path):
    client = make_client(tmp_path)
    now = datetime.now(timezone.utc)
    item = {"feedback_id": "incident-1", "kind": "incident", "summary": "Inspect breaker evidence",
            "observed_at": now.isoformat(), "expires_at": (now + timedelta(hours=1)).isoformat()}
    target = "/agent/v1/feedback"
    def send():
        body = json.dumps(item).encode()
        return client.post(target, content=body, headers={**signed_headers(SECRET, "POST", target, body),
                                                        "Content-Type": "application/json"})
    assert send().json()["execution_authorized"] is False
    assert send().status_code == 200
    item["summary"] = "Changed content"
    assert send().status_code == 409
    item["execution_requested"] = True
    assert send().status_code == 422
    target = "/agent/v1/feedback/incident-1"
    assert make_client(tmp_path).get(target, headers=signed_headers(SECRET, "GET", target)).status_code == 200


def test_expired_signature_and_oversized_body(tmp_path):
    client = make_client(tmp_path)
    target = "/agent/v1/capabilities"
    stamp, nonce = str(int(time.time()) - 120), "a" * 32
    headers = {"X-Nexuss-Time": stamp, "X-Nexuss-Nonce": nonce,
               "X-Nexuss-Signature": signature(SECRET, "GET", target, stamp, nonce, b"")}
    assert client.get(target, headers=headers).status_code == 401
    assert client.post("/agent/v1/feedback", content=b"x" * 65537).status_code == 413
