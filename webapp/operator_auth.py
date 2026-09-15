"""Single-operator security boundary for capital-control APIs."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel


COOKIE_NAME = "ats_operator_session"
SESSION_TTL = 12 * 60 * 60
LOGIN_WINDOW = 5 * 60
MAX_FAILURES = 5


@dataclass
class Session:
    operator_id: str
    expires_at: float
    csrf_token: str


class LoginRequest(BaseModel):
    password: str


class OperatorAuth:
    def __init__(self, password_hash: str, session_secret: str):
        self.password_hash = password_hash.strip()
        self.session_secret = session_secret.strip()
        self._sessions: dict[str, Session] = {}
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_dashboard_data(cls):
        base = Path("/app/dashboard_data/secrets")
        return cls(
            (base / "operator_password_hash").read_text(),
            (base / "session_secret").read_text(),
        )

    def verify_password(self, password: str) -> bool:
        try:
            kind, iterations, salt, expected = self.password_hash.split("$")
            if kind != "pbkdf2_sha256":
                return False

            digest = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode(),
                base64.urlsafe_b64decode(salt),
                int(iterations),
            )

            actual = base64.urlsafe_b64encode(digest).decode()
            return hmac.compare_digest(actual, expected)
        except Exception:
            return False

    def _session_key(self, token: str) -> str:
        return hmac.new(
            self.session_secret.encode("utf-8"),
            token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _client_key(self, request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def _login_allowed(self, request: Request) -> bool:
        now = time.time()
        key = self._client_key(request)

        with self._lock:
            failures = [
                ts for ts in self._failures.get(key, [])
                if now - ts < LOGIN_WINDOW
            ]
            self._failures[key] = failures
            return len(failures) < MAX_FAILURES

    def _record_failure(self, request: Request) -> None:
        with self._lock:
            self._failures.setdefault(
                self._client_key(request), []
            ).append(time.time())

    def login(self, request: Request, password: str) -> tuple[str, Session]:
        if not self._login_allowed(request):
            raise HTTPException(
                status_code=429,
                detail="Too many failed login attempts.",
            )

        if not self.verify_password(password):
            self._record_failure(request)
            raise HTTPException(
                status_code=401,
                detail="Invalid operator credentials.",
            )

        token = secrets.token_urlsafe(48)
        session = Session(
            operator_id="owner",
            expires_at=time.time() + SESSION_TTL,
            csrf_token=secrets.token_urlsafe(32),
        )

        with self._lock:
            self._sessions[self._session_key(token)] = session
            self._failures.pop(self._client_key(request), None)

        return token, session

    def get_session(self, request: Request) -> Session | None:
        token = request.cookies.get(COOKIE_NAME)
        if not token:
            return None

        with self._lock:
            key = self._session_key(token)
            session = self._sessions.get(key)

            if session is None:
                return None

            if session.expires_at <= time.time():
                self._sessions.pop(key, None)
                return None

            return session

    def require_operator(self, request: Request) -> Session:
        session = self.get_session(request)
        if session is None:
            raise HTTPException(
                status_code=401,
                detail="Operator authentication required.",
            )
        return session

    def require_csrf(self, request: Request) -> Session:
        session = self.require_operator(request)
        supplied = request.headers.get("X-CSRF-Token", "")

        if not hmac.compare_digest(
            supplied,
            session.csrf_token,
        ):
            raise HTTPException(
                status_code=403,
                detail="Invalid CSRF token.",
            )

        return session

    def logout(self, request: Request) -> None:
        token = request.cookies.get(COOKIE_NAME)
        if token:
            with self._lock:
                self._sessions.pop(self._session_key(token), None)


def build_operator_auth_router(auth: OperatorAuth) -> APIRouter:
    router = APIRouter(
        prefix="/api/pro/auth",
        tags=["Operator Security"],
    )

    @router.post("/login")
    def login(body: LoginRequest, request: Request, response: Response):
        token, session = auth.login(request, body.password)

        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            samesite="strict",
            secure=False,
            max_age=SESSION_TTL,
            path="/",
        )

        return {
            "authenticated": True,
            "operator_id": session.operator_id,
        }

    @router.get("/session")
    def session(request: Request):
        current = auth.get_session(request)
        return {
            "authenticated": current is not None,
            "operator_id": current.operator_id if current else None,
        }

    @router.get("/csrf")
    def csrf(request: Request):
        current = auth.require_operator(request)
        return {"csrf_token": current.csrf_token}

    @router.post("/logout")
    def logout(request: Request, response: Response):
        auth.require_csrf(request)
        auth.logout(request)
        response.delete_cookie(COOKIE_NAME, path="/")
        return {"authenticated": False}

    return router
