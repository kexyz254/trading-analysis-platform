# VPS Deployment Runbook

This covers the four operational items standing between local testing and a
genuinely unattended, 24/7 VPS deployment. Everything on this list has a
code-level piece already done in this repo; what remains here is the
one-time ops setup to actually turn it on.

## 0. Prerequisites

- A VPS with Docker and Docker Compose installed (any $5/mo box is enough
  for testnet - this is not compute-heavy).
- This repo cloned onto it, `.env` populated from `.env.example` (real
  values, never committed).
- `touch trading_system.db` before the first `docker compose up` -
  `docker-compose.yml` bind-mounts this file, and if it doesn't exist yet
  Docker will silently create it as an empty **directory** instead, which
  breaks SQLite. One-time gotcha, only matters on a fresh clone.

## 1. PID lock - already handled, nothing to do here

`run_system.py` refuses to start a second instance against the same
database (`trading_assistant/process_lock.py`, verified live: a real
second-launch attempt was refused with a clear error). No ops step needed.

## 2. Graceful shutdown - already handled, nothing to do here

`Orchestrator.request_shutdown()` (wired to SIGTERM/SIGINT) checks a
shutdown flag between symbols inside the retrain/tournament loops, not
just at the top of the main cycle loop - bounds a graceful stop to roughly
one symbol's retrain+tournament pass (~45s measured against live data),
not the full 15-25 minute universe pass. `docker-compose.yml`'s
`stop_grace_period: 60s` on the engine service gives that a real margin
before Docker escalates to SIGKILL.

## 3. Database backup automation

The script (`scripts/backup_database.py`) exists and is tested - online
backup via SQLite's own backup API (safe under WAL mode, unlike a raw file
copy), with rotation. What's missing is a scheduler to actually run it.

**Recommended: a host-level cron entry** (not inside the Docker Compose
stack - keeps backup/retention policy, which is an ops concern, separate
from the trading containers, the same way this repo already keeps
`scripts/emergency_flatten.py` and `scripts/backup_database.py`
standalone rather than folding them into the engine process):

```cron
# /etc/cron.d/trading-backup (as root, or crontab -e as the deploy user)
0 2 * * * cd /path/to/trading-analysis-platform && PYTHONPATH=. python3 scripts/backup_database.py --keep 14 >> logs/backup.log 2>&1
```

Runs daily at 02:00, keeps the most recent 14 backups in `backups/`
(gitignored). **This alone is not enough** - a backup on the same disk as
what it's backing up doesn't survive a disk failure. Add an off-server
sync as a second cron line once you've picked a destination (rclone to
S3/B2/etc., or a simple `rsync` to a second machine):

```cron
30 2 * * * rclone sync /path/to/trading-analysis-platform/backups remote:trading-backups
```

Deliberately not chosen for you - `scripts/backup_database.py` doesn't
assume a specific remote target.

## 4. External health monitoring

`/api/health` already exists and returns a real, computed status (not a
fabricated "OK") - `{"score", "label", "staleness_seconds",
"recent_error_rate", "circuit_breaker_tripped", ...}`. `docker-compose.yml`
now also runs this as a container healthcheck (visibility in `docker
compose ps`/`docker inspect` - **not** automatic remediation; plain Docker
Compose doesn't restart a container just because it's marked unhealthy).

The dashboard is deliberately bound to `127.0.0.1` only (zero
authentication - see `docker-compose.yml`'s own comment on why it must
never be exposed publicly), so an external monitor can't hit it directly.
Two options:

**Option A - a second, tiny process on the same VPS** (simplest): a cron
job or systemd timer that curls `localhost:8000/api/health` every few
minutes and pages you (via a webhook, the same `ALERT_WEBHOOK_URL`
mechanism this system already uses) if it fails or stales out:

```bash
#!/usr/bin/env bash
# /path/to/trading-analysis-platform/scripts/healthcheck_ping.sh (not yet
# created - a template, adjust to your actual alerting webhook)
RESPONSE=$(curl -sf http://localhost:8000/api/health) || {
    curl -X POST "$ALERT_WEBHOOK_URL" -d '{"text":"health endpoint unreachable"}'
    exit 1
}
LABEL=$(echo "$RESPONSE" | python3 -c "import json,sys; print(json.load(sys.stdin)['label'])")
if [ "$LABEL" != "healthy" ]; then
    curl -X POST "$ALERT_WEBHOOK_URL" -d "{\"text\":\"health check reports: $LABEL\"}"
fi
```

**Option B - a free external watcher** (UptimeRobot, Healthchecks.io):
these need a **publicly reachable** URL, which conflicts with the
dashboard's deliberate no-auth/loopback-only design. Don't expose
`/api/health` directly to the internet to work around this. If you want a
real external (off-VPS) watcher, put a minimal reverse proxy (Caddy/nginx)
in front of *only* `/api/health` with basic auth or an IP allowlist - a
deliberate, separate decision, not this runbook's default. Option A (a
watcher colocated on the same VPS, alerting via the webhook this system
already has) covers "the engine hung and nobody would otherwise notice"
without opening any new public surface, and is the recommended default.

## 5. First deployment checklist

1. `touch trading_system.db` (see Prerequisites).
2. `docker compose build && docker compose up -d`.
3. `docker compose logs -f engine` - confirm a clean warm-start (symbol
   discovery, retrain attempts, zero tracebacks), same evidence standard
   used throughout local development.
4. `docker compose ps` - both services `Up`, dashboard `(healthy)` once
   `start_period` (30s) elapses.
5. `ssh -L 8000:localhost:8000 user@vps`, then `http://localhost:8000` in
   a local browser - confirm the dashboard loads and shows real data.
6. Set up the cron entries from sections 3 and 4.
7. Kill the engine container (`docker kill <container>`) and confirm
   `restart: unless-stopped` brings it back automatically - the same
   real-restart verification standard already used throughout this
   project, now proven under Docker specifically.
8. Let it run 48-72 hours on testnet, watching the readiness scorecard
   accumulate genuine data, before considering `mode: live`.
