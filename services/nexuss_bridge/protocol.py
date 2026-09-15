"""Versioned HMAC request authentication with persistent replay protection."""
import hashlib
import hmac
import re
import sqlite3
import time
import uuid

MAX_BODY = 65536


def signature(secret, method, target, timestamp, nonce, body):
    digest = hashlib.sha256(body).hexdigest()
    message = f"v1\n{method}\n{target}\n{timestamp}\n{nonce}\n{digest}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def signed_headers(secret, method, target, body=b""):
    timestamp = str(int(time.time()))
    nonce = uuid.uuid4().hex
    return {"X-Nexuss-Time": timestamp, "X-Nexuss-Nonce": nonce,
            "X-Nexuss-Signature": signature(secret, method, target, timestamp, nonce, body)}


class Auth:
    def __init__(self, secret, database):
        if len(secret) < 32:
            raise ValueError("Bridge secret must contain at least 32 bytes")
        self.secret = secret
        self.database = str(database)
        with sqlite3.connect(self.database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS nonces (nonce TEXT PRIMARY KEY, expires INTEGER)")

    def verify(self, method, target, headers, body, now=None):
        now = int(time.time()) if now is None else now
        timestamp = headers.get("x-nexuss-time", "")
        nonce = headers.get("x-nexuss-nonce", "")
        supplied = headers.get("x-nexuss-signature", "")
        if (not re.fullmatch(r"[0-9]{1,12}", timestamp)
                or abs(now - int(timestamp)) > 60
                or not re.fullmatch(r"[a-f0-9]{32}", nonce)
                or len(body) > MAX_BODY):
            raise ValueError("Authentication failed")
        expected = signature(self.secret, method, target, timestamp, nonce, body)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("Authentication failed")
        with sqlite3.connect(self.database, timeout=5) as db:
            db.execute("DELETE FROM nonces WHERE expires < ?", (now,))
            try:
                db.execute("INSERT INTO nonces VALUES (?, ?)", (nonce, int(timestamp) + 61))
            except sqlite3.IntegrityError:
                raise ValueError("Authentication failed") from None
