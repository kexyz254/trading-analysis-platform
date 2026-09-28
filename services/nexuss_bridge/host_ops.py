"""Host-local, allowlisted maintenance executor. No remote shell or trade APIs.

Installed as a systemd service. Only the signed bridge can reach its Unix
socket; Docker's privileged socket never enters the bridge container.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import socketserver
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

MAX_MESSAGE = 4096
SERVICE = "trading-analysis-platform-dashboard-1"
READY = "http://127.0.0.1:8000/api/ready"


def signature(secret: bytes, operation: str, request_id: str, stamp: int) -> str:
    return hmac.new(secret, f"ops-v1\n{operation}\n{request_id}\n{stamp}".encode(),
                    hashlib.sha256).hexdigest()


def ready() -> bool:
    try:
        with urllib.request.urlopen(READY, timeout=3) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


class Operations:
    def __init__(self, secret: bytes, database: str, *, probe=ready, restart=None, clock=time.time):
        if len(secret) < 32:
            raise ValueError("Invalid maintenance secret")
        self.secret, self.database, self.probe, self.clock = secret, database, probe, clock
        self.restart = restart or self._restart
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, operation TEXT, "
                       "requested_at INTEGER, status TEXT, verified INTEGER)")

    @staticmethod
    def _restart():
        result = subprocess.run(["/usr/bin/docker", "restart", "-t", "60", SERVICE],
                                capture_output=True, timeout=80, check=False)
        return result.returncode == 0

    def execute(self, data: dict) -> dict:
        if not isinstance(data, dict) or set(data) != {"operation", "request_id", "time", "signature"}:
            raise ValueError("Invalid request")
        operation, request_id, stamp = data["operation"], data["request_id"], data["time"]
        if (operation not in {"diagnostics", "restart_dashboard"}
                or not isinstance(request_id, str)
                or not re.fullmatch(r"[a-f0-9]{32}", request_id)
                or type(stamp) is not int or abs(int(self.clock()) - stamp) > 60
                or not isinstance(data["signature"], str)
                or not hmac.compare_digest(data["signature"], signature(self.secret, operation, request_id, stamp))):
            raise ValueError("Authentication failed")
        with sqlite3.connect(self.database, timeout=10) as db:
            row = db.execute("SELECT operation, status, verified FROM receipts WHERE id=?",
                             (request_id,)).fetchone()
            if row:
                if row[0] != operation:
                    raise ValueError("Request ID conflict")
                return {"request_id": request_id, "operation": operation,
                        "status": row[1], "verified": bool(row[2]), "replayed": True}
            # Durable reservation before performing any restart. A crash
            # after this point never permits accidental replay of an action.
            db.execute("INSERT INTO receipts VALUES (?, ?, ?, 'reserved', 0)",
                       (request_id, operation, stamp))
        status, verified = "unavailable", False
        if operation == "diagnostics":
            verified = self.probe()
            status = "healthy" if verified else "unhealthy"
        else:
            # A healthy dashboard must never be bounced by a model request.
            # Check twice, allowing a transient failure to self-resolve.
            if self.probe():
                status, verified = "healthy_noop", True
            else:
                time.sleep(2)
                if self.probe():
                    status, verified = "healthy_noop", True
                else:
                    try:
                        if self.restart():
                            verified = self.probe()
                            status = "restarted_verified" if verified else "restarted_unverified"
                        else:
                            status = "restart_failed"
                    except (OSError, subprocess.TimeoutExpired):
                        status = "restart_failed"
        with sqlite3.connect(self.database, timeout=10) as db:
            db.execute("UPDATE receipts SET status=?, verified=? WHERE id=? AND status='reserved'",
                       (status, int(verified), request_id))
        return {"request_id": request_id, "operation": operation,
                "status": status, "verified": verified, "replayed": False}


def serve(socket_path: Path, operations: Operations):
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        # Refuse to replace a live listener or a file placed at this path.
        if not socket_path.is_socket():
            raise RuntimeError("Maintenance socket path is occupied")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            try:
                client.connect(str(socket_path))
            except ConnectionRefusedError:
                socket_path.unlink()
            else:
                raise RuntimeError("Maintenance executor already running")

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            raw = self.rfile.readline(MAX_MESSAGE + 1)
            try:
                if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                    raise ValueError("Invalid request")
                result = operations.execute(json.loads(raw))
            except (ValueError, TypeError, json.JSONDecodeError):
                result = {"status": "rejected", "verified": False}
            self.wfile.write(json.dumps(result, separators=(",", ":")).encode() + b"\n")

    with socketserver.UnixStreamServer(str(socket_path), Handler) as server:
        try:
            os.chown(socket_path, 0, 65532)
            os.chmod(socket_path, 0o660)
            server.serve_forever()
        finally:
            socket_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    serve(Path(args.socket), Operations(Path(args.key_file).read_bytes().strip(), args.state))


if __name__ == "__main__":
    main()
