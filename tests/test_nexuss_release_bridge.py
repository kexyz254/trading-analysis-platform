import json
from uuid import uuid4

from fastapi.testclient import TestClient

from services.nexuss_bridge import app as gateway
from services.nexuss_bridge.protocol import signed_headers


SECRET = b"test-only-release-bridge-key-0123456789"


def test_release_requires_signed_exact_request_and_enabled_socket(tmp_path, monkeypatch):
    called = []

    def release_operation(socket_path, secret, operation, item):
        called.append((socket_path, operation, item.commit))
        return {"release_id": item.request_id.hex, "commit": item.commit,
                "digest": "f" * 64, "status": "prepared"}

    monkeypatch.setattr(gateway, "release_operation", release_operation)
    app = gateway.create_app(SECRET, str(tmp_path / "bridge.db"),
                             release_socket="/run/nexuss-release/release.sock")
    client = TestClient(app)
    item = {"request_id": str(uuid4()), "commit": "a" * 40, "digest": "0" * 64,
            "approval_signature": ""}
    target = "/agent/v1/releases/prepare"
    body = json.dumps(item).encode()
    assert client.post(target, content=body).status_code == 401
    response = client.post(target, content=body,
                           headers={**signed_headers(SECRET, "POST", target, body),
                                    "Content-Type": "application/json"})
    assert response.status_code == 200
    assert called == [("/run/nexuss-release/release.sock", "prepare", "a" * 40)]
    wrong = "/agent/v1/releases/shell"
    assert client.post(wrong, content=body,
                       headers=signed_headers(SECRET, "POST", wrong, body)).status_code == 404


def test_release_unavailable_without_host_socket(tmp_path):
    app = gateway.create_app(SECRET, str(tmp_path / "bridge.db"))
    client = TestClient(app)
    item = {"request_id": str(uuid4()), "commit": "a" * 40, "digest": "0" * 64,
            "approval_signature": ""}
    target = "/agent/v1/releases/prepare"
    body = json.dumps(item).encode()
    assert client.post(target, content=body,
                       headers={**signed_headers(SECRET, "POST", target, body),
                                "Content-Type": "application/json"}).status_code == 503
