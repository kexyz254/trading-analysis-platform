"""Offline-capable health observer. No execution, model calls or remote shell."""
import hashlib
import json
import os
import signal
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .protocol import signed_headers


class ObservationStore:
    def __init__(self, path, capacity=50000):
        self.path, self.capacity = str(path), capacity
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS observations "
                       "(id INTEGER PRIMARY KEY AUTOINCREMENT, observed_at TEXT, payload TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS worker_state "
                       "(id INTEGER PRIMARY KEY CHECK(id=1), digest TEXT, emitted REAL, checked REAL, status TEXT)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def record(self, payload, now=None):
        now = time.time() if now is None else now
        encoded = json.dumps(payload, sort_keys=True, allow_nan=False)
        if len(encoded.encode()) > 65536:
            raise ValueError("Observation too large")
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT digest, emitted FROM worker_state WHERE id=1").fetchone()
            emit = previous is None or previous[0] != digest or now - previous[1] >= 3600
            full = db.execute("SELECT count(*) FROM observations").fetchone()[0] >= self.capacity
            if emit and not full:
                db.execute("INSERT INTO observations(observed_at,payload) VALUES (?,?)",
                           (datetime.fromtimestamp(now, timezone.utc).isoformat(), encoded))
            emitted = now if emit and not full else (previous[1] if previous else 0)
            stored_digest = digest if not full else (previous[0] if previous else "")
            db.execute("INSERT OR REPLACE INTO worker_state VALUES (1,?,?,?,?)",
                       (stored_digest, emitted, now, "backlog_full" if full else "observing"))
        return emit and not full

    def events(self, after=0, limit=100):
        if after < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid cursor or limit")
        with self.connect() as db:
            rows = db.execute("SELECT id,observed_at,payload FROM observations WHERE id>? ORDER BY id LIMIT ?",
                              (after, limit)).fetchall()
        return {"events": [{"id": r[0], "observed_at": r[1], "data": json.loads(r[2])} for r in rows],
                "next_cursor": rows[-1][0] if rows else after}

    def status(self):
        with self.connect() as db:
            state = db.execute("SELECT checked,status FROM worker_state WHERE id=1").fetchone()
            count = db.execute("SELECT count(*) FROM observations").fetchone()[0]
        return {"status": state[1] if state else "not_started", "last_checked": state[0] if state else None,
                "stored_events": count, "capacity": self.capacity, "execution_authorized": False}


def observe(client, secret):
    target = "/agent/v1/evidence/health"
    try:
        with client.stream("GET", "http://127.0.0.1:8300" + target,
                           headers=signed_headers(secret, "GET", target)) as response:
            if response.status_code != 200:
                return {"kind": "bridge_unavailable", "http_status": response.status_code}
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 65536:
                    return {"kind": "invalid_health_response"}
        data = json.loads(raw)["data"]
        score = data.get("score")
        breaker = data.get("circuit_breaker_tripped")
        stale = data.get("staleness_seconds")
        # Copy typed values only; never queue arbitrary upstream strings/errors.
        if type(score) not in (int, float) or not 0 <= score <= 100 or type(breaker) is not bool:
            return {"kind": "invalid_health_response"}
        return {"kind": "health_observation", "score": score, "circuit_breaker_tripped": breaker,
                "heartbeat_stale": (stale > 300 if type(stale) in (int, float) else None)}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return {"kind": "bridge_unavailable"}


def main():
    store = ObservationStore(os.environ["NEXUSS_WORKER_DATABASE"])
    secret = Path(os.environ["NEXUSS_BRIDGE_SECRET_FILE"]).read_bytes().strip()
    if len(secret) < 32:
        raise ValueError("Invalid bridge key")
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
        while not stop.is_set():
            try:
                store.record(observe(client, secret))
            except (sqlite3.Error, OSError):
                print("Observation storage unavailable; retrying without deleting evidence", flush=True)
            stop.wait(60)


if __name__ == "__main__":
    main()
