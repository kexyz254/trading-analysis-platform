import httpx
from fastapi.testclient import TestClient

from services.nexuss_bridge.app import create_app
from services.nexuss_bridge.protocol import signed_headers


def test_results_projection_excludes_unexpected_upstream_fields(tmp_path):
    def upstream(request):
        assert request.url.path == "/api/agent/btc-price-call-results"
        return httpx.Response(200, json={
            "status": "available", "symbol": "BTC/USDT", "credential": "must-not-leak",
            "horizons": {"5m": {"last_24h": {"hits": 1, "directional": 2,
                                              "unapproved": "must-not-leak"}},
                         "15m": {"last_24h": {"hits": 0}}},
        })
    secret = b"a" * 32
    app = create_app(secret, str(tmp_path / "bridge.db"),
                     transport=httpx.MockTransport(upstream))
    target = "/agent/v1/evidence/btc_price_results?symbol=BTC%2FUSDT"
    response = TestClient(app).get(target, headers=signed_headers(secret, "GET", target))
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["horizons"]["5m"]["last_24h"]["hits"] == 1
    assert "credential" not in data
    assert "unapproved" not in data["horizons"]["5m"]["last_24h"]
