import httpx
from fastapi.testclient import TestClient

from services.nexuss_bridge.app import create_app, incident_summary
from services.nexuss_bridge.protocol import signed_headers


def test_incident_projection_does_not_export_raw_reason_or_trades(tmp_path):
    secret = b"test-only-long-key-never-use-live-12345"
    def upstream(request):
        assert request.url.path == "/api/journal"
        return httpx.Response(200, json={"trades": [{"secret": "credential"}],
            "circuit_breaker": {"reason": "3 consecutive execution errors on BTC/USDT",
                                "timestamp": "2026-09-16T08:00:00+00:00"}})
    client = TestClient(create_app(secret, str(tmp_path / "db"), transport=httpx.MockTransport(upstream)))
    target = "/agent/v1/evidence/incident"
    response = client.get(target, headers=signed_headers(secret, "GET", target))
    assert response.status_code == 200
    assert response.json()["data"]["reason_code"] == "execution_errors"
    assert response.json()["data"]["error_count"] == 3
    assert "credential" not in response.text
    assert "BTC" not in response.text


def test_unrecognized_and_missing_reason_never_leak():
    result = incident_summary({"circuit_breaker": {"reason": "password=secret", "timestamp": "secret"}})
    assert result == {"reason_code": "other", "event_at": None, "error_count": None}
    assert incident_summary({})["reason_code"] == "no_event"
