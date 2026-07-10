"""External health-check ping - see RUNBOOK.md section 4.

Runs on a schedule (cron), completely independently of the engine/
dashboard containers, and pages via email if /api/health is unreachable
or reports anything other than "healthy". Deliberately dependency-free
(stdlib only: urllib, smtplib, email) so it runs directly on the VPS host
with the system Python3 - no virtualenv, no pip install, nothing shared
with the containers' own environment to go stale or diverge from.

Reads SMTP_HOST/SMTP_PORT/SMTP_USERNAME/SMTP_PASSWORD/ALERT_EMAIL_FROM/
ALERT_EMAIL_TO directly from the .env file (same convention/values the
engine's own AlertManager uses - see trading_assistant/agents/alerting.py
_send_email for the exact SMTP pattern this mirrors) - parsed manually
rather than via python-dotenv, which may not be installed on the bare host.

Usage (cron, every 5 minutes):
    */5 * * * * cd /path/to/trading-analysis-platform && python3 scripts/healthcheck_ping.py >> logs/healthcheck.log 2>&1
"""

from __future__ import annotations

import json
import smtplib
import sys
import urllib.request
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, Optional

HEALTH_URL = "http://localhost:8000/api/health"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _parse_env(path: Path) -> Dict[str, str]:
    """Minimal KEY=VALUE parser - no external dependency on python-dotenv,
    which may not be installed on the bare host (only inside the
    containers' own image)."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _send_email(env: Dict[str, str], subject: str, body: str) -> None:
    smtp_username = env.get("SMTP_USERNAME")
    smtp_password = env.get("SMTP_PASSWORD")
    email_to = [addr.strip() for addr in env.get("ALERT_EMAIL_TO", "").split(",") if addr.strip()]
    if not (smtp_username and smtp_password and email_to):
        print("SMTP not fully configured in .env - cannot send alert email", file=sys.stderr)
        return

    email_from = env.get("ALERT_EMAIL_FROM") or smtp_username
    smtp_host = env.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(env.get("SMTP_PORT", "587"))

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = ", ".join(email_to)
    message.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001 - a failed alert send must not crash the cron job
        print(f"failed to send alert email: {exc}", file=sys.stderr)


def _fetch_health() -> Optional[dict]:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=10) as response:
            return json.loads(response.read())
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    env = _parse_env(ENV_PATH)
    payload = _fetch_health()

    if payload is None:
        print("UNREACHABLE")
        _send_email(
            env,
            subject="[CRITICAL] trading-platform health endpoint unreachable",
            body="scripts/healthcheck_ping.py could not reach http://localhost:8000/api/health - the dashboard container may be down.",
        )
        sys.exit(1)

    label = payload.get("label", "unknown")
    if label != "healthy":
        print(f"UNHEALTHY: {label} - {payload}")
        _send_email(
            env,
            subject=f"[ALERT] trading-platform health check reports: {label}",
            body=f"Health check result: {json.dumps(payload, indent=2)}",
        )
    else:
        print("healthy")


if __name__ == "__main__":
    main()
