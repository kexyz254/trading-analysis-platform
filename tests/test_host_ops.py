import time
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from services.nexuss_bridge.app import create_app
from services.nexuss_bridge.host_ops import Operations, signature
from services.nexuss_bridge.protocol import signed_headers


SECRET = b"0123456789abcdef0123456789abcdef"


def payload(op="restart_dashboard", request_id=None):
    request_id = request_id or uuid4().hex
    stamp = int(time.time())
    return {"operation": op, "request_id": request_id, "time": stamp,
            "signature": signature(SECRET, op, request_id, stamp)}


def test_host_op_requires_signature_and_fixed_action(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("services.nexuss_bridge.host_ops.time.sleep", lambda _: None)
    ops = Operations(SECRET, str(tmp_path / "receipts.db"), probe=lambda: False,
                     restart=lambda: calls.append("restart") or True)
    bad = payload()
    bad["signature"] = "0" * 64
    with pytest.raises(ValueError):
        ops.execute(bad)
    bad = payload("restart_engine")
    with pytest.raises(ValueError):
        ops.execute(bad)
    assert calls == []


def test_restart_only_after_failed_probes_once_and_receipt_is_durable(tmp_path, monkeypatch):
    monkeypatch.setattr("services.nexuss_bridge.host_ops.time.sleep", lambda _: None)
    checks, restarts = [], []
    def probe():
        checks.append(1)
        return len(checks) >= 3
    db = str(tmp_path / "receipts.db")
    ops = Operations(SECRET, db, probe=probe,
                     restart=lambda: restarts.append(1) or True)
    command = payload()
    result = ops.execute(command)
    assert result["status"] == "restarted_verified"
    assert len(restarts) == 1
    again = Operations(SECRET, db, probe=lambda: False,
                       restart=lambda: restarts.append(2) or True)
    assert again.execute(command)["replayed"] is True
    assert len(restarts) == 1


def test_healthy_dashboard_is_never_restarted(tmp_path):
    ops = Operations(SECRET, str(tmp_path / "receipts.db"), probe=lambda: True,
                     restart=lambda: pytest.fail("must not restart"))
    assert ops.execute(payload())["status"] == "healthy_noop"


def test_disabled_bridge_has_no_executor(tmp_path):
    client = TestClient(create_app(SECRET, str(tmp_path / "bridge.db")))
    target = "/agent/v1/ops/restart_dashboard"
    body = json.dumps({"request_id": str(uuid4())}).encode()
    assert client.post(target, content=body).status_code == 401
    assert client.post(target, content=body,
                       headers=signed_headers(SECRET, "POST", target, body)).status_code == 503


def test_signed_bridge_forwards_only_allowlisted_operations(tmp_path, monkeypatch):
    seen = []
    def forward(path, secret, operation, request_id):
        seen.append((path, operation, request_id))
        return {"request_id": request_id, "operation": operation,
                "status": "healthy_noop", "verified": True, "replayed": False}
    monkeypatch.setattr("services.nexuss_bridge.app.host_operation", forward)
    client = TestClient(create_app(SECRET, str(tmp_path / "bridge.db"),
                                   operations_socket="/run/nexuss-agent/ops.sock"))
    for op in ("restart_engine", "restart_dashboard"):
        target = f"/agent/v1/ops/{op}"
        body = json.dumps({"request_id": str(uuid4())}).encode()
        response = client.post(target, content=body,
                               headers=signed_headers(SECRET, "POST", target, body))
        assert response.status_code == (404 if op == "restart_engine" else 200)
    assert len(seen) == 1
