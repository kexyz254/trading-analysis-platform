import base64
import hashlib
import secrets

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from webapp.operator_auth import OperatorAuth


PASSWORD = "Correct-Horse-Battery-2026"


def password_hash(password=PASSWORD):
    iterations = 10_000
    salt = secrets.token_bytes(16)

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        iterations,
    )

    return (
        f"pbkdf2_sha256${iterations}$"
        f"{base64.urlsafe_b64encode(salt).decode()}$"
        f"{base64.urlsafe_b64encode(digest).decode()}"
    )


def request(cookie=None, csrf=None):
    headers = []

    if cookie:
        headers.append(
            (b"cookie", f"ats_operator_session={cookie}".encode())
        )

    if csrf:
        headers.append((b"x-csrf-token", csrf.encode()))

    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
        "scheme": "http",
        "query_string": b"",
    })


def auth():
    return OperatorAuth(
        password_hash(),
        "independent-test-session-secret",
    )


def test_valid_password_creates_session():
    service = auth()

    token, created = service.login(
        request(),
        PASSWORD,
    )

    restored = service.get_session(request(cookie=token))

    assert restored is created
    assert restored.operator_id == "owner"


def test_wrong_password_is_rejected():
    service = auth()

    with pytest.raises(HTTPException) as exc:
        service.login(request(), "wrong-password")

    assert exc.value.status_code == 401


def test_csrf_required_for_mutating_operator_action():
    service = auth()

    token, session = service.login(request(), PASSWORD)

    with pytest.raises(HTTPException) as exc:
        service.require_csrf(request(cookie=token))

    assert exc.value.status_code == 403

    restored = service.require_csrf(
        request(
            cookie=token,
            csrf=session.csrf_token,
        )
    )

    assert restored.operator_id == "owner"


def test_logout_invalidates_session():
    service = auth()

    token, _ = service.login(request(), PASSWORD)

    authenticated = request(cookie=token)
    assert service.get_session(authenticated) is not None

    service.logout(authenticated)

    assert service.get_session(authenticated) is None


def test_rate_limit_after_repeated_failures():
    service = auth()

    for _ in range(5):
        with pytest.raises(HTTPException):
            service.login(request(), "wrong")

    with pytest.raises(HTTPException) as exc:
        service.login(request(), PASSWORD)

    assert exc.value.status_code == 429
