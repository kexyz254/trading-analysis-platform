"""Pinned dashboard overlay releases; no arbitrary command or engine operations.

This is a separate host service. The bridge sends only signed, typed release
requests over its Unix socket. An operator installs this code and its fixed
Dockerfile; Git source blobs never become Dockerfile instructions.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shutil
import socket
import socketserver
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


REPO = Path("/root/trading-analysis-platform")
STATE = Path("/var/lib/nexuss-release")
BASE = "trading-analysis-platform-dashboard:nexuss-release-base"
SERVICE = "trading-analysis-platform-dashboard-1"
FILES = ("webapp/price_call_results.py", "webapp/nexuss_account_overlay.py")
READY = "http://127.0.0.1:8000/api/ready"
RESULTS = "http://127.0.0.1:8000/api/agent/btc-price-call-results"
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
ID = re.compile(r"[0-9a-f]{32}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def signature(secret, operation, request_id, stamp, commit, digest, approval_signature=""):
    message = (f"release-v1\n{operation}\n{request_id}\n{stamp}\n{commit}\n{digest}\n"
               f"{approval_signature}").encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def approval_signature(secret, request_id, commit, digest):
    message = f"approved-release-v1\n{request_id}\n{commit}\n{digest}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _run(argv, timeout=25, input=None):
    result = subprocess.run(argv, input=input, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError("Release prerequisite or command failed")
    return result.stdout


def _probe():
    try:
        with urllib.request.urlopen(READY, timeout=3) as response:
            if response.status != 200:
                return False
        with urllib.request.urlopen(RESULTS, timeout=4) as response:
            if response.status != 200:
                return False
            body = response.read(200_000)
        value = json.loads(body)
        return (value.get("status") == "available" and value.get("symbol") == "BTC/USDT"
                and set((value.get("horizons") or {})) == {"5m", "15m"})
    except (OSError, ValueError, TypeError):
        return False


class Releases:
    def __init__(self, secret, database, *, approval_secret, repo=REPO, state=STATE, run=_run,
                 probe=_probe, clock=time.time):
        if len(secret) < 32 or len(approval_secret) < 32 or secret == approval_secret:
            raise ValueError("Invalid release secret")
        self.secret, self.approval_secret = secret, approval_secret
        self.repo, self.state = Path(repo), Path(state)
        self.run, self.probe, self.clock = run, probe, clock
        self.database = str(database)
        with sqlite3.connect(database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS releases (
                id TEXT PRIMARY KEY, commit_sha TEXT, digest TEXT,
                base_image TEXT, file_hashes TEXT, status TEXT,
                verified INTEGER DEFAULT 0, created_at INTEGER
            )""")

    def _files(self, commit):
        if not COMMIT.fullmatch(commit):
            raise ValueError("Invalid source commit")
        contents = {}
        for path in FILES:
            raw = self.run(["/usr/bin/git", "-C", str(self.repo), "show", f"{commit}:{path}"], 10)
            if len(raw) > 100_000 or b"\x00" in raw:
                raise ValueError("Oversized or binary release source")
            source = raw.decode("utf-8")
            compile(source, path, "exec")
            contents[path] = raw
        return contents

    def _image(self):
        image = self.run(["/usr/bin/docker", "inspect", "-f", "{{.Image}}", SERVICE], 10).decode().strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image):
            raise ValueError("Invalid running dashboard image")
        return image

    def execute(self, data):
        if not isinstance(data, dict) or set(data) != {"operation", "request_id", "time", "commit", "digest", "signature", "approval_signature"}:
            raise ValueError("Invalid release request")
        op, rid, stamp, commit, digest = (data[x] for x in
            ("operation", "request_id", "time", "commit", "digest"))
        if (op not in {"prepare", "apply"} or not isinstance(rid, str) or not ID.fullmatch(rid)
                or type(stamp) is not int or abs(int(self.clock()) - stamp) > 60
                or not isinstance(commit, str) or not COMMIT.fullmatch(commit)
                or not isinstance(digest, str) or not SHA256.fullmatch(digest)
                or not isinstance(data["signature"], str)
                or not isinstance(data["approval_signature"], str)
                or not hmac.compare_digest(data["signature"],
                        signature(self.secret, op, rid, stamp, commit, digest,
                                  data["approval_signature"]))):
            raise ValueError("Release authentication failed")
        if op == "prepare":
            if data["approval_signature"]:
                raise ValueError("Prepare cannot carry approval")
            return self.prepare(rid, commit, digest)
        if not hmac.compare_digest(data["approval_signature"],
                approval_signature(self.approval_secret, rid, commit, digest)):
            raise ValueError("Exact owner approval required")
        return self.apply(rid, commit, digest)

    def prepare(self, rid, commit, digest):
        if digest != "0" * 64:
            raise ValueError("Prepare has no prior digest")
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT commit_sha,digest,base_image,file_hashes,status,verified FROM releases WHERE id=?", (rid,)).fetchone()
        if row:
            if row[0] != commit:
                raise ValueError("Release ID conflict")
            return {"release_id": rid, "commit": commit, "digest": row[1], "base_image": row[2],
                    "file_hashes": json.loads(row[3]), "status": row[4], "verified": bool(row[5]), "replayed": True}
        if shutil.disk_usage("/").free < 2 * 1024 ** 3 or not self.probe():
            raise ValueError("Release prerequisites unavailable")
        files = self._files(commit)
        hashes = {p: hashlib.sha256(v).hexdigest() for p, v in files.items()}
        base = self._image()
        manifest = json.dumps({"commit": commit, "base_image": base, "files": hashes},
                              sort_keys=True, separators=(",", ":")).encode()
        actual = hashlib.sha256(manifest).hexdigest()
        with sqlite3.connect(self.database) as db:
            db.execute("INSERT INTO releases VALUES (?, ?, ?, ?, ?, 'prepared', 0, ?)",
                       (rid, commit, actual, base, json.dumps(hashes), int(self.clock())))
        return {"release_id": rid, "commit": commit, "digest": actual, "base_image": base,
                "file_hashes": hashes, "status": "prepared", "verified": False, "replayed": False}

    def apply(self, rid, commit, digest):
        with sqlite3.connect(self.database, timeout=10) as db:
            row = db.execute("SELECT commit_sha,digest,base_image,file_hashes,status,verified,created_at FROM releases WHERE id=?", (rid,)).fetchone()
            if not row or row[0] != commit or not hmac.compare_digest(row[1], digest):
                raise ValueError("Unknown or changed release")
            if row[4] != "prepared":
                return {"release_id": rid, "commit": commit, "digest": digest,
                        "status": row[4], "verified": bool(row[5]), "replayed": True}
            if int(self.clock()) - row[6] > 1800:
                raise ValueError("Release approval window expired")
            # Reserve before building or replacing the container. A crash cannot replay a write.
            db.execute("UPDATE releases SET status='reserved' WHERE id=? AND status='prepared'", (rid,))
        status, verified = "failed", False
        deployed = False
        try:
            files = self._files(commit)
            if ({p: hashlib.sha256(v).hexdigest() for p, v in files.items()} != json.loads(row[3])
                    or self._image() != row[2] or shutil.disk_usage("/").free < 2 * 1024 ** 3):
                status = "precondition_changed"
            else:
                self.state.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix="release-", dir=self.state) as folder:
                    context = Path(folder)
                    for path, content in files.items():
                        output = context / path
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_bytes(content)
                    (context / "Dockerfile").write_text(
                        "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n"
                        "COPY --chown=appuser:appuser webapp/price_call_results.py "
                        "webapp/nexuss_account_overlay.py /app/webapp/\n", encoding="utf-8")
                    tag = "trading-analysis-platform-dashboard:nexuss-release-" + rid
                    rollback = "trading-analysis-platform-dashboard:nexuss-rollback-" + rid
                    self.run(["/usr/bin/docker", "image", "tag", row[2], rollback], 15)
                    self.run(["/usr/bin/docker", "build", "--pull=false", "--network=none",
                              "--build-arg", "BASE_IMAGE=" + rollback, "-t", tag, str(context)], 120)
                    override = context / "dashboard.json"
                    override.write_text(json.dumps({"services": {"dashboard": {"image": tag,
                        "command": ["uvicorn", "webapp.nexuss_account_overlay:app", "--host",
                                    "0.0.0.0", "--port", "8000"]}}}), encoding="utf-8")
                    compose = ["/usr/bin/docker", "compose", "-p", "trading-analysis-platform",
                               "-f", str(self.repo / "docker-compose.yml"), "-f", str(override),
                               "up", "-d", "--no-deps", "--no-build", "dashboard"]
                    # Docker Compose can replace the container before its
                    # process exits with an error. Treat every attempted up
                    # as a possible live mutation and attempt rollback.
                    deployed = True
                    try:
                        self.run(compose, 90)
                        compose_completed = True
                    except (OSError, RuntimeError, subprocess.TimeoutExpired):
                        compose_completed = False
                    if compose_completed:
                        for _ in range(10):
                            if self.probe():
                                status, verified = "deployed_verified", True
                                break
                            time.sleep(3)
                    if not verified:
                        override.write_text(json.dumps({"services": {"dashboard": {"image": rollback,
                            "command": ["uvicorn", "webapp.nexuss_account_overlay:app", "--host",
                                        "0.0.0.0", "--port", "8000"]}}}), encoding="utf-8")
                        try:
                            self.run(compose, 90)
                            status = "rolled_back" if self.probe() else "rollback_unverified"
                        except (OSError, RuntimeError, subprocess.TimeoutExpired):
                            status = "rollback_unverified"
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
            # If Compose failed after a partial replacement, the executor
            # must not claim that the old container is running.
            status = "outcome_unverified" if deployed else "build_failed"
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE releases SET status=?,verified=? WHERE id=? AND status='reserved'",
                       (status, int(verified), rid))
        return {"release_id": rid, "commit": commit, "digest": digest,
                "status": status, "verified": verified, "replayed": False}


def serve(path, releases):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_socket():
            raise RuntimeError("Release socket occupied")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            try:
                client.connect(str(path))
            except ConnectionRefusedError:
                path.unlink()
            else:
                raise RuntimeError("Release executor already running")

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            raw = self.rfile.readline(4097)
            try:
                if len(raw) > 4096 or not raw.endswith(b"\n"):
                    raise ValueError("Invalid release message")
                result = releases.execute(json.loads(raw))
            except (ValueError, RuntimeError, TypeError, json.JSONDecodeError):
                result = {"status": "rejected", "verified": False}
            self.wfile.write(json.dumps(result, separators=(",", ":")).encode() + b"\n")

    with socketserver.UnixStreamServer(str(path), Handler) as server:
        try:
            os.chown(path, 0, 65532)
            os.chmod(path, 0o660)
            server.serve_forever()
        finally:
            path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--approval-key-file", required=True)
    parser.add_argument("--state", required=True)
    args = parser.parse_args()
    serve(args.socket, Releases(Path(args.key_file).read_bytes().strip(), args.state,
        approval_secret=Path(args.approval_key_file).read_bytes().strip()))


if __name__ == "__main__":
    main()
