# P6.17 supervisory gateway — connection milestone

This is a limited, opt-in first milestone, not full autonomous management.
It exposes projected health/status/decision data and durable advisory feedback.
No engine files, production configuration, exchange keys, Docker socket, execution
endpoints, strategy parameters or breaker controls are mounted or callable.
Existing engine and dashboard containers do not need restarting.

## Protocol

All requests require HMAC-SHA256 over UTF-8 `v1\nMETHOD\nPATH_AND_QUERY\nTIMESTAMP\nNONCE\nBODY_SHA256`.
Headers: X-Nexuss-Time (Unix seconds), X-Nexuss-Nonce (32 lowercase hex),
X-Nexuss-Signature (hex HMAC). Timestamp tolerance is 60 seconds. Replay records
survive restart. Use a random 32-byte-or-longer secret supplied through a private
file. Never put it in source control or chat. HTTP is for loopback only here;
remote access requires a separately configured private tunnel or HTTPS proxy.

GET /agent/v1/capabilities
GET /agent/v1/evidence/health
GET /agent/v1/evidence/status
GET /agent/v1/evidence/decisions?symbol=BTC%2FUSDT
POST /agent/v1/feedback
GET /agent/v1/feedback/{feedback_id}

Feedback fields: feedback_id, kind (research/incident/repair_proposal/evaluation),
summary, sources (HTTPS URLs without credentials or queries), observed_at,
expires_at, execution_requested=false. Times require timezone offsets. IDs are
idempotent; conflicting reuse returns 409. Feedback is stored only, never executed.
Do not submit credentials in feedback text. Data field projection deliberately
omits raw logs and reason text; this is not a universal secret scrubber.

## Deployment, after review

Use a separate checkout of the reviewed bridge branch. Do not pull over the VPS's
modified config.yaml/docker-compose.yml. From that separate checkout:

```bash
sudo install -d -m 0700 /etc/nexuss-bridge /var/lib/nexuss-bridge
sudo chown 65532:65532 /etc/nexuss-bridge /var/lib/nexuss-bridge
sudo bash -c 'set -e; umask 077; if [ ! -e /etc/nexuss-bridge/bridge.key ]; then openssl rand -hex 32 > /etc/nexuss-bridge/bridge.key; fi; chown 65532:65532 /etc/nexuss-bridge/bridge.key'
docker compose -p nexuss-supervisor -f services/nexuss_bridge/compose.yaml up -d --build
```

The gateway binds only 127.0.0.1:8300. Transfer the key privately to the Nexuss
machine using your existing authenticated SSH connection. For an initial session,
use `ssh -N -L 8300:127.0.0.1:8300 root@167.233.158.160` from that machine.
Configure NEXUSS_TAS_BRIDGE_URL=http://127.0.0.1:8300 and
NEXUSS_TAS_SECRET_FILE to the private local key path before restarting Nexuss.
This SSH tunnel is session-bound, not an always-on solution.

Verify with Nexuss GET /v1/trading/evidence/health from local control.
HTTP success is separate from TAS health: a tripped breaker remains critical.
Rollback: `docker compose -p nexuss-supervisor -f services/nexuss_bridge/compose.yaml down`.
Retain /var/lib/nexuss-bridge and its replay/feedback database; do not delete volumes.

## Outstanding before the complete requested system

### Offline observer

Compose also starts offline-observer on the VPS. Every 60 seconds it makes a
signed local health request; this is health polling, not decision-event streaming.
It stores health transitions and hourly checkpoints in observations.db and
continues while Windows Nexuss is offline. It never calls an LLM, trades, resets
a breaker, or restarts an execution worker. If TAS is unreachable it records an
unavailable observation and retries; it cannot gather fresh data during an outage.

GET /agent/v1/worker/status reports last_checked (Unix time), queue count and state.
Consumers must check last_checked for staleness; observing is not a liveness proof.
GET /agent/v1/observations?after=0&limit=100 provides ordered replay with next_cursor.
Persist that cursor only after processing; reads do not remove events. Replay is
consumer-driven, not automatic delivery to Windows. At 50,000 events recording
stops with backlog_full, preserving existing evidence. No automatic purge exists.
This is a bounded observer, not the full research/repair worker requested.

After startup, verify without printing secrets:

```bash
docker compose -p nexuss-supervisor -f services/nexuss_bridge/compose.yaml exec -T nexuss-bridge python -m services.nexuss_bridge.check
```

### Remaining work

### Optional tunnel-free transport

The additional tailscale.yaml service listens only on 100.93.64.23:8300,
the VPS Tailscale IP observed during setup. It leaves the loopback listener and
offline observer in place. Both listeners share persistent replay protection.
Enable only after confirming both machines belong to your private tailnet and
network ACLs allow the intended Windows client. No Tailscale enrollment or ACL
changes are performed by this code. The startup address validator refuses public
and wildcard binds; a missing Tailscale address causes startup to fail.

```bash
docker compose -p nexuss-supervisor -f services/nexuss_bridge/compose.yaml -f services/nexuss_bridge/tailscale.yaml up -d --build
```

On Windows, configure the client with http://100.93.64.23:8300 using the existing
private key. A successful signed health request verifies the complete private path.
Do not close the SSH tunnel until that request succeeds. This transport removes
the open-terminal dependency, but is not an always-on Nexuss AI worker.

- Transactional decision outbox and event push/replay; current reads are on demand.
- Live news/economic/research providers, provenance, freshness and evaluation.
- Capability registry, conversation routing and dashboard integration in Nexuss.
- Automated diagnosis, isolated patch/test workflow and governed maintenance.
- Approved strategy activation and outcome comparison; feedback alone is not improvement.
- Always-on Nexuss worker and production private transport provisioning.
- Review of server-specific configuration and live deployment verification.

No profitability or accuracy gain is claimed. Tests use fake TAS responses and
temporary SQLite files; no exchange or production database is contacted.
