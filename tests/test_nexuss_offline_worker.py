import httpx
from services.nexuss_bridge.worker import ObservationStore, observe


def test_offline_restart_and_cursor_replay(tmp_path):
    path = tmp_path / "events.db"
    store = ObservationStore(path)
    assert store.record({"score": 40}, now=100)
    assert not store.record({"score": 40}, now=110)
    store = ObservationStore(path)
    assert store.record({"score": 60}, now=120)
    first = store.events(limit=1)
    second = store.events(after=first["next_cursor"])
    assert first["events"][0]["data"] == {"score": 40}
    assert second["events"][0]["data"] == {"score": 60}


def test_full_queue_preserves_old_evidence(tmp_path):
    store = ObservationStore(tmp_path / "events.db", capacity=1)
    store.record({"score": 40}, now=100)
    assert not store.record({"score": 80}, now=110)
    assert store.status()["status"] == "backlog_full"
    assert store.events()["events"][0]["data"] == {"score": 40}


def test_hourly_checkpoint(tmp_path):
    store = ObservationStore(tmp_path / "events.db")
    store.record({"score": 40}, now=100)
    assert store.record({"score": 40}, now=3700)


def test_typed_health_only_and_connection_failure():
    secret = b"test-only-long-key-12345678901234567890"
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": {
            "score": 40, "circuit_breaker_tripped": True, "staleness_seconds": 400,
            "password": "must-not-persist"}}))) as client:
        assert observe(client, secret) == {"kind": "health_observation", "score": 40,
                                          "circuit_breaker_tripped": True, "heartbeat_stale": True}
    def fail(request):
        raise httpx.ConnectError("sensitive exception text")
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        assert observe(client, secret) == {"kind": "bridge_unavailable"}


def test_authenticated_observer_round_trip(tmp_path):
    from fastapi.testclient import TestClient
    from services.nexuss_bridge.app import create_app
    from services.nexuss_bridge.protocol import signed_headers
    secret = b"test-only-long-key-12345678901234567890"
    observations = tmp_path / "observations.db"
    upstream = httpx.MockTransport(lambda r: httpx.Response(200, json={
        "score": 40, "circuit_breaker_tripped": True, "staleness_seconds": 60}))
    app = create_app(secret, tmp_path / "bridge.db", transport=upstream,
                     worker_database=observations)
    with TestClient(app) as server:
        def gateway(request):
            return server.request(request.method, request.url.raw_path.decode(),
                                  headers=dict(request.headers), content=request.content)
        with httpx.Client(transport=httpx.MockTransport(gateway)) as client:
            payload = observe(client, secret)
        assert payload["kind"] == "health_observation"
        ObservationStore(observations).record(payload)
        target = "/agent/v1/observations?after=0&limit=1"
        assert server.get(target).status_code == 401
        response = server.get(target, headers=signed_headers(secret, "GET", target))
        assert response.json()["events"][0]["data"] == payload
        target = "/agent/v1/worker/status"
        response = server.get(target, headers=signed_headers(secret, "GET", target))
        assert response.json()["stored_events"] == 1
