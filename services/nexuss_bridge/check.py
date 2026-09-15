"""Signed deployment smoke check; does not print credentials or raw error bodies."""
import json
import os
from pathlib import Path

import httpx

from .protocol import signed_headers


def main():
    secret = Path(os.environ["NEXUSS_BRIDGE_SECRET_FILE"]).read_bytes().strip()
    with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
        for target in ("/agent/v1/evidence/health", "/agent/v1/worker/status"):
            try:
                response = client.get("http://127.0.0.1:8300" + target,
                                      headers=signed_headers(secret, "GET", target))
                if response.status_code != 200:
                    print(f"{target}: HTTP {response.status_code}")
                    raise SystemExit(1)
                payload = response.json()
                keys = ("score", "circuit_breaker_tripped", "staleness_seconds") if "data" in payload else (
                    "status", "last_checked", "stored_events", "execution_authorized")
                data = payload.get("data", payload)
                print(target + ": " + json.dumps({k: data.get(k) for k in keys}))
            except (httpx.HTTPError, ValueError):
                print("Bridge check failed: unavailable or invalid response")
                raise SystemExit(1) from None


if __name__ == "__main__":
    main()
